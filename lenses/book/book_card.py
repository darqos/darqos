# darqos
# Copyright (C) 2026 David Arnold

# Rendering for the Book lens: a book, as HTML for a QTextBrowser.
#
# It uses no Qt, so it can be tested without a display.  Every value is
# escaped.  As in the entity lens, a link is an anchor, "#" and its
# number in the page's targets, so nothing is parsed out of URLs.  A
# target is an item to open: a person, say, or something to read.

import dataclasses
import html
import typing

import darq.book
from darq.book import Book, Copy, Edition, Text
from darq.ui import style


# Pixels each level's indented under the one above: a work's at the edge,
# with its texts, their editions, and their copies stepping in.
INDENT = 28

STYLE = f"""
body {{ color: {style.TEXT}; font-size: 15px; }}
a {{ color: {style.LINK}; text-decoration: none; }}
h1 {{ font-size: 26px; font-weight: normal; margin-bottom: 0; }}
h2 {{ font-size: 18px; font-weight: normal; margin-top: 2px; color: {style.TEXT_SOFT}; }}
p {{ margin-top: 0; margin-bottom: 2px; }}
.subtitle {{ color: {style.TEXT_SOFT}; }}
.detail {{ color: {style.TEXT_DIM}; font-size: 13px; }}
.text {{ margin-left: {INDENT}px; }}
.edition {{ margin-left: {2 * INDENT}px; }}
.copy {{ margin-left: {3 * INDENT}px; margin-top: 2px; }}
.opened {{ background-color: {style.RAISED}; }}
.add a, .edit a {{ color: {style.TEXT_DIM}; font-size: 13px; }}
.add {{ margin-top: 4px; }}
"""

# A faint line between entities: a QTextBrowser's rule takes the widget's
# text colour, which the lens sets, as the page's own text says its colour.
LINE = "<hr>"

# The levels.
WORK = "Work"
EXPRESSION = "Expression"
MANIFESTATION = "Manifestation"
COPY = "Copy"

# Their types.
TYPES = {WORK: darq.book.WORK_TYPE, EXPRESSION: darq.book.EXPRESSION_TYPE,
         MANIFESTATION: darq.book.MANIFESTATION_TYPE, COPY: darq.book.ITEM_TYPE}


def acquisition(copy: Copy) -> str:
    """Return how a copy was acquired, in words: "acquired 2020-03-04, from
    A shop, for $9.95"."""
    parts = [f"acquired {copy.acquired}" if copy.acquired else "acquired" if copy.acquired_from or copy.price else ""]
    if copy.acquired_from:
        parts.append(f"from {copy.acquired_from}")
    if copy.price:
        parts.append(f"for {copy.price}")
    return ", ".join(p for p in parts if p)


@dataclasses.dataclass(frozen=True)
class Add:
    """A link to add a level, to an entity of the one above."""

    level: str
    parent: typing.Any


@dataclasses.dataclass(frozen=True)
class Edit:
    """A link to edit a level's entity, or remove it."""

    level: str
    entity: typing.Any


@dataclasses.dataclass(frozen=True)
class Prefer:
    """A link to make a copy the one to read, of a book's."""

    copy: typing.Any


# What a page's link can lead to: an item, adding something, editing it,
# or preferring a copy.
Target = str | Add | Edit | Prefer

# Pixels high that a cover's shown.
COVER_HEIGHT = 120

# What a copy on paper that's in no known place says.
NO_LOCATION = "Location unknown"


def cover_url(item_id: str) -> str:
    """Return what a cover's called on the page."""
    return "cover:" + html.escape(item_id.replace(":", "/"), quote=True)


