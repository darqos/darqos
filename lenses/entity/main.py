# darqos
# Copyright (C) 2026 David Arnold

# Entity lens: a window showing a Knowledge Base item, darq.entity or
# any of its kinds, in the Terminal context.  It's read-only, for now.
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  It reads the
# item through the EntityItem protocol (lib/didl/kb.didl): the entity,
# the entities it refers to, and those that link to it, shown as HTML
# (see render.py).  It exits when its window is closed.
#
# Following a link shows the linked item in the same window, like a web
# browser, with Back to return; Ctrl+click, or the link's context menu,
# opens it in a new window, with its own lens.  Each item shown is
# watched, so the view keeps up with changes, and recorded in History
# as opened.
#
# Its thread is Qt's: the lens port, and the ports it watches items on,
# are served on Qt's event loop (see darq.Server.attach), and its calls
# to items and History are synchronous, on queued reply ports.

import logging
import os
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QTextBrowser,
                             QVBoxLayout, QWidget)

import darq
import darq.history
import darq.kb as model
import darq.launch
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.kb import Changed, EntityItem, Listener, Role
from darq.protocols.kb import Status as KBStatus
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.ui.launcher import Launcher

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import render  # noqa: E402


# History source for the events it records.
SOURCE = "entity-lens"

# Seconds to wait for items' replies.
TIMEOUT = 10.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Most items listed as linking to the one shown.
MAX_LINKED_FROM = 200

# How many of those are asked for: the KB's limit, so "and N more" is
# right for up to that many.
LINKED_FROM_BATCH = 1000

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """Alt+Left, Backspace   Back
Ctrl+click            Open a link in a new window
F5                    Refresh
F1                    Help
Ctrl+W                Close"""


class Opener(typing.Protocol):
    """Opens items, and starts lenses for them: darq.launch, and a
    Launcher, or fakes in tests."""

    def open(self, item_id: str) -> int:
        """Return a port for an item: raises DarqError if it can't."""
        ...

    def use(self, item_id: str) -> None:
        """Use an item, with its own lens, without blocking."""
        ...


class Shown:
    """The item being shown: its port, a client for it, and the port it's
    watched on."""

    def __init__(self, item_id: str, port: int, client: EntityItem.Client):
        self.item_id = item_id
        self.port = port
        self.client = client
        self.listener: int | None = None


