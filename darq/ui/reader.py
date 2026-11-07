# darqos
# Copyright (C) 2026 David Arnold

"""What the reading lenses share, so that a book reads the same in each
(see doc/book.rst): the PDF lens's reading view, and the e-book lens.

It's their keys, their modes, and what they say.  They differ in what
turns a page, and in what + and − do."""

import typing

from PyQt6 import QtCore


# Control exports
__all__ = [
    "CATALOGUE",
    "CONTENTS",
    "FIND",
    "FIRST",
    "FULL_SCREEN",
    "HELP",
    "LARGER",
    "LAST",
    "MODES",
    "MODE",
    "NEXT",
    "PREVIOUS",
    "SMALLER",
    "SHOW_HELP",
    "action_for",
    "next_mode",
]


# What a key asks for.
NEXT = "next"
PREVIOUS = "previous"
FIRST = "first"
LAST = "last"
LARGER = "larger"
SMALLER = "smaller"
CONTENTS = "contents"
FIND = "find"
MODE = "mode"
CATALOGUE = "catalogue"
FULL_SCREEN = "full screen"
SHOW_HELP = "help"

Key = QtCore.Qt.Key

KEYS = {
    Key.Key_Space: NEXT, Key.Key_PageDown: NEXT,
    Key.Key_Backspace: PREVIOUS, Key.Key_PageUp: PREVIOUS,
    Key.Key_Home: FIRST, Key.Key_End: LAST,
    Key.Key_Plus: LARGER, Key.Key_Equal: LARGER, Key.Key_Minus: SMALLER,
    Key.Key_O: CONTENTS, Key.Key_M: MODE, Key.Key_B: CATALOGUE,
    Key.Key_F1: SHOW_HELP, Key.Key_F11: FULL_SCREEN,
}

CONTROL_KEYS = {
    Key.Key_F: NEXT,
    Key.Key_B: PREVIOUS,
    Key.Key_S: FIND,
}

HELP = """Space, Page Down, C-f   Next page
Backspace, Page Up, C-b Previous page
Home, End               Front, back of the book
+, −                    Larger, smaller
O                       Contents
C-s                     Find
M                       Dark, sepia, light
B                       Catalogue it
F1                      Help
F11                     Full screen
S-x                     Close"""

# Modes: their name, background, text and link colours.
MODES = [
    ("dark", "#1f1f1f", "#d8d8d8", "#8ab4f8"),
    ("sepia", "#f4ecd8", "#433422", "#7b4f12"),
    ("light", "#ffffff", "#111111", "#1a0dab"),
]


def action_for(key: int, control: bool) -> str | None:
    """Return what a key asks for, or None."""
    table: typing.Mapping[int, str] = CONTROL_KEYS if control else KEYS
    return table.get(key)


def next_mode(name: str) -> str:
    """Return the mode after one."""
    names = [mode[0] for mode in MODES]
    return names[(names.index(name) + 1) % len(names)] if name in names else names[0]
