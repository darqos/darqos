# darqos
# Copyright (C) 2026 David Arnold

# What the lenses share: text shown over a window for a moment (a
# pop-over), in place of a status line; and the part of the Lens
# protocol every lens answers the same way, LensHandler, with announce,
# which makes a lens findable from its window; and Viewing, which tells
# an item that the operator's looking at it, for History.  Their colours
# and stylesheets are in darq.ui.style.

import logging
import time
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QLabel, QWidget

import darq
from darq.protocols.lens import Lens, ShownRequest, ShownResponse

from .style import TEXT


# Control exports
__all__ = [
    "LensHandler",
    "MESSAGE_SHOWN",
    "VIEWED_GAP",
    "VIEWED_INTERVAL",
    "Viewing",
    "announce",
    "lens_name",
    "overlay",
    "show_over",
]


def lens_name(dpid: int) -> str:
    """Return the name a lens's port is registered as, and its windows'
    Wayland app_id."""
    return f"lens.{dpid}"


def announce(port: int) -> str:
    """Make a lens findable from its windows: register its Lens port as
    lens.<dpid>, and make that its windows' Wayland app_id, so that the
    Terminal can ask the focused window's lens what it's showing.  It's
    called once a QApplication's made, before any window is.

    :returns: The name."""
    name = lens_name(darq.getpid())
    QGuiApplication.setDesktopFileName(name)
    try:
        darq.register_name(name, port)
    except darq.DarqError as e:
        # Sys-i won't find it: it's no reason not to show the item.
        logging.warning(f"Can't register {name}: {e!r}")
    return name


class LensHandler(Lens.Handler):
    """What every lens answers alike: Shown, the item it's showing, which
    it keeps in shown_item, as it uses one, or moves to another."""

    # Whether what it shows is an item's page, as the entity lens's is.
    PAGE = False

    # The item it's showing, or empty, before it's used one.
    shown_item = ""

    def shown(self, request: ShownRequest, source: int) -> ShownResponse:
        return ShownResponse(item_id=self.shown_item, page=self.PAGE)


# Seconds between Viewed calls while a window has the focus, and the
# least between those that say where the reader's got to, as pages turn.
VIEWED_INTERVAL = 300.0
VIEWED_GAP = 30.0


class ViewedClient(typing.Protocol):
    """An item's port: every item protocol, and EntityItem, has Viewed."""

    def viewed(self, source: str = "", note: str = "") -> None: ...


class Viewing:
    """Tells the item a window shows that the operator's looking at it,
    with Viewed, which its type records in History (see doc/history.rst):
    when it's shown, again when the reader moves on, as a page turns, at
    most every VIEWED_GAP seconds, with the latest place, and every
    VIEWED_INTERVAL seconds while the window has the focus.  The type
    records the changes made through the port with the lens's name too.

    History counts each again within ten minutes together, keeping the
    latest note, so an evening with a book is a few events, with where
    the reader had got to."""

    def __init__(self, window: QWidget, source: str, clock: typing.Callable[[], float] = time.monotonic):
        """Constructor.

        :param window: Whose focus says whether it's still being looked at.
        :param source: The lens's name, History's source."""
        self.window = window
        self.source = source
        self.clock = clock
        self.client: ViewedClient | None = None
        self.note = ""

        # When Viewed was last called, and the note it was last given.
        self.last: float | None = None
        self.sent = ""

        self.focused = QtCore.QTimer(window)
        self.focused.setInterval(int(VIEWED_INTERVAL * 1000))
        self.focused.timeout.connect(self.tick)
        self.pending = QtCore.QTimer(window)
        self.pending.setSingleShot(True)
        self.pending.timeout.connect(self.send)

    def show(self, client: ViewedClient, note: str = ""):
        """An item's being shown, through its port: say so now."""
        self.client = client
        self.note = note
        self.pending.stop()
        self.send()
        self.focused.start()

    def move(self, note: str):
        """The reader's moved on, to somewhere a note describes: say so, if
        it's been a while, or else once it has."""
        if self.client is None or note == self.note:
            return
        self.note = note
        waited = self.clock() - self.last if self.last is not None else VIEWED_GAP
        if waited >= VIEWED_GAP:
            self.pending.stop()
            self.send()
        elif not self.pending.isActive():
            self.pending.start(int((VIEWED_GAP - waited) * 1000))

    def stop(self):
        """Nothing's shown any more, as the item's closed."""
        self.client = None
        self.pending.stop()
        self.focused.stop()

    def tick(self):
        if self.client is not None and self.window.isActiveWindow():
            self.send()

    def send(self):
        if self.client is None:
            return
        self.last = self.clock()
        self.sent = self.note
        try:
            self.client.viewed(source=self.source, note=self.note)
        except darq.DarqError as e:
            logging.warning(f"Can't say it's viewed: {e!r}")


# Milliseconds a message is shown over a window.
MESSAGE_SHOWN = 3000

# How what's shown over a window looks.
OVERLAY_STYLE = ("QLabel { background-color: rgba(0, 0, 0, 160); color: " + TEXT + "; padding: 8px 16px;"
                 " border-radius: 8px; font-size: %dpx; }")


def overlay(parent: QWidget, size: int) -> tuple[QLabel, QtCore.QTimer]:
    """Return a label to show over a window, with text of a size (in
    pixels), and the timer that hides it."""
    label = QLabel(parent)
    label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(OVERLAY_STYLE % size)
    label.hide()
    timer = QtCore.QTimer(parent)
    timer.setSingleShot(True)
    timer.timeout.connect(label.hide)
    return label, timer


def show_over(label: QLabel, timer: QtCore.QTimer, text: str, area: QtCore.QRect, shown: int, middle: bool):
    """Show text over an area of a window, in its middle or at its foot,
    for a while (in milliseconds)."""
    label.setText(text)
    label.setMaximumWidth(max(1, area.width() - 32))
    label.adjustSize()
    y = area.y() + ((area.height() - label.height()) // 2 if middle else area.height() - label.height() - 16)
    label.move(area.x() + (area.width() - label.width()) // 2, y)
    label.show()
    label.raise_()
    timer.start(shown)
