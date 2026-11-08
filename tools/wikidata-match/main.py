# darqos
# Copyright (C) 2026 David Arnold

# Match with Wikidata: a window for making the people and organisations
# made here Wikidata's, one at a time, and for bringing Wikidata's
# languages (see doc/wikidata.rst).
#
# It's a tool (see tool.json), started from the development Terminal's
# Tools menu.  It lists those that aren't Wikidata's yet: choose one, and
# what Wikidata has by its name is shown, with descriptions, to choose
# from, or to search again.  Nothing's guessed: Link makes it the one
# chosen, and Skip leaves it.  What's asked of Wikidata's done on another
# thread, through the HTTP service, saying who's asking.

import logging
import typing

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import darq
import darq.kb as model
import darq.wikidata
from darq.loop import QtEventLoop
from darq.ui import wikidata as wikidata_ui
from darq.ui.launcher import start_thread


# The kinds that are matched, by their classes: people and organisations.
CLASSES = {"darq.person": darq.wikidata.PERSON_CLASS, "darq.organisation": darq.wikidata.ORGANISATION_CLASS}

# Seconds to wait for the KB.
TIMEOUT = 60.0

ERRORS = (darq.wikidata.WikidataError, darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError)


class Unmatched(typing.NamedTuple):
    entity_id: str
    label: str
    kind: str


def unmatched(kb: model.KB) -> list[Unmatched]:
    """Return the people and organisations that aren't Wikidata's."""
    found = []
    for kind, class_id in CLASSES.items():
        ids = [i for i in kb.instances(class_id) if not darq.wikidata.is_wikidata(i)]
        for start in range(0, len(ids), 200):
            entities, _ = kb.get_entities(ids[start:start + 200], follow=False)
            found += [Unmatched(e.id, e.get_label() or e.id, kind) for e in entities
                      if e.redirect is None and not e.destroyed]
    return sorted(found, key=lambda u: u.label.casefold())


class MatchWindow(QWidget):
    """Those that aren't matched, what Wikidata has for one, and the
    buttons to link, skip, and bring languages."""

    def __init__(self, loop: QtEventLoop, make_linker: typing.Callable[[], darq.wikidata.Linker] = wikidata_ui.linker,
                 run: typing.Callable[[typing.Callable[[], None]], None] = start_thread):
        super().__init__()
        self.loop = loop
        self.make_linker = make_linker
        self.run = run
        self.setWindowTitle("Match with Wikidata")
        self.resize(900, 600)
        self.waiting: list[Unmatched] = []
        self.candidates: list[darq.wikidata.Candidate] = []
        self.busy = False

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.people = QListWidget(self)
        self.people.currentRowChanged.connect(self.chosen)
        row.addWidget(self.people, 1)
        right = QVBoxLayout()
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Search Wikidata for")
        self.query.returnPressed.connect(self.search)
        right.addWidget(self.query)
        self.found = QListWidget(self)
        self.found.itemActivated.connect(lambda _: self.link())
        right.addWidget(self.found, 1)
        buttons = QHBoxLayout()
        self.link_button = QPushButton("Link", self)
        self.link_button.clicked.connect(self.link)
        self.skip_button = QPushButton("Skip", self)
        self.skip_button.clicked.connect(self.skip)
        buttons.addWidget(self.link_button)
        buttons.addWidget(self.skip_button)
        right.addLayout(buttons)
        row.addLayout(right, 2)
        layout.addLayout(row, 1)

        bottom = QHBoxLayout()
        self.languages_button = QPushButton("Bring Wikidata's Languages", self)
        self.languages_button.clicked.connect(self.bring_languages)
        bottom.addWidget(self.languages_button)
        self.status = QLabel(self)
        bottom.addWidget(self.status, 1)
        layout.addLayout(bottom)

    # Doing things with Wikidata, on another thread

    def background(self, job: typing.Callable[[darq.wikidata.Linker], typing.Any],
                   done: typing.Callable[[typing.Any], None], saying: str):
        if self.busy:
            return
        self.busy = True
        self.status.setText(saying)
        make_linker = self.make_linker

        def work():
            try:
                outcome = job(make_linker())
            except ERRORS as e:
                logging.warning(f"Wikidata: {e!r}")
                outcome = e
            if self.run is start_thread:
                darq.release_reply_port()
            self.loop.post(lambda: self.finished(outcome, done))

        self.run(work)

    def finished(self, outcome: typing.Any, done: typing.Callable[[typing.Any], None]):
        self.busy = False
        if isinstance(outcome, Exception):
            self.status.setText(str(outcome))
            return
        done(outcome)

    # The list

    def load(self):
        self.background(lambda linker: unmatched(linker.kb), self.loaded, "Finding who isn't matched ...")

    def loaded(self, found: list[Unmatched]):
        self.waiting = found
        self.people.clear()
        for one in found:
            kind = "person" if one.kind == "darq.person" else "organisation"
            self.people.addItem(QListWidgetItem(f"{one.label}  ({kind})"))
        self.status.setText(f"{len(found)} not matched yet")
        if found:
            self.people.setCurrentRow(0)

    def current(self) -> Unmatched | None:
        row = self.people.currentRow()
        return self.waiting[row] if 0 <= row < len(self.waiting) else None

    def chosen(self, row: int):
        one = self.current()
        self.found.clear()
        self.candidates = []
        if one is not None:
            self.query.setText(one.label)
            self.search()

    def search(self):
        text = self.query.text().strip()
        if text:
            self.background(lambda linker: darq.wikidata.search(text, linker.fetch, linker.agent), self.show_found,
                            f"Looking for {text} ...")

    def show_found(self, found: list[darq.wikidata.Candidate]):
        self.candidates = found
        self.found.clear()
        for candidate in found:
            self.found.addItem(f"{candidate.describe()}  [{candidate.id}]")
        self.status.setText(f"Wikidata has {len(found)}" if found else "Wikidata has nothing by that name")
        if found:
            self.found.setCurrentRow(0)

    def link(self):
        one, row = self.current(), self.found.currentRow()
        if one is None or not 0 <= row < len(self.candidates):
            return
        target = self.candidates[row].id
        self.background(lambda linker: linker.link(one.entity_id, target),
                        lambda _: self.done(f"{one.label} is {target}"), f"Making {one.label} {target} ...")

    def skip(self):
        self.done("")

    def done(self, said: str):
        """Take the current one off the list, and go on to the next."""
        row = self.people.currentRow()
        if 0 <= row < len(self.waiting):
            del self.waiting[row]
            self.people.takeItem(row)
        self.status.setText(said)
        if self.waiting:
            # The next one's often at the same row, which isn't a change.
            following = min(row, len(self.waiting) - 1)
            self.people.blockSignals(True)
            self.people.setCurrentRow(following)
            self.people.blockSignals(False)
            self.chosen(following)
        else:
            self.found.clear()
            self.candidates = []

    def bring_languages(self):
        self.background(lambda linker: linker.bring_languages(),
                        lambda merged: self.status.setText(f"Brought Wikidata's languages, and merged {merged} "
                                                           "made here"),
                        "Bringing Wikidata's languages ...")


def darq_main():
    app = QApplication([])
    loop = QtEventLoop()
    window = MatchWindow(loop)
    window.show()
    window.load()
    app.exec()
    return 0
