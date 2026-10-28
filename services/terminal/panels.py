# darqos
# Copyright (C) 2026 David Arnold

# The Terminal's panels: overlay windows, which the compositor centres
# on the cursor's screen, and gives the focus.  One is shown at a time;
# Escape closes it, and so does losing the focus (but the switcher's,
# which closes when System is released).

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QLabel, QListWidget, QVBoxLayout, QWidget

from darq.ui import Host
from darq.ui.history import HistoryView
from darq.ui.types import TypeList

from screens import is_modifier, power_choice


Key = QtCore.Qt.Key


class Panel(QWidget):
    """A panel: a heading, its contents, and a status line."""

    TITLE = "darq:overlay"

    # Whether it closes when it loses the focus.
    CLOSES_ON_DEACTIVATE = True

    def __init__(self, heading: str, width: int, height: int):
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.resize(width, height)
        self.was_active = False

        self.heading = QLabel(heading)
        self.heading.setStyleSheet("font-size: 18px; font-weight: bold")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.contents = QVBoxLayout()
        layout = QVBoxLayout()
        layout.addWidget(self.heading)
        layout.addLayout(self.contents)
        layout.addWidget(self.status)
        self.setLayout(layout)

    def open(self):
        """Show it, refreshed, with the focus."""
        self.status.clear()
        self.refresh()
        self.was_active = False
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        """Bring its contents up to date: before it's shown."""

    def set_status(self, message: str):
        self.status.setText(message)

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and a0.key() == Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(a0)

    def changeEvent(self, a0: QtCore.QEvent | None):
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QtCore.QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self.was_active = True
            elif self.was_active and self.CLOSES_ON_DEACTIVATE and self.isVisible():
                self.hide()


class NewPanel(Panel):
    """Sys-n: create an item of an implemented type."""

    def __init__(self, host: Host):
        super().__init__("New", 800, 500)
        self.types = TypeList(host, filtered=True)
        self.contents.addWidget(self.types)

    def refresh(self):
        assert self.types.filter is not None
        self.types.filter.clear()
        self.types.refresh()
        self.types.filter.setFocus()


class HistoryPanel(Panel):
    """Sys-h: events, from History, to use their items."""

    def __init__(self, host: Host, type_ids: typing.Callable[[], list[str]]):
        super().__init__("History", 1000, 700)
        self.type_ids = type_ids
        self.view = HistoryView(host)
        self.contents.addWidget(self.view)

    def refresh(self):
        self.view.set_types(self.type_ids())
        self.view.set_period(3600)
        self.view.table.setFocus()


class SwitcherPanel(Panel):
    """Sys-Tab: the windows, most recently focused first, with the one
    that'll be chosen when System is released selected."""

    CLOSES_ON_DEACTIVATE = False

    def __init__(self):
        super().__init__("Windows", 500, 300)
        self.list = QListWidget()
        self.contents.addWidget(self.list)

    def set_windows(self, titles: list[str], index: int):
        self.list.clear()
        self.list.addItems([title or "(untitled)" for title in titles])
        self.list.setCurrentRow(index)


class PowerPanel(Panel):
    """Sys-b: s shuts down, b reboots, and any other key cancels."""

    def __init__(self, power: typing.Callable[[str], None]):
        super().__init__("Shut down or reboot?", 480, 120)
        self.power = power
        self.contents.addWidget(QLabel("s: Shut down    b: Reboot    any other key: Cancel"))

    def keyPressEvent(self, a0: QKeyEvent | None):
        choice = power_choice(a0) if a0 is not None else None
        if choice is None:
            return
        self.hide()
        if choice != "cancel":
            self.power(choice)


class NoticePanel(Panel):
    """A launch that failed after its panel had closed: any key closes it."""

    def __init__(self):
        super().__init__("Couldn't start it", 600, 140)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.contents.addWidget(self.message)

    def show_message(self, message: str):
        self.message.setText(message)
        self.open()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and not is_modifier(a0):
            self.hide()


class TbdPanel(Panel):
    """Sys-space, Sys-e and Sys-i, for now: any key closes it."""

    def __init__(self):
        super().__init__("", 400, 120)
        self.contents.addWidget(QLabel("TBD: not there yet."))

    def show_for(self, name: str):
        self.heading.setText(name)
        self.open()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and not is_modifier(a0):
            self.hide()
