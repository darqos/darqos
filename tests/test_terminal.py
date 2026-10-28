# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Terminal, services/terminal: its hotkeys, panels, window
# list and locking, offscreen, with faked services and a socket pair
# for the compositor's end of the policy channel; and then started at
# boot through a real p-kernel, with the compositor headless.

import importlib.util
import os
import pathlib
import socket
import sys
import time

import pytest

from darq.compositor import COMPOSITOR, PolicyChannel
from darq.protocols import compositor as protocol
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

from test_devterm import DOC, TEXT, FakeServices, inline
from test_pkernel import live_pkernel, pkernel, run_kernel_until  # noqa: F401


TERMINAL_PATH = pathlib.Path(__file__).parent.parent / "services" / "terminal"


def load():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(TERMINAL_PATH))
    try:
        spec = importlib.util.spec_from_file_location("terminal_main", TERMINAL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TERMINAL_PATH))
    return module


main = load()


class Compositor:
    """The compositor's end of the policy channel."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.sock.setblocking(False)

    def received(self) -> list:
        """The Policy messages the Terminal has sent, since last asked."""
        messages = []
        while True:
            try:
                data = self.sock.recv(4096)
            except BlockingIOError:
                return messages
            message = protocol.Policy.REQUESTS[int.from_bytes(data[6:8], "little")]()
            message.decode(data)
            messages.append(message)


class FakeApp:
    """Stands in for the QApplication, where the Terminal would exit it."""

    def __init__(self, app):
        self.app = app
        self.exited: list[int] = []

    def exit(self, status: int):
        self.exited.append(status)

    def __getattr__(self, name):
        return getattr(self.app, name)


@pytest.fixture
def terminal(qt_app):
    """A started Terminal, locked, and the compositor's end of its
    channel, with the hotkey bindings read."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    services = FakeServices()
    started = main.Terminal(qt_app, PolicyChannel(ours), services)
    started.launcher.run = started.launcher.post = inline
    started.start()
    compositor = Compositor(theirs)
    started.bindings = compositor.received()
    started.compositor = compositor
    started.fake_services = services
    yield started

    for panel in started.panels:
        panel.close()
    started.loop.close()
    for screen in list(started.screens.windows):
        started.screens.remove(screen)
    started.notifier.setEnabled(False)
    ours.close()
    theirs.close()


def key(code, text: str = ""):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier, text)


def hotkey(terminal, keysym: int, modifiers: int = 0):
    terminal.handle(HotkeyPressed(keysym=keysym, modifiers=modifiers))


def unlock(terminal):
    from PyQt6.QtCore import Qt
    lock = terminal.screens.locks()[0]
    lock.keyPressEvent(key(Qt.Key.Key_Return))
    assert terminal.compositor.received() == [Unlock()]


def visible(terminal) -> list:
    return [panel for panel in terminal.panels if panel.isVisible()]


################################################################
# Starting
################################################################

def test_binds_hotkeys(terminal):
    assert terminal.bindings == [BindHotkey(keysym=k, modifiers=m) for k, m in main.HOTKEYS]
    assert (main.XK_Tab, Modifier.SHIFT) in main.HOTKEYS


def test_starts_locked(terminal):
    from PyQt6.QtCore import Qt
    [(background, lock)] = terminal.screens.windows.values()
    assert background.windowTitle() == "darq:background" and background.isVisible()
    assert lock.windowTitle() == "darq:lock" and lock.isVisible()

    # Hotkeys do nothing while it's locked (bar Sys-b), and only Enter unlocks.
    hotkey(terminal, main.XK_n)
    hotkey(terminal, main.XK_l)
    assert visible(terminal) == []
    lock.keyPressEvent(key(Qt.Key.Key_A, "a"))
    assert terminal.locked and terminal.compositor.received() == []

    unlock(terminal)
    assert not terminal.locked and not lock.isVisible()
    assert background.isVisible()


