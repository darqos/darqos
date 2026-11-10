# darqos
# Copyright (C) 2026 David Arnold

"""Tools, for the Terminal's Tools panel, Sys-t, and the development
Terminal's Tools menu (see doc/terminal.rst): finding the installed tools,
running one, and saying how it finished.

A tool is a program under tools/, with a tool.json manifest, its name and
description, that an operator runs, such as an import.  It's spawned as
the Terminal's child, and waited for, off the UI's thread, so its exit
status, and its error, if it failed, are known.  What it did, beyond
that, is only in its log, for now: Second Edition's Events will tell."""

import dataclasses
import json
import logging
import pathlib
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

import darq


# Control exports
__all__ = ["Tool", "ToolList", "find_tools", "outcome", "run_tool"]

Key = QtCore.Qt.Key

# What's shown of a tool's state.
RUNNING = "Running"


@dataclasses.dataclass(frozen=True)
class Tool:
    """An installed tool: its name, what it does, and its directory, which
    is spawned to run it."""
    name: str
    description: str
    path: str


def find_tools(root: pathlib.Path) -> list[Tool]:
    """Return the installed tools, by name: those under tools/ with a
    tool.json manifest, {"name": ..., "description": ...}."""
    tools = []
    for manifest in sorted((root / "tools").glob("*/tool.json")):
        try:
            data = json.loads(manifest.read_text())
            name = str(data.get("name") or manifest.parent.name)
            description = str(data.get("description") or "")
        except (OSError, ValueError, AttributeError) as e:
            logging.warning(f"Can't read {manifest}: {e}")
            continue
        tools.append(Tool(name, description, str(manifest.parent)))
    return sorted(tools, key=lambda t: t.name.casefold())


def outcome(name: str, finished: darq.ChildStatus) -> str:
    """Return how a tool finished, in words: its error's last line, if it
    failed, which is what's gone wrong."""
    if finished.reason == darq.ExitReason.KILLED:
        return f"{name} was stopped"
    if finished.reason == darq.ExitReason.LOST:
        return f"{name} was lost"
    if finished.status == 0:
        return f"{name} finished"
    lines = [line.strip() for line in (finished.error or "").splitlines() if line.strip()]
    reason = lines[-1] if lines else f"exit status {finished.status}"
    return f"{name} failed: {reason}"


def run_tool(launcher: typing.Any, tool: Tool, finished: typing.Callable[[str], None],
             spawn: typing.Callable[[str], int] = darq.spawn_process,
             wait: typing.Callable[[int], darq.ChildStatus | None] = darq.wait):
    """Start a tool, as a child, and wait for it, off the UI's thread, with
    a launcher's background: spawning waits for it to run, and waiting for
    it to finish.  Then say how it did, on the UI's thread."""
    def job() -> darq.ChildStatus | None:
        return wait(spawn(tool.path))

    def done(status: darq.ChildStatus | None, error: Exception | None):
        if error is not None:
            message = f"Can't run {tool.name}: {error}"
        elif status is None:
            message = f"{tool.name} finished"
        else:
            message = outcome(tool.name, status)
        logging.info(message)
        finished(message)

    launcher.background(job, done)


class Host(typing.Protocol):
    """What a ToolList's in: a launcher, to run jobs off the UI's thread,
    and somewhere to say what's happened."""

    launcher: typing.Any

    def show_status(self, message: str) -> None: ...

    def tool_finished(self, message: str) -> None:
        """A tool's finished: say how, in words, as outcome gives it."""
        ...


class ToolList(QWidget):
    """The installed tools: typing narrows the list, by name or
    description, the arrow keys move through what's left, and Enter runs
    the one selected.  A tool that's running isn't started again."""

    def __init__(self, host: Host, root: pathlib.Path,
                 spawn: typing.Callable[[str], int] = darq.spawn_process,
                 wait: typing.Callable[[int], darq.ChildStatus | None] = darq.wait):
        super().__init__()
        self.host = host
        self.root = root
        self.spawn = spawn
        self.wait = wait
        self.tools: list[Tool] = []

        # The tools running, by name.
        self.running: set[str] = set()

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Type to filter; Enter runs")
        self.filter.textChanged.connect(self.apply_filter)
        self.filter.returnPressed.connect(self.run_selected)
        self.filter.installEventFilter(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Name", "Description", ""))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.horizontalHeader().setStretchLastSection(True)  # pyright: ignore[reportOptionalMemberAccess]
        self.table.cellDoubleClicked.connect(lambda row, column: self.run_selected())

        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.run_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def refresh(self):
        """Find the tools again, and show them."""
        self.tools = find_tools(self.root)
        self.table.setRowCount(len(self.tools))
        for row, tool in enumerate(self.tools):
            for column, text in enumerate((tool.name, tool.description,
                                           RUNNING if tool.name in self.running else "")):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.apply_filter()

    def apply_filter(self):
        """Show only the tools matching the filter, selecting the first."""
        text = self.filter.text().casefold()
        for row, tool in enumerate(self.tools):
            self.table.setRowHidden(row, text not in tool.name.casefold() and text not in tool.description.casefold())
        shown = self.shown_rows()
        if shown:
            self.table.selectRow(shown[0])
        else:
            self.table.clearSelection()

    def shown_rows(self) -> list[int]:
        return [row for row in range(len(self.tools)) if not self.table.isRowHidden(row)]

    def eventFilter(self, a0, a1) -> bool:
        """In the filter, Up and Down move the selection."""
        if isinstance(a1, QKeyEvent) and a1.type() == QtCore.QEvent.Type.KeyPress \
                and a1.key() in (Key.Key_Up, Key.Key_Down):
            rows = self.shown_rows()
            if rows:
                current = {index.row() for index in self.table.selectedIndexes()}
                row = current.pop() if len(current) == 1 else None
                position = rows.index(row) + (-1 if a1.key() == Key.Key_Up else 1) if row in rows else 0
                self.table.selectRow(rows[max(0, min(len(rows) - 1, position))])
            return True
        return super().eventFilter(a0, a1)

    def selected(self) -> Tool | None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        return self.tools[rows.pop()] if len(rows) == 1 else None

    def run_selected(self):
        tool = self.selected()
        if tool is None:
            self.host.show_status("Select a tool")
            return
        self.run(tool)

    def run(self, tool: Tool):
        """Start a tool, and wait for it, off the UI's thread: spawning
        waits for it to run, and waiting for it to finish."""
        if tool.name in self.running:
            self.host.show_status(f"{tool.name} is still running")
            return
        self.running.add(tool.name)
        self.mark(tool, RUNNING)
        self.host.show_status(f"Started {tool.name}")

        def finished(message: str):
            self.running.discard(tool.name)
            self.mark(tool, "")
            self.host.tool_finished(message)

        run_tool(self.host.launcher, tool, finished, self.spawn, self.wait)

    def mark(self, tool: Tool, state: str):
        """Show a tool's state, in its row."""
        for row, shown in enumerate(self.tools):
            if shown.name == tool.name:
                self.table.setItem(row, 2, QTableWidgetItem(state))