class Page:
    """A book's page: its HTML, and the items its links lead to, by
    number.  Lines separate its entities, and each level's indented: a
    work, its texts, their editions, and their copies.  The text it's
    about comes first, and the others are shown too, so that a translation
    can be seen for what it is, with a link to open it."""

    def __init__(self, book: Book):
        self.book = book
        self.targets: list[Target] = []

        # The covers it shows, which the lens has to give the page.
        self.covers: list[str] = []

        # Whether it has more than one copy to read, when one's preferred.
        editions = [*book.editions, *(e for text in book.texts for e in text.editions)]
        self.choosing = sum(1 for e in editions for c in e.copies if c.content) > 1

    def anchor(self, target: Target, text: str) -> str:
        """Return an anchor for a target, or just the text, without one."""
        if not target:
            return html.escape(text)
        self.targets.append(target)
        return f'<a href="#{len(self.targets) - 1}">{html.escape(text)}</a>'

    def names(self, names: list[str]) -> str:
        """Return names, as links."""
        return ", ".join(self.anchor(self.book.links.get(name, ""), name) for name in names)

    def opened(self, entity: object) -> str:
        """Return a class, if the book was opened on an entity."""
        return " opened" if self.book.opened == getattr(entity, "id", None) else ""

    @staticmethod
    def about_edition(edition: Edition) -> str:
        """Return an edition, in words: its format, for one that's an item
        with no binding given, as that's what it is."""
        binding = edition.binding or next((darq.book.content_format(c) for c in edition.copies if c.content), "")
        parts = [edition.publisher, edition.imprint, binding, edition.edition and f"{edition.edition} edition",
                 edition.date[:4]]
        return html.escape(", ".join(p for p in parts if p)) or html.escape(darq.book.EDITION_LABEL)

    @staticmethod
    def details(edition: Edition) -> str:
        parts = []
        if edition.pages:
            parts.append(f"{edition.pages} pages")
        if edition.isbn_13 or edition.isbn_10:
            parts.append(f"ISBN {edition.isbn_13 or edition.isbn_10}")
        if edition.lccn:
            parts.append(f"LCCN {edition.lccn}")
        return html.escape(" · ".join(parts))

    def about_copy(self, copy: Copy) -> str:
        """Return a copy, in words: Read, for one that's an item, whose
        edition says what format it is."""
        parts = [self.anchor(copy.content, "Read")] if copy.content else []
        if copy.content and self.choosing:
            parts.append("★ the one to read" if copy.preferred
                         else self.anchor(Prefer(copy), "Make it the one to read"))
        if copy.location or not copy.content:
            parts.append(html.escape(copy.location or NO_LOCATION))
        if copy.printing:
            printing = f"printing {copy.printing}"
            parts.append(html.escape(f"{printing}, {copy.printed}" if copy.printed else printing))
        elif copy.printed:
            parts.append(html.escape(f"printed {copy.printed}"))
        acquired = acquisition(copy)
        if acquired:
            parts.append(html.escape(acquired))
        return " · ".join(parts)

    def edition(self, edition: Edition) -> str:
        """Return an edition, with its copies under it."""
        edit = self.edit_link(MANIFESTATION, edition.entity)
        parts = [f'<p class="edition{self.opened(edition.entity)}">{self.about_edition(edition)}{edit}</p>']
        details = self.details(edition)
        if details:
            parts.append(f'<p class="edition detail">{details}</p>')
        if edition.cover:
            self.covers.append(edition.cover)
            parts.append(f'<p class="edition"><img src="{cover_url(edition.cover)}" height="{COVER_HEIGHT}"></p>')
        for copy in edition.copies:
            edit = self.edit_link(COPY, copy.entity)
            parts.append(f'<p class="copy{self.opened(copy.entity)}">{self.about_copy(copy)}{edit}</p>')
            if copy.notes:
                parts.append(f'<p class="copy detail">{html.escape(copy.notes)}</p>')
        if not edition.copies:
            parts.append('<p class="copy detail">No copies</p>')

        # A digital copy comes from its item: see doc/book.rst.
        if edition.entity is not None and not any(c.content for c in edition.copies):
            parts.append(self.add_link("copy", COPY, edition.entity))
        return "".join(parts)

    def edit_link(self, level: str, entity: object) -> str:
        """Return a link to edit an entity, beside what's said of it."""
        if entity is None:
            return ""
        return f' <span class="edit">{self.anchor(Edit(level, entity), "Edit")}</span>'

    def add_link(self, indent: str, level: str, parent: object) -> str:
        return f'<p class="{indent} add">{self.anchor(Add(level, parent), f"+ Add {level}")}</p>'

    def work(self) -> str:
        book = self.book
        parts = [f"<h1>{html.escape(book.title or 'Untitled')}</h1>"]
        if book.subtitle:
            parts.append(f'<p class="subtitle">{html.escape(book.subtitle)}</p>')
        if book.authors:
            parts.append(f"<h2>{self.names(book.authors)}</h2>")
        if book.work is not None:
            parts.append(f"<p>{self.edit_link(WORK, book.work)}</p>")
        if book.series:
            volume = f" {html.escape(book.volume)}" if book.volume else ""
            parts.append(f'<p class="detail">{self.names([book.series])}{volume}</p>')
        classes = [f"Dewey {book.dewey}" if book.dewey else "", f"LC {book.lc_class}" if book.lc_class else ""]
        if any(classes):
            parts.append(f'<p class="detail">{html.escape(" · ".join(c for c in classes if c))}</p>')
        return "".join(parts)

    def text(self, text: Text, link: bool = False) -> str:
        """Return a text.

        :param link: Whether it needs to be shown, with a link to open it."""
        about = []
        if link and text.entity is not None:
            label = text.entity.get_label() or darq.book.TEXT_LABEL
            about.append(self.anchor(darq.book.item_of(text.entity), label))
        elif text.title:
            about.append(html.escape(text.title))
        if text.language and (not link or text.title):
            about.append(self.names([text.language]))
        if text.translators:
            about.append(f"translated by {self.names(text.translators)}")
        if text.editors:
            about.append(f"edited by {self.names(text.editors)}")
        for name, role in text.contributors:
            words = f"{html.escape(role)}: " if role else "with "
            about.append(f"{words}{self.names([name])}")
        if not about:
            about.append(f'<span class="detail">{html.escape(darq.book.TEXT_LABEL)}</span>')
        edit = self.edit_link(EXPRESSION, text.entity)
        parts = [f'<p class="text{self.opened(text.entity)}">{" · ".join(about)}{edit}</p>']

        reading = []
        if text.started:
            reading.append(f"Started {html.escape(text.started)}")
        if text.finished:
            reading.append(f"Finished {html.escape(text.finished)}")
        if reading:
            parts.append(f'<p class="text">{" · ".join(reading)}</p>')
        return "".join(parts)

    def render(self) -> str:
        book = self.book
        main = Text(book.expression, book.language, book.translators, book.editors, book.contributors, book.started,
                    book.finished, book.editions, book.text_title)
        sections = [self.work()]
        for text, link in ((main, False), *((other, True) for other in book.texts)):
            if text.entity is None and not text.editions:
                continue
            sections.append(self.text(text, link))
            sections += [self.edition(e) for e in text.editions]

            # It's no entity, so no line sets it apart.
            if text.entity is not None:
                sections[-1] += self.add_link("edition", MANIFESTATION, text.entity)
        if book.work is not None:
            sections.append(self.add_link("text", EXPRESSION, book.work))
        page = LINE.join(section for section in sections if section)
        return f"<html><head><style>{STYLE}</style></head><body>{page}</body></html>"
