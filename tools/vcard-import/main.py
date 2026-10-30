# darqos
# Copyright (C) 2026 David Arnold

# vCard import: a window for importing an address book, a vCard file,
# into the Knowledge Base.
#
# It's a tool (see tool.json), started from the development Terminal's
# Tools menu.  Choose a file, and the address book's name, which its
# statements are stated in, so it can be imported again, updating what
# it imported before.  The cards are read (cards.py), and imported
# (importer.py) on another thread, through the KB service, with new items
# created by the KB type implementation, and recorded in History.
#
# Its summary is counts, and what's to be reviewed: the matches by
# natural key, to be checked, and the addresses that changed, to be
# dated.  They're logged too, under "vcard-import".

import logging
import os
import pathlib
import sys
import typing

from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

import darq
import darq.kb as model
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.type import Implementation, Status, Type
from darq.ui.launcher import start_thread

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cards  # noqa: E402
import importer  # noqa: E402
import vcard  # noqa: E402
import vocabulary  # noqa: E402


# Seconds to wait for services' replies.
TIMEOUT = 30.0

# The type whose implementation creates the items.
ENTITY_TYPE = model.ENTITY_TYPE


def run_import(path: str, name: str, progress: typing.Callable[[str, int, int], None]) \
        -> tuple[list[str], importer.Result]:
    """Read and import an address book, on the calling thread.

    :returns: Counts of what was read, and what was imported.
    :raises OSError, ValueError: if the file can't be read.
    :raises darq.DarqError: if a service can't be reached."""
    progress("Reading cards", 0, 1)
    wd = vocabulary.Vocabulary()
    reader = cards.Importer(wd)
    reader.read(vcard.read(path))

    kb = model.KB.connect(timeout=TIMEOUT)
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    activated = types.activate(type_id=ENTITY_TYPE)
    if activated.status != Status.OK or activated.implementation is None:
        raise darq.DarqError(f"Can't activate {ENTITY_TYPE}: {activated.status.name}")
    implementation = Implementation.Client(activated.implementation, timeout=TIMEOUT)

    # History is best effort: the import works without it.
    handle = darq.lookup_name("darq.history", blocking=False)
    history = History.Client(handle, timeout=TIMEOUT) if handle is not None else None

    result = importer.Import(kb, implementation, history, wd, name, progress).run(reader.drafts)
    return cards.summary(reader), result


class ImportWindow(QWidget):
    """Choose a vCard file, import it, and show what happened."""

    def __init__(self, loop: QtEventLoop):
        super().__init__()
        self.loop = loop
        self.setWindowTitle("Import vCards")
        self.resize(760, 560)

        layout = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("File:"))
        self.path = QLineEdit(self)
        row.addWidget(self.path, 1)
        self.browse = QPushButton("Browse ...", self)
        self.browse.clicked.connect(self.choose)
        row.addWidget(self.browse)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Address book:"))
        self.name = QLineEdit("Address book", self)
        row.addWidget(self.name, 1)
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

    def choose(self):
        # Qt's own dialog: the compositor has no file chooser portal.
        path, _ = QFileDialog.getOpenFileName(self, "vCard file", str(pathlib.Path.home()),
                                              "vCards (*.vcf *.vcard);;All files (*)",
                                              options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.path.setText(path)

    def begin(self):
        path, name = self.path.text().strip(), self.name.text().strip()
        if not os.path.isfile(path):
            self.status.setText("Choose a vCard file to import.")
            return
        if not name:
            self.status.setText("Give the address book a name.")
            return
        self.set_running(True)
        self.summary.clear()

        def progress(step: str, done: int, total: int):
            if done % 25 == 0 or done == total - 1:
                self.loop.post(lambda: self.show_progress(step, done, total))

        def job():
            try:
                outcome: typing.Any = run_import(path, name, progress)
            except Exception as e:
                logging.exception("Import failed")
                outcome = e
            darq.release_reply_port()
            try:
                self.loop.post(lambda: self.finished(outcome))
            except RuntimeError:
                logging.info("Import finished after the window")

        start_thread(job)

    def set_running(self, running: bool):
        self.running = running
        for widget in (self.path, self.browse, self.name, self.start):
            widget.setEnabled(not running)

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
    app.exec()
    return 0
