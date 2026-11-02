# darqos
# Copyright (C) 2026 David Arnold

# Search: items found by the Index service (see doc/search-service.rst),
# by the words of their text, as they're typed.  The arrow keys move
# through what's found, and choosing one (Enter, or double-click) uses
# its item, with the host's Launcher.

import datetime

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import darq
from darq.protocols.index import Result
from darq.protocols.type import Status as TypeStatus

from . import Host


# Control exports
__all__ = [
    "DELAY",
    "PAGE",
    "SearchView",
    "format_updated",
]


Key = QtCore.Qt.Key

# Milliseconds after typing stops that what's typed is searched for.
DELAY = 200

# Items found at a time.
PAGE = 50


def format_updated(micros: int, now: datetime.datetime | None = None) -> str:
    """Return when an item was updated, in UTC microseconds, as local
    time: just the time, if it's today."""
    when = datetime.datetime.fromtimestamp(micros / 1_000_000).astimezone()
    now = now or datetime.datetime.now().astimezone()
    return when.strftime("%H:%M" if when.date() == now.date() else "%Y-%m-%d")


class SearchField(QLineEdit):
    """The search text: the up and down arrows, and Enter, are the
    results'."""

    def __init__(self, view: "SearchView"):
        super().__init__()
        self.view = view

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and a0.key() in (Key.Key_Up, Key.Key_Down):
            self.view.move(-1 if a0.key() == Key.Key_Up else 1)
        elif a0 is not None and a0.key() in (Key.Key_Return, Key.Key_Enter):
            self.view.use(self.view.table.currentRow())
        else:
            super().keyPressEvent(a0)


class SearchView(QWidget):
    """Search the Index: choose an item found to use it."""

    def __init__(self, host: Host):
        super().__init__()
        self.host = host

        self.field = SearchField(self)
        self.field.setPlaceholderText("Search")
        self.field.textChanged.connect(lambda text: self.timer.start())

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DELAY)
        self.timer.timeout.connect(self.search)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Type", "Found", "Updated"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)  # pyright: ignore[reportOptionalMemberAccess]
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.activated.connect(lambda index: self.use(index.row()))

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.field)
        layout.addWidget(self.table)
        self.setLayout(layout)

        self.shown: list[Result] = []

        # Types' names, by identifier, as they're needed.
        self.names: dict[str, str] = {}

    def clear(self):
        """Start again: no text, and nothing found."""
        self.field.clear()
        self.timer.stop()
        self.show_results([])

    def search(self):
        """Search for what's typed, and show what's found."""
        self.timer.stop()
        text = self.field.text()
        if not text.strip():
            self.show_results([])
            self.host.show_status("")
            return
        try:
            results = self.host.services.index.search(text, max=PAGE)
        except (darq.DarqError, TimeoutError) as e:
            self.host.show_status(f"Can't search: {e}")
            return
        self.show_results(results)
        if not results:
            self.host.show_status("Nothing found")
        else:
            self.host.show_status(f"{len(results)} found" + (", and more" if len(results) == PAGE else ""))

    def show_results(self, results: list[Result]):
        self.shown = list(results)
        self.table.setRowCount(len(self.shown))
        for row, result in enumerate(self.shown):
            type_id, _, _ = result.item_id.partition(":")
            snippet = " ".join(result.snippet.split())
            for column, text in enumerate((self.type_name(type_id), snippet, format_updated(result.updated))):
                cell = QTableWidgetItem(text)
                if column == 1:
                    cell.setToolTip(result.item_id)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(2)
        if self.shown:
            self.table.selectRow(0)

    def type_name(self, type_id: str) -> str:
        """Return a type's name, or its identifier if it hasn't one."""
        if type_id not in self.names:
            try:
                described = self.host.services.types.describe(type_id=type_id)
                ok = described.status == TypeStatus.OK
                self.names[type_id] = (described.description.name if ok else "") or type_id
            except (darq.DarqError, TimeoutError):
                return type_id
        return self.names[type_id]

    def move(self, step: int):
        """Select the result before or after the selected one."""
        if self.shown:
            row = min(max(self.table.currentRow() + step, 0), len(self.shown) - 1)
            self.table.selectRow(row)

    def use(self, row: int):
        """Use a result's item: the host's launched() is called with this
        as the context."""
        if 0 <= row < len(self.shown):
            self.host.launcher.use(self.shown[row].item_id, context=self)
