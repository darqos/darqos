# darqos
# Copyright (C) 2026 David Arnold

# Book lens: the card for a book, in the Terminal context, for an item
# of any of a book's four levels (see doc/book.rst).
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  The book is
# read through items' ports (see darq.ui.book_store), and shown as one card,
# whichever level was opened, with that one highlighted (book_card.py).
# E edits it (book_form.py), and A adds more books: New starts there
# (book_add.py).  People, publishers and files open in their
# own lenses, in new windows, and I shows what was opened in the entity
# lens, with all of its statements.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to items and History are
# synchronous, on queued reply ports.

import logging
import mimetypes
import os
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent, QTextDocument
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.book
import darq.history
import darq.kb as model
import darq.launch
import darq.openlibrary
from darq.book import Book, BookError, Copy, Edition, Text
from darq.loop import QtEventLoop
from darq.openlibrary import LookupError as OpenLibraryError
from darq.protocols.history import History
from darq.protocols.image import Image as ImageItem
from darq.protocols.http import Http
from darq.protocols.http import Status as HttpStatus
from darq.protocols.lens import Close, Ready, Status, UseRequest, UseResponse
from darq.ui import book_store, catalogue, style
from darq.ui.lens import LensHandler, announce
from darq.ui.launcher import Launcher, start_thread
from darq.ui.style import CONTROLS

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import book_add  # noqa: E402
import book_card  # noqa: E402
import book_form  # noqa: E402


# History source for the events it records.
SOURCE = "book-lens"

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Seconds to wait for Open Library, and most bytes of an answer.
LOOKUP_TIMEOUT = 30
MAX_ANSWER = 4 * 1024 * 1024

# Where books are looked up: a test's server, or Open Library.
LOOKUP_BASE = "DARQ_OPEN_LIBRARY"

# Fallback language, when the locale doesn't say.
DEFAULT_LANGUAGE = "en"

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """E                     Edit
A                     Add more books
R                     Read: the first format
I                     Show in the entity lens
F5                    Read it again
F1                    Help
F11                   Full screen
S-x                   Close

While editing:
Ctrl+S                Save
Escape                Cancel

While adding:
Enter                 Look up, then add
Y, N, M               Answer what's asked
Escape                Clear, then finish"""


def languages() -> list[str]:
    """Return the chain of languages, best first."""
    tag = os.environ.get("LANG", "").split(".")[0].replace("_", "-")
    chain = [tag, tag.split("-")[0], DEFAULT_LANGUAGE]
    return [t for n, t in enumerate(chain) if t and t not in chain[:n]]


def fetch_url(url: str) -> bytes:
    """Fetch a URL with the HTTP service, on the calling thread.

    :raises OpenLibraryError: if it can't be done."""
    handle = darq.lookup_name("darq.http", blocking=False)
    if handle is None:
        raise OpenLibraryError("The HTTP service isn't running")
    client = Http.Client(handle, timeout=LOOKUP_TIMEOUT)
    try:
        response = client.request(method="GET", url=url, headers=[], body=b"", max_size=MAX_ANSWER, id=0,
                                  listener=None)
    except (darq.DarqError, TimeoutError) as e:
        raise OpenLibraryError(f"Can't reach Open Library: {e}") from None
    if response.status != HttpStatus.OK:
        raise OpenLibraryError(f"Can't reach Open Library: {response.status.name}")
    if response.http_status != 200:
        raise OpenLibraryError(f"Open Library said {response.http_status}")
    return response.body


def cover_data(item_id: str) -> bytes:
    """Return a cover's image, from its item, or nothing, as it's only for
    showing."""
    try:
        port = darq.launch.open_item(item_id)
    except (darq.DarqError, TimeoutError) as e:
        logging.info(f"Can't open a cover: {e!r}")
        return b""
    item = ImageItem.Client(port, timeout=LOOKUP_TIMEOUT)
    try:
        return item.get().data
    except (darq.DarqError, TimeoutError) as e:
        logging.info(f"Can't read a cover: {e!r}")
        return b""
    finally:
        try:
            item.close()
        except (darq.DarqError, TimeoutError):
            pass
        darq.close_port(port)


