# darqos
# Copyright (C) 2026 David Arnold

# E-book lens: reading an EPUB, MOBI or AZW item, read-only, in the
# Terminal context, whatever its format (see doc/book.rst).
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  The type gives
# it a book's parts as HTML (see lib/didl/ebook.didl), through a book:
# URL scheme, so nothing's written to disk, and Qt WebEngine shows them,
# in pages (see ebook_pages.py).  Its keys are those of the PDF lens's
# reading view (darq.ui.reader).
#
# A book's untrusted: its scripts are off, and nothing outside it's
# loaded, with a link out of it offered to the HTTP service, as a URL in
# Search is.  Keys and the wheel reach the lens before Chromium, so
# it's the lens that decides what they do.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to the item and History are
# synchronous, on queued reply ports.

import logging
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QUrl
from PyQt6.QtGui import QColor, QKeyEvent, QWheelEvent
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.history
import darq.launch
from darq.loop import QtEventLoop
from darq.protocols.ebook import ContentsResponse, Ebook
from darq.protocols.ebook import Status as EbookStatus
from darq.protocols.history import History
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.ui import catalogue, reader, style
from darq.ui.launcher import Launcher
from darq.ui.style import CONTROLS

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ebook_pages  # noqa: E402


# History source for the events it records.
SOURCE = "ebook-lens"

# Seconds to wait for the item's replies, and after starting for a Use.
TIMEOUT = 30.0
USE_TIMEOUT = 60.0

SCHEME = b"book"

# runJavaScript's world for the lens: not the book's.
APPLICATION_WORLD = 1

# Text sizes, by zoom, and how far apart.
MIN_ZOOM = 0.5
MAX_ZOOM = 3.0
ZOOM_STEP = 1.15

# Milliseconds of quiet after a resize before laying out again.
RELAY_DELAY = 150

# The wheel's movement for one page.
WHEEL_STEP = 120

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

# Read errors from the item.
ERRORS = (darq.DarqError, TimeoutError, ValueError)


# The scheme has to be registered before there's an application.
_scheme = QWebEngineUrlScheme(SCHEME)
_scheme.setFlags(QWebEngineUrlScheme.Flag.LocalScheme | QWebEngineUrlScheme.Flag.LocalAccessAllowed)
QWebEngineUrlScheme.registerScheme(_scheme)


class Parts(QWebEngineUrlSchemeHandler):
    """Answers book: URLs, from the item."""

    def __init__(self, item: Ebook.Client, parent: QObject, mode: typing.Callable[[], str]):
        """Constructor.

        :param mode: Returns the reader's mode, which a part's styled in."""
        super().__init__(parent)
        self.item = item
        self.mode = mode

    def requestStarted(self, a0: QWebEngineUrlRequestJob | None):
        job = a0
        if job is None:
            return
        name = ebook_pages.name_of(job.requestUrl().path())
        try:
            response = self.item.get_part(name=name)
        except ERRORS as e:
            logging.warning(f"Can't read a part: {e!r}")
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        if response.status != EbookStatus.OK:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)
        buffer.setData(QByteArray(ebook_pages.styled(response.data, response.media_type, self.mode())))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(response.media_type.encode(), buffer)


class BookOnly(QWebEngineUrlRequestInterceptor):
    """Stops a book reaching anything outside itself."""

    def interceptRequest(self, info):
        if info.requestUrl().scheme() != SCHEME.decode():
            info.block(True)


class BookPage(QWebEnginePage):
    """A book's page: links are the lens's to decide."""

    def __init__(self, profile: QWebEngineProfile, window: "ReaderWindow"):
        super().__init__(profile, window)
        self.window_ = window

    def acceptNavigationRequest(self, url: QUrl, type: QWebEnginePage.NavigationType, isMainFrame: bool) -> bool:
        if url.scheme() == SCHEME.decode():
            if type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                self.window_.link(url)
                return False
            return True
        if type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.window_.outside_link(url)
        return False

    def createWindow(self, type: QWebEnginePage.WebWindowType) -> QWebEnginePage | None:
        return None

    def javaScriptConsoleMessage(self, level: QWebEnginePage.JavaScriptConsoleMessageLevel, message: str | None,
                                 lineNumber: int, sourceID: str | None):
        logging.debug(f"From the book: {message}")


