# darqos
# Copyright (C) 2026 David Arnold

# The Terminal (see doc/terminal.rst): the session.  The p-kernel starts
# it at boot, and reboots if it exits.  It starts the compositor, with
# its own Wayland connection and the policy channel (darq.compositor),
# and decides what the compositor does: the hotkeys (see doc/ux.rst),
# its panels, and locking.
#
# - A background and a lock window on every screen (screens.py).  It
#   starts locked, as the compositor does.
# - Sys-n New, Sys-h History, Sys-Tab the switcher, Sys-b shut down or
#   reboot, Sys-x close the focused window, Sys-l lock; Sys-space,
#   Sys-e and Sys-i show a TBD panel (panels.py).  While it's locked,
#   only Sys-b works, on the lock screen.
#
# Its calls to services are synchronous, on Qt's thread, as devterm's
# are, but for creating and using items, which its Launcher does on other
# threads (see darq.ui.launcher).

import logging
import os
import select
import time
import typing

from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication
from PyQt6.sip import voidptr

from darq.compositor import COMPOSITOR, Compositor, PolicyChannel
from darq.dipc import Message
from darq.errors import ChannelClosedError, MessageDecodingError
from darq.loop import QtEventLoop
from darq.protocols.compositor import (
    BindHotkey,
    CloseWindow,
    FocusWindow,
    HotkeyPressed,
    Lock,
    Modifier,
    RaiseWindow,
    Ready,
    SystemReleased,
    Unlock,
    WindowAdded,
    WindowFocused,
    WindowRemoved,
    WindowRetitled,
)
from darq.ui import Services
from darq.ui.launcher import Launcher

from panels import HistoryPanel, NewPanel, NoticePanel, Panel, PowerPanel, SwitcherPanel, TbdPanel
from screens import Screens


# History source for the events it records.
SOURCE = "terminal"

# Seconds to wait for the compositor to be ready.
READY_TIMEOUT = 10.0

# xkb keysyms of its hotkeys, as on their keys' first level.
XK_space = 0x20
XK_b = 0x62
XK_e = 0x65
XK_h = 0x68
XK_i = 0x69
XK_l = 0x6c
XK_n = 0x6e
XK_x = 0x78
XK_Tab = 0xff09

# Hotkeys, with System and these modifiers, and the method each calls.
HOTKEYS: dict[tuple[int, int], str] = {
    (XK_n, 0): "show_new",
    (XK_h, 0): "show_history",
    (XK_Tab, 0): "switch_forward",
    (XK_Tab, Modifier.SHIFT): "switch_back",
    (XK_x, 0): "close_window",
    (XK_b, 0): "show_power",
    (XK_l, 0): "lock",
    (XK_space, 0): "show_search",
    (XK_e, 0): "show_events",
    (XK_i, 0): "show_item_info",
}


