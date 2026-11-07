# darqos
# Copyright (C) 2026 David Arnold

# The Book lens's add loop: paper books are the bulk of a library, so
# adding one's built for speed (see doc/book.rst).
#
# Type or scan an ISBN and press Enter, and it's looked up, and shown to
# be checked; or type a title, and choose.  Enter adds it, and it's ready
# for the next.  What's usually the same as the last book, its location
# and language, stays.  It says what the book is to what's here, before
# it's added.  Nearly always it's new.  If it might not be, it stops and
# asks, on keys of their own: a scanner sends Enter by itself, and a stray
# one mustn't count as a yes.
#
# This is the view: the lens does the looking up and saving.

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QFormLayout, QFrame, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget

import darq.book
from darq.book import Book, Copy, Edition
from darq.ui import style

import book_card
import book_form


Key = QtCore.Qt.Key

# The fields of each level: their name, and label, divided as a card is.
# Location and language stay.
LEVELS = [
    [("title", "Title"), ("subtitle", "Subtitle"), ("authors", "Authors")],
    [("language", "Language")],
    [("publisher", "Publisher"), ("imprint", "Imprint"), ("date", "Date published"), ("edition", "Edition"),
     ("binding", "Binding"), ("isbn_13", "ISBN-13"), ("isbn_10", "ISBN-10"), ("pages", "Pages")],
    [("printing", "Printing"), ("printed", "Date printed"), ("location", "Shelved at")],
]
STAYS = ("language", "location")

# Pixels wide that a field's label is, so that a level's fields line up.
LABEL_WIDTH = 110

# What separates names.
SEPARATOR = ";"

# What each kind of match is said to be.
MATCHES = {
    darq.book.NEW: "A new book",
    darq.book.ANOTHER_EDITION: "It might be another edition of {title}",
    darq.book.ANOTHER_COPY: "You have {title} already",
}


class Host(typing.Protocol):
    """What the view needs of the lens."""

    def look_up(self, text: str) -> None: ...

    def add_shown(self) -> None: ...

    def stop_adding(self) -> None: ...

    def fields_changed(self) -> None: ...

    def names(self, text: str, kind: str) -> list[str]: ...



