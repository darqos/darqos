# darqos
# Copyright (C) 2026 David Arnold

# The Book lens's form: a book's fields, each saved to its own level,
# which nobody needs to think about (see darq.book.save).

import typing

from PyQt6.QtWidgets import QFormLayout, QFrame, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from darq.book import Book, Copy, Edition
from darq.ui import style


# What separates names, in a field that has several.
SEPARATOR = ";"

# Pixels wide that a field's label is, so that the levels' fields line up.
LABEL_WIDTH = 96

# Each level's fields: their attribute, and label.
BOOK_FIELDS = [("title", "Title"), ("subtitle", "Subtitle"), ("authors", "Authors"), ("series", "Series"),
               ("volume", "Volume"), ("language", "Language"), ("translators", "Translators"),
               ("editors", "Editors"), ("started", "Started"), ("finished", "Finished")]
EDITION_FIELDS = [("publisher", "Publisher"), ("imprint", "Imprint"), ("date", "Date"), ("edition", "Edition"),
                  ("binding", "Binding"), ("isbn_13", "ISBN-13"), ("isbn_10", "ISBN-10"), ("pages", "Pages")]
COPY_FIELDS = [("location", "Location"), ("printing", "Printing"), ("printed", "Printed"), ("file", "File")]


def as_text(value: str | list[str]) -> str:
    return f"{SEPARATOR} ".join(value) if isinstance(value, list) else value


def from_text(text: str, was: str | list[str]) -> str | list[str]:
    """Return a field's value, as what it was: a list is of names."""
    if isinstance(was, list):
        return [name.strip() for name in text.split(SEPARATOR) if name.strip()]
    return text.strip()


class BookForm(QScrollArea):
    """A form on a book: apply() puts what's typed in the book."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"QScrollArea {{ background-color: {style.SURFACE}; }}")
        self.book = Book()

        # The fields shown: what each is of, and its attribute.
        self.fields: list[tuple[typing.Any, str, QLineEdit]] = []

    def show_book(self, book: Book):
        """Show a book's fields, with an empty edition, if it hasn't one,
        since most books have just the one."""
        self.book = book
        if not book.editions:
            book.editions.append(Edition(copies=[Copy()]))
        self.build()

    def group(self, layout: QVBoxLayout, title: str, target: object,
              fields: list[tuple[str, str]]) -> QFormLayout:
        heading = QLabel(title, self)
        heading.setStyleSheet(f"QLabel {{ color: {style.TEXT}; font-size: 15px; margin-top: 12px; }}")
        layout.addWidget(heading)
        form = QFormLayout()
        for attribute, label in fields:
            edit = QLineEdit(as_text(getattr(target, attribute)), self)
            edit.setObjectName(attribute)
            caption = QLabel(label, self)
            caption.setMinimumWidth(LABEL_WIDTH)
            form.addRow(caption, edit)
            self.fields.append((target, attribute, edit))
        layout.addLayout(form)
        return form

    def build(self):
        self.fields = []
        content = QWidget(self)
        content.setObjectName("content")
        content.setStyleSheet(f"QWidget#content {{ background-color: {style.SURFACE}; }}"
                              f"QLabel {{ color: {style.TEXT_DIM}; }}")
        layout = QVBoxLayout(content)
        self.group(layout, "Book", self.book, BOOK_FIELDS)
        for n, edition in enumerate(self.book.editions):
            self.group(layout, f"Edition {n + 1}", edition, EDITION_FIELDS)
            for m, copy in enumerate(edition.copies):
                self.group(layout, f"Edition {n + 1}, copy {m + 1}", copy, COPY_FIELDS)
            add_copy = QPushButton("Add Copy", self)
            add_copy.clicked.connect(lambda _=False, e=edition: self.add_copy(e))
            layout.addWidget(add_copy)
        add_edition = QPushButton("Add Edition", self)
        add_edition.clicked.connect(self.add_edition)
        layout.addWidget(add_edition)
        layout.addStretch(1)
        self.setWidget(content)
        if self.fields:
            self.fields[0][2].setFocus()

    def apply(self):
        """Put what's typed in the book."""
        for target, attribute, edit in self.fields:
            setattr(target, attribute, from_text(edit.text(), getattr(target, attribute)))

    def add_copy(self, edition: Edition):
        self.apply()
        edition.copies.append(Copy())
        self.build()

    def add_edition(self):
        self.apply()
        self.book.editions.append(Edition(copies=[Copy()]))
        self.build()
