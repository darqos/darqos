# darqos
# Copyright (C) 2026 David Arnold

# vCard import: a window for importing an address book, a vCard file,
# into the Knowledge Base.
#
# It's a tool (see tool.json), started from the Terminal's Tools panel,
# Sys-t.  Choose where it comes from (darq.ui.sources): an item, fetched
# from a URL, chosen, or made from a local file; and the address book's
# name, which its statements are stated in, so it can be imported again,
# updating what it imported before, and which remembers the item it was
# imported from, to offer it again.  The cards are read (cards.py), and imported
# (importer.py) on another thread: read from the KB service, and created
# and written through the KB type implementation, and recorded in History.
#
# Its summary is counts, and what's to be reviewed: the matches by
# natural key, to be checked, and the addresses that changed, to be
# dated.  They're logged too, under "vcard-import".

import logging
import pathlib
import sys
import typing

from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

import darq
import darq.kb as model
from darq.loop import QtEventLoop
from darq.protocols.kb import EntityWriter
from darq.protocols.type import Implementation, Status, Type
from darq.ui import sources

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cards  # noqa: E402
import importer  # noqa: E402
import vcard  # noqa: E402
import vocabulary  # noqa: E402


# Seconds to wait for services' replies.
TIMEOUT = 30.0

# History's source of what it brings in, and the items it reads: vCards.
SOURCE = importer.SOURCE
ACCEPTS = sources.accepting(["text/vcard", "text/x-vcard", "text/directory"], ["vcf", "vcard"])

# A type whose implementation, the KB's, creates the items.
KB_TYPE = model.THING_TYPE


def address_book(name: str) -> str:
    """Return the entity of an address book, by its name."""
    return model.key_id(f"address book:{name}")


def run_import(source: sources.Source, name: str, progress: typing.Callable[[str, int, int], None]) \
        -> tuple[list[str], importer.Result]:
    """Read and import an address book, from a source item, on the calling
    thread, and remember it as what the address book was imported from.

    :returns: Counts of what was read, and what was imported.
    :raises sources.SourceError, ValueError: if it can't be read.
    :raises darq.DarqError: if a service can't be reached."""
    progress("Reading cards", 0, 1)
    try:
        text = sources.read(source).decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{source.name} isn't UTF-8 text: {e}") from None
    wd = vocabulary.Vocabulary()
    reader = cards.Importer(wd)
    reader.read(list(vcard.parse(text)))

    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    activated = types.activate(type_id=KB_TYPE)
    if activated.status != Status.OK or activated.implementation is None:
        raise darq.DarqError(f"Can't activate {KB_TYPE}: {activated.status.name}")
    implementation = Implementation.Client(activated.implementation, timeout=TIMEOUT)
    kb = model.KB.connect(timeout=TIMEOUT)
    items = model.KB(kb.client, EntityWriter.Client(activated.implementation, timeout=TIMEOUT))

    result = importer.Import(kb, items, implementation, wd, name, progress).run(reader.drafts)
    sources.remember(kb, address_book(name), source)
    sources.describe(source.item_id, f"vCard address book, imported as {name}")
    return cards.summary(reader), result


class ImportWindow(QWidget):
    """Choose where a vCard file comes from, import it, and show what
    happened."""

    def __init__(self, loop: QtEventLoop):
        super().__init__()
        self.loop = loop
        self.run = sources.runner(loop)
        self.setWindowTitle("Import vCards")
        self.resize(760, 560)

        layout = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("Address book:"))
        self.name = QLineEdit("Address book", self)
        self.name.editingFinished.connect(self.offer_previous)
        row.addWidget(self.name, 1)
        layout.addLayout(row)

        self.source = sources.SourceChooser(self, SOURCE, ACCEPTS, self.run, "vCards (*.vcf *.vcard);;All files (*)")
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

    def offer_previous(self):
        """Offer what the address book of this name was last imported from."""
        name = self.name.text().strip()
        if name:
            self.source.offer_previous(address_book(name))

    def begin(self):
        source, name = self.source.source, self.name.text().strip()
        if source is None:
            self.status.setText("Choose where the vCards come from.")
            return
        if not name:
            self.status.setText("Give the address book a name.")
            return
        self.set_running(True)
        self.summary.clear()

        def progress(step: str, done: int, total: int):
            if done % 25 == 0 or done == total - 1:
                self.loop.post(lambda: self.show_progress(step, done, total))

        self.run(lambda: run_import(source, name, progress), self.finished)

    def set_running(self, running: bool):
        self.running = running
        for widget in (self.name, self.start):
            widget.setEnabled(not running)
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
        read, result = outcome
        self.status.setText("Imported.")
        lines = [*read, "", *result.lines()]
        if result.failed:
            lines.append("Failed cards' UIDs are in the log.")
        if result.matches:
            lines += ["", "Matches to check (also in the log):"]
            for m in result.matches:
                line = f"  {m.what} {m.detail!r}, for card {m.card}: {m.item_id}"
                if m.others:
                    line += f" (also {', '.join(m.others)})"
                lines.append(line)
        if result.address_changes:
            lines += ["", "Addresses changed, to add end and start times to (also in the log):"]
            for a in result.address_changes:
                lines.append(f"  {a.item_id} {a.what}, for card {a.card}: {a.detail}")
        self.summary.setPlainText("\n".join(lines))

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
    window.offer_previous()
    app.exec()
    return 0
