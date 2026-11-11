# darqos
# Copyright (C) 2026 David Arnold

# Tests for the PDF lens, lenses/pdf/main.py: its helpers, PdfSync with
# a faked item, and the window, offscreen.

import importlib.util
import pathlib

import pytest
from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent

from darq.errors import DeadPortError
from darq.protocols import pdf as wire
from darq.protocols.lens import Status, UseRequest

from test_pdf_type import make_pdf


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "pdf"
ITEM_ID = "com.adobe.pdf:0123456789abcdef0123456789abcdef"


def load():
    spec = importlib.util.spec_from_file_location("pdf_lens", LENS_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lens = load()


class FakePdf:
    """Stands in for a Pdf.Client."""

    def __init__(self, data: bytes = b""):
        self.data = data
        self.closed = False
        self.dead = False
        self.position = ""
        self.views: list[tuple[str, str]] = []

    def get(self):
        if self.dead:
            raise DeadPortError("item is gone")
        return wire.GetResponse(data=self.data)

    def close(self):
        if self.dead:
            raise DeadPortError("item is gone")
        self.closed = True

    def get_position(self):
        if self.dead:
            raise DeadPortError("item is gone")
        return wire.GetPositionResponse(position=self.position)

    def set_position(self, position):
        if self.dead:
            raise DeadPortError("item is gone")
        self.position = position

    def viewed(self, source="", note=""):
        if self.dead:
            raise DeadPortError("item is gone")
        self.views.append((source, note))


# Helpers


def test_zoom_text():
    assert lens.zoom_text(1.0) == "100%"
    assert lens.zoom_text(0.5) == "50%"


def test_next_zoom():
    assert lens.next_zoom(1.0, 1) == 5 / 4
    assert lens.next_zoom(1.0, -1) == 3 / 4
    assert lens.next_zoom(0.9, 1) == 1.0
    assert lens.next_zoom(0.9, -1) == 3 / 4
    assert lens.next_zoom(lens.ZOOMS[-1], 1) == lens.ZOOMS[-1]
    assert lens.next_zoom(lens.ZOOMS[0], -1) == lens.ZOOMS[0]


def test_fitted_zoom():
    # At 72 dpi, a point is a pixel.
    assert lens.fitted_zoom((100, 200), (300, 300), 72, whole_page=False) == 3.0
    assert lens.fitted_zoom((100, 200), (300, 300), 72, whole_page=True) == 1.5
    assert lens.fitted_zoom((100, 200), (300, 300), 144, whole_page=False) == 1.5
    assert lens.fitted_zoom((0, 0), (300, 300), 72, whole_page=False) == 1.0


def test_step_match():
    assert lens.step_match(-1, 0, 1) == -1
    assert lens.step_match(-1, 3, 1) == 0
    assert lens.step_match(-1, 3, -1) == 2
    assert lens.step_match(0, 3, -1) == 2
    assert lens.step_match(2, 3, 1) == 0
    assert lens.step_match(1, 3, 1) == 2


# PdfSync


def test_sync_reloads_and_closes():
    client = FakePdf(b"data")
    sync = lens.PdfSync(client, ITEM_ID)
    assert sync.reload() and sync.data == b"data"
    sync.close()
    sync.close()
    assert client.closed


def test_sync_when_the_item_has_gone():
    client = FakePdf()
    client.dead = True
    sync = lens.PdfSync(client, ITEM_ID)
    assert not sync.reload() and not sync.available
    sync.close()
    assert not client.closed


# The window


def wait_for(qt_app, ready, what):
    """Run Qt's events until something's happened."""
    import time

    deadline = time.monotonic() + 5
    while not ready():
        assert time.monotonic() < deadline, f"Timed out waiting for {what}"
        qt_app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def window(qt_app):
    created = []

    def make(data=None):
        client = FakePdf(make_pdf(("One", "Two", "Three", "Four")) if data is None else data)
        sync = lens.PdfSync(client, ITEM_ID)
        assert sync.reload()
        w = lens.PdfWindow(sync)
        w.show()
        qt_app.processEvents()
        created.append(w)
        return w

    yield make
    for w in created:
        w.close()


def key(window, k, ctrl=False, shift=False):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    modifiers = Qt.KeyboardModifier.NoModifier
    if ctrl:
        modifiers |= Qt.KeyboardModifier.ControlModifier
    if shift:
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, k, modifiers, ""))


