# darqos
# Copyright (C) 2026 David Arnold

# Tests for the image lens, lenses/image/main.py: its helpers, ImageSync
# with a faked item that renders with the image type's own imaging.py,
# and the window, offscreen.

import importlib.util
import io
import pathlib

import pytest
from PIL import Image

from darq.errors import DeadPortError
from darq.protocols import image as wire
from darq.protocols.lens import Status, UseRequest

from test_image_type import BLUE, GREEN, RED, encode, image_module, imaging, located, pixels, rows


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "image"
ITEM_ID = "public.png:0123456789abcdef0123456789abcdef"


def load():
    spec = importlib.util.spec_from_file_location("image_lens", LENS_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lens = load()


class FakeImage:
    """Stands in for an Image.Client: an item's original and edits,
    rendered as the implementation renders them, and the calls made."""

    def __init__(self, data: bytes = b"", type_id: str = "public.png"):
        self.data = data
        self.type_id = type_id
        self.edits: list[imaging.Edit] = []
        self.version = 1
        self.sets = 0
        self.closed = False
        self.views: list[tuple[str, str]] = []
        self.dead = False

        # Set to make the next set_edits conflict, with these edits.
        self.conflict_with: list[imaging.Edit] | None = None

    def check(self):
        if self.dead:
            raise DeadPortError("item is gone")

    def get(self):
        self.check()
        edits = [image_module.edit_to_wire(e) for e in self.edits]
        if not self.data:
            return wire.GetResponse(edits=edits, version=self.version, status=wire.Status.EMPTY)
        data = imaging.render(self.type_id, self.data, self.edits)
        info = imaging.check(self.type_id, data)
        return wire.GetResponse(data=data, width=info.width, height=info.height, frames=info.frames, edits=edits,
                                version=self.version, status=wire.Status.OK)

    def set_edits(self, edits, expected):
        self.check()
        if self.conflict_with is not None:
            self.edits, self.conflict_with = self.conflict_with, None
            self.version += 1
            return wire.SetEditsResponse(version=self.version, status=wire.Status.CONFLICT)
        assert expected == self.version
        edits = [image_module.edit_from_wire(e) for e in edits]
        try:
            imaging.check_edits(self.type_id, imaging.check(self.type_id, self.data), edits)
        except imaging.InvalidEdit:
            return wire.SetEditsResponse(version=self.version, status=wire.Status.INVALID_EDIT)
        self.edits = edits
        self.version += 1
        self.sets += 1
        return wire.SetEditsResponse(version=self.version, status=wire.Status.OK)

    def get_metadata(self):
        self.check()
        data = imaging.render(self.type_id, self.data, self.edits)
        entries = [wire.Entry(e.group, e.name, e.value, image_module.FROM_FIELDS.get(e.field, wire.Field.NONE))
                   for e in imaging.metadata(self.type_id, data)]
        return wire.GetMetadataResponse(entries=entries, status=wire.Status.OK)

    def close(self):
        self.check()
        self.closed = True

    def viewed(self, source="", note=""):
        self.check()
        self.views.append((source, note))


def make_sync(data: bytes | None = None, type_id="public.png"):
    client = FakeImage(encode(pixels(), "PNG") if data is None else data, type_id)
    sync = lens.ImageSync(client, ITEM_ID)
    assert sync.reload()
    return sync, client


TURN = wire.Edit(wire.EditType.ROTATE, turns=1)
FLIP = wire.Edit(wire.EditType.FLIP)


################################################################
# Helpers
################################################################

@pytest.mark.parametrize("x, y, zoom, ratio, expected", [
    (10, 4, 1, 1, (10, 4)),
    (10, 4, 2, 1, (5, 2)),
    # On a HiDPI screen, a logical point is two device pixels.
    (10, 4, 1, 2, (20, 8)),
    (10, 4, 0.5, 1, (20, 8)),
    # Clamped to the image, 30 x 20.
    (-5, 50, 1, 1, (0, 20)),
    (100, 4, 1, 1, (30, 4)),
])
def test_to_image(x, y, zoom, ratio, expected):
    assert lens.to_image(x, y, zoom, ratio, 30, 20) == expected


def test_selection():
    assert lens.selection((10, 2), (4, 8)) == (4, 2, 6, 6)
    assert lens.selection((3, 3), (3, 3)) == (3, 3, 0, 0)


def test_zoom_text():
    assert lens.zoom_text(1) == "100%"
    assert lens.zoom_text(2 / 3) == "66.67%"
    assert lens.zoom_text(1 / 16) == "6.25%"
    assert lens.zoom_text(32) == "3200%"


def test_next_zoom():
    assert lens.next_zoom(1, 1) == 1.5
    assert lens.next_zoom(1, -1) == 2 / 3
    assert lens.next_zoom(0.7, 1) == 1
    assert lens.next_zoom(32, 1) == 32
    assert lens.next_zoom(1 / 16, -1) == 1 / 16


def test_decode():
    image = lens.decode(encode(pixels(), "PNG"))
    assert (image.width(), image.height()) == (3, 2)
    assert image.pixelColor(2, 0).getRgb()[:3] == BLUE

    # Its EXIF orientation is made: 40 x 20, turned.
    turned = lens.decode(encode(Image.new("RGB", (40, 20)), "JPEG", exif=located().tobytes()))
    assert (turned.width(), turned.height()) == (20, 40)

    heic = lens.decode(encode(Image.new("RGB", (8, 6), RED), "HEIF"))
    assert (heic.width(), heic.height()) == (8, 6)

    with pytest.raises(ValueError):
        lens.decode(b"junk")


################################################################
# ImageSync
################################################################

def test_edit_undo_redo():
    sync, client = make_sync()
    assert (sync.width, sync.height) == (3, 2)
    assert sync.edit(TURN) == sync.OK
    assert (sync.width, sync.height) == (2, 3)
    assert sync.edit(FLIP) == sync.OK
    assert len(sync.edits) == 2

    assert sync.undo() == sync.OK
    assert client.edits == [imaging.Edit("rotate", turns=1)]
    assert sync.undo() == sync.OK
    assert client.edits == [] and sync.data == client.data
    assert sync.undo() == sync.REFUSED and sync.reason == "Nothing to undo"

    assert sync.redo() == sync.OK
    assert sync.redo() == sync.OK
    assert len(client.edits) == 2
    assert sync.redo() == sync.REFUSED

    # A new edit drops what could be redone.
    sync.undo()
    sync.edit(TURN)
    assert sync.redo_stack == []


def test_revert():
    sync, client = make_sync()
    sync.edit(TURN)
    sync.edit(FLIP)
    assert sync.revert() == sync.OK
    assert client.edits == []
    assert sync.revert() == sync.REFUSED
    assert sync.undo() == sync.OK
    assert len(client.edits) == 2


def test_conflict_reloads():
    sync, client = make_sync()
    sync.edit(FLIP)
    client.conflict_with = [imaging.Edit("rotate", turns=1)]
    assert sync.edit(FLIP) == sync.CONFLICT
    assert (sync.width, sync.height) == (2, 3)
    assert sync.undo_stack == [] and "changed elsewhere" in sync.reason


def test_refused():
    sync, _ = make_sync()
    assert sync.edit(wire.Edit(wire.EditType.CROP, x=0, y=0, width=10, height=10)) == sync.REFUSED
    assert sync.reason == "That edit doesn't fit the image"
    assert sync.undo_stack == []


def test_empty_and_animated_arent_edited():
    sync, client = make_sync(b"")
    assert sync.empty
    assert sync.edit(FLIP) == sync.REFUSED and "Import File" in sync.reason
    assert client.sets == 0

    frames = [Image.new("RGB", (4, 4), colour) for colour in (RED, GREEN, BLUE)]
    sync, client = make_sync(encode(frames[0], "GIF", save_all=True, append_images=frames[1:]), "com.compuserve.gif")
    assert sync.frames == 3
    assert sync.edit(FLIP) == sync.REFUSED
    assert sync.reason == "This image is animated (3 frames): editing animations isn't supported yet"
    assert client.sets == 0


def test_item_gone():
    sync, client = make_sync()
    client.dead = True
    assert sync.edit(FLIP) == sync.FAILED
    assert not sync.available
    sync.close()
    assert not client.closed


################################################################
# The window
################################################################

@pytest.fixture
def window(qt_app):
    created = []

    def make(data=None, type_id="public.png"):
        sync, client = make_sync(data, type_id)
        w = lens.ImageWindow(sync)
        w.show()
        created.append(w)
        return w, client

    yield make
    for w in created:
        w.close()


def key(window, k, ctrl=False, shift=False, text=""):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    modifiers = Qt.KeyboardModifier.NoModifier
    if ctrl:
        modifiers |= Qt.KeyboardModifier.ControlModifier
    if shift:
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, k, modifiers, text))


