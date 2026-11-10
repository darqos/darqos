# darqos
# Copyright (C) 2026 David Arnold

# Conversation lens: a read-only window on a conversation
# (darq.conversation) and its messages (darq.message), in the Terminal
# context (see doc/message-type.rst).
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  A
# conversation opens at its newest message.  A message is shown in its
# conversation, whose item it opens, at that message, highlighted.
#
# Messages are read a page at a time through the conversation's item
# port (see timeline.py), as they're scrolled to, and shown as HTML (see
# message_page.py).  Senders and attachments open in their own lenses, in new
# windows.  What's shown doesn't change as messages arrive: F5 reads the
# newest.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to the item and History are
# synchronous, on queued reply ports.

import logging
import os
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QTextBrowser, QVBoxLayout, QWidget

import darq
import darq.history
import darq.kb as model
import darq.launch
import darq.message
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.kb import EntityItem, Linked
from darq.protocols.kb import Status as KBStatus
from darq.protocols.lens import Close, Ready, Status, UseRequest, UseResponse
from darq.ui import style
from darq.ui.lens import LensHandler, announce
from darq.ui.launcher import Launcher
from darq.ui.style import CONTROLS

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import message_page  # noqa: E402
import timeline  # noqa: E402


# History source for the events it records.
SOURCE = "conversation-lens"

# Seconds to wait for items' replies.
TIMEOUT = 10.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Pixels from an end of what's held at which more is read.
NEAR_END = 200

# Fallback languages, when the locale doesn't say.
DEFAULT_LANGUAGE = "en"

# Reads failing, from items and the timeline.
READ_ERRORS = (darq.DarqError, TimeoutError, ValueError, timeline.TimelineError)

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """Page Up, Page Down    Move, reading more as needed
Home, End             Oldest, newest messages
F5                    Read the newest messages
F1                    Help
F11                   Full screen
Ctrl+W                Close"""


def languages() -> list[str]:
    """Return the locale's chain of languages."""
    tag = (os.environ.get("LANG", "") or DEFAULT_LANGUAGE).split(".")[0].replace("_", "-")
    chain = [tag, tag.split("-")[0], DEFAULT_LANGUAGE]
    return [t for n, t in enumerate(chain) if t and t not in chain[:n]]


def label(entity: model.Entity, chain: list[str]) -> str:
    """Return an entity's label, in a chain of languages, or any."""
    for language in chain:
        if language in entity.labels:
            return entity.labels[language]
    return next(iter(entity.labels.values()), "")


def conversation_of(message: model.Entity) -> str:
    """Return the conversation a message is part of, or an empty string."""
    for statement in message.get_statements(darq.message.CONVERSATION):
        value = statement.get_value()
        if isinstance(value, model.EntityValue):
            return value.entity_id
    return ""


def participants(conversation: model.Entity, linked: dict[str, Linked]) -> str:
    """Return who's in a conversation, by name."""
    names = []
    for statement in conversation.get_statements(darq.message.PARTICIPANT):
        value = statement.get_value()
        if isinstance(value, model.EntityValue):
            link = linked.get(value.entity_id)
            names.append(link.label if link is not None and link.label else value.entity_id)
    return ", ".join(names)


class Opener(typing.Protocol):
    """Opens items, and starts lenses for them: darq.launch, and a
    Launcher, or fakes in tests."""

    def open(self, item_id: str) -> int:
        """Return a port for an item: raises DarqError if it can't."""
        ...

    def use(self, item_id: str) -> None:
        """Use an item, with its own lens, without blocking."""
        ...