class ReaderWindow(QWidget):
    """A frameless, distraction-free window on a book, and Ebook's Page."""

    def __init__(self, lens: "EbookLens", item: Ebook.Client, on_closed: typing.Callable[[], None]):
        super().__init__()
        self.lens = lens
        self.on_closed = on_closed
        self.pager: ebook_pages.Pager | None = None
        self.mode = reader.MODES[0][0]
        self.wheel = 0

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(CONTROLS + f"ReaderWindow {{ background-color: {style.SURFACE}; }}")
        self.resize(900, 760)

        self.profile = QWebEngineProfile(self)
        self.parts = Parts(item, self, lambda: self.mode)
        self.blocker = BookOnly(self)
        self.profile.installUrlSchemeHandler(SCHEME, self.parts)
        self.profile.setUrlRequestInterceptor(self.blocker)
        self.page_ = BookPage(self.profile, self)
        settings = self.page_.settings()
        assert settings is not None
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page_)
        self.paint_background()
        self.view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self.page_.loadFinished.connect(self.loaded)

        self.contents = QListWidget(self)
        self.contents.itemActivated.connect(self.chosen)
        self.contents.hide()
        self.contents.setMaximumWidth(320)

        self.finder = QLineEdit(self)
        self.finder.setPlaceholderText("Find")
        self.finder.returnPressed.connect(self.find_text)
        self.finder.hide()

        self.status = QLabel(self)
        self.status.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; font-size: 13px; }}")

        self.notice = QLabel(self)
        self.notice.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"QLabel {{ color: {style.TEXT_SOFT}; font-size: 18px; }}")

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.notice)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.contents)
        row.addWidget(self.stack, 1)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 4)
        layout.addLayout(row, 1)
        layout.addWidget(self.finder)
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(reader.HELP, self)
        self.help.setStyleSheet(style.HELP)
        self.help.hide()

        self.relay = QtCore.QTimer(self)
        self.relay.setSingleShot(True)
        self.relay.setInterval(RELAY_DELAY)
        self.relay.timeout.connect(self.relaid)

    def paint_background(self):
        """Give the page its mode's background, which shows while a part's
        loading, before it's painted."""
        background = next(m[1] for m in reader.MODES if m[0] == self.mode)
        self.page_.setBackgroundColor(QColor(background))

    # As ebook_pages.Page

    def run(self, script: str, done: typing.Callable[[typing.Any], None]):
        self.page_.runJavaScript(script, APPLICATION_WORLD, done)

    def load(self, name: str):
        self.page_.load(QUrl(ebook_pages.url_of(name)))

    # Showing

    def read(self, contents: ContentsResponse, position: str):
        """Show a book, from where the reader had got to."""
        self.setWindowTitle(contents.title or "Book")
        for entry in contents.contents:
            item = QListWidgetItem("    " * entry.level + entry.title)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, entry.target)
            self.contents.addItem(item)
        self.pager = ebook_pages.Pager(self, list(contents.parts), self.mode, self.page_shown)
        self.pager.open_at(position)

    def say(self, reason: str):
        """Show why a book can't be read."""
        self.notice.setText(f"This book can't be read: {reason}\n\nIt's kept, and B catalogues it.")
        self.stack.setCurrentWidget(self.notice)

    def loaded(self, ok: bool):
        if self.pager is not None:
            if ok:
                self.pager.part_loaded()
            else:
                logging.info("A page didn't load")

    def page_shown(self):
        if self.pager is not None:
            self.status.setText(f"  {self.pager.describe()}")

    def show_status(self, message: str):
        self.status.setText(f"  {message}")

    # Links

    def link(self, url: QUrl):
        if self.pager is not None:
            name = ebook_pages.name_of(url.path())
            self.pager.go_to(f"{name}#{url.fragment()}" if url.hasFragment() else name)

    def outside_link(self, url: QUrl):
        self.lens.fetch(url.toString())

    def chosen(self, item: QListWidgetItem | None):
        if item is not None and self.pager is not None:
            self.pager.go_to(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.contents.hide()
            self.view.setFocus()

    # What keys ask for

    def do(self, action: str):
        pager = self.pager
        if action == reader.SHOW_HELP:
            self.toggle_help()
        elif action == reader.FULL_SCREEN:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif action == reader.CATALOGUE:
            self.lens.catalogue()
        elif pager is None:
            return
        elif action == reader.NEXT:
            pager.next()
        elif action == reader.PREVIOUS:
            pager.previous()
        elif action == reader.FIRST:
            pager.first()
        elif action == reader.LAST:
            pager.last()
        elif action == reader.LARGER:
            self.zoom(ZOOM_STEP)
        elif action == reader.SMALLER:
            self.zoom(1 / ZOOM_STEP)
        elif action == reader.CONTENTS:
            self.contents.setVisible(not self.contents.isVisible() and self.contents.count() > 0)
            if self.contents.isVisible():
                self.contents.setFocus()
        elif action == reader.MODE:
            self.mode = reader.next_mode(self.mode)
            self.paint_background()
            pager.set_mode(self.mode)
            self.show_status(f"{self.mode.capitalize()} mode")
        elif action == reader.FIND:
            self.finder.show()
            self.finder.setFocus()
            self.finder.selectAll()

    def zoom(self, by: float):
        self.view.setZoomFactor(max(MIN_ZOOM, min(MAX_ZOOM, self.view.zoomFactor() * by)))
        self.relay.start()

    def relaid(self):
        if self.pager is not None:
            self.pager.relaid()

    def find_text(self):
        text = self.finder.text()
        if text:
            self.page_.findText(text, QWebEnginePage.FindFlag(0), self.found)

    def found(self, result):
        pager = self.pager
        if pager is None:
            return
        if result.numberOfMatches() == 0:
            self.show_status("Not in this part")
            return
        pager.view.run(ebook_pages.page_of_selection(pager.page),
                       lambda page: pager.show(page) if isinstance(page, int) and page >= 0 else None)

    def toggle_help(self):
        if self.help.isVisible():
            self.help.hide()
        else:
            self.help.adjustSize()
            self.help.move((self.width() - self.help.width()) // 2, (self.height() - self.help.height()) // 2)
            self.help.show()
            self.help.raise_()

    # Input

    def key(self, event: QKeyEvent) -> bool:
        """Handle a key, returning whether it's dealt with: it's here before
        Chromium.  A field being typed in has them, or the contents."""
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            if event.key() == Key.Key_Escape:
                self.finder.hide()
                self.view.setFocus()
                return True
            return False
        if focus is self.contents and event.key() != Key.Key_Escape:
            return False
        if event.key() == Key.Key_Escape:
            self.help.hide()
            self.contents.hide()
            self.view.setFocus()
            return True
        action = reader.action_for(event.key(), bool(event.modifiers() & Modifier.ControlModifier))
        if action is None:
            return False
        self.do(action)
        return True

    def wheeled(self, event: QWheelEvent):
        """Turn a page for each click of the wheel."""
        self.wheel += event.angleDelta().y()
        while abs(self.wheel) >= WHEEL_STEP:
            self.do(reader.NEXT if self.wheel < 0 else reader.PREVIOUS)
            self.wheel -= WHEEL_STEP if self.wheel > 0 else -WHEEL_STEP

    def eventFilter(self, a0, a1) -> bool:
        if a1 is None or not self.isActiveWindow():
            return False
        if a1.type() == QEvent.Type.KeyPress and isinstance(a1, QKeyEvent):
            return self.key(a1)
        if a1.type() == QEvent.Type.Wheel and isinstance(a1, QWheelEvent) and a0 is self.view.windowHandle():
            self.wheeled(a1)
            return True
        return False

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self.relay.start()

    def closeEvent(self, a0):
        super().closeEvent(a0)
        self.on_closed()


class EbookLens(Lens.Handler):
    """The lens port: it uses one item, for the life of the process."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None):
        self.loop = loop
        self.history = history
        self.window: ReaderWindow | None = None
        self.item: Ebook.Client | None = None
        self.port = 0
        self.item_id = ""
        self.details: ContentsResponse | None = None
        self.services = typing.cast(typing.Any, catalogue.Services(history))
        self.launcher = Launcher(self, SOURCE, loop.post)

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)
        self.port = request.item
        self.item_id = request.item_id
        self.item = Ebook.Client(request.item, timeout=TIMEOUT)
        try:
            contents = self.item.contents()
            position = self.item.get_position().position
        except ERRORS as e:
            logging.error(f"Can't read {request.item_id}: {e!r}")
            self.close_item()
            return UseResponse(status=Status.FAILED)
        self.details = contents

        self.window = ReaderWindow(self, self.item, self.finished)
        application = QApplication.instance()
        assert application is not None
        application.installEventFilter(self.window)
        if contents.status == EbookStatus.OK:
            self.window.read(contents, position)
        else:
            self.window.say(contents.reason)
        self.window.show()
        self.record(request.item_id, darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def fetch(self, url: str):
        self.launcher.fetch(url)

    def catalogue(self):
        """Catalogue the book, or show its card."""
        window, details = self.window, self.details
        if window is None or details is None:
            return
        about = catalogue.Details(details.title, list(details.authors), details.isbn, details.publisher, details.date,
                                  details.language)
        if not catalogue.find_copy(self.item_id):
            about.cover = self.import_cover(details.cover)
        problem = catalogue.catalogue(window, self.launcher, self.item_id, about, self.record)
        if problem:
            self.show_status(problem)

    def import_cover(self, name: str) -> str:
        """Make an image item of a book's cover, and return it, or an empty
        string if it hasn't one, or no type takes it."""
        if not name or self.item is None:
            return ""
        try:
            part = self.item.get_part(name=name)
            if part.status != EbookStatus.OK:
                return ""
            return darq.launch.import_data(part.media_type, name.rpartition(".")[2], part.data) or ""
        except ERRORS as e:
            logging.warning(f"Can't keep a cover: {e!r}")
            return ""

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def close_item(self):
        if self.item is None:
            return
        item, self.item = self.item, None
        try:
            item.close()
        except ERRORS as e:
            logging.warning(f"Can't close an item: {e!r}")
        darq.close_port(self.port)

    def record(self, item_id: str, event: str):
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")

    def show_status(self, message: str):
        if self.window is not None:
            self.window.show_status(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        self.show_status(error or "")

    def expire(self):
        if self.window is None:
            logging.error(f"Not asked to Use an item within {USE_TIMEOUT}s: exiting")
            self.finished()

    def finished(self):
        """Save where the reader's got to: it rewrites the blob, so it's
        only done now.  A lens that's killed loses it."""
        window = self.window
        if self.item is not None and window is not None and window.pager is not None and window.pager.loaded:
            try:
                self.item.set_position(position=window.pager.position())
            except ERRORS as e:
                logging.warning(f"Can't save the position: {e!r}")
        self.close_item()
        self.loop.stop()


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: a lens is started for an item, by the Terminal")
        return 1

    # WebEngine's Chromium won't start without a program name.
    app = QApplication(["ebook-lens"])
    app.setQuitOnLastWindowClosed(False)
    loop = QtEventLoop()

    # History is best effort: the lens works without it.
    handle = darq.lookup_name("darq.history", blocking=False)
    history = History.Client(handle) if handle is not None else None

    lens = EbookLens(loop, history)
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