class Terminal:
    """The Terminal's state, and its handling of the compositor's
    messages and hotkeys.  It's also the Host for the panels' shared
    parts (see darq.ui)."""

    def __init__(self, app: QApplication, channel: PolicyChannel, services: Services):
        self.app = app
        self.channel = channel
        self.services = services
        self.loop = QtEventLoop()
        self.launcher = Launcher(self, SOURCE, self.loop.post)

        # The compositor's windows, by number, with their titles; their
        # numbers, most recently focused first; and the focused one's (0
        # if it's none of them).
        self.windows: dict[int, str] = {}
        self.recent: list[int] = []
        self.focused = 0

        # While the switcher's showing: the windows it offers, and the
        # one that'll be chosen.
        self.switching: list[int] | None = None
        self.switch_index = 0

        self.locked = True
        self.screens = Screens(app, self.unlock, self.power)
        self.new = NewPanel(self)
        self.history = HistoryPanel(self, self.type_ids)
        self.switcher = SwitcherPanel()
        self.power_panel = PowerPanel(self.power)
        self.tbd = TbdPanel()
        self.notice = NoticePanel()
        self.panels: list[Panel] = [self.new, self.history, self.switcher, self.power_panel, self.tbd,
                                    self.notice]

        self.notifier = QtCore.QSocketNotifier(voidptr(channel.fileno()), QtCore.QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.read_channel)

    def start(self):
        """Bind the hotkeys, and show the screens, locked."""
        for keysym, modifiers in HOTKEYS:
            self.channel.send(BindHotkey(keysym=keysym, modifiers=modifiers))
        self.screens.start()

    # Host

    def show_status(self, message: str):
        logging.info(message)
        for panel in self.panels:
            if panel.isVisible():
                panel.set_status(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        """A launch from New or History has finished: close its panel, or
        show what went wrong, in the panel, or, if it's closed, a notice."""
        panel = {self.new.types: self.new, self.history.view: self.history}.get(context)
        if error is None:
            logging.info(f"Using {item_id}")
            if panel is not None:
                panel.hide()
        elif panel is not None and panel.isVisible():
            logging.info(error)
            panel.set_status(error)
        else:
            logging.info(error)
            self.hide_panels(but=self.notice)
            self.notice.show_message(error)

    def type_ids(self) -> list[str]:
        return [entry.type_id for entry in self.new.types.entries]

    # The compositor

    def read_channel(self):
        while True:
            try:
                message = self.channel.receive()
            except ChannelClosedError:
                logging.error("The compositor closed the policy channel: exiting")
                self.notifier.setEnabled(False)
                self.app.exit(1)
                return
            except MessageDecodingError as e:
                logging.warning(f"Bad message from the compositor: {e}")
                continue
            if message is None:
                return
            self.handle(message)

    def handle(self, message: Message):
        if isinstance(message, HotkeyPressed):
            self.hotkey(message.keysym, message.modifiers)
        elif isinstance(message, SystemReleased):
            self.choose()
        elif isinstance(message, WindowAdded):
            self.windows[message.window] = message.title
            if message.window not in self.recent:
                self.recent.append(message.window)
        elif isinstance(message, WindowRemoved):
            self.window_removed(message.window)
        elif isinstance(message, WindowRetitled):
            if message.window in self.windows:
                self.windows[message.window] = message.title
                self.show_switcher()
        elif isinstance(message, WindowFocused):
            self.focused = message.window
            if message.window in self.recent:
                self.recent.remove(message.window)
                self.recent.insert(0, message.window)
        elif not isinstance(message, Ready):
            logging.warning(f"Unexpected message from the compositor: {message!r}")

    def hotkey(self, keysym: int, modifiers: int):
        name = HOTKEYS.get((keysym, modifiers))
        if name is None:
            logging.warning(f"Unbound hotkey {keysym:#x}, modifiers {modifiers:#x}")
        elif not self.locked:
            getattr(self, name)()
        elif name == "show_power":
            # While it's locked, only Sys-b works, on the lock screen.
            self.screens.power_prompt()

    # Panels

    def show_panel(self, panel: Panel):
        """Show one panel, and no other."""
        self.hide_panels(but=panel)
        panel.open()

    def hide_panels(self, but: Panel | None = None):
        for panel in self.panels:
            if panel is not but:
                panel.hide()
        if but is not self.switcher:
            self.switching = None

    def show_new(self):
        self.show_panel(self.new)

    def show_history(self):
        self.show_panel(self.history)

    def show_power(self):
        self.show_panel(self.power_panel)

    def show_search(self):
        self.hide_panels(but=self.tbd)
        self.tbd.show_for("Search")

    def show_events(self):
        self.hide_panels(but=self.tbd)
        self.tbd.show_for("Events")

    def show_item_info(self):
        self.hide_panels(but=self.tbd)
        self.tbd.show_for("Item info")

    # The switcher

    def switch_forward(self):
        self.switch(1)

    def switch_back(self):
        self.switch(-1)

    def switch(self, step: int):
        """Start switching, at the previous window, or move along."""
        if self.switching is None:
            if not self.recent:
                return
            self.switching = list(self.recent)
            self.switch_index = 1 % len(self.switching) if step > 0 else len(self.switching) - 1
            self.show_panel(self.switcher)
        else:
            self.switch_index = (self.switch_index + step) % len(self.switching)
        self.show_switcher()

    def show_switcher(self):
        if self.switching is not None:
            self.switcher.set_windows([self.windows.get(w, "") for w in self.switching], self.switch_index)

    def choose(self):
        """System's released: raise and focus the chosen window."""
        if self.switching is None:
            return
        window = self.switching[self.switch_index]
        self.switching = None
        self.switcher.hide()
        self.channel.send(RaiseWindow(window=window))
        self.channel.send(FocusWindow(window=window))

    def window_removed(self, window: int):
        self.windows.pop(window, None)
        if window in self.recent:
            self.recent.remove(window)
        if self.switching is not None and window in self.switching:
            index = self.switching.index(window)
            self.switching.remove(window)
            if not self.switching:
                self.switching = None
                self.switcher.hide()
                return
            if index < self.switch_index or self.switch_index == len(self.switching):
                self.switch_index -= 1
            self.show_switcher()

    # Windows, locking, and power

    def close_window(self):
        if self.focused:
            self.channel.send(CloseWindow(window=self.focused))

    def lock(self):
        self.hide_panels()
        self.locked = True
        self.channel.send(Lock())
        self.screens.lock()

    def unlock(self):
        if not self.locked:
            return
        self.locked = False
        self.screens.unlock_screens()
        self.channel.send(Unlock())

    def power(self, choice: str):
        logging.info(f"{choice} requested")
        if choice == "shutdown":
            self.services.shutdown()
        elif choice == "reboot":
            self.services.reboot()


def wait_ready(channel: PolicyChannel, timeout: float = READY_TIMEOUT) -> bool:
    """Wait for the compositor's Ready."""
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        select.select([channel], [], [], left)
        try:
            message = channel.receive()
        except (ChannelClosedError, MessageDecodingError) as e:
            logging.error(f"The compositor didn't start: {e}")
            return False
        if isinstance(message, Ready):
            return True
    logging.error(f"The compositor wasn't ready within {timeout:.0f}s")
    return False


def darq_main(compositor_path: typing.Any = COMPOSITOR):
    if not os.path.exists(compositor_path):
        logging.error(f"No compositor at {compositor_path}: see doc/terminal.rst")
        return 1

    compositor = Compositor.start(compositor_path)
    try:
        if not wait_ready(compositor.channel):
            return 1

        # Qt connects over the Terminal's own connection: see
        # doc/terminal.rst for why its GTK platform theme can't be used.
        os.environ["WAYLAND_SOCKET"] = str(compositor.take_wayland())
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        os.environ["QT_QPA_PLATFORMTHEME"] = "generic"
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        terminal = Terminal(app, compositor.channel, Services())
        terminal.start()
        return app.exec()
    finally:
        compositor.stop()