class ConversationWindow(QWidget):
    """A frameless window on a conversation: its name, who's in it, and
    its messages.  Drag its margin to move it."""

    def __init__(self, lens: "ConversationLens", messages: timeline.Timeline, on_closed: typing.Callable[[], None]):
        super().__init__()
        self.lens = lens
        self.timeline = messages
        self.on_closed = on_closed

        # The message that was opened, if one was.
        self.highlight = ""

        # What the page's links lead to, by number.
        self.targets: list[message_page.Target] = []

        # Set while the page changes, when scrolling is the lens's doing.
        self.showing = False

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(CONTROLS + f"ConversationWindow {{ background-color: {style.SURFACE}; }}"
                           f"QTextBrowser {{ background-color: {style.SURFACE}; border: none; }}")
        self.resize(900, 700)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 20)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        self.title = QLabel(self)
        self.title.setStyleSheet("QLabel { font-size: 22px; }")
        self.people = QLabel(self)
        self.people.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; font-size: 13px; }}")
        self.people.setWordWrap(True)
        titles.addWidget(self.title)
        titles.addWidget(self.people)
        header.addLayout(titles, 1)
        layout.addLayout(header)

        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor)
        self.browser.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self.on_context_menu)
        layout.addWidget(self.browser, 1)
        bar = self.browser.verticalScrollBar()
        assert bar is not None
        self.bar = bar
        self.bar.valueChanged.connect(self.scrolled)

        self.status = QLabel(self)
        self.status.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; font-size: 14px; }}")
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(HELP, self)
        self.help.setStyleSheet(style.HELP)
        self.help.hide()

    # Showing

    def set_header(self, title: str, people: str):
        self.title.setText(title)
        self.setWindowTitle(title)
        self.people.setText(people)
        self.people.setVisible(bool(people))

    def show_messages(self, anchor: str = "", bottom: bool = False):
        """Show the messages held, at a message, or the last."""
        page = message_page.Page(self.timeline.linked, self.highlight)
        html = page.render(self.timeline.messages, self.timeline.more_older, self.timeline.more_newer)
        self.showing = True
        try:
            self.targets = page.targets
            self.browser.setHtml(html)
            if bottom:
                self.bar.setValue(self.bar.maximum())
            elif anchor:
                self.browser.scrollToAnchor(message_page.anchor_name(anchor))
        finally:
            self.showing = False

    def show_status(self, message: str):
        self.status.setText(message)

    def try_read(self, read: typing.Callable[[], object]) -> object:
        """Read, showing why if it can't be: None if it can't."""
        try:
            return read()
        except READ_ERRORS as e:
            logging.error(f"Can't read messages: {e!r}")
            self.show_status(f"Can't read messages: {e}")
            return None

    def newest(self):
        """Show the newest messages."""
        self.highlight = ""
        if self.try_read(lambda: self.timeline.load_newest() or True):
            self.show_messages(bottom=True)
            self.show_status("")

    def oldest(self):
        """Show the oldest messages."""
        self.highlight = ""
        if self.try_read(lambda: self.timeline.load_oldest() or True):
            self.show_messages()
            self.bar.setValue(0)

    def at_message(self, message: model.Entity):
        """Show a message, highlighted, with those around it."""
        self.highlight = message.id
        if self.try_read(lambda: self.timeline.load_around(message) or True):
            self.show_messages(anchor=message.id)

    def scrolled(self, value: int):
        """Read more, if it's been scrolled near an end of what's held."""
        if self.showing or self.help.isVisible():
            return
        if value <= NEAR_END and self.timeline.more_older:
            self.read_older()
        elif value >= self.bar.maximum() - NEAR_END and self.timeline.more_newer:
            self.read_newer()

    def read_older(self):
        """Read earlier messages, staying at the message shown."""
        first = self.timeline.messages[0].id if self.timeline.messages else ""
        if self.try_read(self.timeline.load_older):
            self.show_messages(anchor=first)

    def read_newer(self):
        """Read later messages, staying at the message shown."""
        last = self.timeline.messages[-1].id if self.timeline.messages else ""
        if self.try_read(self.timeline.load_newer):
            self.show_messages(anchor=last)

    def jump(self, entity_id: str):
        """Go to another message of the conversation."""
        if self.timeline.holds(entity_id):
            self.highlight = entity_id
            self.show_messages(anchor=entity_id)
            return
        message = self.lens.read_message(entity_id)
        if message is None:
            self.show_status("That message can't be read")
            return
        self.at_message(message)

    # Links

    def target(self, href: str) -> message_page.Target | None:
        """Return what a link leads to, from its anchor."""
        if not href.startswith("#") or not href[1:].isdigit():
            return None
        n = int(href[1:])
        return self.targets[n] if n < len(self.targets) else None

    def choose(self, target: message_page.Target):
        if isinstance(target, message_page.Jump):
            self.jump(target.entity_id)
        else:
            self.lens.opener.use(target.item_id)
            self.show_status(f"Opening {target.item_id} in a new window ...")

    def on_anchor(self, url: QtCore.QUrl):
        target = self.target("#" + url.fragment())
        if target is not None:
            self.choose(target)

    def on_context_menu(self, pos: QtCore.QPoint):
        menu = QMenu(self)
        target = self.target(self.browser.anchorAt(pos))
        if target is not None:
            menu.addAction("Open", lambda: self.choose(target))
            menu.addSeparator()
        menu.addAction("Newest Messages", self.newest)
        menu.addAction("Oldest Messages", self.oldest)
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        viewport = self.browser.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

    # The window

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
        key, control = a0.key(), bool(a0.modifiers() & Modifier.ControlModifier)
        if key == Key.Key_Escape and self.help.isVisible():
            self.help.hide()
        elif key == Key.Key_PageDown:
            self.bar.setValue(self.bar.value() + self.bar.pageStep())
        elif key == Key.Key_PageUp:
            self.bar.setValue(self.bar.value() - self.bar.pageStep())
        elif key == Key.Key_Home:
            self.oldest()
        elif key in (Key.Key_End, Key.Key_F5):
            self.newest()
        elif key == Key.Key_F1:
            self.toggle_help()
        elif key == Key.Key_F11:
            self.toggle_full_screen()
        elif key == Key.Key_W and control:
            self.close()
        else:
            super().keyPressEvent(a0)

    def mousePressEvent(self, a0: QMouseEvent | None):
        # Dragging the margin moves the window: the compositor does it,
        # since a Wayland client can't place itself.
        handle = self.windowHandle()
        if a0 is not None and a0.button() == QtCore.Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def closeEvent(self, a0):
        self.lens.close_items()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, lambda: None
            on_closed()


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


