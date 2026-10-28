# darqos
# Copyright (C) 2026 David Arnold

# The implemented types, for New: create an item of one, and use it in
# its lens.  Types with no Terminal lens are shown greyed out.

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.history
from darq.protocols.type import Status, Type

from . import Host


# Control exports
__all__ = [
    "TypeEntry",
    "TypeList",
    "load_types",
]


ItemFlag = QtCore.Qt.ItemFlag
Key = QtCore.Qt.Key


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


class TypeList(QWidget):
    """The implemented types: create an item of one, and use it.

    With a filter, typing narrows the list, by name or type identifier,
    the arrow keys move through what's left, and Enter creates."""

    def __init__(self, host: Host, filtered: bool = False):
        super().__init__()
        self.host = host

        self.filter: QLineEdit | None = None
        layout = QVBoxLayout()
        if filtered:
            self.filter = QLineEdit()
            self.filter.setPlaceholderText("Type to filter; Enter creates")
            self.filter.textChanged.connect(self.apply_filter)
            self.filter.returnPressed.connect(self.create_item)
            self.filter.installEventFilter(self)
            layout.addWidget(self.filter)

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
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.entries: list[TypeEntry] = []

    def refresh(self) -> bool:
        """Load the types again.

        :returns: True if there are any."""
        try:
            self.entries = load_types(self.host.services.types)
        except (darq.DarqError, TimeoutError) as e:
            self.host.show_status(f"Can't list types: {e}")
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
        self.apply_filter()
        return bool(self.entries)

    def apply_filter(self):
        """Show only the types matching the filter, selecting the first
        usable one."""
        text = self.filter.text().casefold() if self.filter is not None else ""
        for row, entry in enumerate(self.entries):
            matches = text in entry.name.casefold() or text in entry.type_id.casefold()
            self.table.setRowHidden(row, not matches)
        if self.filter is not None:
            selectable = self.selectable_rows()
            if selectable:
                self.table.selectRow(selectable[0])
            else:
                self.table.clearSelection()

    def selectable_rows(self) -> list[int]:
        return [row for row, entry in enumerate(self.entries)
                if entry.usable and not self.table.isRowHidden(row)]

    def eventFilter(self, watched, event) -> bool:
        """In the filter, Up and Down move the selection."""
        if isinstance(event, QKeyEvent) and event.type() == QtCore.QEvent.Type.KeyPress \
                and event.key() in (Key.Key_Up, Key.Key_Down):
            self.move_selection(-1 if event.key() == Key.Key_Up else 1)
            return True
        return super().eventFilter(watched, event)

    def move_selection(self, step: int):
        rows = self.selectable_rows()
        if not rows:
            return
        current = {index.row() for index in self.table.selectedIndexes()}
        row = current.pop() if len(current) == 1 else None
        position = rows.index(row) + step if row in rows else 0
        self.table.selectRow(rows[max(0, min(len(rows) - 1, position))])

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
            self.host.show_status("Select a type with a lens")
            return
        launch = self.host.services.launch
        try:
            item_id, item = launch.create_item(entry.type_id)
        except (darq.DarqError, TimeoutError) as e:
            self.host.show_status(f"Can't create a {entry.name}: {e}")
            return
        self.host.record(item_id, darq.history.CREATED)
        self.host.use(item_id, item)
