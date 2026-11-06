# darqos
# Copyright (C) 2026 David Arnold

# Rendering for the Book lens: a book, as HTML for a QTextBrowser.
#
# It uses no Qt, so it can be tested without a display.  Every value is
# escaped.  As in the entity lens, a link is an anchor, "#" and its
# number in the page's targets, so nothing is parsed out of URLs.  A
# target is an item to open: a person, say, or a file to read.

import html

import darq.book
from darq.book import Book, Copy, Edition
from darq.ui import style


STYLE = f"""
body {{ color: {style.TEXT}; font-size: 15px; }}
a {{ color: {style.LINK}; text-decoration: none; }}
h1 {{ font-size: 26px; font-weight: normal; margin-bottom: 0; }}
h2 {{ font-size: 18px; font-weight: normal; margin-top: 2px; color: {style.TEXT_SOFT}; }}
h3 {{ color: {style.TEXT_SOFT}; font-size: 15px; margin-top: 18px; margin-bottom: 4px; }}
td {{ padding-right: 24px; padding-bottom: 2px; }}
.subtitle {{ color: {style.TEXT_SOFT}; }}
.detail {{ color: {style.TEXT_DIM}; font-size: 13px; }}
.opened {{ background-color: {style.RAISED}; }}
"""

# What a copy on paper that's in no known place says.
NO_LOCATION = "Location unknown"


class Page:
    """A book's page: its HTML, and the items its links lead to, by
    number."""

    def __init__(self, book: Book):
        self.book = book
        self.targets: list[str] = []

    def anchor(self, item_id: str, text: str) -> str:
        """Return an anchor for an item, or just the text, without one."""
        if not item_id:
            return html.escape(text)
        self.targets.append(item_id)
        return f'<a href="#{len(self.targets) - 1}">{html.escape(text)}</a>'

    def names(self, names: list[str]) -> str:
        """Return names, as links."""
        return ", ".join(self.anchor(self.book.links.get(name, ""), name) for name in names)

    def opened(self, *entities: object) -> str:
        """Return a row's attribute, if the book was opened on it."""
        ids = (getattr(e, "id", None) for e in entities)
        return ' class="opened"' if self.book.opened in ids else ""

    @staticmethod
    def about_edition(edition: Edition, shown: str = "") -> str:
        """Return an edition, in words, leaving out what's shown already."""
        binding = "" if edition.binding.casefold() == shown.casefold() else edition.binding
        parts = [edition.publisher, edition.imprint, binding, edition.edition and f"{edition.edition} edition",
                 edition.date[:4]]
        return html.escape(", ".join(p for p in parts if p))

    @staticmethod
    def details(edition: Edition) -> str:
        parts = []
        if edition.pages:
            parts.append(f"{edition.pages} pages")
        if edition.isbn_13 or edition.isbn_10:
            parts.append(f"ISBN {edition.isbn_13 or edition.isbn_10}")
        return html.escape(" · ".join(parts))

    @staticmethod
    def about_copy(copy: Copy) -> str:
        """Return a paper copy, in words."""
        parts = [copy.location or NO_LOCATION]
        if copy.printing:
            printing = f"printing {copy.printing}"
            parts.append(f"{printing}, {copy.printed}" if copy.printed else printing)
        elif copy.printed:
            parts.append(f"printed {copy.printed}")
        return html.escape(" · ".join(parts))

    def formats(self) -> str:
        """Return the rows of the copies that are files."""
        rows = []
        for edition in self.book.editions:
            for copy in edition.copies:
                if copy.file:
                    name = html.escape(darq.book.file_format(copy))
                    rows.append(f"<tr{self.opened(edition.entity, copy.entity)}><td>{name}</td>"
                                f"<td>{self.about_edition(edition, name)}</td>"
                                f"<td>{self.anchor(copy.file, 'Read')}</td></tr>")
        return self.section("Formats", rows)

    def paper(self) -> str:
        """Return the rows of editions, with their copies on paper."""
        rows = []
        for edition in self.book.editions:
            copies = [c for c in edition.copies if not c.file]
            if not copies and any(c.file for c in edition.copies):
                continue
            first = (f"{self.about_edition(edition)}"
                     f'<br><span class="detail">{self.details(edition)}</span>')
            if not copies:
                rows.append(f"<tr{self.opened(edition.entity)}><td>{first}</td>"
                            '<td><span class="detail">No copies</span></td></tr>')
            for n, copy in enumerate(copies):
                rows.append(f"<tr{self.opened(edition.entity, copy.entity)}><td>{first if n == 0 else ''}</td>"
                            f"<td>{self.about_copy(copy)}</td></tr>")
        return self.section("Paper", rows)

    @staticmethod
    def section(title: str, rows: list[str]) -> str:
        return f"<h3>{title}</h3><table>{''.join(rows)}</table>" if rows else ""

    def render(self) -> str:
        book = self.book
        parts = [f"<html><head><style>{STYLE}</style></head><body>"]
        parts.append(f"<h1>{html.escape(book.title or 'Untitled')}</h1>")
        if book.subtitle:
            parts.append(f'<p class="subtitle">{html.escape(book.subtitle)}</p>')
        if book.authors:
            parts.append(f"<h2>{self.names(book.authors)}</h2>")

        about = []
        if book.series:
            volume = f" {html.escape(book.volume)}" if book.volume else ""
            about.append(f"{self.names([book.series])}{volume}")
        if book.language:
            about.append(self.names([book.language]))
        if book.translators:
            about.append(f"translated by {self.names(book.translators)}")
        if book.editors:
            about.append(f"edited by {self.names(book.editors)}")
        for name, role in book.contributors:
            words = f"{html.escape(role)}: " if role else "with "
            about.append(f"{words}{self.names([name])}")
        if about:
            parts.append(f'<p class="detail">{" · ".join(about)}</p>')

        parts.append(self.formats())
        parts.append(self.paper())

        reading = []
        if book.started:
            reading.append(f"Started {html.escape(book.started)}")
        if book.finished:
            reading.append(f"Finished {html.escape(book.finished)}")
        if reading:
            parts.append(f'<h3>Reading</h3><p>{" · ".join(reading)}</p>')

        if book.others:
            others = ", ".join(self.anchor(darq.book.item_of(e), e.get_label() or e.id) for e in book.others)
            parts.append(f"<h3>Other texts</h3><p>{others}</p>")
        if not book.editions and book.work is not None:
            parts.append('<p class="detail">No editions yet: E adds one.</p>')
        parts.append("</body></html>")
        return "\n".join(parts)
