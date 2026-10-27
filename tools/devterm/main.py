# darqos
# Copyright (C) 2026 David Arnold

# Development Terminal: a throw-away stand-in for the Terminal, as an
# ordinary window on the host's desktop, for trying types and lenses
# before the compositor and the real Terminal exist.
#
# The p-kernel starts it at boot, after the services, if
# DARQ_DEV_TERMINAL is set (see kernel/pkernel), so it's a system
# process, and may shut down and reboot.  It has:
#
# - New: the implemented types, from the Type service, to create an
#   item of, and use it in its lens.  Types with no Terminal lens are
#   shown greyed out.
# - History: events from the History service, between two times, and
#   filtered by type, event and source.  Double-click one to use its
#   item.
# - System: Shutdown, Reboot, and Quit (just this window).
#
# Its calls are synchronous, on Qt's thread: starting a lens freezes the
# window for a second or so.

import datetime
import logging
import re
import typing

from PyQt6 import QtCore
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.history
import darq.launch
from darq.protocols.history import Event, History
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.type import Status, Type


# History source for the events it records.
SOURCE = "devterm"

# Seconds to wait for services' replies.
TIMEOUT = 10.0

# Milliseconds between looking for types, while there are none: the
# type sync registers them at boot.
TYPES_RETRY = 2000

# Events fetched at a time.
PAGE = 200

ItemFlag = QtCore.Qt.ItemFlag


class TypeEntry(typing.NamedTuple):
    type_id: str
    name: str
    description: str

    # True if it has a Terminal lens, so its items can be used.
    usable: bool


def load_types(types: Type.Client) -> list[TypeEntry]:
    """Return the implemented types, sorted by name."""
    entries = []
    for type_id in types.list_types(implemented=True).type_ids:
        described = types.describe(type_id=type_id)
        if described.status != Status.OK:
            continue
        lenses = types.find_lenses(type_id=type_id, context="terminal")
        entries.append(TypeEntry(type_id, described.description.name or type_id,
                                 described.description.description,
                                 lenses.status == Status.OK and bool(lenses.lenses)))
    return sorted(entries, key=lambda e: (e.name.casefold(), e.type_id))


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


class Services:
    """What the window uses: the Type and History services, and
    darq.launch.  Tests replace them."""

    def __init__(self):
        self.types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
        self.history = History.Client(darq.connect("darq.history"), timeout=TIMEOUT)
        self.launch = darq.launch

    def shutdown(self):
        darq.shutdown()

    def reboot(self):
        darq.reboot()


class FactoryTab(QWidget):
    """The implemented types: create an item of one, and use it."""

    def __init__(self, terminal: "DevTerminal"):
        super().__init__()
        self.terminal = terminal

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Name", "Type", "Description"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.horizontalHeader().setStretchLastSection(True)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.cellDoubleClicked.connect(lambda row, column: self.create_item())

        self.create_button = QPushButton("Create")
        self.create_button.clicked.connect(self.create_item)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(refresh)
        buttons.addWidget(self.create_button)
        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.entries: list[TypeEntry] = []

    def refresh(self) -> bool:
        """Load the types again.

        :returns: True if there are any."""
        try:
            self.entries = load_types(self.terminal.services.types)
        except (darq.DarqError, TimeoutError) as e:
            self.terminal.show_status(f"Can't list types: {e}")
            return False

        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            for column, text in enumerate((entry.name, entry.type_id, entry.description)):
                cell = QTableWidgetItem(text)
                if not entry.usable:
                    # Greyed out: there's no lens to use its items.
                    cell.setFlags(cell.flags() & ~ItemFlag.ItemIsEnabled & ~ItemFlag.ItemIsSelectable)
                    cell.setToolTip("No Terminal lens")
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        return bool(self.entries)

    def selected(self) -> TypeEntry | None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        entry = self.entries[rows.pop()]
        return entry if entry.usable else None

    def create_item(self):
        """Create an item of the selected type, and use it."""
        entry = self.selected()
        if entry is None:
            self.terminal.show_status("Select a type with a lens")
            return
        launch = self.terminal.services.launch
        try:
            item_id, item = launch.create_item(entry.type_id)
        except (darq.DarqError, TimeoutError) as e:
            self.terminal.show_status(f"Can't create a {entry.name}: {e}")
            return
        self.terminal.record(item_id, darq.history.CREATED)
        self.terminal.use(item_id, item)


class HistoryTab(QWidget):
    """Events from History, filtered: double-click one to use its item."""

    def __init__(self, terminal: "DevTerminal"):
        super().__init__()
        self.terminal = terminal

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
        self.sources.setPlaceholderText("Any: text-lens, devterm, ...")

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
        self.table.cellDoubleClicked.connect(lambda row, column: self.use(row))

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
            response = self.terminal.services.history.get_events(**{**self.query, "end": end}, max=PAGE)
        except (darq.DarqError, TimeoutError) as e:
            self.terminal.show_status(f"Can't get events: {e}")
            return
        if response.status != HistoryStatus.OK:
            self.terminal.show_status("Invalid filter: names have no spaces, and are at most 31 bytes")
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
        self.more.setEnabled(len(response.events) == PAGE)
        self.terminal.show_status(f"{len(self.shown)} events" + (", and more" if len(response.events) == PAGE else ""))

    def use(self, row: int):
        if 0 <= row < len(self.shown):
            self.terminal.use(self.shown[row].item_id)


class DevTerminal(QMainWindow):
    """The window: New and History tabs, and a System menu."""

    def __init__(self, services: Services):
        super().__init__()
        self.services = services
        self.setWindowTitle("DarqOS Development Terminal")
        self.resize(1000, 700)

        self.factory = FactoryTab(self)
        self.history = HistoryTab(self)
        tabs = QTabWidget()
        tabs.addTab(self.factory, "New")
        tabs.addTab(self.history, "History")
        self.setCentralWidget(tabs)

        menu_bar = self.menuBar()
        assert menu_bar is not None
        system = menu_bar.addMenu("System")
        assert system is not None
        system.addAction("Shutdown", self.services.shutdown)
        system.addAction("Reboot", self.services.reboot)
        system.addSeparator()
        system.addAction("Quit", self.close)

        self.status = QLabel()
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addWidget(self.status)

        self.retry = QtCore.QTimer(self)
        self.retry.setInterval(TYPES_RETRY)
        self.retry.timeout.connect(self.load_types)
        self.load_types()

    def load_types(self):
        """Load the types, trying again until the type sync has run."""
        if self.factory.refresh():
            self.retry.stop()
            self.history.set_types([entry.type_id for entry in self.factory.entries])
            self.show_status(f"{len(self.factory.entries)} implemented types")
        elif not self.retry.isActive():
            self.retry.start()

    def show_status(self, message: str):
        self.status.setText(message)

    def record(self, item_id: str, event: str):
        try:
            self.services.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")

    def use(self, item_id: str, item: int | None = None):
        """Start a lens for an item: it's opened, if no port is given."""
        self.show_status(f"Starting a lens for {item_id} ...")
        QApplication.processEvents()
        try:
            self.services.launch.use_item(item_id, item)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(str(e))
            return
        self.show_status(f"Using {item_id}")


def darq_main():
    app = QApplication([])
    window = DevTerminal(Services())
    window.show()
    app.exec()
    return 0
