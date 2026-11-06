# darqos
# Copyright (C) 2026 David Arnold

# Book lens: the card for a book, in the Terminal context, for an item
# of any of a book's four levels (see doc/book.rst).
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  The book is
# read through items' ports (see book_store.py), and shown as one card,
# whichever level was opened, with that one highlighted (book_card.py).
# E edits it (book_form.py).  People, publishers and files open in their
# own lenses, in new windows, and I shows what was opened in the entity
# lens, with all of its statements.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to items and History are
# synchronous, on queued reply ports.

import logging
import os
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget

import darq
import darq.book
import darq.history
import darq.kb as model
import darq.launch
from darq.book import Book, BookError
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.ui import style
from darq.ui.launcher import Launcher
from darq.ui.style import CONTROLS

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import book_card  # noqa: E402
import book_form  # noqa: E402
import book_store  # noqa: E402


# History source for the events it records.
SOURCE = "book-lens"

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Fallback language, when the locale doesn't say.
DEFAULT_LANGUAGE = "en"

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """E                     Edit
R                     Read: the first format
I                     Show in the entity lens
F5                    Read it again
F1                    Help
F11                   Full screen
Ctrl+W                Close

While editing:
Ctrl+S                Save
Escape                Cancel"""


def languages() -> list[str]:
    """Return the chain of languages, best first."""
    tag = os.environ.get("LANG", "").split(".")[0].replace("_", "-")
    chain = [tag, tag.split("-")[0], DEFAULT_LANGUAGE]
    return [t for n, t in enumerate(chain) if t and t not in chain[:n]]


def first_file(book: Book) -> str:
    """Return the first file of a book, or an empty string."""
    return next((c.file for e in book.editions for c in e.copies if c.file), "")


class Opener(typing.Protocol):
    """How the lens opens other items."""

    def use(self, item_id: str, lens_for: str = "") -> None:
        """Use an item, with the lens of a type, if one's given."""
        ...


class BookWindow(QWidget):
    """A frameless window on a book: its card, or the form.  Drag its
    margin to move it."""

    def __init__(self, lens: "BookLens", on_closed: typing.Callable[[], None]):
        super().__init__()
        self.lens = lens
        self.on_closed = on_closed

        # What the page's links lead to, by number.
        self.targets: list[str] = []

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(CONTROLS + f"BookWindow {{ background-color: {style.SURFACE}; }}"
                           f"QTextBrowser {{ background-color: {style.SURFACE}; border: none; }}")
        self.resize(800, 700)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 20)
        self.stack = QStackedWidget(self)
        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor)
        self.browser.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self.on_context_menu)
        self.form = book_form.BookForm(self)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.form)
        layout.addWidget(self.stack, 1)

        self.status = QLabel(self)
        self.status.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; font-size: 14px; }}")
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(HELP, self)
        self.help.setStyleSheet(style.HELP)
        self.help.hide()

    @property
    def editing(self) -> bool:
        return self.stack.currentWidget() is self.form

    def show_book(self, book: Book):
        page = book_card.Page(book)
        self.browser.setHtml(page.render())
        self.targets = page.targets
        self.setWindowTitle(book.title or "Book")
        self.stack.setCurrentWidget(self.browser)
        self.browser.setFocus()

    def edit(self, book: Book):
        self.form.show_book(book)
        self.stack.setCurrentWidget(self.form)
        self.show_status("Ctrl+S saves, and Escape cancels")

    def show_status(self, message: str):
        self.status.setText(message)

    def on_anchor(self, url: QtCore.QUrl):
        fragment = url.fragment()
        if fragment.isdigit() and int(fragment) < len(self.targets):
            self.lens.opener.use(self.targets[int(fragment)])

    def on_context_menu(self, pos: QtCore.QPoint):
        menu = QMenu(self)
        menu.addAction("Edit", self.lens.edit)
        menu.addAction("Read", self.lens.read_book)
        menu.addAction("Show in Entity Lens", self.lens.show_entity)
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        viewport = self.browser.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

    def toggle_help(self):
        if self.help.isVisible():
            self.help.hide()
        else:
            self.help.adjustSize()
            self.help.move(self.width() - self.help.width() - 24, 24)
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
        elif key == Key.Key_F1:
            self.toggle_help()
        elif key == Key.Key_F11:
            self.toggle_full_screen()
        elif key == Key.Key_W and control:
            self.close()
        elif self.editing:
            if key == Key.Key_S and control:
                self.lens.save()
            elif key == Key.Key_Escape:
                self.lens.cancel()
            else:
                super().keyPressEvent(a0)
        elif key == Key.Key_E:
            self.lens.edit()
        elif key == Key.Key_R:
            self.lens.read_book()
        elif key == Key.Key_I:
            self.lens.show_entity()
        elif key == Key.Key_F5:
            self.lens.reread()
        else:
            super().keyPressEvent(a0)

    def mousePressEvent(self, a0: QMouseEvent | None):
        if a0 is not None and a0.button() == QtCore.Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def closeEvent(self, a0):
        super().closeEvent(a0)
        self.on_closed()