def snapshot_url(url: str) -> str:
    """Keep what's at a URL, with the HTTP service, returning its item.

    :raises OpenLibraryError: if it can't be."""
    http = catalogue.Services.http()
    if http is None:
        raise OpenLibraryError("The HTTP service isn't running")
    response = http.snapshot(url=url, max_size=MAX_ANSWER, id=0, listener=None)
    if response.status != HttpStatus.OK or not response.item:
        raise OpenLibraryError(f"Can't keep {url}: {response.status.name}")
    return response.item


def first_content(book: Book) -> str:
    """Return the first of a book's content, or an empty string."""
    return next((c.content for e in book.editions for c in e.copies if c.content), "")


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
        self.targets: list[book_card.Target] = []

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(CONTROLS + f"BookWindow {{ background-color: {style.SURFACE}; }}"
                           f"QTextBrowser {{ background-color: {style.SURFACE}; color: {style.BORDER};"
                           " border: none; }")
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
        self.form.names = lens.names
        self.adding = book_add.AddView(lens, self)
        self.level = book_form.LevelForm(lens, self)
        self.level.names = lens.names
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.form)
        self.stack.addWidget(self.adding)
        self.stack.addWidget(self.level)
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
        html = page.render()
        document = self.browser.document()
        assert document is not None
        for item_id in page.covers:
            data = self.lens.cover(item_id)
            if data:
                document.addResource(QTextDocument.ResourceType.ImageResource.value,
                                     QtCore.QUrl(book_card.cover_url(item_id)), QImage.fromData(data))
        self.browser.setHtml(html)
        self.targets = page.targets
        self.setWindowTitle(book.title or "Book")
        self.stack.setCurrentWidget(self.browser)
        self.browser.setFocus()

    def edit(self, book: Book):
        self.form.show_book(book)
        self.stack.setCurrentWidget(self.form)
        self.show_status("Ctrl+S saves, and Escape cancels")

    def add(self):
        self.adding.start()
        self.stack.setCurrentWidget(self.adding)

    def add_level(self, target: book_card.Add):
        self.stack.setCurrentWidget(self.level)
        self.level.show_level(target)

    def add_level_form(self, target: book_card.Edit, what: object):
        self.stack.setCurrentWidget(self.level)
        self.level.show_edit(target, what)

    def show_status(self, message: str):
        self.status.setText(message)

    def on_anchor(self, url: QtCore.QUrl):
        fragment = url.fragment()
        if fragment.isdigit() and int(fragment) < len(self.targets):
            target = self.targets[int(fragment)]
            if isinstance(target, book_card.Add):
                self.lens.start_add(target)
            elif isinstance(target, book_card.Edit):
                self.lens.start_edit(target)
            elif isinstance(target, book_card.Prefer):
                self.lens.prefer(target.copy)
            else:
                self.lens.opener.use(target)

    def on_context_menu(self, pos: QtCore.QPoint):
        menu = QMenu(self)
        menu.addAction("Edit", self.lens.edit)
        menu.addAction("Add Books", self.lens.start_adding)
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
        elif key == Key.Key_F1:
            self.toggle_help()
        elif key == Key.Key_F11:
            self.toggle_full_screen()
        elif self.stack.currentWidget() is self.adding:
            if key == Key.Key_S and control:
                self.lens.add_shown()
            else:
                super().keyPressEvent(a0)
        elif self.stack.currentWidget() is self.level:
            if key == Key.Key_S and control:
                self.lens.save_level()
            elif key == Key.Key_Escape:
                self.lens.cancel()
            else:
                super().keyPressEvent(a0)
        elif self.editing:
            if key == Key.Key_S and control:
                self.lens.save()
            elif key == Key.Key_Escape:
                self.lens.cancel()
            else:
                super().keyPressEvent(a0)
        elif key == Key.Key_E:
            self.lens.edit()
        elif key == Key.Key_A:
            self.lens.start_adding()
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


