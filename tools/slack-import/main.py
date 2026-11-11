# darqos
# Copyright (C) 2026 David Arnold

# Slack import: a window for importing a Slack workspace's history, from
# an export, into the Knowledge Base (see doc/slack-import-tool.rst).
#
# It's a tool (see tool.json), started from the Terminal's Tools panel,
# Sys-t.  Choose the export's directory, and the workspace's name.  The
# export is read (slack_export.py), and imported (slack_importer.py) on
# another thread: read from the KB service, and created and written
# through the KB type implementation, and recorded in History, as
# slack_importer.SOURCE describes.
#
# Its summary is counts, and the people found by name, to be checked.
# They're logged too, under "slack-import".

import logging
import pathlib
import sys
import typing

from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

import darq
import darq.kb as model
import darq.launch
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.kb import EntityWriter
from darq.protocols.type import Context as TypeContext
from darq.protocols.type import Implementation, Status, Type
from darq.ui.launcher import start_thread

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import slack_export  # noqa: E402
import slack_importer  # noqa: E402


# Seconds to wait for services' replies: a large file takes a while.
TIMEOUT = 60.0

# A type whose implementation, the KB's, creates the items.
KB_TYPE = model.THING_TYPE


class LaunchFiles:
    """Makes items from files' data, with darq.launch."""

    def create(self, media_type: str, extension: str, data: bytes, name: str,
               context: TypeContext | None = None) -> str | None:
        return darq.launch.import_data(media_type, extension, data, name, context)


def run_import(path: str, name: str, progress: typing.Callable[[str, int, int], None]) -> slack_importer.Result:
    """Read and import an export, on the calling thread.

    :raises OSError, ValueError: if it can't be read.
    :raises darq.DarqError: if a service can't be reached."""
    export = slack_export.Export(path)
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    activated = types.activate(type_id=KB_TYPE)
    if activated.status != Status.OK or activated.implementation is None:
        raise darq.DarqError(f"Can't activate {KB_TYPE}: {activated.status.name}")
    implementation = Implementation.Client(activated.implementation, timeout=TIMEOUT)
    kb = model.KB.connect(timeout=TIMEOUT)
    items = model.KB(kb.client, EntityWriter.Client(activated.implementation, timeout=TIMEOUT))

    # History is best effort: the import works without it.
    handle = darq.lookup_name("darq.history", blocking=False)
    history = History.Client(handle, timeout=TIMEOUT) if handle is not None else None

    return slack_importer.Importer(kb, items, implementation, history, LaunchFiles(), export, name, progress).run()


class ImportWindow(QWidget):
    """Choose an export, import it, and show what happened."""

    def __init__(self, loop: QtEventLoop):
        super().__init__()
        self.loop = loop
        self.setWindowTitle("Import Slack")
        self.resize(760, 560)

        layout = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("Export:"))
        self.path = QLineEdit(self)
        row.addWidget(self.path, 1)
        self.browse = QPushButton("Browse ...", self)
        self.browse.clicked.connect(self.choose)
        row.addWidget(self.browse)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Workspace:"))
        self.name = QLineEdit("Slack", self)
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
        path = QFileDialog.getExistingDirectory(self, "Slack export", str(pathlib.Path.home()),
                                                QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.path.setText(path)

    def begin(self):
        path, name = self.path.text().strip(), self.name.text().strip()
        if not (pathlib.Path(path) / "conversations.json").is_file():
            self.status.setText("Choose an export's directory.")
            return
        if not name:
            self.status.setText("Give the workspace a name.")
            return
        self.set_running(True)
        self.summary.clear()

        def progress(step: str, done: int, total: int):
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
        self.status.setText("Imported.")
        lines = outcome.lines()
        if outcome.failed:
            lines.append("Failed batches are in the log.")
        if outcome.matches:
            lines += ["", "People found by name, to check (also in the log):"]
            lines += [f"  {name}: {item_id}" for name, item_id in outcome.matches]
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
