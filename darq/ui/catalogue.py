# darqos
# Copyright (C) 2026 David Arnold

"""Cataloguing an item as a book, from the lens that's showing it (see
doc/book.rst).

Being catalogued is what makes a PDF a book, so it's what a lens looks
for when it opens one, and what B does.  Its details start a card,
which is asked about if it might be one that's here."""

import dataclasses
import logging
import typing

from PyQt6.QtWidgets import QInputDialog, QWidget

import darq
import darq.book
import darq.kb as model
import darq.launch
from darq.book import ANOTHER_COPY, ANOTHER_EDITION, Book, BookError, Copy, Edition, Match
from darq.protocols.http import Http
from darq.ui import book_store
from darq.ui.launcher import Launcher


# Control exports
__all__ = ["Details", "Services", "add_content", "catalogue", "describe", "find_copy"]


# Seconds to wait for the KB, and for a fetch.
TIMEOUT = 5.0
FETCH_TIMEOUT = 600


@dataclasses.dataclass
class Details:
    """What an item says of itself, as far as it does."""

    title: str = ""
    authors: list[str] = dataclasses.field(default_factory=list)
    isbn: str = ""
    publisher: str = ""
    date: str = ""
    language: str = ""

    # The image item of its cover, if it has one.
    cover: str = ""


def find_copy(content: str, reader: typing.Any = None) -> str:
    """Return the item of the copy an item is, or an empty string if it
    isn't catalogued, or the KB isn't there: it's only shown the
    plain way then.

    :param reader: A client of the KB service, if there is one."""
    try:
        if reader is None:
            if darq.lookup_name("darq.kb", blocking=False) is None:
                return ""
            reader = model.KB.connect(TIMEOUT)
        # What links to it, or, from before every item was an entity, what
        # has its identifier.
        found = sorted(reader.find_links(darq.split_item_id(content)[1], darq.book.CONTENT)) or \
            sorted(reader.find_by_value(darq.book.OLD_CONTENT, content))
    except (darq.DarqError, TimeoutError, ValueError) as e:
        logging.info(f"Can't look for {content} in the catalogue: {e!r}")
        return ""
    return model.item_id(found[0], darq.book.ITEM_TYPE) if found else ""


def describe(match: Match) -> str:
    """Return what adding to a match would be, in words."""
    title = match.book.title if match.book is not None else ""
    if match.kind == ANOTHER_COPY:
        return f"Another copy of {title}"
    if match.kind == ANOTHER_EDITION:
        return f"Another format of {title}"
    return "A new book"


def add_content(store: darq.book.Store, content: str, details: Details,
                choose: typing.Callable[[Match], bool]) -> str:
    """Catalogue an item as the content of a copy.

    :param choose: Asked, if it might be a book that's here, whether it is.
    :returns: The item of the book's work.
    :raises BookError: if it can't be done."""
    edition = Edition(publisher=details.publisher, date=details.date,
                      binding=darq.book.content_format(Copy(content=content)), copies=[Copy(content=content)],
                      cover=details.cover)
    if len(details.isbn) == 13:
        edition.isbn_13 = details.isbn
    elif details.isbn:
        edition.isbn_10 = details.isbn
    book = Book(title=details.title, authors=details.authors, language=details.language, editions=[edition])
    match = darq.book.find_match(store, book)
    if match.kind != darq.book.NEW and not choose(match):
        match = Match()
    saved = darq.book.add(store, book, match)
    if saved.work is None:
        raise BookError("It wasn't saved")
    return model.item_id(saved.work.id, darq.book.WORK_TYPE)


class Services:
    """What the Launcher needs from a reading lens."""

    def __init__(self):
        self.launch = darq.launch

    @staticmethod
    def http() -> Http.Client | None:
        handle = darq.lookup_name("darq.http", blocking=False)
        return Http.Client(handle, timeout=FETCH_TIMEOUT) if handle is not None else None


def catalogue(window: QWidget, launcher: Launcher, content: str, details: Details, source: str) -> str:
    """B, in a reading lens: show an item's book, if it's been catalogued,
    or catalogue it, asking what's needed, and show that.

    :param source: The lens, as History's source of what's made.
    :returns: What went wrong, if anything: an empty string if it worked."""
    copy = find_copy(content)
    if copy:
        launcher.use(copy)
        return ""
    if not details.title:
        details.title, ok = QInputDialog.getText(window, "Catalogue", "Title:")
        details.title = details.title.strip()
        if not ok or not details.title:
            return "Not catalogued"

    def choose(match: Match) -> bool:
        choices = [describe(match), "A new book"]
        chosen, ok = QInputDialog.getItem(window, "Catalogue", "This is:", choices, 0, False)
        return ok and chosen == choices[0]

    store = book_store.ItemStore([darq.book.OWN_LANGUAGE], source=source)
    try:
        work = add_content(store, content, details, choose)
    except BookError as e:
        logging.error(f"Can't catalogue {content}: {e}")
        return str(e)
    finally:
        store.close()
    launcher.use(work)
    return ""
