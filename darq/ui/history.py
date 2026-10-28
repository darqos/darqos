# darqos
# Copyright (C) 2026 David Arnold

# History's event browser: events from the History service, between two
# times, and filtered by type, event and source, newest first.  Choosing
# one (double-click, or Enter) uses its item.

import datetime
import re
import typing

from PyQt6 import QtCore
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import darq
from darq.protocols.history import Event
from darq.protocols.history import Status as HistoryStatus

from . import Host


# Control exports
__all__ = [
    "HistoryView",
    "PAGE",
    "format_time",
    "parse_names",
    "to_micros",
]


# Events fetched at a time.
PAGE = 200


def parse_names(text: str) -> list[str]:
    """Split a list of names, separated by commas or spaces."""
    return [name for name in re.split(r"[,\s]+", text) if name]


def to_micros(when: QtCore.QDateTime) -> int:
    """Return a time in UTC microseconds since the epoch."""
    return when.toMSecsSinceEpoch() * 1000


def format_time(micros: int) -> str:
    """Return a time, in UTC microseconds, as local time."""
    when = datetime.datetime.fromtimestamp(micros / 1_000_000).astimezone()
    return when.strftime("%Y-%m-%d %H:%M:%S")


class HistoryView(QWidget):
    """Events from History, filtered: choose one to use its item."""

    def __init__(self, host: Host):
        super().__init__()
        self.host = host

        now = QtCore.QDateTime.currentDateTime()
        self.start = QDateTimeEdit(now.addSecs(-3600))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.end = QDateTimeEdit(now)
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.until_now = QCheckBox("Now")
        self.until_now.setChecked(True)
        self.until_now.toggled.connect(lambda checked: self.end.setEnabled(not checked))
        self.end.setEnabled(False)

        shortcuts = QHBoxLayout()
        for label, seconds in (("Last hour", 3600), ("Today", None), ("All", 0)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, s=seconds: self.set_period(s))
            shortcuts.addWidget(button)
        shortcuts.addStretch()

        end = QHBoxLayout()
        end.addWidget(self.end)
        end.addWidget(self.until_now)

        self.types = QListWidget()
        self.types.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.types.setMaximumHeight(100)
        self.events = QLineEdit()
        self.events.setPlaceholderText("Any: created, opened, edited, destroyed, ...")
        self.sources = QLineEdit()
        self.sources.setPlaceholderText("Any: text-lens, terminal, ...")

        form = QFormLayout()
        form.addRow("From", self.start)
        form.addRow("", shortcuts)
        form.addRow("Until", end)
        form.addRow("Types", self.types)
        form.addRow("Events", self.events)
        form.addRow("Sources", self.sources)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Time", "Type", "Item", "Event", "Source", "Count"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.activated.connect(lambda index: self.use(index.row()))

        search = QPushButton("Search")
        search.clicked.connect(self.search)
        self.more = QPushButton("More")
        self.more.clicked.connect(self.fetch_more)
        self.more.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.more)
        buttons.addWidget(search)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        self.setLayout(layout)

        self.shown: list[Event] = []
        self.query: dict[str, typing.Any] = {}

    def set_types(self, type_ids: list[str]):
        """Offer these types to filter by, keeping those selected."""
        selected = set(self.selected_types())
        self.types.clear()
        for type_id in type_ids:
            item = QListWidgetItem(type_id)
            self.types.addItem(item)
            item.setSelected(type_id in selected)

    def selected_types(self) -> list[str]:
        return [item.text() for item in self.types.selectedItems()]

    def set_period(self, seconds: int | None):
        """From some seconds ago (None for midnight, 0 for ever), until now."""
        now = QtCore.QDateTime.currentDateTime()
        if seconds is None:
            start = QtCore.QDateTime(now.date(), QtCore.QTime(0, 0))
        elif seconds == 0:
            start = QtCore.QDateTime.fromMSecsSinceEpoch(0)
        else:
            start = now.addSecs(-seconds)
        self.start.setDateTime(start)
        self.until_now.setChecked(True)
        self.search()

    def filters(self) -> dict[str, typing.Any]:
        """The GetEvents arguments for the form, but max and paging."""
        return {
            "start": to_micros(self.start.dateTime()),
            "end": 0 if self.until_now.isChecked() else to_micros(self.end.dateTime()),
            "type_ids": self.selected_types(),
            "events": parse_names(self.events.text()),
            "sources": parse_names(self.sources.text()),
        }

    def search(self):
        self.query = self.filters()
        self.shown = []
        self.table.setRowCount(0)
        self.fetch(self.query["end"])

    def fetch_more(self):
        if self.shown:
            self.fetch(self.shown[-1].time)

    def fetch(self, end: int):
        """Fetch a page of events, before end (0 for now), and show them."""
        try:
            response = self.host.services.history.get_events(**{**self.query, "end": end}, max=PAGE)
        except (darq.DarqError, TimeoutError) as e:
            self.host.show_status(f"Can't get events: {e}")
            return
        if response.status != HistoryStatus.OK:
            self.host.show_status("Invalid filter: names have no spaces, and are at most 31 bytes")
            return

        row = len(self.shown)
        self.shown.extend(response.events)
        self.table.setRowCount(len(self.shown))
        for n, event in enumerate(response.events, row):
            type_id, _, specifier = event.item_id.partition(":")
            for column, text in enumerate((format_time(event.time), type_id, specifier, event.event,
                                           event.source, str(event.count))):
                self.table.setItem(n, column, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        if row == 0 and self.shown:
            self.table.selectRow(0)
        self.more.setEnabled(len(response.events) == PAGE)
        self.host.show_status(f"{len(self.shown)} events" + (", and more" if len(response.events) == PAGE else ""))

    def use(self, row: int):
        if 0 <= row < len(self.shown):
            self.host.use(self.shown[row].item_id)