class EntityWindow(QWidget):
    """A frameless window showing an item, with Back, and a status line.

    Drag its margin to move it."""

    def __init__(self, lens: "EntityLens", on_closed: typing.Callable[[], None] | None = None):
        super().__init__()
        self.lens = lens
        self.on_closed = on_closed
        self.chain = render.languages(os.environ.get("LANG", "") or "en")

        # The item shown, and the items to go back to, with where they
        # were scrolled to.
        self.shown: Shown | None = None
        self.back: list[tuple[str, int]] = []

        # The item identifiers the page's links lead to, by number.
        self.targets: list[str] = []

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("EntityWindow { background-color: #323232; }"
                           "QLabel { color: #e0e0e0; }"
                           "QPushButton { color: #e0e0e0; background-color: #454545; border: none;"
                           " padding: 6px 12px; }"
                           "QPushButton:disabled { color: #707070; }"
                           "QTextBrowser { background-color: #323232; border: none; }")
        self.resize(900, 700)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 20)
        header = QHBoxLayout()
        self.back_button = QPushButton("◀ Back", self)
        self.back_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.back_button.clicked.connect(self.go_back)
        header.addWidget(self.back_button)
        titles = QVBoxLayout()
        self.title = QLabel(self)
        self.title.setStyleSheet("QLabel { font-size: 22px; }")
        self.item_label = QLabel(self)
        self.item_label.setStyleSheet("QLabel { color: #9a9a9a; font-size: 12px; }")
        self.item_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title)
        titles.addWidget(self.item_label)
        header.addLayout(titles, 1)
        self.kind = QLabel(self)
        self.kind.setStyleSheet("QLabel { color: #9a9a9a; font-size: 15px; }")
        header.addWidget(self.kind, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor)
        self.browser.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self.on_context_menu)
        layout.addWidget(self.browser, 1)

        self.status = QLabel(self)
        self.status.setStyleSheet("QLabel { color: #9a9a9a; font-size: 14px; }")
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(HELP, self)
        self.help.setStyleSheet("QLabel { background-color: rgba(20, 20, 20, 230); color: #e0e0e0;"
                                " font-family: monospace; font-size: 15px; padding: 24px; }")
        self.help.hide()
        self.update_back()

    # Showing items

    def show_item(self, item_id: str, port: int) -> bool:
        """Show an item, from a port for it, in place of any shown.

        :returns: False if it can't be read: then nothing's changed, and
                  the port's been closed."""
        shown = Shown(item_id, port, self.lens.client(port))
        page = self.page(shown)
        if page is None:
            self.close_item(shown)
            return False

        if self.shown is not None:
            self.leave()
        self.shown = shown
        self.display(page, 0)
        self.watch(shown)
        self.lens.record(item_id, darq.history.OPENED)
        logging.info(f"Showing {item_id}")
        return True

    def page(self, shown: Shown) -> tuple[str, str, list[str]] | None:
        """Read an item, and return its title, its page, and its links'
        targets, or None if it can't be read."""
        try:
            response = shown.client.get_entity(languages=self.chain)
            if response.status != KBStatus.OK:
                self.show_status(f"Can't read {shown.item_id}: {response.status.name}")
                return None
            links = shown.client.get_links(property_id="", role=Role.MAIN, languages=self.chain,
                                           max=LINKED_FROM_BATCH, after="")
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't read {shown.item_id}: {e!r}")
            self.show_status(f"Can't read {shown.item_id}: {e}")
            return None

        entity = model.entity_from_wire(response.entity)
        page = render.Page(self.chain, {link.entity_id: render.Link(link.item_id, link.label)
                                        for link in response.linked})
        linked_from = [render.Link(link.item_id, link.label) for link in links.linked if link.item_id]
        note = None
        _, requested = darq.split_item_id(shown.item_id)
        if entity.id != requested:
            note = f"{requested} was merged into this."
        html = page.render(entity, linked_from[:MAX_LINKED_FROM], max(0, len(linked_from) - MAX_LINKED_FROM),
                           note)
        title = render.best(entity.labels, self.chain) or entity.id
        return title, html, page.targets

    def display(self, page: tuple[str, str, list[str]], scroll: int):
        """Show a page, scrolled to a position."""
        title, html, self.targets = page
        assert self.shown is not None
        self.title.setText(title)
        self.setWindowTitle(title)
        self.item_label.setText(self.shown.item_id)
        self.kind.setText(render.kind_name(self.shown.item_id))
        self.browser.setHtml(html)
        bar = self.browser.verticalScrollBar()
        if bar is not None:
            bar.setValue(scroll)
        self.update_back()

    def scroll_position(self) -> int:
        bar = self.browser.verticalScrollBar()
        return bar.value() if bar is not None else 0

    def refresh(self):
        """Read the item shown again, keeping the scroll position."""
        if self.shown is None:
            return
        page = self.page(self.shown)
        if page is not None:
            self.display(page, self.scroll_position())

    def changed(self, entity_id: str):
        """The KB says an entity's changed: it's the one shown."""
        self.refresh()

    def watch(self, shown: Shown):
        """Have changes to an item reported, to refresh it."""
        listener = darq.open_port()
        self.lens.listen(listener, self)
        try:
            response = shown.client.watch_entity(listener=listener)
            if response.status != KBStatus.OK:
                raise darq.DarqError(response.status.name)
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't watch {shown.item_id}: {e!r}")
            self.lens.unlisten(listener)
            return
        shown.listener = listener

    def leave(self):
        """Stop showing, and close, the item shown."""
        shown, self.shown = self.shown, None
        if shown is not None:
            self.close_item(shown)

    def close_item(self, shown: Shown):
        if shown.listener is not None:
            # The KB stops watching when the port's destroyed.
            self.lens.unlisten(shown.listener)
            shown.listener = None
        try:
            shown.client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close {shown.item_id}: {e!r}")
        darq.close_port(shown.port)

    # Following links

    def follow(self, item_id: str, new_window: bool = False):
        """Show a linked item: in this window, after the one shown, or in a
        new one."""
        if new_window:
            self.lens.opener.use(item_id)
            self.show_status(f"Opening {item_id} in a new window ...")
            return

        try:
            port = self.lens.opener.open(item_id)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't open {item_id}: {e}")
            return
        previous = (self.shown.item_id, self.scroll_position()) if self.shown is not None else None
        if self.show_item(item_id, port) and previous is not None:
            self.back.append(previous)
            self.update_back()
            self.show_status("")

    def go_back(self):
        """Show the item shown before, as it was scrolled."""
        if not self.back:
            return
        item_id, scroll = self.back[-1]
        try:
            port = self.lens.opener.open(item_id)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't go back to {item_id}: {e}")
            return
        if self.show_item(item_id, port):
            self.back.pop()
            bar = self.browser.verticalScrollBar()
            if bar is not None:
                bar.setValue(scroll)
            self.update_back()
            self.show_status("")

    def update_back(self):
        self.back_button.setEnabled(bool(self.back))

    def target(self, href: str) -> str | None:
        """Return the item a link leads to, from its anchor."""
        if not href.startswith("#") or not href[1:].isdigit():
            return None
        n = int(href[1:])
        return self.targets[n] if n < len(self.targets) else None

    def on_anchor(self, url: QtCore.QUrl):
        item_id = self.target("#" + url.fragment())
        if item_id is not None:
            new_window = bool(QApplication.keyboardModifiers() & Modifier.ControlModifier)
            self.follow(item_id, new_window)

    def on_context_menu(self, pos: QtCore.QPoint):
        menu = QMenu(self)
        item_id = self.target(self.browser.anchorAt(pos))
        if item_id is not None:
            menu.addAction("Open", lambda: self.follow(item_id))
            menu.addAction("Open in New Window", lambda: self.follow(item_id, new_window=True))
            menu.addSeparator()
        back = menu.addAction("Back", self.go_back)
        if back is not None:
            back.setEnabled(bool(self.back))
        menu.addAction("Refresh", self.refresh)
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        viewport = self.browser.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

    # The window

    def show_status(self, message: str):
        self.status.setText(message)

    def toggle_help(self):
        if self.help.isVisible():
            self.help.hide()
        else:
            self.help.adjustSize()
            self.help.move((self.width() - self.help.width()) // 2, (self.height() - self.help.height()) // 2)
            self.help.show()
            self.help.raise_()

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None:
            return
        key, mods = a0.key(), a0.modifiers()
        if (key == Key.Key_Left and mods & Modifier.AltModifier) or key == Key.Key_Backspace:
            self.go_back()
        elif key == Key.Key_F5:
            self.refresh()
        elif key == Key.Key_F1:
            self.toggle_help()
        elif key == Key.Key_W and mods & Modifier.ControlModifier:
            self.close()
        elif key == Key.Key_Escape and self.help.isVisible():
            self.help.hide()
        else:
            super().keyPressEvent(a0)

    def mousePressEvent(self, a0: QMouseEvent | None):
        # Dragging the margin moves the window: the compositor does it,
        # since a Wayland client can't place itself.
        handle = self.windowHandle()
        if a0 is not None and a0.button() == QtCore.Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def closeEvent(self, a0):
        self.leave()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, None
            on_closed()


class ChangeListener(Listener.Handler):
    """Refreshes a window when the KB says its item's changed."""

    def __init__(self, window: EntityWindow):
        self.window = window

    def changed(self, request: Changed, source: int) -> None:
        self.window.changed(request.entity_id)


class LaunchOpener:
    """Opens items with darq.launch, and starts lenses with a Launcher."""

    def __init__(self, launcher: Launcher):
        self.launcher = launcher

    def open(self, item_id: str) -> int:
        return darq.launch.open_item(item_id)

    def use(self, item_id: str):
        self.launcher.use(item_id)


class LensServices:
    """What a Launcher uses: darq.launch, and History."""

    def __init__(self, history: History.Client | None):
        self.launch = darq.launch
        self.history = history


class EntityLens(Lens.Handler):
    """Answers the Lens protocol: uses one item, in a window, and is the
    Launcher's host, for items opened in new windows."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None, opener: Opener | None = None):
        self.loop = loop
        self.history = history
        self.window: EntityWindow | None = None
        self.services = typing.cast(typing.Any, LensServices(history))
        self.launcher = Launcher(self, SOURCE, loop.post)
        self.opener: Opener = opener if opener is not None else LaunchOpener(self.launcher)

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        window = EntityWindow(self, on_closed=self.finished)
        if not window.show_item(request.item_id, request.item):
            window.on_closed = None
            window.deleteLater()
            return UseResponse(status=Status.FAILED)
        self.window = window
        window.show()
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def client(self, port: int) -> EntityItem.Client:
        """Return a client for an item's port."""
        return EntityItem.Client(port, timeout=TIMEOUT)

    def listen(self, port: int, window: EntityWindow):
        """Serve the Listener protocol on a port, for a window."""
        assert self.server is not None
        self.server.add(port, ChangeListener(window))

    def unlisten(self, port: int):
        assert self.server is not None
        self.server.remove(port)
        darq.close_port(port)

    def record(self, item_id: str, event: str):
        """Record a History event about an item, if History is running."""
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")

    # As the Launcher's host

    def show_status(self, message: str):
        if self.window is not None:
            self.window.show_status(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        self.show_status(error or "")

    def expire(self):
        """Give up, if no Use has come."""
        if self.window is None:
            logging.error(f"Not asked to Use an item within {USE_TIMEOUT}s: exiting")
            self.finished()

    def finished(self):
        self.loop.stop()


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: a lens is started for an item, by the Terminal")
        return 1

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    loop = QtEventLoop()

    # History is best effort: the lens works without it.
    handle = darq.lookup_name("darq.history", blocking=False)
    history = History.Client(handle) if handle is not None else None

    lens = EntityLens(loop, history)
    port = darq.open_port()
    server = darq.Server()
    server.add(port, lens)
    server.attach(loop)
    server.call_later(USE_TIMEOUT, lens.expire)
    darq.send(bootstrap, Ready(lens=port))

    loop.run()
    loop.close()
    del app
    return 0