def test_shows_the_image(window):
    w, _ = window()
    assert (w.view.image.width(), w.view.image.height()) == (3, 2)
    assert (w.info.zoom.text(), w.info.edits.text()) == ("100%", "None")
    assert not w.notice.isVisible()
    assert not w.empty.isVisible()


def test_dark(window):
    from PyQt6.QtCore import Qt
    w, _ = window(encode(Image.new("RGB", (400, 300), (200, 200, 200)), "PNG"))
    assert w.styleSheet() == lens.STYLE
    assert w.info.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    # The scroll bars, and the panel's table, are dark.
    from PyQt6.QtWidgets import QApplication
    w.view.set_zoom(8)
    QApplication.processEvents()
    bar = w.scroll_area.horizontalScrollBar()
    assert bar is not None and bar.isVisible()
    grab = bar.grab().toImage()
    assert grab.pixelColor(grab.width() // 2, 1).lightness() < 80
    w.toggle_info()
    viewport = w.info.table.viewport()
    assert viewport is not None
    image = viewport.grab().toImage()
    assert image.pixelColor(image.width() - 2, image.height() - 2).lightness() < 80


def test_keys_edit(window):
    from PyQt6.QtCore import Qt
    w, client = window()
    key(w, Qt.Key.Key_BracketRight)
    assert client.edits == [imaging.Edit("rotate", turns=1)]
    assert (w.view.image.width(), w.view.image.height()) == (2, 3)
    key(w, Qt.Key.Key_BracketLeft)
    key(w, Qt.Key.Key_H)
    key(w, Qt.Key.Key_V)
    assert [e.type for e in client.edits] == ["rotate", "rotate", "flip", "flip"]
    assert w.info.edits.text() == "4"

    key(w, Qt.Key.Key_Z, ctrl=True)
    assert len(client.edits) == 3
    key(w, Qt.Key.Key_Z, ctrl=True, shift=True)
    assert len(client.edits) == 4


def test_zoom(window):
    from PyQt6.QtCore import Qt
    w, client = window()
    assert not w.zoom_label.isVisible()
    key(w, Qt.Key.Key_Plus)
    assert w.view.zoom == 1.5

    # The new zoom is shown over the image, for a moment, and in the
    # information panel.
    assert w.zoom_label.isVisible() and w.zoom_label.text() == "150%"
    assert w.info.zoom.text() == "150%"
    assert w.zoom_timer.isActive() and w.zoom_timer.interval() == lens.ZOOM_SHOWN
    w.zoom_timer.timeout.emit()
    assert not w.zoom_label.isVisible()
    key(w, Qt.Key.Key_Minus)
    key(w, Qt.Key.Key_Minus)
    assert w.view.zoom == 2 / 3
    key(w, Qt.Key.Key_0)
    assert w.view.zoom == 1
    key(w, Qt.Key.Key_F)
    assert w.view.zoom > 1

    # Zooming is the view's: the item isn't edited.
    assert client.sets == 0


def test_crop(window):
    from PyQt6.QtCore import Qt
    w, client = window()
    key(w, Qt.Key.Key_C)
    assert w.view.cropping
    w.view.start, w.view.end = (2, 0), (0, 1)
    key(w, Qt.Key.Key_Return)
    assert client.edits == [imaging.Edit("crop", x=0, y=0, width=2, height=1)]
    assert not w.notice.isVisible()
    assert rows(w.sync.data) == [[RED, GREEN]]
    assert not w.view.cropping

    # Esc cancels.
    key(w, Qt.Key.Key_C)
    key(w, Qt.Key.Key_Escape)
    assert not w.view.cropping
    assert len(client.edits) == 1


def test_info_panel_edits_fields(window):
    from PyQt6.QtCore import Qt
    w, client = window(encode(Image.new("RGB", (40, 20)), "JPEG", exif=located().tobytes()), "public.jpeg")
    key(w, Qt.Key.Key_I)
    assert w.info.isVisible()
    assert set(w.info.fields) == set(lens.FIELD_NAMES)
    assert w.info.location.isVisible()

    title = w.info.fields[wire.Field.TITLE]
    title.setText("Brisbane")
    title.editingFinished.emit()
    assert client.edits == [imaging.Edit("set_field", field="title", value="Brisbane")]
    assert w.info.fields[wire.Field.TITLE].text() == "Brisbane"

    w.info.location.click()
    assert client.edits[-1] == imaging.Edit("remove_location")
    assert not w.info.location.isVisible()

    # A bad date is refused.
    date = w.info.fields[wire.Field.DATE_TAKEN]
    date.setText("soon")
    date.editingFinished.emit()
    assert w.notice.isVisible() and w.notice.text() == "That edit doesn't fit the image"
    assert w.notice_timer.interval() == lens.MESSAGE_SHOWN
    assert len(client.edits) == 2


def test_empty(window):
    w, _ = window(b"")
    assert w.empty.isVisible() and not w.scroll_area.isVisible()
    w.rotate(1)
    assert "Import File" in w.notice.text()


def test_lens_use(qt_app, monkeypatch):
    client = FakeImage(encode(pixels(), "PNG"))
    monkeypatch.setattr(lens.wire.Image, "Client", lambda port, timeout: client)
    stopped = []

    class Loop:
        def stop(self):
            stopped.append(True)

    image_lens = lens.ImageLens(Loop())  # pyright: ignore[reportArgumentType]
    image_lens.name_of = lambda item_id: "A name"
    assert image_lens.use(UseRequest(item_id=ITEM_ID, item=5), 0).status == Status.OK
    assert image_lens.use(UseRequest(item_id=ITEM_ID, item=6), 0).status == Status.BUSY
    # The item's told it's viewed: its type records it, and its edits.
    assert client.views == [("image-lens", "")]
    assert image_lens.window is not None
    image_lens.window.close()
    assert client.closed and stopped == [True]


def test_scale_dialog_keeps_aspect(qt_app):
    from PyQt6.QtWidgets import QWidget
    parent = QWidget()
    dialog = lens.ScaleDialog(parent, 400, 200)
    dialog.width_box.setValue(100)
    assert dialog.size() == (100, 50)
    dialog.keep.setChecked(False)
    dialog.height_box.setValue(80)
    assert dialog.size() == (100, 80)


def test_scale(window, monkeypatch):
    w, client = window(encode(Image.new("RGB", (40, 20)), "PNG"))
    monkeypatch.setattr(lens.ScaleDialog, "exec", lambda self: (self.width_box.setValue(10),
                                                                 lens.QDialog.DialogCode.Accepted)[1])
    w.scale()
    assert client.edits == [imaging.Edit("scale", width=10, height=5)]
    assert (w.sync.width, w.sync.height) == (10, 5)
    assert Image.open(io.BytesIO(w.sync.data)).size == (10, 5)