class ConversationLens(LensHandler):
    """Answers the Lens protocol: uses one item, in a window, and is the
    Launcher's host, for items opened in new windows."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None, opener: Opener | None = None):
        self.loop = loop
        self.history = history
        self.window: ConversationWindow | None = None
        self.services = typing.cast(typing.Any, LensServices(history))
        self.launcher = Launcher(self, SOURCE, loop.post)
        self.opener: Opener = opener if opener is not None else LaunchOpener(self.launcher)
        self.chain = languages()

        # The ports it has open: closed with their items.
        self.ports: dict[int, EntityItem.Client] = {}

    def client(self, port: int) -> EntityItem.Client:
        """Return a client for an item's port."""
        client = EntityItem.Client(port, timeout=TIMEOUT)
        self.ports[port] = client
        return client

    def read(self, client: EntityItem.Client) -> tuple[model.Entity, list[Linked]] | None:
        """Read an item's entity, and those it refers to, or None."""
        try:
            response = client.get_entity(languages=self.chain)
            if response.status != KBStatus.OK:
                return None
            return model.entity_from_wire(response.entity), list(response.linked)
        except READ_ERRORS as e:
            logging.error(f"Can't read an item: {e!r}")
            return None

    def read_message(self, entity_id: str) -> model.Entity | None:
        """Read a message of the conversation, for going to it."""
        try:
            port = self.opener.open(model.item_id(entity_id, timeline.MESSAGE_TYPE))
        except READ_ERRORS as e:
            logging.error(f"Can't open {entity_id}: {e!r}")
            return None
        client = self.client(port)
        read = self.read(client)
        self.close_item(port)
        return read[0] if read is not None else None

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        client = self.client(request.item)
        read = self.read(client)
        if read is None:
            self.close_items()
            return UseResponse(status=Status.FAILED)
        entity, linked = read
        if darq.split_item_id(request.item_id)[0] == timeline.MESSAGE_TYPE:
            shown = self.use_message(request.item, entity, linked)
        else:
            shown = self.use_conversation(client, entity, linked)
        if not shown:
            self.close_items()
            return UseResponse(status=Status.FAILED)
        self.record(request.item_id, darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        self.shown_item = request.item_id
        return UseResponse(status=Status.OK)

    def show(self, messages: timeline.Timeline, entity: model.Entity | None, linked: list[Linked],
             title: str) -> ConversationWindow:
        """Make the window on some messages, in a conversation."""
        messages.remember(linked)
        self.window = ConversationWindow(self, messages, on_closed=self.finished)
        people = participants(entity, messages.linked) if entity is not None else ""
        self.window.set_header(title, people)
        self.window.show()
        return self.window

    def use_conversation(self, client: EntityItem.Client, entity: model.Entity, linked: list[Linked]) -> bool:
        window = self.show(timeline.Timeline(client, self.chain), entity, linked,
                           label(entity, self.chain) or "Conversation")
        window.newest()
        return True

    def use_message(self, port: int, message: model.Entity, linked: list[Linked]) -> bool:
        """Show a message in its conversation, whose item takes the place
        of the message's: alone, if it isn't in one."""
        conversation = next((link for link in linked if link.entity_id == conversation_of(message)), None)
        client = None
        if conversation is not None and conversation.item_id:
            try:
                client = self.client(self.opener.open(conversation.item_id))
            except READ_ERRORS as e:
                logging.error(f"Can't open {conversation.item_id}: {e!r}")
        read = self.read(client) if client is not None else None
        if client is None or read is None:
            alone = timeline.Timeline(None, self.chain)
            self.show(alone, None, linked, "Message")
            alone.messages = [message]
            assert self.window is not None
            self.window.show_messages()
            return True

        self.close_item(port)
        entity, conversation_linked = read
        messages = timeline.Timeline(client, self.chain)
        messages.remember(linked)
        window = self.show(messages, entity, conversation_linked, label(entity, self.chain) or "Conversation")
        window.at_message(message)
        return True

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def close_item(self, port: int):
        """Close an item, and its port."""
        client = self.ports.pop(port, None)
        if client is None:
            return
        try:
            client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close an item: {e!r}")
        darq.close_port(port)

    def close_items(self):
        for port in list(self.ports):
            self.close_item(port)

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

    lens = ConversationLens(loop, history)
    port = darq.open_port()
    announce(port)
    server = darq.Server()
    server.add(port, lens)
    server.attach(loop)
    server.call_later(USE_TIMEOUT, lens.expire)
    darq.send(bootstrap, Ready(lens=port))

    loop.run()
    loop.close()
    del app
    return 0