def test_power_from_lock_screen(terminal):
    from PyQt6.QtCore import Qt
    lock = terminal.screens.locks()[0]
    hotkey(terminal, main.XK_b)
    assert lock.prompting and visible(terminal) == []

    # A modifier isn't an answer; another key cancels; s shuts down.
    lock.keyPressEvent(key(Qt.Key.Key_Shift))
    assert lock.prompting
    lock.keyPressEvent(key(Qt.Key.Key_Q, "q"))
    assert not lock.prompting and terminal.fake_services.calls == []
    lock.keyPressEvent(key(Qt.Key.Key_Return))
    assert terminal.compositor.received() == [Unlock()]

    terminal.lock()
    terminal.compositor.received()
    hotkey(terminal, main.XK_b)
    lock.keyPressEvent(key(Qt.Key.Key_S, "s"))
    assert terminal.fake_services.calls == ["shutdown"]


def test_wait_ready(qt_app):
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    channel = PolicyChannel(ours)
    theirs.send(Ready().encode()[0])
    assert main.wait_ready(channel, 1)
    assert not main.wait_ready(channel, 0.1)
    theirs.close()
    assert not main.wait_ready(channel, 1)
    ours.close()


def test_no_compositor(tmp_path):
    assert main.darq_main(tmp_path / "missing") == 1


def test_channel_closed_exits(terminal):
    app = FakeApp(terminal.app)
    terminal.app = app
    terminal.compositor.sock.close()
    terminal.read_channel()
    assert app.exited == [1]


################################################################
# Panels
################################################################