def test_opens_fitted_to_the_width(window):
    from PyQt6.QtPdfWidgets import QPdfView

    w = window()
    assert w.page_count == 4 and w.page == 0
    assert w.describe_failure() == ""
    assert w.view.zoomMode() == QPdfView.ZoomMode.FitToWidth
    assert w.view.pageMode() == QPdfView.PageMode.MultiPage


def test_dark(window):
    w = window()
    corner = w.view.grab().toImage().pixelColor(0, 0)
    assert corner.name() == lens.BACKGROUND


def test_shows_the_pages(window):
    w = window()
    image = w.view.grab().toImage()
    colours = {image.pixelColor(x, 100).name() for x in range(0, image.width(), 16)}
    assert "#ffffff" in colours


def test_zoom(window):
    from PyQt6.QtCore import Qt
    from PyQt6.QtPdfWidgets import QPdfView

    w = window()
    fitted = w.current_zoom()
    assert fitted > 0

    # A zoom starts from what's shown, though the view was fitting.
    key(w, Qt.Key.Key_Plus)
    assert w.view.zoomMode() == QPdfView.ZoomMode.Custom
    assert w.view.zoomFactor() > fitted
    assert w.zoom_label.isVisibleTo(w)
    key(w, Qt.Key.Key_Minus)
    key(w, Qt.Key.Key_Minus)
    assert w.view.zoomFactor() < fitted

    key(w, Qt.Key.Key_0)
    assert w.view.zoomFactor() == 1.0
    key(w, Qt.Key.Key_F)
    assert w.view.zoomMode() == QPdfView.ZoomMode.FitInView
    assert 0 < w.current_zoom() < fitted
    key(w, Qt.Key.Key_W)
    assert w.view.zoomMode() == QPdfView.ZoomMode.FitToWidth


def test_paging_scrolls(window, qt_app):
    from PyQt6.QtCore import Qt

    w = window()
    bar = w.view.verticalScrollBar()
    assert bar.maximum() > 0

    key(w, Qt.Key.Key_PageDown)
    moved = bar.value()
    assert moved > 0
    key(w, Qt.Key.Key_Space)
    assert bar.value() > moved
    key(w, Qt.Key.Key_Space, shift=True)
    assert bar.value() == moved
    key(w, Qt.Key.Key_PageUp)
    assert bar.value() == 0
    key(w, Qt.Key.Key_Down)
    assert bar.value() > 0
    key(w, Qt.Key.Key_Up)
    assert bar.value() == 0


def test_scrolling_moves_the_current_page(window, qt_app):
    w = window()
    bar = w.view.verticalScrollBar()
    bar.setValue(bar.maximum())
    wait_for(qt_app, lambda: w.page == 3, "the last page")
    assert w.windowTitle().endswith("(4 of 4)")


def test_go_to(window, qt_app, monkeypatch):
    from PyQt6.QtCore import Qt

    w = window()
    key(w, Qt.Key.Key_End)
    wait_for(qt_app, lambda: w.page == 3, "the last page")
    assert w.view.verticalScrollBar().value() > 0
    key(w, Qt.Key.Key_Home)
    wait_for(qt_app, lambda: w.page == 0, "the first page")
    assert w.view.verticalScrollBar().value() <= lens.MARGIN

    monkeypatch.setattr(w, "ask_page", lambda: 3)
    key(w, Qt.Key.Key_G)
    wait_for(qt_app, lambda: w.page == 2, "page 3")

    monkeypatch.setattr(w, "ask_page", lambda: None)
    key(w, Qt.Key.Key_G)
    assert w.page == 2

    # Beyond the ends is the ends.
    w.go_to(100)
    wait_for(qt_app, lambda: w.page == 3, "the last page")