class BookLens(LensHandler):
    """The lens port: it uses one item, for the life of the process."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None, opener: Opener | None = None,
                 store: book_store.ItemStore | None = None):
        self.loop = loop
        self.history = history
        self.window: BookWindow | None = None
        self.services = typing.cast(typing.Any, LensServices(history))
        self.launcher = Launcher(self, SOURCE, loop.post)
        self.opener: Opener = opener if opener is not None else LaunchOpener(self.launcher)
        self.store = store if store is not None else book_store.ItemStore(languages(), record=self.record)
        self.item_id = ""
        self.book = Book()

        # The new, empty work a loop can use for its first book, and how
        # many have been added.
        self.spare: Book | None = None
        self.added = 0

        # Asks before removing anything, and for what's needed to move it:
        # tests replace these.
        self.confirm: typing.Callable[[str], bool] = self.ask_to_confirm
        self.ask_text: typing.Callable[[str], str | None] = self.ask_for_text
        self.choose: typing.Callable[[str, list[str]], int | None] = self.ask_to_choose

        # Gets a cover's image, and keeps what's at a URL, returning its
        # item: tests replace these.
        self.cover: typing.Callable[[str], bytes] = cover_data
        self.snapshot: typing.Callable[[str], str] = snapshot_url

        # How lookups are fetched and run: tests replace these.
        self.fetch: darq.openlibrary.Fetch = fetch_url
        self.run: typing.Callable[[typing.Callable[[], None]], None] = start_thread
        self.base = os.environ.get(LOOKUP_BASE, darq.openlibrary.BASE)

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

        # A new book has nothing to show: it's the start of adding some.
        if not self.book.title:
            self.spare = self.book
            self.start_adding()
        self.record(request.item_id, darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        self.shown_item = request.item_id
        return UseResponse(status=Status.OK)

    def edit(self):
        if self.window is not None:
            self.window.edit(self.book)

    # Adding

    def start_adding(self):
        if self.window is not None:
            self.window.add()
            self.show_status("Enter looks up what's typed: Escape finishes")

    def look_up(self, text: str):
        """Look something up, on another thread: it's sent to Open Library,
        so it's only done when asked."""
        self.show_status(f"Looking up {text} ...")
        fetch, base = self.fetch, self.base

        def job():
            try:
                outcome: typing.Any = darq.openlibrary.lookup(text, fetch, base)
            except OpenLibraryError as e:
                outcome = e
            if self.run is start_thread:
                darq.release_reply_port()
            self.loop.post(lambda: self.looked_up(text, outcome))

        self.run(job)

    def looked_up(self, text: str, outcome: typing.Any):
        window = self.window
        if window is None:
            return
        if isinstance(outcome, OpenLibraryError):
            self.show_status(str(outcome))
            return
        if window.stack.currentWidget() is window.level:
            if outcome.isbn and outcome.books:
                window.level.fill(outcome.books[0].editions[0])
                self.show_status("Check it, and Ctrl+S saves it")
            else:
                self.show_status(f"Open Library doesn't know {text}: type it in")
            return
        if outcome.books:
            window.adding.show_results(outcome.books)
            self.show_status("Check it, and press Enter to add it")
            self.fields_changed()
            return

        # Nothing's known, so it's typed in, with what's known kept.
        if outcome.isbn:
            field = "isbn_13" if len(outcome.isbn) == 13 else "isbn_10"
            window.adding.fields[field].setText(outcome.isbn)
        else:
            window.adding.fields["title"].setText(text)
        window.adding.fields["title"].setFocus()
        self.show_status(f"Open Library doesn't know {text}: type it in")
        self.fields_changed()

    def fields_changed(self):
        """Say what the book being added is to what's here."""
        window = self.window
        if window is None or window.adding.asking:
            return
        book = window.adding.book()
        if not book.title and not book.editions[0].isbn_13 and not book.editions[0].isbn_10:
            window.adding.match.clear()
            return
        try:
            match = darq.book.find_match(self.store, book)
        except BookError as e:
            logging.warning(f"Can't look for a book: {e}")
            return
        window.adding.show_match(match.kind, match.book.title if match.book is not None else "")

    def add_shown(self):
        """Add the book that's shown, unless it might be one that's here,
        when that's asked: nearly always, it's new."""
        window = self.window
        if window is None or window.adding.asking:
            return
        book = window.adding.book()
        problem = darq.book.check(book)
        if problem:
            self.show_status(problem)
            return
        try:
            match = darq.book.find_match(self.store, book)
        except BookError as e:
            logging.error(f"Can't look for a book: {e}")
            self.show_status(str(e))
            return

        title = match.book.title if match.book is not None else ""
        new = darq.book.Match()
        if match.kind == darq.book.ANOTHER_COPY:
            copies = match.edition.copies if match.edition is not None else []
            where = ", ".join(c.location or "no location" for c in copies)
            window.adding.ask(f"You have this: {title}{f' ({where})' if where else ''}.\n"
                              "Y adds another copy, and N doesn't.",
                              {Key.Key_Y: lambda: self.commit(book, match), Key.Key_N: self.declined})
        elif match.kind == darq.book.ANOTHER_EDITION and match.language_differs(book):
            here = match.book.language if match.book is not None else ""
            window.adding.ask(f"{title} is in {here}, and this is in {book.language}.\n"
                              "N adds it as a new book.  Escape, and add it from that book's card, if it's a "
                              "text of that one.", {Key.Key_N: lambda: self.commit(book, new)})
        elif match.kind == darq.book.ANOTHER_EDITION:
            window.adding.ask(f"This might be {title}.\nM adds it as a new Manifestation of that book, "
                              "and N as a new book.",
                              {Key.Key_M: lambda: self.commit(book, match), Key.Key_N: lambda: self.commit(book, new)})
        else:
            self.commit(book, match)
            return

        # What it said's no longer true: Enter does nothing now.
        self.show_status("")

    def declined(self):
        """Nothing's added: it's ready for the next."""
        if self.window is not None:
            self.window.adding.start()
            self.show_status("Not added")

    def commit(self, book: Book, match: darq.book.Match):
        """Add a book, and be ready for the next."""
        window = self.window
        if window is None:
            return
        edition = book.editions[0] if book.editions else None
        try:
            if match.kind == darq.book.NEW and self.spare is not None:
                book.work = self.spare.work
            saved = darq.book.add(self.store, book, match)
        except BookError as e:
            logging.error(f"Can't add a book: {e}")
            self.show_status(str(e))
            return

        if saved is book:
            self.spare = None
        self.store.settled()
        if saved.work is not None:
            self.item_id = model.item_id(saved.work.id, darq.book.WORK_TYPE)
        self.added += 1
        if edition is not None and edition.entity is not None and edition.cover_url:
            self.fetch_cover(edition.entity, edition.cover_url)
        window.adding.start()
        said = {darq.book.NEW: "Added", darq.book.ANOTHER_EDITION: "Added an edition of",
                darq.book.ANOTHER_COPY: "Added a copy of"}[match.kind]
        self.show_status(f"{said} {saved.title}: {self.added} so far")

    # Adding a level, from a card's links

    def start_add(self, target: book_card.Add):
        if self.window is not None:
            self.window.add_level(target)
            self.show_status("Ctrl+S saves, and Escape cancels")

    def start_edit(self, target: book_card.Edit):
        """Edit a level, as it's read now, from wherever it is: E's form
        only reaches the text the card's opened on."""
        window = self.window
        if window is None:
            return
        try:
            _, what = self.level_of(target)
        except BookError as e:
            self.show_status(str(e))
            return
        window.add_level_form(target, what)
        self.show_status("Ctrl+S saves, and Escape cancels")

    def level_of(self, target: book_card.Edit) -> tuple[Book, object]:
        """Return the book a level's in, read with it first, and what in it
        has that level's fields.

        :raises BookError: if it can't be read."""
        book = darq.book.read(self.store, model.item_id(target.entity.id, book_card.TYPES[target.level]))
        if target.level in (book_card.WORK, book_card.EXPRESSION):
            return book, book
        for edition in book.editions:
            if edition.entity is not None and edition.entity.id == target.entity.id:
                return book, edition
            for copy in edition.copies:
                if copy.entity is not None and copy.entity.id == target.entity.id:
                    return book, copy
        raise BookError("It's gone")

    def save_level(self):
        """Add the level that's been filled in, or save what's been edited,
        and show it on the card."""
        window = self.window
        target = window.level.target if window is not None else None
        if window is None or target is None:
            return
        values: typing.Any = window.level.values()
        try:
            if isinstance(target, book_card.Edit):
                book, what = self.level_of(target)
                for attribute, text in values.items():
                    setattr(what, attribute, book_form.from_text(text, getattr(what, attribute)))
                darq.book.save(self.store, book)
            elif target.level == book_card.EXPRESSION:
                people = book_form.from_text
                darq.book.add_text(self.store, target.parent, Text(
                    title=values["title"], language=values["language"],
                    translators=typing.cast(list[str], people(values["translators"], [])),
                    editors=typing.cast(list[str], people(values["editors"], []))))
            elif target.level == book_card.MANIFESTATION:
                edition = Edition(**values, open_library=window.level.open_library)
                darq.book.add_edition(self.store, target.parent, edition)
                if edition.entity is not None and window.level.cover_url:
                    self.fetch_cover(edition.entity, window.level.cover_url)
            else:
                darq.book.add_copy(self.store, target.parent, Copy(**values))
        except BookError as e:
            logging.error(f"Can't save a {target.level}: {e}")
            window.show_status(str(e))
            return
        self.store.settled()
        if self.read():
            window.show_book(self.book)
            self.show_status(f"Saved the {target.level}")

    def remove_level(self):
        """Remove what's being edited, once it's been confirmed."""
        window = self.window
        target = window.level.target if window is not None else None
        if window is None or not isinstance(target, book_card.Edit):
            return
        if not self.confirm(f"Remove this {target.level}?"):
            return
        removing = model.item_id(target.entity.id, book_card.TYPES[target.level])
        work = self.book.work
        try:
            darq.book.remove(self.store, book_card.TYPES[target.level], target.entity)
        except BookError as e:
            window.show_status(str(e))
            return
        self.store.settled()
        if target.level == book_card.WORK:
            window.close()
            return
        if removing == self.item_id and work is not None:
            self.item_id = model.item_id(work.id, darq.book.WORK_TYPE)
        if self.read():
            window.show_book(self.book)
            self.show_status(f"Removed the {target.level}")

    def names(self, text: str, kind: str) -> list[str]:
        """Return names that are here, starting with some words."""
        return self.store.names_like(text, kind)

    def prefer(self, copy: Copy):
        """Make a copy the one to read."""
        try:
            darq.book.prefer(self.store, self.book, copy)
        except BookError as e:
            self.show_status(str(e))
            return
        self.store.settled()
        self.reread()

    def fetch_cover(self, manifestation: model.Entity, url: str):
        """Keep a cover, from where a lookup said it is, on another thread,
        and give it to a manifestation: it's for showing, so nothing's said
        if it can't be had."""
        snapshot = self.snapshot

        def job():
            try:
                item_id = snapshot(url)
            except (darq.DarqError, TimeoutError, OpenLibraryError) as e:
                logging.info(f"Can't keep a cover: {e!r}")
                item_id = ""
            if self.run is start_thread:
                darq.release_reply_port()
            if item_id:
                self.loop.post(lambda: self.got_cover(manifestation, item_id))

        self.run(job)

    def got_cover(self, manifestation: model.Entity, item_id: str):
        try:
            darq.book.set_cover(self.store, manifestation, item_id)
        except BookError as e:
            logging.info(f"Can't give a manifestation its cover: {e}")
            return
        self.store.settled()
        if self.window is not None and self.window.stack.currentWidget() is self.window.browser:
            self.reread()

    def choose_cover(self):
        """Make a cover of an image file on the host."""
        window = self.window
        if window is None:
            return
        path, _ = QFileDialog.getOpenFileName(window, "Cover", str(pathlib.Path.home()),
                                              "Images (*.jpg *.jpeg *.png *.gif *.webp *.tif *.tiff *.heic)",
                                              options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            data = pathlib.Path(path).read_bytes()
            item_id = darq.launch.import_data(mimetypes.guess_type(path)[0] or "", pathlib.Path(path).suffix[1:],
                                              data)
        except (OSError, darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't use it: {e}")
            return
        if not item_id:
            self.show_status("That isn't an image that can be kept")
            return
        window.level.edits["cover"].setText(item_id)

    def move_level(self):
        """Move what's being edited to another book, chosen by its title."""
        window = self.window
        target = window.level.target if window is not None else None
        if window is None or not isinstance(target, book_card.Edit) or target.level == book_card.WORK:
            return
        title = self.ask_text("Move it to the book titled:")
        if not title:
            return
        try:
            works = self.store.works_like(title)
            if not works:
                self.show_status(f"There's no book titled {title}")
                return
            chosen = self.choose("Which book?", [label for _, label in works])
            if chosen is None:
                return
            there = darq.book.read(self.store, works[chosen][0])
            parent = self.new_parent(target.level, there)
            if parent is None:
                return
            darq.book.move(self.store, book_card.TYPES[target.level], target.entity, parent)
        except BookError as e:
            window.show_status(str(e))
            return
        self.store.settled()
        if self.read():
            window.show_book(self.book)
            self.show_status(f"Moved the {target.level} to {there.title}")

    def new_parent(self, level: str, there: Book) -> model.Entity | None:
        """Return what in a book something of a level's to move under,
        asking which, if there's a choice, or None."""
        texts = [t for t in [darq.book.Text(there.expression, there.language, title=there.text_title,
                                            editions=there.editions), *there.texts] if t.entity is not None]
        if level == book_card.EXPRESSION:
            return there.work
        if level == book_card.MANIFESTATION:
            options = [(t.entity, darq.book.expression_label(t.language, t.title)) for t in texts]
        else:
            options = [(e.entity, book_card.Page.about_edition(e)) for t in texts for e in t.editions
                       if e.entity is not None]
        if not options:
            self.show_status(f"{there.title} has nowhere for it to go")
            return None
        if len(options) == 1:
            return options[0][0]
        chosen = self.choose("Under which?", [label for _, label in options])
        return options[chosen][0] if chosen is not None else None

    def ask_for_text(self, question: str) -> str | None:
        text, ok = QInputDialog.getText(self.window, "Book", question)
        return text.strip() if ok and text.strip() else None

    def ask_to_choose(self, question: str, choices: list[str]) -> int | None:
        chosen, ok = QInputDialog.getItem(self.window, "Book", question, choices, 0, False)
        return choices.index(chosen) if ok and chosen in choices else None

    def ask_to_confirm(self, question: str) -> bool:
        answer = QMessageBox.question(self.window, "Book", question)
        return answer == QMessageBox.StandardButton.Yes

    def stop_adding(self):
        """Stop adding: show the last book, or close if there's nothing to
        show, and clear away a new book that wasn't used."""
        spare, self.spare = self.spare, None
        if spare is not None and spare.work is not None:
            self.destroy(model.item_id(spare.work.id, darq.book.WORK_TYPE))
            if not self.added:
                if self.window is not None:
                    self.window.close()
                return
        if self.window is not None and self.read():
            self.window.show_book(self.book)
            self.show_status(f"{self.added} added" if self.added else "")

    def destroy(self, item_id: str):
        try:
            self.store.destroy(item_id)
        except BookError as e:
            logging.warning(f"Can't clear away {item_id}: {e}")

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
        self.store.settled()
        if self.read():
            window.show_book(self.book)
            self.show_status("Added" if new else "Saved")

    def read_book(self):
        """Read the book: the copy that's preferred, or else the first."""
        content = darq.book.preferred_content(self.book)
        if content:
            self.opener.use(content)
        else:
            self.show_status("This book has nothing to read")

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
