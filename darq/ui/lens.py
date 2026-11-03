# darqos
# Copyright (C) 2026 David Arnold

# What the lenses share of their look: slim, dark scroll bars, and text
# shown over a window for a moment (a pop-over), in place of a status
# line.

from PyQt6 import QtCore
from PyQt6.QtWidgets import QLabel, QWidget


# Control exports
__all__ = [
    "MESSAGE_SHOWN",
    "SCROLL_BARS",
    "overlay",
    "show_over",
]


# Milliseconds a message is shown over a window.
MESSAGE_SHOWN = 3000

# Slim, dark scroll bars, on the lenses' #2b2b2b.
SCROLL_BARS = """
QScrollBar:vertical { background: #2b2b2b; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #2b2b2b; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #555555; min-height: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:horizontal { background: #555555; min-width: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:hover { background: #6a6a6a; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QAbstractScrollArea::corner { background: #2b2b2b; }
"""

# How what's shown over a window looks.
OVERLAY_STYLE = ("QLabel { background-color: rgba(0, 0, 0, 160); color: #e0e0e0; padding: 8px 16px;"
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
