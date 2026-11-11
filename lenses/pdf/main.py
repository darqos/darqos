# darqos
# Copyright (C) 2026 David Arnold

# PDF lens: a read-only window for reading a PDF item (com.adobe.pdf),
# in the Terminal context (see doc/pdf-type.rst).
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  It gets the
# PDF from the item, and renders it itself, with Qt PDF: pages in one
# continuous column, fitted to the window's width to begin with, with
# Find, and the document's outline, if it has one.  A PDF that's been
# catalogued as a book opens in the reading view, a page at a time, with
# the keys of the e-book lens (darq.ui.reader): being catalogued is the
# mark (see doc/book.rst).  It reopens where the reader left off.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to the item are synchronous, on
# queued reply ports.  It tells the item it's being viewed, and on which
# page (see darq.ui.lens.Viewing): the item's type records that in
# History.

import logging
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QColor, QKeyEvent, QPalette
from PyQt6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfSearchModel
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.book
from darq.loop import QtEventLoop
from darq.protocols import pdf as wire
from darq.protocols.lens import Close, Ready, Status, UseRequest, UseResponse
from darq.ui import catalogue, reader, style
from darq.ui.catalogue import Details
from darq.ui.launcher import Launcher
from darq.ui.names import item_name
from darq.ui.lens import MESSAGE_SHOWN, LensHandler, Viewing, announce, overlay, show_over
from darq.ui.style import BACKGROUND, CONTROLS


# History's source of what's done through it.
SOURCE = "pdf-lens"

# Seconds to wait for the item's replies: a large PDF takes a while.
TIMEOUT = 30.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Milliseconds a new zoom or page is shown over the document (and a
# message, for MESSAGE_SHOWN).
ZOOM_SHOWN = 1500

# Pixels between pages, and around them, and the width of the outline.
PAGE_SPACING = 8
MARGIN = 8
OUTLINE_WIDTH = 280

# The usual controls, with the outline and find bar on the surface.
STYLE = CONTROLS + f"""
PdfWindow, FindBar {{ background-color: {style.SURFACE}; }}
QTreeView {{ color: {style.TEXT}; background-color: {style.SURFACE}; border: none;
            selection-background-color: {style.SELECTION}; selection-color: {style.TEXT_STRONG}; }}
"""

# Zoom factors: 1 is the document's actual size.
ZOOMS = [1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 4, 1, 5 / 4, 3 / 2, 2, 3, 4, 6, 8]

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """\
<b>PDF</b><br>
+ / −&nbsp;&nbsp;Zoom in, out<br>
W&nbsp;&nbsp;Fit the width &nbsp;&nbsp; F&nbsp;&nbsp;Fit a page &nbsp;&nbsp; 0&nbsp;&nbsp;Actual size<br>
Page Up / Page Down, Space&nbsp;&nbsp;Move a screen<br>
Home / End&nbsp;&nbsp;First, last page &nbsp;&nbsp; G&nbsp;&nbsp;Go to a page<br>
Ctrl+S, Ctrl+F&nbsp;&nbsp;Find &nbsp;&nbsp; F3 / Shift+F3&nbsp;&nbsp;Next, previous match<br>
O&nbsp;&nbsp;Outline<br>
V&nbsp;&nbsp;Reading view &nbsp;&nbsp; B&nbsp;&nbsp;Catalogue it as a book<br>
F1&nbsp;&nbsp;This help &nbsp;&nbsp; F11&nbsp;&nbsp;Full screen"""

# How many of the first pages are looked through for an ISBN.
ISBN_PAGES = 8


def zoom_text(zoom: float) -> str:
    return f"{zoom * 100:.0f}%"


def next_zoom(zoom: float, step: int) -> float:
    """Return the next zoom in or out from a zoom, which needn't be one
    of ZOOMS, as a fitted zoom isn't."""
    if step > 0:
        return next((z for z in ZOOMS if z > zoom * 1.001), ZOOMS[-1])
    return next((z for z in reversed(ZOOMS) if z < zoom * 0.999), ZOOMS[0])


def fitted_zoom(page: tuple[float, float], area: tuple[float, float], dpi: float, whole_page: bool) -> float:
    """Return the zoom that fits a page, in points, to an area, in pixels:
    to its width, or the whole page."""
    width, height = page
    if width <= 0 or height <= 0:
        return 1.0
    pixels = dpi / 72.0
    zoom = area[0] / (width * pixels)
    return min(zoom, area[1] / (height * pixels)) if whole_page else zoom


def step_match(index: int, count: int, step: int) -> int:
    """Return the match to move to from one, wrapping around, or -1 if
    there aren't any."""
    if count <= 0:
        return -1
    if index < 0:
        return 0 if step > 0 else count - 1
    return (index + step) % count


class PdfSync:
    """The lens's use of the item.  It uses no Qt, so it can be tested
    without a display."""

    def __init__(self, client: wire.Pdf.Client, item_id: str):
        self.client = client
        self.item_id = item_id
        self.data = b""

        # False once the item has stopped answering.
        self.available = True

    def reload(self) -> bool:
        """Get the PDF.

        :returns: False if the item doesn't answer."""
        try:
            self.data = self.client.get().data
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't get {self.item_id}: {e!r}")
            self.available = False
            return False
        return True

    def position(self) -> int:
        """Return the page the reader had got to, from 0."""
        try:
            return int(self.client.get_position().position or 0)
        except (darq.DarqError, TimeoutError, ValueError) as e:
            logging.warning(f"Can't get the position of {self.item_id}: {e!r}")
            return 0

    def save_position(self, page: int):
        """Save the page the reader's got to: it rewrites the blob, so
        it's only done on closing."""
        if not self.available:
            return
        try:
            self.client.set_position(position=str(page))
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't save the position of {self.item_id}: {e!r}")

    def close(self):
        """Close the item, once finished with it."""
        if not self.available:
            return
        self.available = False
        try:
            self.client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close {self.item_id}: {e!r}")


class FindBar(QWidget):
    """The bar at the foot of the window for finding text."""

    def __init__(self, window: "PdfWindow"):
        super().__init__(window)
        self.window_ = window
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
        self.field = QLineEdit(self)
        self.field.setPlaceholderText("Find")
        self.field.textChanged.connect(window.find_text)
        self.field.installEventFilter(self)
        self.count = QLabel(self)
        previous = QPushButton("Previous", self)
        previous.clicked.connect(lambda: window.find_step(-1))
        following = QPushButton("Next", self)
        following.clicked.connect(lambda: window.find_step(1))
        for button in (previous, following):
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.field, 1)
        layout.addWidget(self.count)
        layout.addWidget(previous)
        layout.addWidget(following)
        self.setLayout(layout)

    def eventFilter(self, a0, a1):
        """Take Enter and Esc from the field."""
        if a0 is self.field and isinstance(a1, QKeyEvent) and a1.type() == QKeyEvent.Type.KeyPress:
            if a1.key() in (Key.Key_Return, Key.Key_Enter):
                self.window_.find_step(-1 if a1.modifiers() & Modifier.ShiftModifier else 1)
                return True
            if a1.key() == Key.Key_Escape:
                self.window_.close_find()
                return True
        return super().eventFilter(a0, a1)


