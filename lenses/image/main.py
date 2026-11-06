# darqos
# Copyright (C) 2026 David Arnold

# Image lens: a window for using an image item (public.image, and the
# types that conform to it), in the Terminal context.
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  It shows the
# image at its native pixel size, one image pixel to a device pixel,
# and zooms the view, never the image.  Edits (crop, scale, quarter
# turns, flips, and metadata fields) are sent to the item as they're
# made, as its whole list of edits, so undo is a shorter list: the item
# keeps its original (see lib/didl/image.didl).
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to the item and History are
# synchronous, on queued reply ports.  Images are decoded with Pillow,
# which reads every image type's format, HEIC included.

import io
import logging
import typing

from PIL import Image, ImageOps

import pillow_heif

from PyQt6 import QtCore
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.history
from darq.loop import QtEventLoop
from darq.protocols import image as wire
from darq.protocols.history import History
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.ui import style
from darq.ui.lens import MESSAGE_SHOWN, overlay, show_over
from darq.ui.style import CONTROLS

pillow_heif.register_heif_opener()


# History source for the events it records.
SOURCE = "image-lens"

# Seconds to wait for the item's replies: rendering a large photo, as
# HEIC especially, can take a few.
TIMEOUT = 30.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Milliseconds a new zoom is shown over the image (and a message, for
# MESSAGE_SHOWN).
ZOOM_SHOWN = 1500

# The usual controls, with the image on the background, and its panels'
# table.
STYLE = CONTROLS + f"""
ImageWindow, InfoPanel {{ background-color: {style.SURFACE}; }}
QScrollArea {{ background-color: {style.BACKGROUND}; border: none; }}
QTableWidget {{ color: {style.TEXT}; background-color: {style.SURFACE}; gridline-color: {style.FIELD}; border: none;
               selection-background-color: {style.SELECTION}; selection-color: {style.TEXT_STRONG}; }}
QHeaderView::section {{ color: {style.TEXT_SOFT}; background-color: {style.RAISED}; border: none; padding: 4px; }}
QTableCornerButton::section {{ background-color: {style.RAISED}; border: none; }}
"""

# Zoom factors, in device pixels per image pixel.
ZOOMS = [1 / 16, 1 / 12, 1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32]

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

# The editable metadata fields, in the order they're shown.
FIELD_NAMES = {
    wire.Field.TITLE: "Title",
    wire.Field.ARTIST: "Artist",
    wire.Field.COPYRIGHT: "Copyright",
    wire.Field.DATE_TAKEN: "Date taken",
    wire.Field.COMMENT: "Comment",
}

HELP = """\
<b>Image</b> (drag it to move the window)<br>
+ / −&nbsp;&nbsp;Zoom in, out (the view, not the image)<br>
0&nbsp;&nbsp;Actual pixels &nbsp;&nbsp; F&nbsp;&nbsp;Fit the window<br>
[ / ]&nbsp;&nbsp;Turn left, right &nbsp;&nbsp; H / V&nbsp;&nbsp;Flip left-right, top-bottom<br>
C&nbsp;&nbsp;Crop: drag a rectangle, then Enter, or Esc<br>
S&nbsp;&nbsp;Scale...<br>
I&nbsp;&nbsp;Information: metadata, and its fields<br>
Ctrl+Z / Ctrl+Shift+Z&nbsp;&nbsp;Undo, redo<br>
F1&nbsp;&nbsp;This help &nbsp;&nbsp; F11&nbsp;&nbsp;Full screen"""


def decode(data: bytes) -> QImage:
    """Return an image's first frame, as it's seen (its EXIF orientation
    made), for showing.

    :raises ValueError: if it can't be decoded."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGBA")
    except Exception as e:
        raise ValueError(f"can't decode the image: {e}") from None
    raw = image.tobytes("raw", "RGBA")
    return QImage(raw, image.width, image.height, 4 * image.width, QImage.Format.Format_RGBA8888).copy()


def to_image(x: float, y: float, zoom: float, ratio: float, width: int, height: int) -> tuple[int, int]:
    """Return the image pixel under a point in the view, clamped to the
    image: the view's points are logical, ratio device pixels each."""
    return (max(0, min(width, round(x * ratio / zoom))), max(0, min(height, round(y * ratio / zoom))))


def selection(start: tuple[int, int], end: tuple[int, int]) -> tuple[int, int, int, int]:
    """Return the rectangle two corners make: x, y, width, height."""
    x0, x1 = sorted((start[0], end[0]))
    y0, y1 = sorted((start[1], end[1]))
    return x0, y0, x1 - x0, y1 - y0


def zoom_text(zoom: float) -> str:
    """Return a zoom as a percentage."""
    return f"{zoom * 100:.4g}%"


def next_zoom(zoom: float, step: int) -> float:
    """Return the zoom a step in or out (step 1 or -1) from another."""
    if step > 0:
        return next((z for z in ZOOMS if z > zoom * 1.001), ZOOMS[-1])
    return next((z for z in reversed(ZOOMS) if z < zoom / 1.001), ZOOMS[0])


class ImageSync:
    """Keeps the lens in step with the item: its image, edits, and
    version, and the undo and redo stacks of edit lists.  It uses no Qt,
    so it can be tested without a display."""

    # Results of edit(), undo(), and the like.
    OK = "ok"
    CONFLICT = "conflict"
    REFUSED = "refused"
    FAILED = "failed"

    def __init__(self, client: wire.Image.Client, item_id: str, history: History.Client | None = None):
        self.client = client
        self.item_id = item_id
        self.history = history

        self.data = b""
        self.width = self.height = self.frames = 0
        self.edits: list[wire.Edit] = []
        self.version = 0
        self.status = wire.Status.OK

        # Edit lists to go back and forward to.
        self.undo_stack: list[list[wire.Edit]] = []
        self.redo_stack: list[list[wire.Edit]] = []

        # Why the last change wasn't made, if it wasn't.
        self.reason = ""

        # Whether this use has edited it yet: edited is recorded once.
        self.edited = False

        # False once the item has stopped answering.
        self.available = True

    def reload(self) -> bool:
        """Get the image again.

        :returns: False if the item doesn't answer."""
        try:
            response = self.client.get()
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't get {self.item_id}: {e!r}")
            self.available = False
            return False
        self.data, self.width, self.height, self.frames = \
            response.data, response.width, response.height, response.frames
        self.edits, self.version, self.status = list(response.edits), response.version, response.status
        return True

    @property
    def empty(self) -> bool:
        return self.status == wire.Status.EMPTY

    def editable(self) -> bool:
        """Return whether it can be edited, setting the reason if not."""
        if self.empty:
            self.reason = "There's no image to edit: use Import File... in New"
            return False
        if self.frames > 1:
            self.reason = (f"This image is animated ({self.frames} frames): editing animations isn't "
                           "supported yet")
            return False
        return True

    def set_edits(self, edits: list[wire.Edit]) -> str:
        """Make the item's edits these, and get the image they make."""
        try:
            response = self.client.set_edits(edits=edits, expected=self.version)
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't edit {self.item_id}: {e!r}")
            self.available = False
            self.reason = f"Can't edit the image: {e}"
            return self.FAILED

        if response.status == wire.Status.CONFLICT:
            logging.info(f"Edit to {self.item_id} refused: it's changed: reloading")
            self.reason = "The image was changed elsewhere: it's been reloaded"
            self.undo_stack.clear()
            self.redo_stack.clear()
            return self.CONFLICT if self.reload() else self.FAILED
        if response.status != wire.Status.OK:
            self.reason = {
                wire.Status.INVALID_EDIT: "That edit doesn't fit the image",
                wire.Status.ANIMATED: "Animated images can't be edited yet",
                wire.Status.EMPTY: "There's no image to edit",
            }.get(response.status, f"Can't edit the image: {response.status.name}")
            return self.REFUSED

        if not self.edited:
            self.edited = True
            self.record(darq.history.EDITED)
        return self.OK if self.reload() else self.FAILED

    def edit(self, edit: wire.Edit) -> str:
        """Make another edit."""
        if not self.editable():
            return self.REFUSED
        before = list(self.edits)
        result = self.set_edits(before + [edit])
        if result == self.OK:
            self.undo_stack.append(before)
            self.redo_stack.clear()
        return result

    def undo(self) -> str:
        if not self.undo_stack:
            self.reason = "Nothing to undo"
            return self.REFUSED
        current = list(self.edits)
        result = self.set_edits(self.undo_stack[-1])
        if result == self.OK:
            self.undo_stack.pop()
            self.redo_stack.append(current)
        return result

    def redo(self) -> str:
        if not self.redo_stack:
            self.reason = "Nothing to redo"
            return self.REFUSED
        current = list(self.edits)
        result = self.set_edits(self.redo_stack[-1])
        if result == self.OK:
            self.redo_stack.pop()
            self.undo_stack.append(current)
        return result

    def revert(self) -> str:
        """Go back to the original: undo can bring the edits back."""
        if not self.edits:
            self.reason = "It's the original already"
            return self.REFUSED
        current = list(self.edits)
        result = self.set_edits([])
        if result == self.OK:
            self.undo_stack.append(current)
            self.redo_stack.clear()
        return result

    def metadata(self) -> list[wire.Entry]:
        """Return the image's metadata, or none if it can't be read."""
        try:
            response = self.client.get_metadata()
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't get {self.item_id}'s metadata: {e!r}")
            return []
        return response.entries if response.status == wire.Status.OK else []

    def record(self, event: str):
        """Record a History event about the item, if History is running."""
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=self.item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {self.item_id}: {e!r}")

    def close(self):
        """Close the item, once finished with it."""
        if not self.available:
            return
        self.available = False
        try:
            self.client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close {self.item_id}: {e!r}")


class ImageView(QWidget):
    """The image, drawn at a zoom, with a crop selection, if one's being
    made."""

    def __init__(self, window: "ImageWindow"):
        super().__init__()
        self.window_ = window
        self.image = QImage()
        self.zoom = 1.0

        # While cropping: the selection's corners, in image pixels.
        self.cropping = False
        self.start: tuple[int, int] | None = None
        self.end: tuple[int, int] | None = None

    def ratio(self) -> float:
        return self.devicePixelRatioF() or 1.0

    def set_image(self, image: QImage):
        self.image = image
        self.cancel_crop()
        self.resize_to_zoom()

    def set_zoom(self, zoom: float):
        self.zoom = max(ZOOMS[0], min(ZOOMS[-1], zoom))
        self.resize_to_zoom()

    def resize_to_zoom(self):
        ratio = self.ratio()
        self.resize(max(1, round(self.image.width() * self.zoom / ratio)),
                    max(1, round(self.image.height() * self.zoom / ratio)))
        self.update()

    def pixel(self, point: QtCore.QPointF) -> tuple[int, int]:
        return to_image(point.x(), point.y(), self.zoom, self.ratio(), self.image.width(), self.image.height())

    def view_rect(self, x: int, y: int, width: int, height: int) -> QtCore.QRectF:
        scale = self.zoom / self.ratio()
        return QtCore.QRectF(x * scale, y * scale, width * scale, height * scale)

    def start_crop(self):
        self.cropping = True
        self.start = self.end = None
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.update()

    def cancel_crop(self):
        self.cropping = False
        self.start = self.end = None
        self.unsetCursor()
        self.update()

    def crop_rect(self) -> tuple[int, int, int, int] | None:
        """Return the selection, if one's been made."""
        if self.start is None or self.end is None:
            return None
        rect = selection(self.start, self.end)
        return rect if rect[2] > 0 and rect[3] > 0 else None

    def paintEvent(self, a0):
        painter = QPainter(self)
        if not self.image.isNull():
            # Smoothed when it's smaller; pixels kept square when larger.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1)
            painter.drawImage(self.view_rect(0, 0, self.image.width(), self.image.height()), self.image)
        rect = self.crop_rect()
        if self.cropping and rect is not None:
            shade = QColor(0, 0, 0, 128)
            whole = QtCore.QRectF(self.rect())
            inside = self.view_rect(*rect)
            for part in (QtCore.QRectF(whole.left(), whole.top(), whole.width(), inside.top()),
                         QtCore.QRectF(whole.left(), inside.bottom(), whole.width(), whole.bottom() - inside.bottom()),
                         QtCore.QRectF(whole.left(), inside.top(), inside.left(), inside.height()),
                         QtCore.QRectF(inside.right(), inside.top(), whole.right() - inside.right(), inside.height())):
                painter.fillRect(part, shade)
            painter.setPen(QPen(QColor(style.ACCENT), 1, QtCore.Qt.PenStyle.DashLine))
            painter.drawRect(inside)
        painter.end()

    def mousePressEvent(self, a0: QMouseEvent | None):
        if a0 is None or a0.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if self.cropping:
            self.start = self.end = self.pixel(a0.position())
            self.update()
            return
        # Otherwise, dragging the image moves the window, as the other
        # lenses' margins do: the compositor moves it.
        handle = self.window_.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def mouseMoveEvent(self, a0: QMouseEvent | None):
        if a0 is not None and self.cropping and self.start is not None:
            self.end = self.pixel(a0.position())
            rect = self.crop_rect()
            if rect is not None:
                self.window_.show_status(f"Crop {rect[2]} × {rect[3]} at {rect[0]}, {rect[1]}: "
                                         "Enter crops, Esc cancels")
            self.update()

    def wheelEvent(self, a0: QWheelEvent | None):
        if a0 is not None and a0.modifiers() & Modifier.ControlModifier:
            self.window_.zoom_step(1 if a0.angleDelta().y() > 0 else -1)
            a0.accept()
            return
        super().wheelEvent(a0)


class ScaleDialog(QDialog):
    """Asks for a new size, keeping the aspect ratio unless it's told
    not to."""

    def __init__(self, parent: QWidget, width: int, height: int):
        super().__init__(parent)
        self.setWindowTitle("Scale")
        self.aspect = width / height
        self.width_box = QSpinBox()
        self.height_box = QSpinBox()
        for box, value in ((self.width_box, width), (self.height_box, height)):
            box.setRange(1, 32768)
            box.setValue(value)
            box.setSuffix(" px")
        self.keep = QCheckBox("Keep the aspect ratio")
        self.keep.setChecked(True)
        self.width_box.valueChanged.connect(lambda v: self.follow(self.height_box, v / self.aspect))
        self.height_box.valueChanged.connect(lambda v: self.follow(self.width_box, v * self.aspect))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Width", self.width_box)
        form.addRow("Height", self.height_box)
        form.addRow(self.keep)
        form.addRow(buttons)
        self.setLayout(form)

    def follow(self, other: QSpinBox, value: float):
        """Keep the other side in proportion, without it following back."""
        if self.keep.isChecked():
            other.blockSignals(True)
            other.setValue(max(1, round(value)))
            other.blockSignals(False)

    def size(self) -> tuple[int, int]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.width_box.value(), self.height_box.value()


class InfoPanel(QWidget):
    """The image's metadata: its editable fields, and all its entries."""

    def __init__(self, window: "ImageWindow"):
        super().__init__()
        self.window_ = window
        self.fields: dict[wire.Field, QLineEdit] = {}
        self.values: dict[wire.Field, str] = {}

        self.zoom = QLabel()
        self.edits = QLabel()
        self.form = QFormLayout()
        self.location = QPushButton("Remove Location")
        self.location.clicked.connect(window.remove_location)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Group", "Name", "Value"))
        self.table.verticalHeader().setVisible(False)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.horizontalHeader().setStretchLastSection(True)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.table.setFrameShape(QTableWidget.Shape.NoFrame)

        view = QFormLayout()
        view.addRow("Zoom", self.zoom)
        view.addRow("Edits", self.edits)
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 0)
        layout.addLayout(view)
        layout.addLayout(self.form)
        layout.addWidget(self.location)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setMinimumWidth(360)

        # A QWidget subclass draws its style sheet's background only if
        # it's told to.
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)

    def show_entries(self, entries: list[wire.Entry]):
        """Show the metadata: the fields the image's format has, editable,
        and every entry, as text."""
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields.clear()
        self.values = {e.field: e.value for e in entries if e.field != wire.Field.NONE}
        for field, name in FIELD_NAMES.items():
            if field in self.values:
                edit = QLineEdit(self.values[field])
                edit.editingFinished.connect(lambda field=field: self.field_edited(field))
                if field == wire.Field.DATE_TAKEN:
                    edit.setPlaceholderText("2024-06-30 14:05")
                self.fields[field] = edit
                self.form.addRow(name, edit)
        self.location.setVisible(any(e.group == "GPS" for e in entries))

        shown = [e for e in entries if e.field == wire.Field.NONE]
        self.table.setRowCount(len(shown))
        for row, e in enumerate(shown):
            for column, text in enumerate((e.group, e.name, e.value)):
                # Plain text, never rich: metadata comes from outside.
                cell = QTableWidgetItem(text if len(text) <= 300 else text[:300] + "...")
                cell.setToolTip(text[:2000])
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()

    def field_edited(self, field: wire.Field):
        edit = self.fields.get(field)
        if edit is None:
            return
        value = edit.text().strip()
        if value != self.values.get(field, ""):
            self.values[field] = value
            self.window_.set_field(field, value)