class AddView(QWidget):
    def __init__(self, host: Host, parent: QWidget | None = None):
        super().__init__(parent)
        self.host = host
        self.results: list[Book] = []

        # What Open Library calls the edition shown, if it knows it, and
        # where its cover is.
        self.open_library = ""
        self.cover_url = ""

        layout = QVBoxLayout(self)
        heading = QLabel("Add books", self)
        heading.setStyleSheet(f"QLabel {{ color: {style.TEXT}; font-size: 20px; }}")
        layout.addWidget(heading)
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Scan or type an ISBN, or a title, and press Enter")
        self.query.returnPressed.connect(self.search)
        layout.addWidget(self.query)

        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        # What's being asked: what each key does.
        self.answers: dict[int, typing.Callable[[], None]] = {}
        self.question = QLabel(self)
        self.question.setWordWrap(True)
        self.question.setStyleSheet(f"QLabel {{ color: {style.TEXT}; background-color: {style.RAISED};"
                                    " padding: 8px; }")
        self.question.hide()
        layout.addWidget(self.question)

        self.choices = QListWidget(self)
        self.choices.itemActivated.connect(lambda _: self.choose())
        self.choices.hide()
        layout.addWidget(self.choices)

        self.match = QLabel(self)
        self.match.setStyleSheet(f"QLabel {{ color: {style.TEXT_SOFT}; font-size: 15px; }}")
        layout.addWidget(self.match)

        self.fields: dict[str, QLineEdit] = {}
        for depth, level in enumerate(LEVELS):
            if depth:
                line = QFrame(self)
                line.setFixedHeight(1)
                line.setStyleSheet(f"QFrame {{ background-color: {style.BORDER}; }}")
                layout.addWidget(line)
            form = QFormLayout()
            form.setContentsMargins(depth * book_card.INDENT, 0, 0, 0)
            for name, label in level:
                edit = QLineEdit(self)
                edit.setObjectName(name)
                edit.returnPressed.connect(self.host.add_shown)
                edit.editingFinished.connect(self.edited)
                caption = QLabel(label, self)
                caption.setMinimumWidth(LABEL_WIDTH)
                caption.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; }}")
                form.addRow(caption, edit)
                self.fields[name] = edit
                book_form.complete(edit, name, self.host.names)
            layout.addLayout(form)

        self.add_button = QPushButton("Add", self)
        self.add_button.clicked.connect(self.host.add_shown)
        layout.addWidget(self.add_button)
        layout.addStretch(1)

    def edited(self):
        """A field's been left: what it is may have changed."""
        self.host.fields_changed()

    def start(self):
        """Be ready for the next book: what stays, stays."""
        kept = {name: self.fields[name].text() for name in STAYS}
        for name, edit in self.fields.items():
            edit.setText(kept.get(name, ""))
        self.query.clear()
        self.match.clear()
        self.choices.hide()
        self.results = []
        self.open_library = ""
        self.cover_url = ""
        self.stop_asking()
        self.query.setFocus()

    def search(self):
        text = self.query.text().strip()
        if text:
            self.host.look_up(text)

    def show_results(self, books: list[Book]):
        """Show what was found: one's filled in, and several to choose from."""
        self.results = books
        if len(books) == 1:
            self.show_book(books[0])
            return
        self.choices.clear()
        for book in books:
            self.choices.addItem(f"{book.title}, {', '.join(book.authors)}" if book.authors else book.title)
        self.choices.setCurrentRow(0)
        self.choices.show()
        self.choices.setFocus()

    def choose(self):
        row = self.choices.currentRow()
        if 0 <= row < len(self.results):
            self.choices.hide()
            self.show_book(self.results[row])
            self.host.fields_changed()

    def show_book(self, book: Book):
        """Fill the fields in, to be checked: what's blank, stays blank to
        be typed, and what's kept isn't lost to a lookup that doesn't say."""
        edition = book.editions[0] if book.editions else Edition()
        values = {"title": book.title, "subtitle": book.subtitle, "authors": f"{SEPARATOR} ".join(book.authors),
                  "publisher": edition.publisher, "imprint": edition.imprint, "date": edition.date,
                  "edition": edition.edition, "binding": edition.binding, "isbn_13": edition.isbn_13,
                  "isbn_10": edition.isbn_10, "pages": edition.pages, "printing": "", "printed": ""}
        for name, value in values.items():
            self.fields[name].setText(value)
        self.open_library = edition.open_library
        self.cover_url = edition.cover_url
        self.fields["location"].setFocus()
        self.fields["location"].selectAll()

    def book(self) -> Book:
        """Return the book the fields describe."""
        text = {name: edit.text().strip() for name, edit in self.fields.items()}
        authors = [n.strip() for n in text["authors"].split(SEPARATOR) if n.strip()]
        copy = Copy(location=text["location"], printing=text["printing"], printed=text["printed"])
        edition = Edition(publisher=text["publisher"], imprint=text["imprint"], date=text["date"],
                          edition=text["edition"], binding=text["binding"], isbn_13=text["isbn_13"],
                          isbn_10=text["isbn_10"], pages=text["pages"], open_library=self.open_library, copies=[copy],
                          cover_url=self.cover_url)
        return Book(title=text["title"], subtitle=text["subtitle"], authors=authors, language=text["language"],
                    editions=[edition])

    def ask(self, question: str, answers: dict[int, typing.Callable[[], None]]):
        """Ask something, on keys that a scanner doesn't send: what's typed
        stays, and Escape's no answer."""
        self.answers = answers
        self.question.setText(question)
        self.question.show()
        self.match.clear()
        self.setFocus()

    def stop_asking(self):
        self.answers = {}
        self.question.hide()

    @property
    def asking(self) -> bool:
        return bool(self.answers)

    def answer(self, key: int):
        """Take an answer: one that isn't one does nothing."""
        action = self.answers.get(key)
        if action is not None:
            self.stop_asking()
            action()

    def show_match(self, kind: str, title: str):
        self.match.setText(MATCHES[kind].format(title=title))

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None:
            return
        if self.asking:
            if a0.key() == Key.Key_Escape:
                self.stop_asking()
                self.fields["location"].setFocus()
            else:
                self.answer(a0.key())
        elif a0.key() == Key.Key_Escape:
            if self.query.text() or self.fields["title"].text():
                self.start()
            else:
                self.host.stop_adding()
        else:
            super().keyPressEvent(a0)