class PdfWindow(QWidget):
    """The window: the pages, with the outline beside them, the find bar
    below, and messages shown over them."""

    def __init__(self, sync: PdfSync, on_closed: typing.Callable[[], None] | None = None,
                 on_catalogue: typing.Callable[[], None] | None = None):
        super().__init__()
        self.viewing = Viewing(self, SOURCE)
        self.sync = sync
        self.on_closed = on_closed
        self.on_catalogue = on_catalogue

        # Whether it's in the reading view.
        self.reading = False
        self.set_name(sync.item_id)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(STYLE)

        # The document reads from the buffer, and the buffer from the
        # bytes, so they last as long as the window.
        self.contents = QtCore.QByteArray(sync.data)
        self.buffer = QtCore.QBuffer(self.contents, self)
        self.buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
        self.document = QPdfDocument(self)
        self.document.load(self.buffer)

        self.view = QPdfView(self)
        palette = self.view.palette()
        palette.setColor(QPalette.ColorRole.Dark, QColor(BACKGROUND))
        self.view.setPalette(palette)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setPageSpacing(PAGE_SPACING)
        self.view.setDocumentMargins(QtCore.QMargins(MARGIN, MARGIN, MARGIN, MARGIN))
        self.view.setFrameShape(QPdfView.Shape.NoFrame)
        self.view.setDocument(self.document)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        # The keys are the window's, not the view's.
        self.view.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self.navigator = self.view.pageNavigator()
        assert self.navigator is not None
        self.navigator.currentPageChanged.connect(self.page_changed)

        self.search = QPdfSearchModel(self)
        self.search.setDocument(self.document)
        self.search.countChanged.connect(self.matches_changed)
        self.view.setSearchModel(self.search)
        self.view.currentSearchResultIndexChanged.connect(lambda index: self.show_match_count())

        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.document)
        self.outline = QTreeView(self)
        self.outline.setModel(self.bookmarks)
        self.outline.setHeaderHidden(True)
        self.outline.setFixedWidth(OUTLINE_WIDTH)
        self.outline.activated.connect(self.outline_chosen)
        self.outline.clicked.connect(self.outline_chosen)
        self.outline.hide()

        self.find_bar = FindBar(self)
        self.find_bar.hide()

        self.help = QLabel(HELP, self)
        self.help.setStyleSheet(style.HELP)
        self.help.hide()

        # A new zoom or page, in its middle, and messages, at its foot,
        # shown over the pages for a moment: there's no status line.
        self.zoom_label, self.zoom_timer = overlay(self, 28)
        self.notice, self.notice_timer = overlay(self, 16)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.outline)
        body.addWidget(self.view, 1)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(body, 1)
        layout.addWidget(self.find_bar)
        self.setLayout(layout)
        self.resize(1024, 768)

    def set_name(self, name: str):
        """Title the window with its item's name, which the switcher shows:
        its identifier, until its name's known."""
        self.name = name
        self.setWindowTitle(name)

    # Showing

    @property
    def page_count(self) -> int:
        return self.document.pageCount()

    @property
    def page(self) -> int:
        """The current page, from 0."""
        assert self.navigator is not None
        return self.navigator.currentPage()

    def describe_failure(self) -> str:
        """Return why the document can't be shown, or an empty string."""
        if self.document.status() == QPdfDocument.Status.Ready:
            return ""
        if self.document.error() == QPdfDocument.Error.IncorrectPassword:
            return "This PDF is protected by a password"
        return "This PDF can't be read"

    def show_failure(self):
        """Say so, if the document can't be shown."""
        failure = self.describe_failure()
        if failure:
            self.show_over(self.notice, self.notice_timer, failure, 3600000, middle=True)

    def show_over(self, label: QLabel, timer: QtCore.QTimer, text: str, shown: int, middle: bool):
        show_over(label, timer, text, self.view.geometry(), shown, middle)

    def show_status(self, message: str):
        self.show_over(self.notice, self.notice_timer, message, MESSAGE_SHOWN, middle=False)

    def show_zoom(self, text: str):
        self.show_over(self.zoom_label, self.zoom_timer, text, ZOOM_SHOWN, middle=True)

    def page_changed(self, page: int):
        self.setWindowTitle(f"{self.name} ({page + 1} of {self.page_count})")
        self.viewing.move(self.where())

    def where(self) -> str:
        """Return where the reader's got to, for History."""
        return f"page {self.page + 1} of {self.page_count}" if self.page_count else ""

    # Zooming

    def current_zoom(self) -> float:
        """Return the zoom being shown, which the view only knows when
        it isn't fitting pages to the window."""
        mode = self.view.zoomMode()
        if mode == QPdfView.ZoomMode.Custom:
            return self.view.zoomFactor()
        size = self.document.pagePointSize(self.page)
        viewport = self.view.viewport()
        assert viewport is not None
        area = (viewport.width() - 2 * MARGIN, viewport.height() - 2 * MARGIN)
        return fitted_zoom((size.width(), size.height()), area, self.view.logicalDpiX(),
                           mode == QPdfView.ZoomMode.FitInView)

    def set_zoom(self, zoom: float):
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(zoom)
        self.show_zoom(zoom_text(zoom))

    def zoom_step(self, step: int):
        self.set_zoom(next_zoom(self.current_zoom(), step))

    def actual_size(self):
        self.set_zoom(1.0)

    def fit_width(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.show_zoom("Fit width")

    def fit_page(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.show_zoom("Fit page")

    # Moving

    def scroll_screens(self, steps: int):
        """Scroll by screens."""
        bar = self.view.verticalScrollBar()
        assert bar is not None
        bar.setValue(bar.value() + steps * bar.pageStep())

    def scroll_lines(self, steps: int):
        bar = self.view.verticalScrollBar()
        assert bar is not None
        bar.setValue(bar.value() + steps * bar.singleStep())

    def go_to(self, page: int):
        """Go to a page, from 0."""
        if self.page_count == 0:
            return
        assert self.navigator is not None
        page = max(0, min(self.page_count - 1, page))
        self.navigator.jump(page, QtCore.QPointF(), self.navigator.currentZoom())
        self.show_zoom(f"Page {page + 1} of {self.page_count}")

    def ask_page(self) -> int | None:
        """Ask which page to go to, from 1.  Tests replace it."""
        page, chosen = QInputDialog.getInt(self, "Go to page", "Page:", self.page + 1, 1, max(1, self.page_count))
        return page if chosen else None

    # The reading view

    def set_reading(self, reading: bool):
        """Show a page at a time, fitted to the window, or the continuous
        column."""
        page = self.page
        self.reading = reading
        self.view.setPageMode(QPdfView.PageMode.SinglePage if reading else QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView if reading else QPdfView.ZoomMode.FitToWidth)
        self.go_to(page)

    def turn(self, step: int):
        self.go_to(self.page + step)

    def catalogue(self):
        if self.on_catalogue is not None:
            self.on_catalogue()

    def read_key(self, a0: QKeyEvent) -> bool:
        """Handle a key as the reading view does, returning whether it was
        one of its keys."""
        action = reader.action_for(a0.key(), bool(a0.modifiers() & Modifier.ControlModifier))
        actions: dict[str, typing.Callable[[], object]] = {
            reader.NEXT: lambda: self.turn(1),
            reader.PREVIOUS: lambda: self.turn(-1),
            reader.FIRST: lambda: self.go_to(0),
            reader.LAST: lambda: self.go_to(self.page_count - 1),
            reader.LARGER: lambda: self.zoom_step(1),
            reader.SMALLER: lambda: self.zoom_step(-1),
            reader.CONTENTS: self.toggle_outline,
            reader.FIND: self.open_find,
            reader.CATALOGUE: self.catalogue,
            reader.SHOW_HELP: self.toggle_help,
            reader.FULL_SCREEN: self.toggle_full_screen,
        }
        if action not in actions:
            return False
        actions[action]()
        return True

    def details(self) -> Details:
        """Return the document's title, authors and ISBN, as far as it
        says: an ISBN's looked for in its first pages."""
        title = str(self.document.metaData(QPdfDocument.MetaDataField.Title) or "").strip()
        author = str(self.document.metaData(QPdfDocument.MetaDataField.Author) or "").strip()
        pages = range(min(ISBN_PAGES, self.page_count))
        text = "\n".join(self.document.getAllText(page).text() for page in pages)
        return Details(title, [author] if author else [], darq.book.find_isbn(text))

    def go_to_page(self):
        page = self.ask_page()
        if page is not None:
            self.go_to(page - 1)

    # Finding

    def open_find(self):
        self.find_bar.show()
        self.find_bar.field.setFocus()
        self.find_bar.field.selectAll()

    def close_find(self):
        self.find_bar.hide()
        self.find_bar.field.clear()
        self.setFocus()

    def find_text(self, text: str):
        self.view.setCurrentSearchResultIndex(-1)
        self.search.setSearchString(text)
        self.show_match_count()

    def matches_changed(self):
        """Go to the first match, as soon as there is one.  The model
        searches a page at a time, so more can come.

        It's gone to after the model's signal, not in it: from Qt 6.12,
        jumping the view while the model's adding matches crashes it, in
        QPdfView's paintEvent."""
        if self.search.count() > 0 and self.view.currentSearchResultIndex() < 0:
            QtCore.QTimer.singleShot(0, self.first_match)
        self.show_match_count()

    def first_match(self):
        if self.search.count() > 0 and self.view.currentSearchResultIndex() < 0:
            self.go_to_match(0)
            self.show_match_count()

    def go_to_match(self, index: int):
        assert self.navigator is not None
        self.view.setCurrentSearchResultIndex(index)
        self.navigator.jump(self.search.resultAtIndex(index))

    def find_step(self, step: int):
        index = step_match(self.view.currentSearchResultIndex(), self.search.count(), step)
        if index >= 0:
            self.go_to_match(index)

    def find_next(self, step: int):
        """Move between matches, or open the bar if nothing's being found."""
        if self.search.searchString():
            self.find_step(step)
        else:
            self.open_find()

    def show_match_count(self):
        count = self.search.count()
        if not self.search.searchString():
            text = ""
        elif count == 0:
            text = "No matches"
        else:
            text = f"{self.view.currentSearchResultIndex() + 1} of {count}"
        self.find_bar.count.setText(text)

    # The outline

    def toggle_outline(self):
        if self.outline.isVisible():
            self.outline.hide()
            self.setFocus()
        elif self.bookmarks.rowCount() == 0:
            self.show_status("This PDF has no outline")
        else:
            self.outline.show()
            self.outline.setFocus()

    def outline_chosen(self, index: QtCore.QModelIndex):
        page = index.data(QPdfBookmarkModel.Role.Page.value)
        if page is not None:
            self.go_to(int(page))

    # Everything else

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
        if self.reading and self.read_key(a0):
            return
        try:
            key = Key(a0.key())
        except ValueError:
            super().keyPressEvent(a0)
            return
        modifiers = a0.modifiers()
        shift = bool(modifiers & Modifier.ShiftModifier)
        if modifiers & Modifier.ControlModifier:
            if key in (Key.Key_F, Key.Key_S):
                self.open_find()
            else:
                super().keyPressEvent(a0)
            return

        actions: dict[Key, typing.Callable[[], None]] = {
            Key.Key_Plus: lambda: self.zoom_step(1),
            Key.Key_Equal: lambda: self.zoom_step(1),
            Key.Key_Minus: lambda: self.zoom_step(-1),
            Key.Key_0: self.actual_size,
            Key.Key_W: self.fit_width,
            Key.Key_F: self.fit_page,
            Key.Key_PageDown: lambda: self.scroll_screens(1),
            Key.Key_PageUp: lambda: self.scroll_screens(-1),
            Key.Key_Space: lambda: self.scroll_screens(-1 if shift else 1),
            Key.Key_Down: lambda: self.scroll_lines(1),
            Key.Key_Up: lambda: self.scroll_lines(-1),
            Key.Key_Home: lambda: self.go_to(0),
            Key.Key_End: lambda: self.go_to(self.page_count - 1),
            Key.Key_G: self.go_to_page,
            Key.Key_F3: lambda: self.find_next(-1 if shift else 1),
            Key.Key_O: self.toggle_outline,
            Key.Key_V: lambda: self.set_reading(not self.reading),
            Key.Key_B: self.catalogue,
            Key.Key_Escape: lambda: self.help.hide(),
            Key.Key_F1: self.toggle_help,
            Key.Key_F11: self.toggle_full_screen,
        }
        action = actions.get(key)
        if action is None:
            super().keyPressEvent(a0)
            return
        action()

    def contextMenuEvent(self, a0):
        if a0 is None:
            return
        menu = QMenu(self)
        menu.addAction("Zoom In", lambda: self.zoom_step(1))
        menu.addAction("Zoom Out", lambda: self.zoom_step(-1))
        menu.addAction("Actual Size", self.actual_size)
        menu.addAction("Fit Width", self.fit_width)
        menu.addAction("Fit Page", self.fit_page)
        menu.addSeparator()
        menu.addAction("Go to Page...", self.go_to_page)
        menu.addAction("Find", self.open_find)
        menu.addAction("Outline", self.toggle_outline)
        menu.addAction("Reading View", lambda: self.set_reading(not self.reading))
        menu.addAction("Catalogue as a Book", self.catalogue)
        menu.addSeparator()
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        menu.exec(a0.globalPos())

    def closeEvent(self, a0):
        self.viewing.stop()
        self.sync.save_position(self.page)
        self.sync.close()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, None
            on_closed()


class PdfLens(LensHandler):
    """Answers the Lens protocol: uses one item, in a window."""

    def __init__(self, loop: QtEventLoop, find_copy: typing.Callable[[str], str] = catalogue.find_copy):
        # Looks up an item's name, for its window's title: tests replace it.
        self.name_of: typing.Callable[[str], str] = item_name
        """Constructor.

        :param find_copy: Returns the copy an item is of a book, if any."""
        self.loop = loop
        self.find_copy = find_copy
        self.window: PdfWindow | None = None
        self.item_id = ""
        self.services = typing.cast(typing.Any, catalogue.Services())
        self.launcher = Launcher(self, SOURCE, lambda job: loop.post(job))

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        sync = PdfSync(wire.Pdf.Client(request.item, timeout=TIMEOUT), request.item_id)
        if not sync.reload():
            return UseResponse(status=Status.FAILED)
        self.window = PdfWindow(sync, on_closed=self.finished, on_catalogue=self.catalogue)
        self.window.set_name(self.name_of(request.item_id))
        self.item_id = request.item_id
        self.window.show()
        self.window.show_failure()
        self.window.go_to(sync.position())
        if self.find_copy(request.item_id):
            self.window.set_reading(True)
        self.window.viewing.show(sync.client, self.window.where())
        logging.info(f"Using {request.item_id}")
        self.shown_item = request.item_id
        return UseResponse(status=Status.OK)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def catalogue(self):
        """Catalogue the PDF as a book, or show its card."""
        window = self.window
        if window is None:
            return
        problem = catalogue.catalogue(window, self.launcher, self.item_id, window.details(), SOURCE)
        if problem:
            window.show_zoom(problem)
        else:
            window.set_reading(True)

    # As the Launcher's host

    def show_status(self, message: str):
        if self.window is not None:
            self.window.show_zoom(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        if error:
            self.show_status(error)

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

    lens = PdfLens(loop)
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
