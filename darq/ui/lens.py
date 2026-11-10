# darqos
# Copyright (C) 2026 David Arnold

# What the lenses share: text shown over a window for a moment (a
# pop-over), in place of a status line; and the part of the Lens
# protocol every lens answers the same way, LensHandler, with announce,
# which makes a lens findable from its window.  Their colours and
# stylesheets are in darq.ui.style.

import logging

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