def test_find(window, qt_app):
    from PyQt6.QtCore import Qt

    w = window(make_pdf(("apple", "pear", "apple pie", "plum")))
    key(w, Qt.Key.Key_F, ctrl=True)
    assert w.find_bar.isVisible()

    w.find_bar.field.setText("apple")
    wait_for(qt_app, lambda: w.search.count() == 2, "the matches")
    assert w.view.currentSearchResultIndex() == 0
    assert w.find_bar.count.text() == "1 of 2"

    key(w, Qt.Key.Key_F3)
    assert w.view.currentSearchResultIndex() == 1
    wait_for(qt_app, lambda: w.page == 2, "the second match's page")
    assert w.find_bar.count.text() == "2 of 2"

    key(w, Qt.Key.Key_F3)
    assert w.view.currentSearchResultIndex() == 0
    key(w, Qt.Key.Key_F3, shift=True)
    assert w.view.currentSearchResultIndex() == 1

    w.find_bar.field.setText("nothing like it")
    wait_for(qt_app, lambda: w.find_bar.count.text() == "No matches", "no matches")


def test_find_bar_keys(window, qt_app):
    from PyQt6.QtCore import QCoreApplication, Qt
    from PyQt6.QtGui import QKeyEvent

    def press(k, modifiers=Qt.KeyboardModifier.NoModifier):
        QCoreApplication.sendEvent(w.find_bar.field, QKeyEvent(QKeyEvent.Type.KeyPress, k, modifiers, ""))

    w = window(make_pdf(("apple", "apple", "pear")))
    w.open_find()
    w.find_bar.field.setText("apple")
    wait_for(qt_app, lambda: w.search.count() == 2, "the matches")

    press(Qt.Key.Key_Return)
    assert w.view.currentSearchResultIndex() == 1
    press(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert w.view.currentSearchResultIndex() == 0

    press(Qt.Key.Key_Escape)
    assert not w.find_bar.isVisible()
    assert w.search.searchString() == ""


def test_f3_opens_find_with_nothing_to_find(window):
    from PyQt6.QtCore import Qt

    w = window()
    key(w, Qt.Key.Key_F3)
    assert w.find_bar.isVisible()


def test_outline(window, qt_app):
    from PyQt6.QtCore import Qt

    w = window(make_pdf(("One", "Two", "Three"), outline=(("First", 0), ("Last", 2))))
    assert not w.outline.isVisible()
    key(w, Qt.Key.Key_O)
    assert w.outline.isVisible()
    assert w.bookmarks.rowCount() == 2

    last = w.bookmarks.index(1, 0)
    assert last.data() == "Last"
    w.outline_chosen(last)
    wait_for(qt_app, lambda: w.page == 2, "the outline entry's page")

    key(w, Qt.Key.Key_O)
    assert not w.outline.isVisible()


def test_no_outline(window):
    from PyQt6.QtCore import Qt

    w = window()
    key(w, Qt.Key.Key_O)
    assert not w.outline.isVisible()
    assert w.notice.text() == "This PDF has no outline"


def test_protected(window):
    w = window(make_pdf(protected=True))
    assert w.page_count == 0
    assert w.describe_failure() == "This PDF is protected by a password"
    w.show_failure()
    assert w.notice.text() == "This PDF is protected by a password"

    # Nothing goes wrong for having no pages.
    w.go_to(3)
    w.zoom_step(1)


def test_help(window):
    from PyQt6.QtCore import Qt

    w = window()
    key(w, Qt.Key.Key_F1)
    assert w.help.isVisible()
    key(w, Qt.Key.Key_Escape)
    assert not w.help.isVisible()


def test_lens_use(qt_app, monkeypatch):
    client = FakePdf(make_pdf())
    monkeypatch.setattr(lens.wire.Pdf, "Client", lambda port, timeout: client)
    stopped = []

    class Loop:
        def stop(self):
            stopped.append(True)

    pdf_lens = lens.PdfLens(Loop(), lambda item_id: "")  # pyright: ignore[reportArgumentType]
    pdf_lens.name_of = lambda item_id: "A name"
    assert pdf_lens.use(UseRequest(item_id=ITEM_ID, item=5), 0).status == Status.OK
    assert pdf_lens.use(UseRequest(item_id=ITEM_ID, item=6), 0).status == Status.BUSY
    # The item's told it's viewed, and where: its type records it.
    assert client.views == [("pdf-lens", "page 1 of 1")]
    assert pdf_lens.window is not None
    pdf_lens.window.close()
    assert client.closed and stopped == [True]


def press(window, key, modifiers=None):
    event = QKeyEvent(QtCore.QEvent.Type.KeyPress, key, modifiers or QtCore.Qt.KeyboardModifier.NoModifier)
    window.keyPressEvent(event)


def test_a_plain_pdf_opens_in_the_column(window):
    view = window()
    assert not view.reading
    assert view.view.pageMode() == lens.QPdfView.PageMode.MultiPage


def test_the_reading_view_is_a_page_at_a_time(window):
    view = window()
    view.set_reading(True)
    assert view.view.pageMode() == lens.QPdfView.PageMode.SinglePage
    assert view.view.zoomMode() == lens.QPdfView.ZoomMode.FitInView
    view.set_reading(False)
    assert view.view.pageMode() == lens.QPdfView.PageMode.MultiPage


def test_the_reading_views_keys_turn_pages(window):
    K = QtCore.Qt.Key
    view = window()
    view.set_reading(True)
    press(view, K.Key_Space)
    press(view, K.Key_PageDown)
    press(view, K.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert view.page == 3
    press(view, K.Key_Space)
    assert view.page == 3
    press(view, K.Key_Backspace)
    press(view, K.Key_B, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert view.page == 1
    press(view, K.Key_End)
    assert view.page == 3
    press(view, K.Key_Home)
    assert view.page == 0


def test_control_f_finds_in_the_column(window):
    view = window()
    press(view, QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert view.find_bar.isVisibleTo(view) and view.page == 0


def test_v_switches_views(window):
    view = window()
    press(view, QtCore.Qt.Key.Key_V)
    assert view.reading


def test_b_catalogues_in_either_view(window):
    view = window()
    asked = []
    view.on_catalogue = lambda: asked.append(True)
    press(view, QtCore.Qt.Key.Key_B)
    view.set_reading(True)
    press(view, QtCore.Qt.Key.Key_B)
    assert len(asked) == 2


def test_a_documents_details(window):
    data = make_pdf(("ISBN 0-596-00712-4",), title="A Title", author="An Author")
    details = window(data).details()
    assert (details.title, details.authors, details.isbn) == ("A Title", ["An Author"], "0596007124")


def test_closing_saves_the_page(window):
    view = window()
    view.go_to(2)
    client = view.sync.client
    view.close()
    assert client.position == "2"


def run_lens(monkeypatch, client, copy=""):
    monkeypatch.setattr(lens.wire.Pdf, "Client", lambda port, timeout: client)

    class Loop:
        def post(self, job):
            job()

        def stop(self):
            pass

    pdf_lens = lens.PdfLens(Loop(), lambda item_id: copy)  # pyright: ignore[reportArgumentType]
    pdf_lens.name_of = lambda item_id: "A name"
    assert pdf_lens.use(UseRequest(item_id=ITEM_ID, item=5), 0).status == Status.OK
    assert pdf_lens.window is not None
    return pdf_lens.window


def test_it_reopens_where_it_was(qt_app, monkeypatch):
    client = FakePdf(make_pdf(("One", "Two", "Three")))
    client.position = "2"
    window = run_lens(monkeypatch, client)
    assert window.page == 2
    window.close()


def test_nonsense_in_a_position_is_the_start(qt_app, monkeypatch):
    client = FakePdf(make_pdf(("One", "Two")))
    client.position = "1:0.5"
    window = run_lens(monkeypatch, client)
    assert window.page == 0
    window.close()


def test_being_catalogued_is_the_mark(qt_app, monkeypatch):
    window = run_lens(monkeypatch, FakePdf(make_pdf(("One",))), "darq.book.item:c")
    assert window.reading
    window.close()


def test_a_pdf_not_catalogued_is_plain(qt_app, monkeypatch):
    window = run_lens(monkeypatch, FakePdf(make_pdf(("One",))))
    assert not window.reading
    window.close()


def test_lens_use_when_the_item_has_gone(qt_app, monkeypatch):
    client = FakePdf()
    client.dead = True
    monkeypatch.setattr(lens.wire.Pdf, "Client", lambda port, timeout: client)

    class Loop:
        def stop(self):
            pass

    pdf_lens = lens.PdfLens(Loop())  # pyright: ignore[reportArgumentType]
    pdf_lens.name_of = lambda item_id: "A name"
    assert pdf_lens.use(UseRequest(item_id=ITEM_ID, item=5), 0).status == Status.FAILED
    assert pdf_lens.window is None
