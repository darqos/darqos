# darqos
# Copyright (C) 2026 David Arnold

# The lenses' look: their dark palette, and stylesheets for what they
# have in common.  A lens adds rules for its own widgets, after
# CONTROLS, which they can override.
#
# It uses no Qt, so anything can use the colours without a display:
# darq.ui.lens has the widgets.

# Control exports
__all__ = [
    "ACCENT",
    "BACKGROUND",
    "BORDER",
    "BUTTON",
    "CONFLICT",
    "CONTROLS",
    "ERROR",
    "FAINT",
    "FIELD",
    "HELP",
    "LINK",
    "RAISED",
    "SCROLL_BARS",
    "SELECTION",
    "SURFACE",
    "TEXT",
    "TEXT_DIM",
    "TEXT_DISABLED",
    "TEXT_SOFT",
    "TEXT_STRONG",
]


# Behind what a lens shows, such as an image, or a PDF's pages.
BACKGROUND = "#2b2b2b"

# Windows and panels.
SURFACE = "#323232"

# What's raised above them: menus, table headers.
RAISED = "#3a3a3a"

# Text fields, and buttons.
FIELD = "#404040"
BUTTON = "#454545"

# Borders, and buttons being pointed at.
BORDER = "#505050"

# Text, from most to least prominent.
TEXT_STRONG = "#ffffff"
TEXT = "#e0e0e0"
TEXT_SOFT = "#c0c0c0"
TEXT_DIM = "#9a9a9a"
FAINT = "#808080"
TEXT_DISABLED = "#707070"

# A chosen row or item, its highlight colour, and links.
SELECTION = "#4a5f73"
ACCENT = "#7bbbec"
LINK = "#8ab4f8"

# Something wrong, and the background of a conflict.
ERROR = "#f28b82"
CONFLICT = "#5c3c3c"

# Slim, dark scroll bars.
SCROLL_BARS = f"""
QScrollBar:vertical {{ background: {BACKGROUND}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {BACKGROUND}; height: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #555555; min-height: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #555555; min-width: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:hover {{ background: #6a6a6a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: {BACKGROUND}; }}
"""

# The usual controls, and the scroll bars.
CONTROLS = f"""
QDialog {{ background-color: {SURFACE}; }}
QLabel, QCheckBox {{ color: {TEXT}; }}
QLineEdit, QSpinBox, QComboBox, QListWidget {{ color: {TEXT}; background-color: {FIELD};
                                               border: 1px solid {BORDER}; padding: 3px; }}
QPushButton {{ color: {TEXT}; background-color: {BUTTON}; border: none; padding: 6px 12px; }}
QPushButton:hover {{ background-color: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; }}
QMenu {{ color: {TEXT}; background-color: {RAISED}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background-color: {SELECTION}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolTip {{ color: {TEXT}; background-color: {FIELD}; border: 1px solid {BORDER}; }}
""" + SCROLL_BARS

# The panel of keys that F1 shows over a lens.
HELP = (f"QLabel {{ background-color: rgba(20, 20, 20, 230); color: {TEXT};"
        " font-family: monospace; font-size: 15px; padding: 24px; }")