def test_one_panel_at_a_time(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    assert visible(terminal) == [terminal.new]
    assert terminal.new.windowTitle() == "darq:overlay"
    hotkey(terminal, main.XK_h)
    assert visible(terminal) == [terminal.history]

    # The History panel searches the last hour, offering the types.
    assert terminal.fake_services.history.queries[-1]["end"] == 0
    assert terminal.history.view.types.count() == 2

    terminal.history.keyPressEvent(key(Qt.Key.Key_Escape))
    assert visible(terminal) == []


def test_new_creates_and_uses(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    assert types.filter is not None

    # The type with a lens is selected; filtering leaves none.
    assert types.selected().type_id == TEXT
    types.filter.setText("png")
    assert types.selected() is None
    types.filter.setText("utf")
    types.filter.returnPressed.emit()

    services = terminal.fake_services
    assert services.launch.created == [TEXT]
    assert services.history.added == [(DOC, "created", "terminal")]
    assert services.launch.used == [(DOC, 42)]
    assert visible(terminal) == []


def test_new_arrow_keys(terminal):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    unlock(terminal)
    terminal.fake_services.types.types = {TEXT: ("UTF-8 Text", True), "public.png": ("PNG", True),
                                          "public.jpeg": ("JPEG", False)}
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    assert types.filter is not None

    # In the filter, Down and Up move through the usable types, stopping
    # at the ends.
    assert types.selected().name == "PNG"
    for code, name in ((Qt.Key.Key_Down, "UTF-8 Text"), (Qt.Key.Key_Down, "UTF-8 Text"),
                       (Qt.Key.Key_Up, "PNG"), (Qt.Key.Key_Up, "PNG")):
        QApplication.sendEvent(types.filter, key(code))
        assert types.selected().name == name


def test_new_failure_stays(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    terminal.fake_services.launch.fail = True
    terminal.new.types.create_item()
    assert visible(terminal) == [terminal.new]
    assert terminal.new.status.text() == f"Can't create a UTF-8 Text: Can't activate {TEXT}"


class Deferred:
    """Runs launches only when told: as if they were still starting."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def finish(self):
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


def test_one_create_at_a_time(terminal):
    unlock(terminal)
    terminal.launcher.run = deferred = Deferred()
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    types.create_item()
    assert not types.create_button.isEnabled()

    # A second Enter, even after reopening, doesn't make a second item.
    types.create_item()
    assert terminal.new.status.text() == "Still starting a UTF-8 Text ..."
    hotkey(terminal, main.XK_n)
    types.create_item()
    assert len(deferred.jobs) == 1

    deferred.finish()
    assert types.creating is None and types.create_button.isEnabled()
    assert terminal.fake_services.launch.created == [TEXT]
    assert visible(terminal) == []


def test_failure_after_panel_closed(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    terminal.launcher.run = deferred = Deferred()
    terminal.fake_services.launch.fail = True
    hotkey(terminal, main.XK_n)
    terminal.new.types.create_item()
    terminal.new.keyPressEvent(key(Qt.Key.Key_Escape))
    deferred.finish()

    # It's shown in a notice, which any key closes.
    assert visible(terminal) == [terminal.notice]
    assert terminal.notice.message.text() == f"Can't create a UTF-8 Text: Can't activate {TEXT}"
    terminal.notice.keyPressEvent(key(Qt.Key.Key_Shift))
    assert terminal.notice.isVisible()
    terminal.notice.keyPressEvent(key(Qt.Key.Key_Return))
    assert visible(terminal) == []


def test_history_use(terminal):
    unlock(terminal)
    services = terminal.fake_services
    services.history.events = [main_event()]
    hotkey(terminal, main.XK_h)
    terminal.history.view.use(0)
    assert services.launch.used == [(DOC, None)]
    assert visible(terminal) == []

    # A failure, while it's open, is shown in it.
    hotkey(terminal, main.XK_h)
    services.launch.fail = True
    terminal.history.view.use(0)
    assert visible(terminal) == [terminal.history]
    assert terminal.history.status.text() == f"No lens for {DOC}"


def main_event():
    from darq.protocols.history import Event
    return Event(time=int(time.time() * 1_000_000) - 1_000_000, item_id=DOC, event="edited",
                 source="text-lens", count=1)


def test_closes_when_deactivated(terminal):
    from PyQt6.QtCore import QEvent
    unlock(terminal)
    for panel, shown in ((terminal.new, False), (terminal.switcher, True)):
        panel.open()
        panel.was_active = True
        panel.changeEvent(QEvent(QEvent.Type.ActivationChange))
        assert panel.isVisible() == shown
        panel.hide()


def test_power_panel(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, main.XK_b)
    assert visible(terminal) == [terminal.power_panel]
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_Control))
    assert visible(terminal) == [terminal.power_panel]
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_X, "x"))
    assert visible(terminal) == [] and terminal.fake_services.calls == []

    hotkey(terminal, main.XK_b)
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_B, "b"))
    assert terminal.fake_services.calls == ["reboot"]


@pytest.mark.parametrize("keysym, name", [(main.XK_space, "Search"), (main.XK_e, "Events"),
                                          (main.XK_i, "Item info")])
def test_tbd(terminal, keysym, name):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, keysym)
    assert visible(terminal) == [terminal.tbd]
    assert terminal.tbd.heading.text() == name
    terminal.tbd.keyPressEvent(key(Qt.Key.Key_Shift))
    assert terminal.tbd.isVisible()
    terminal.tbd.keyPressEvent(key(Qt.Key.Key_Space, " "))
    assert visible(terminal) == []


def test_lock(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    hotkey(terminal, main.XK_l)
    assert terminal.compositor.received() == [Lock()]
    assert visible(terminal) == []
    assert all(lock.isVisible() for lock in terminal.screens.locks())

    # Sys-l does nothing while it's locked.
    hotkey(terminal, main.XK_l)
    assert terminal.compositor.received() == []


################################################################
# Windows
################################################################

def windows(terminal, *titles: str):
    """Add windows 1, 2, ..., focusing each in turn: the last is the
    most recent."""
    for n, title in enumerate(titles, 1):
        terminal.handle(WindowAdded(window=n, title=title, app_id=""))
        terminal.handle(WindowFocused(window=n))


def switcher_titles(terminal) -> list[str]:
    widget = terminal.switcher.list
    return [widget.item(n).text() for n in range(widget.count())]


def test_switcher(terminal):
    unlock(terminal)
    windows(terminal, "a", "b", "c")
    assert terminal.recent == [3, 2, 1]

    # Most recent first, starting at the previous window, and moving on
    # with each Tab, round to the start.
    hotkey(terminal, main.XK_Tab)
    assert visible(terminal) == [terminal.switcher]
    assert switcher_titles(terminal) == ["c", "b", "a"]
    assert terminal.switcher.list.currentRow() == 1
    hotkey(terminal, main.XK_Tab)
    hotkey(terminal, main.XK_Tab)
    assert terminal.switcher.list.currentRow() == 0
    hotkey(terminal, main.XK_Tab, Modifier.SHIFT)
    assert terminal.switcher.list.currentRow() == 2

    # The panel's focus isn't a window's.
    terminal.handle(WindowFocused(window=0))
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]
    assert visible(terminal) == []
    terminal.handle(WindowFocused(window=1))
    assert terminal.recent == [1, 3, 2]


def test_switcher_tap_and_back(terminal):
    unlock(terminal)
    windows(terminal, "a", "b")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]

    # Starting backwards is at the least recent.
    windows(terminal, "a", "b", "c")
    hotkey(terminal, main.XK_Tab, Modifier.SHIFT)
    assert terminal.switcher.list.currentRow() == 2

    # System's release without switching is nothing.
    terminal.handle(SystemReleased())
    terminal.compositor.received()
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == []


def test_switcher_with_none_or_one(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_Tab)
    assert visible(terminal) == []
    windows(terminal, "a")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]


def test_windows_change_while_switching(terminal):
    unlock(terminal)
    windows(terminal, "a", "b", "c")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(WindowRetitled(window=2, title="B"))
    assert switcher_titles(terminal) == ["c", "B", "a"]

    # The chosen one goes: the next is chosen; and one before it goes,
    # keeping it chosen.
    terminal.handle(WindowRemoved(window=2))
    assert switcher_titles(terminal) == ["c", "a"]
    assert terminal.switcher.list.currentRow() == 1
    terminal.handle(WindowRemoved(window=3))
    assert switcher_titles(terminal) == ["a"]
    assert terminal.switcher.list.currentRow() == 0
    terminal.handle(WindowRemoved(window=1))
    assert terminal.switching is None and visible(terminal) == []
    assert terminal.windows == {} and terminal.recent == []


def test_close_window(terminal):
    unlock(terminal)
    windows(terminal, "a", "b")
    hotkey(terminal, main.XK_x)
    assert terminal.compositor.received() == [CloseWindow(window=2)]

    # Not one of the Terminal's own.
    terminal.handle(WindowFocused(window=0))
    hotkey(terminal, main.XK_x)
    assert terminal.compositor.received() == []


################################################################
# At boot
################################################################

def test_terminal_at_boot(live_pkernel, monkeypatch):  # noqa: F811
    # Started at boot, it starts the compositor (headless), and keeps
    # running, until the system's shut down: then, the compositor exits
    # too, removing its socket.
    if not COMPOSITOR.exists():
        pytest.skip("the compositor isn't built: see doc/terminal.rst")
    kernel = live_pkernel
    runtime = pathlib.Path(f"/tmp/darq-test-{os.getpid()}")
    runtime.mkdir(mode=0o700)
    socket_path = runtime / "darq-0"
    monkeypatch.setenv("DARQ_TERMINAL", "1")
    monkeypatch.setenv("DARQ_DEV_TERMINAL", "0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("WLR_BACKENDS", "headless")
    monkeypatch.setenv("WLR_RENDERER", "pixman")
    try:
        kernel.do_boot()
        terminal = kernel._terminal
        assert terminal is not None
        running = kernel.module.KernelProcessStatus.RUNNING
        run_kernel_until(kernel, lambda: terminal.get_status() == running and socket_path.exists(),
                         "the Terminal and its compositor to run")

        deadline = time.monotonic() + 2
        run_kernel_until(kernel, lambda: time.monotonic() > deadline, "a while")
        assert terminal.get_status() == running and not kernel._stopping

        kernel.begin_shutdown(reboot=False)
        run_kernel_until(kernel, lambda: kernel._stopped, "the shutdown")
        assert not kernel.reboot_requested()
        assert terminal.get_child().wait(10) == 0
        deadline = time.monotonic() + 5
        while socket_path.exists():
            assert time.monotonic() < deadline, "the compositor didn't exit"
            time.sleep(0.05)
    finally:
        for path in runtime.iterdir():
            path.unlink()
        runtime.rmdir()