class ImageWindow(QWidget):
    """The window: the image in a scroll area, with the information
    panel beside it, and messages shown over the image."""

    def __init__(self, sync: ImageSync, on_closed: typing.Callable[[], None] | None = None):
        super().__init__()
        self.sync = sync
        self.on_closed = on_closed
        self.setWindowTitle(sync.item_id)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet(STYLE)

        self.view = ImageView(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.view)
        self.scroll_area.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.empty = QLabel("No image: use Import File... in New")
        self.empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.info = InfoPanel(self)
        self.info.hide()
        self.help = QLabel(HELP, self)
        self.help.setStyleSheet(style.HELP)
        self.help.hide()

        # A new zoom, in its middle, and messages, at its foot, shown over
        # the image for a moment: there's no status line.
        self.zoom_label, self.zoom_timer = overlay(self, 28)
        self.notice, self.notice_timer = overlay(self, 16)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.scroll_area, 1)
        body.addWidget(self.empty, 1)
        body.addWidget(self.info)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(body, 1)
        self.setLayout(layout)
        self.resize(1024, 768)
        self.show_image()

    # Showing

    def show_over(self, label: QLabel, timer: QtCore.QTimer, text: str, shown: int, middle: bool):
        """Show text over the image, in its middle or at its foot, for a
        while (in milliseconds)."""
        area = self.scroll_area.geometry() if self.scroll_area.isVisible() else self.empty.geometry()
        show_over(label, timer, text, area, shown, middle)

    def show_status(self, message: str):
        """Show a message over the image for a moment, or hide the one
        shown, if it's empty."""
        if not message:
            self.notice.hide()
            self.notice_timer.stop()
            return
        self.show_over(self.notice, self.notice_timer, message, MESSAGE_SHOWN, middle=False)

    def show_image(self):
        """Show the image the item has now, at the same zoom."""
        sync = self.sync
        self.scroll_area.setVisible(not sync.empty)
        self.empty.setVisible(sync.empty)
        if sync.empty:
            self.show_status("")
            return
        try:
            image = decode(sync.data)
        except ValueError as e:
            self.show_status(str(e))
            return
        self.view.set_image(image)
        self.describe()
        if self.info.isVisible():
            self.info.show_entries(sync.metadata())

    def describe(self):
        """Show the zoom, and the number of edits, in the information
        panel: its size and frames are among its entries."""
        self.info.zoom.setText(zoom_text(self.view.zoom))
        self.info.edits.setText(str(len(self.sync.edits)) if self.sync.edits else "None")

    def set_zoom(self, zoom: float):
        """Zoom the view, and show the new zoom over it for a moment."""
        self.view.set_zoom(zoom)
        self.describe()
        self.show_over(self.zoom_label, self.zoom_timer, zoom_text(self.view.zoom), ZOOM_SHOWN, middle=True)

    def zoom_step(self, step: int):
        self.set_zoom(next_zoom(self.view.zoom, step))

    def actual_size(self):
        self.set_zoom(1)

    def fit(self):
        viewport = self.scroll_area.viewport()
        if viewport is None or self.view.image.isNull():
            return
        ratio = self.view.ratio()
        self.set_zoom(min(viewport.width() * ratio / self.view.image.width(),
                          viewport.height() * ratio / self.view.image.height()))

    # Editing

    def result(self, result: str):
        """Show how a change went."""
        if result == ImageSync.OK:
            self.show_status("")
            self.show_image()
        elif result == ImageSync.CONFLICT:
            self.show_image()
            self.show_status(self.sync.reason)
        else:
            self.show_status(self.sync.reason)

    def edit(self, edit: wire.Edit):
        self.view.cancel_crop()
        self.result(self.sync.edit(edit))

    def rotate(self, turns: int):
        self.edit(wire.Edit(wire.EditType.ROTATE, turns=turns))

    def flip(self, vertical: bool):
        self.edit(wire.Edit(wire.EditType.FLIP, vertical=vertical))

    def start_crop(self):
        if not self.sync.editable():
            self.show_status(self.sync.reason)
            return
        self.view.start_crop()
        self.show_status("Crop: drag a rectangle, then Enter, or Esc to cancel")

    def finish_crop(self):
        rect = self.view.crop_rect()
        if rect is None:
            self.show_status("Drag a rectangle to crop to, or Esc to cancel")
            return
        x, y, width, height = rect
        self.edit(wire.Edit(wire.EditType.CROP, x=x, y=y, width=width, height=height))

    def cancel_crop(self):
        self.view.cancel_crop()
        self.show_status("")

    def scale(self):
        if not self.sync.editable():
            self.show_status(self.sync.reason)
            return
        dialog = ScaleDialog(self, self.sync.width, self.sync.height)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            width, height = dialog.size()
            if (width, height) != (self.sync.width, self.sync.height):
                self.edit(wire.Edit(wire.EditType.SCALE, width=width, height=height))

    def set_field(self, field: wire.Field, value: str):
        self.edit(wire.Edit(wire.EditType.SET_FIELD, field=field, value=value))

    def remove_location(self):
        self.edit(wire.Edit(wire.EditType.REMOVE_LOCATION))

    def undo(self):
        self.result(self.sync.undo())

    def redo(self):
        self.result(self.sync.redo())

    def revert(self):
        self.result(self.sync.revert())

    def toggle_info(self):
        if self.info.isVisible():
            self.info.hide()
        else:
            self.info.show_entries(self.sync.metadata())
            self.info.show()

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
        try:
            key = Key(a0.key())
        except ValueError:
            super().keyPressEvent(a0)
            return
        modifiers = a0.modifiers()
        ctrl = bool(modifiers & Modifier.ControlModifier)
        shift = bool(modifiers & Modifier.ShiftModifier)
        if ctrl and key == Key.Key_Z:
            if shift:
                self.redo()
            else:
                self.undo()
            return
        if ctrl:
            super().keyPressEvent(a0)
            return

        if self.view.cropping and key in (Key.Key_Return, Key.Key_Enter):
            self.finish_crop()
            return
        actions: dict[Key, typing.Callable[[], None]] = {
            Key.Key_Plus: lambda: self.zoom_step(1),
            Key.Key_Equal: lambda: self.zoom_step(1),
            Key.Key_Minus: lambda: self.zoom_step(-1),
            Key.Key_0: self.actual_size,
            Key.Key_F: self.fit,
            Key.Key_BracketLeft: lambda: self.rotate(-1),
            Key.Key_BracketRight: lambda: self.rotate(1),
            Key.Key_H: lambda: self.flip(False),
            Key.Key_V: lambda: self.flip(True),
            Key.Key_C: self.start_crop,
            Key.Key_S: self.scale,
            Key.Key_I: self.toggle_info,
            Key.Key_Escape: self.cancel_crop if self.view.cropping else (lambda: self.help.hide()),
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
        menu.addAction("Actual Pixels", self.actual_size)
        menu.addAction("Fit", self.fit)
        menu.addSeparator()
        menu.addAction("Turn Left", lambda: self.rotate(-1))
        menu.addAction("Turn Right", lambda: self.rotate(1))
        menu.addAction("Flip Left-Right", lambda: self.flip(False))
        menu.addAction("Flip Top-Bottom", lambda: self.flip(True))
        menu.addAction("Crop", self.start_crop)
        menu.addAction("Scale...", self.scale)
        menu.addSeparator()
        menu.addAction("Undo", self.undo)
        menu.addAction("Redo", self.redo)
        menu.addAction("Revert to Original", self.revert)
        menu.addSeparator()
        menu.addAction("Information", self.toggle_info)
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        menu.exec(a0.globalPos())

    def closeEvent(self, a0):
        self.sync.close()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, None
            on_closed()


class ImageLens(Lens.Handler):
    """Answers the Lens protocol: uses one item, in a window."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None):
        self.loop = loop
        self.history = history
        self.window: ImageWindow | None = None

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        sync = ImageSync(wire.Image.Client(request.item, timeout=TIMEOUT), request.item_id, self.history)
        if not sync.reload():
            return UseResponse(status=Status.FAILED)
        self.window = ImageWindow(sync, on_closed=self.finished)
        self.window.show()
        sync.record(darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

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

    lens = ImageLens(loop, history)
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
