# darqos
# Copyright (C) 2026 David Arnold

"""The questions a lens asks, in its own style, rather than Qt's stock
dialogs, whose drop-down lists and buttons don't follow it: choosing one
of some choices, from a list; typing a line; and saying yes or no.

Each is a modal QDialog over a window, and returns what was answered, or
nothing, if it was cancelled, with Escape."""

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from . import style


# Control exports
__all__ = ["ChooseDialog", "ConfirmDialog", "TextDialog", "ask_text", "choose", "confirm"]

Key = QtCore.Qt.Key

# How they look: the usual controls, a list's choices spaced, and the
# button that Enter presses marked.
STYLE = style.CONTROLS + f"""
QListWidget {{ outline: none; }}
QListWidget::item {{ padding: 6px 4px; }}
QListWidget::item:selected {{ color: {style.TEXT_STRONG}; background-color: {style.SELECTION}; }}
QPushButton:default {{ background-color: {style.SELECTION}; }}
QLabel#question {{ color: {style.TEXT}; padding-bottom: 6px; }}
"""

# Least width, in pixels.
WIDTH = 480


class Dialog(QDialog):
    """A question, and the buttons that answer it, below what's asked."""

    def __init__(self, parent: QWidget | None, title: str, question: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(WIDTH)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 16, 16, 16)
        self.question = QLabel(question, self)
        self.question.setObjectName("question")
        self.question.setWordWrap(True)
        self.layout_.addWidget(self.question)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch()

    def finish_layout(self):
        self.layout_.addLayout(self.buttons)

    def button(self, text: str, action: typing.Callable[[], None], default: bool = False) -> QPushButton:
        made = QPushButton(text, self)
        made.clicked.connect(action)
        made.setDefault(default)
        made.setAutoDefault(default)
        self.buttons.addWidget(made)
        return made


class ChooseDialog(Dialog):
    """One of some choices, from a list: Up and Down move, and Enter or a
    double-click chooses."""

    def __init__(self, parent: QWidget | None, title: str, question: str, choices: list[str]):
        super().__init__(parent, title, question)
        self.list = QListWidget(self)
        for text in choices:
            self.list.addItem(QListWidgetItem(text))
        if choices:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda item: self.accept())
        rows = min(len(choices), 8)
        self.list.setMinimumHeight(rows * 32 + 8)
        self.layout_.addWidget(self.list)
        self.button("Cancel", self.reject)
        self.button("Choose", self.accept, default=True)
        self.finish_layout()
        self.list.setFocus()

    def chosen(self) -> int | None:
        """Return the row chosen, if there is one."""
        row = self.list.currentRow()
        return row if 0 <= row < self.list.count() else None


class TextDialog(Dialog):
    """A line to type, starting with some text, selected."""

    def __init__(self, parent: QWidget | None, title: str, question: str, text: str = ""):
        super().__init__(parent, title, question)
        self.line = QLineEdit(text, self)
        self.line.selectAll()
        self.line.returnPressed.connect(self.accept)
        self.layout_.addWidget(self.line)
        self.button("Cancel", self.reject)
        self.button("OK", self.accept, default=True)
        self.finish_layout()
        self.line.setFocus()


class ConfirmDialog(Dialog):
    """Yes or no, with Y or N, or the buttons: No is the default, as what's
    confirmed, such as a merge, can't be undone."""

    def __init__(self, parent: QWidget | None, title: str, question: str):
        super().__init__(parent, title, question)
        self.button("Yes", self.accept)
        self.no = self.button("No", self.reject, default=True)
        self.finish_layout()
        self.no.setFocus()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and a0.key() == Key.Key_Y:
            self.accept()
        elif a0 is not None and a0.key() == Key.Key_N:
            self.reject()
        else:
            super().keyPressEvent(a0)


def choose(parent: QWidget | None, title: str, question: str, choices: list[str]) -> int | None:
    """Return the index of the choice chosen, or None if none was."""
    dialog = ChooseDialog(parent, title, question, choices)
    return dialog.chosen() if dialog.exec() == QDialog.DialogCode.Accepted else None


def ask_text(parent: QWidget | None, title: str, question: str, text: str = "") -> str | None:
    """Return the line typed, stripped, or None if it was cancelled."""
    dialog = TextDialog(parent, title, question, text)
    return dialog.line.text().strip() if dialog.exec() == QDialog.DialogCode.Accepted else None


def confirm(parent: QWidget | None, title: str, question: str) -> bool:
    """Return whether the answer was yes."""
    return ConfirmDialog(parent, title, question).exec() == QDialog.DialogCode.Accepted