class LaunchOpener:
    """Opens items with the Launcher."""

    def __init__(self, launcher: Launcher):
        self.launcher = launcher

    def use(self, item_id: str, lens_for: str = ""):
        self.launcher.use(item_id, lens_for=lens_for)


class LensServices:
    """What the Launcher needs from a lens."""

    def __init__(self, history: History.Client | None):
        self.launch = darq.launch
        self.history = history


class BookLens(Lens.Handler):
    """The lens port: it uses one item, for the life of the process."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None, opener: Opener | None = None,
                 store: book_store.ItemStore | None = None):
        self.loop = loop
        self.history = history
        self.window: BookWindow | None = None
        self.services = typing.cast(typing.Any, LensServices(history))
        self.launcher = Launcher(self, SOURCE, loop.post)
        self.opener: Opener = opener if opener is not None else LaunchOpener(self.launcher)
        self.store = store if store is not None else book_store.ItemStore(languages())
        self.item_id = ""
        self.book = Book()

    def read(self) -> bool:
        """Read the book, or say why it can't be."""
        try:
            self.book = darq.book.read(self.store, self.item_id)
        except BookError as e:
            logging.error(f"Can't read {self.item_id}: {e}")
            self.show_status(str(e))
            return False
        return True

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)
        self.item_id = request.item_id
        self.store.adopt(request.item_id, request.item)
        if not self.read():
            self.store.close()
            return UseResponse(status=Status.FAILED)

        self.window = BookWindow(self, self.finished)
        self.window.show_book(self.book)
        self.window.show()

        # A new book has nothing to show.
        if not self.book.title:
            self.edit()
        self.record(request.item_id, darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def edit(self):
        if self.window is not None:
            self.window.edit(self.book)

    def cancel(self):
        """Stop editing, without saving."""
        if self.read() and self.window is not None:
            self.window.show_book(self.book)
            self.show_status("")

    def save(self):
        """Save the form: it stays open if that can't be done, with what's
        been typed, and what's been made is kept, so saving again finishes
        the job."""
        window = self.window
        if window is None:
            return
        window.form.apply()
        new = self.book.work is None or not self.book.work.get_label()
        try:
            darq.book.save(self.store, self.book)
        except BookError as e:
            logging.error(f"Can't save {self.item_id}: {e}")
            window.show_status(str(e))
            return
        self.record(self.item_id, darq.history.EDITED)
        if self.read():
            window.show_book(self.book)
            self.show_status("Added" if new else "Saved")

    def read_book(self):
        """Read the first of the book's files."""
        file = first_file(self.book)
        if file:
            self.opener.use(file)
        else:
            self.show_status("This book has no file to read")

    def show_entity(self):
        self.opener.use(self.item_id, model.ENTITY_TYPE)

    def reread(self):
        if self.read() and self.window is not None:
            self.window.show_book(self.book)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

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
        self.store.close()
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

    lens = BookLens(loop, history)
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
