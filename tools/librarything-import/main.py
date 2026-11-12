# darqos
# Copyright (C) 2026 David Arnold

# LibraryThing import: a window for importing a library, from its JSON
# export, into the Knowledge Base (see doc/librarything-import-tool.rst).
#
# It's a tool (see tool.json), started from the Terminal's Tools panel,
# Sys-t.  Choose where the export comes from (darq.ui.sources): an item,
# fetched from a URL, chosen, or made from a local file, which
# LibraryThing's source item remembers, to offer again.  It's read
# (lt_export.py), and imported
# (lt_importer.py) on another thread: read from the KB service, and
# created and written through the KB type implementation, which records
# what's made in History.
#
# Its summary is counts, and what's to be checked: people found by name,
# editions that were here, and publications that weren't understood.
# They're logged too, under "librarything-import".

import logging
import pathlib
import sys
import typing

from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

import darq
import darq.book
import darq.kb as model
from darq.loop import QtEventLoop
from darq.protocols.kb import EntityWriter
from darq.protocols.type import Status, Type
from darq.ui import sources

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import lt_export  # noqa: E402
import lt_importer  # noqa: E402


# Seconds to wait for services' replies.
TIMEOUT = 60.0

# History's source of what it brings in, and the items it reads: JSON.
SOURCE = lt_importer.SOURCE
ACCEPTS = sources.accepting(["application/json"], ["json"])


def run_import(source: sources.Source, progress: typing.Callable[[str, int, int], None]) -> lt_importer.Result:
    """Read and import an export, from a source item, on the calling
    thread, and remember it as what LibraryThing's was imported from.

    :raises sources.SourceError, ValueError: if it can't be read.
    :raises darq.DarqError: if a service can't be reached."""
    try:
        text = sources.read(source).decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{source.name} isn't UTF-8 text: {e}") from None
    records = lt_export.read_export(text)
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    activated = types.activate(type_id=model.THING_TYPE)
    if activated.status != Status.OK or activated.implementation is None:
        raise darq.DarqError(f"Can't activate {model.THING_TYPE}: {activated.status.name}")
    kb = model.KB.connect(timeout=TIMEOUT)
    items = model.KB(kb.client, EntityWriter.Client(activated.implementation, timeout=TIMEOUT))

    result = lt_importer.Importer(kb, items, records, progress).run()
    sources.remember(kb, darq.book.LIBRARYTHING, source)
    sources.describe(source.item_id, "LibraryThing export")
    return result


def summary(outcome: lt_importer.Result) -> list[str]:
    """Return the lines of an import's summary."""
    lines = outcome.lines()
    if outcome.failed:
        lines.append("Failures are in the log: importing again finishes the job.")
    if outcome.matches:
        lines += ["", "People found by name, to check (also in the log):"]
        lines += [f"  {name}: {item_id}" for name, item_id in outcome.matches]
    if outcome.editions_found:
        lines += ["", "Editions that were here already, by ISBN:"]
        lines += [f"  {title}: {item_id}" for title, item_id in outcome.editions_found]
    if outcome.unparsed:
        lines += ["", "Publications not understood, so with no publisher:"]
        lines += [f"  {title}" for title in outcome.unparsed]
    return lines


class ImportWindow(QWidget):
    """Choose where an export comes from, import it, and show what
    happened."""

    def __init__(self, loop: QtEventLoop):
        super().__init__()
        self.loop = loop
        self.run = sources.runner(loop)
        self.setWindowTitle("Import LibraryThing")
        self.resize(760, 560)

        layout = QVBoxLayout()
        self.source = sources.SourceChooser(self, SOURCE, ACCEPTS, self.run, "JSON (*.json);;All files (*)")
        layout.addWidget(self.source)
        row = QHBoxLayout()
        row.addStretch(1)
        self.start = QPushButton("Import", self)
        self.start.clicked.connect(self.begin)
        row.addWidget(self.start)
        layout.addLayout(row)

        self.progress = QProgressBar(self)
        layout.addWidget(self.progress)
        self.status = QLabel(self)
        layout.addWidget(self.status)
        self.summary = QPlainTextEdit(self)
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.setLayout(layout)

        # Set while an import's running.
        self.running = False

    def begin(self):
        source = self.source.source
        if source is None:
            self.status.setText("Choose where the export comes from.")
            return
        self.set_running(True)
        self.summary.clear()

        def progress(step: str, done: int, total: int):
            self.loop.post(lambda: self.show_progress(step, done, total))

        self.run(lambda: run_import(source, progress), self.finished)

    def set_running(self, running: bool):
        self.running = running
        self.start.setEnabled(not running)
        self.source.set_enabled(not running)

    def show_progress(self, step: str, done: int, total: int):
        self.status.setText(f"{step} ...")
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done + 1)

    def finished(self, outcome: typing.Any):
        self.set_running(False)
        self.progress.setValue(self.progress.maximum())
        if isinstance(outcome, Exception):
            self.status.setText(f"Import failed: {outcome}")
            return
        self.status.setText("Imported.")
        self.summary.setPlainText("\n".join(summary(outcome)))

    def closeEvent(self, a0):
        if self.running:
            # The thread's a daemon: it stops when the process exits.
            logging.warning("Closed during an import: it's stopped where it got to")
        super().closeEvent(a0)


def darq_main():
    app = QApplication([])
    loop = QtEventLoop()
    window = ImportWindow(loop)
    window.show()
    window.source.offer_previous(darq.book.LIBRARYTHING)
    app.exec()
    return 0
