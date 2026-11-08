# darqos
# Copyright (C) 2026 David Arnold

# The Book lens's form: a book's fields, each saved to its own level,
# which nobody needs to think about (see darq.book.save).

import typing

from PyQt6 import QtCore
from PyQt6.QtCore import QStringListModel
from PyQt6.QtWidgets import (
    QCompleter,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import darq.book
import darq.kb as model
from darq.book import Book, Edition
from darq.ui import style

import book_card


# What separates names, in a field that has several.
SEPARATOR = ";"

# Pixels wide that a field's label is, so that the levels' fields line up.
LABEL_WIDTH = 110

# Each level's fields: their attribute, and label.
WORK_FIELDS = [("title", "Title"), ("subtitle", "Subtitle"), ("authors", "Authors"), ("series", "Series"),
               ("volume", "Volume")]
TEXT_FIELDS = [("text_title", "Title"), ("language", "Language"), ("translators", "Translators"),
               ("editors", "Editors"), ("started", "Started"), ("finished", "Finished")]
EDITION_FIELDS = [("publisher", "Publisher"), ("imprint", "Imprint"), ("date", "Date published"),
                  ("edition", "Edition"), ("binding", "Binding"), ("isbn_13", "ISBN-13"), ("isbn_10", "ISBN-10"),
                  ("pages", "Pages")]
COPY_FIELDS = [("location", "Shelved at"), ("printing", "Printing"), ("printed", "Date printed"),
               ("content", "Content")]


# Returns the names of entities of a kind that start with some words.
Names = typing.Callable[[str, str], list[str]]

# Fields whose names are completed, and the kinds of what they name.
COMPLETED = {
    "authors": darq.book.PERSON_TYPE,
    "translators": darq.book.PERSON_TYPE,
    "editors": darq.book.PERSON_TYPE,
    "publisher": darq.book.ORGANISATION_TYPE,
    "language": darq.book.LANGUAGE_KIND,
    "series": model.ENTITY_TYPE,
}

# Characters typed before names are offered.
COMPLETE_AFTER = 2


class NameCompleter(QCompleter):
    """Offers names that are here as one's typed: the last of several, in
    a field that has them, separated by semicolons."""

    def __init__(self, edit: QLineEdit, kind: str, names: Names):
        super().__init__(edit)

        # Its own, so that Python doesn't collect it from under Qt.
        self.offered = QStringListModel(self)
        self.setModel(self.offered)
        self.edit = edit
        self.kind = kind
        self.names = names
        self.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.setWidget(edit)
        edit.textEdited.connect(self.typed)
        self.activated.connect(self.chosen)

    def typed(self, text: str):
        word = text.rpartition(SEPARATOR)[2].strip()
        offered = [n for n in self.names(word, self.kind) if n.casefold() != word.casefold()] \
            if len(word) >= COMPLETE_AFTER else []
        popup = self.popup()
        if not offered:
            if popup is not None:
                popup.hide()
            return
        self.offered.setStringList(offered)
        self.setCompletionPrefix(word)
        self.complete()

    def chosen(self, name: str):
        head, separator, _ = self.edit.text().rpartition(SEPARATOR)
        self.edit.setText(f"{head}{separator} {name}" if separator else name)


def complete(edit: QLineEdit, attribute: str, names: Names | None):
    """Offer names in a field, if it's one that names things."""
    if names is not None and attribute in COMPLETED:
        NameCompleter(edit, COMPLETED[attribute], names)


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

        # Offers names as they're typed, if there's a way to.
        self.names: Names | None = None

        # The fields shown: what each is of, and its attribute.
        self.fields: list[tuple[typing.Any, str, QLineEdit]] = []

    def show_book(self, book: Book):
        """Show a book's fields: its links add to it."""
        self.book = book
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
            complete(edit, attribute, self.names)
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
        self.group(layout, "Work", self.book, WORK_FIELDS)
        self.group(layout, "Expression", self.book, TEXT_FIELDS)
        for n, edition in enumerate(self.book.editions):
            self.group(layout, f"Manifestation {n + 1}", edition, EDITION_FIELDS)
            for m, copy in enumerate(edition.copies):
                self.group(layout, f"Manifestation {n + 1}, Copy {m + 1}", copy, COPY_FIELDS)
        layout.addStretch(1)
        self.setWidget(content)
        if self.fields:
            self.fields[0][2].setFocus()

    def apply(self):
        """Put what's typed in the book."""
        for target, attribute, edit in self.fields:
            setattr(target, attribute, from_text(edit.text(), getattr(target, attribute)))


class Adder(typing.Protocol):
    """What the level form needs of the lens."""

    def look_up(self, text: str) -> None: ...

    def remove_level(self) -> None: ...

    def move_level(self) -> None: ...

    def choose_cover(self) -> None: ...


class LevelForm(QWidget):
    """The fields of one level, to add it, or edit or remove it: fill() and
    values() are what the lens has to do with it."""

    # The fields of each level, and which has a box to look an ISBN up in.
    FIELDS: typing.ClassVar[dict[str, list[tuple[str, str]]]] = {
        book_card.EXPRESSION: [("title", "Title"), ("language", "Language"), ("translators", "Translators"),
                               ("editors", "Editors")],
        book_card.MANIFESTATION: [f for f in EDITION_FIELDS],
        book_card.COPY: [f for f in COPY_FIELDS if f[0] != "content"],
    }

    # Those of an existing entity, as a Book has them.
    EDIT_FIELDS: typing.ClassVar[dict[str, list[tuple[str, str]]]] = {
        book_card.WORK: WORK_FIELDS,
        book_card.EXPRESSION: TEXT_FIELDS,
        book_card.MANIFESTATION: [*EDITION_FIELDS, ("cover", "Cover")],
        book_card.COPY: [f for f in COPY_FIELDS if f[0] != "content"],
    }

    def __init__(self, host: Adder, parent: QWidget | None = None):
        super().__init__(parent)
        self.host = host
        self.target: book_card.Add | book_card.Edit | None = None
        self.edits: dict[str, QLineEdit] = {}
        self.open_library = ""
        self.cover_url = ""
        self.names: Names | None = None

        self.outer = QVBoxLayout(self)
        self.heading = QLabel(self)
        self.heading.setStyleSheet(f"QLabel {{ color: {style.TEXT}; font-size: 20px; }}")
        self.outer.addWidget(self.heading)
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Scan or type an ISBN, and press Enter")
        self.query.returnPressed.connect(self.search)
        self.outer.addWidget(self.query)
        self.form_area = QWidget(self)
        self.outer.addWidget(self.form_area)
        self.cover_button = QPushButton("Cover from a File ...", self)
        self.cover_button.clicked.connect(lambda: self.host.choose_cover())
        self.outer.addWidget(self.cover_button)
        self.move_button = QPushButton("Move ...", self)
        self.move_button.clicked.connect(lambda: self.host.move_level())
        self.outer.addWidget(self.move_button)
        self.remove = QPushButton("Remove", self)
        self.remove.clicked.connect(lambda: self.host.remove_level())
        self.outer.addWidget(self.remove)
        self.outer.addStretch(1)

    def show_level(self, target: book_card.Add):
        """Start adding a level."""
        self.target = target
        self.heading.setText(f"Add {target.level}")
        self.open_library = ""
        self.cover_url = ""
        self.build(self.FIELDS[target.level], target.level == book_card.MANIFESTATION)
        for button in (self.cover_button, self.move_button, self.remove):
            button.hide()

    def show_edit(self, target: book_card.Edit, what: object):
        """Start editing a level's entity, as a card's read it."""
        self.target = target
        self.heading.setText(f"Edit {target.level}")
        self.open_library = getattr(what, "open_library", "")
        self.cover_url = ""
        self.build(self.EDIT_FIELDS[target.level], False)
        for attribute, edit in self.edits.items():
            edit.setText(as_text(getattr(what, attribute)))
        self.cover_button.setVisible(target.level == book_card.MANIFESTATION)
        self.move_button.setVisible(target.level != book_card.WORK)
        self.remove.show()

    def build(self, fields: list[tuple[str, str]], looks_up: bool):
        self.query.clear()
        self.query.setVisible(looks_up)
        area = QWidget(self)
        form = QFormLayout(area)
        self.edits = {}
        for attribute, label in fields:
            edit = QLineEdit(area)
            edit.setObjectName(attribute)
            caption = QLabel(label, area)
            caption.setMinimumWidth(LABEL_WIDTH)
            caption.setStyleSheet(f"QLabel {{ color: {style.TEXT_DIM}; }}")
            form.addRow(caption, edit)
            self.edits[attribute] = edit
            complete(edit, attribute, self.names)
        self.outer.replaceWidget(self.form_area, area)
        self.form_area.deleteLater()
        self.form_area = area
        if looks_up:
            self.query.setFocus()
        else:
            next(iter(self.edits.values())).setFocus()

    def search(self):
        text = self.query.text().strip()
        if text:
            self.host.look_up(text)

    def fill(self, edition: Edition):
        """Fill the fields in from a lookup, to be checked."""
        for attribute, edit in self.edits.items():
            edit.setText(str(getattr(edition, attribute)))
        self.open_library = edition.open_library
        self.cover_url = edition.cover_url

    def values(self) -> dict[str, str]:
        return {attribute: edit.text().strip() for attribute, edit in self.edits.items()}
