# darqos
# Copyright (C) 2026 David Arnold

"""Looking books up on Open Library (see doc/book.rst).

It turns what comes back into a Book, of the fields the card has, and
knows nothing of how it's fetched, so it's tested alone.  It's only used
when someone asks, as it sends what they typed to a third party, and
it's one book at a time: Open Library's APIs aren't for bulk use, at a
request a second, or three for an application that gives its contact.

An ISBN uses the Books API's data form, as the one request that has
authors' names, with publishers and identifiers.  Open Library calls
that endpoint legacy, so it's kept in this module: Search is the
successor, but has the work, not the edition.

What comes back isn't taken on trust: a junk ISBN can find a stray
record, so a record has to carry the ISBN that was asked for."""

import dataclasses
import json
import re
import typing
import urllib.parse

from . import book as books
from .book import Book, Copy, Edition


# Control exports
__all__ = [
    "BASE",
    "Fetch",
    "LookupError",
    "isbn_url",
    "lookup",
    "parse_date",
    "parse_isbn",
    "parse_search",
    "search_url",
]


BASE = "https://openlibrary.org"

# Most books to offer for a title.
MAX_RESULTS = 8

# Search's fields that are used.
SEARCH_FIELDS = "key,title,subtitle,author_name"

MONTHS = {name: n + 1 for n, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}

# Open Library's dates are free text: "1982", "March 1982", "Jan 5, 2004"
# and "5 January 2004".
YEAR = re.compile(r"\b(\d{4})\b")
MONTH = re.compile(r"\b([A-Za-z]{3})[a-z]*\b")
DAY = re.compile(r"(?<![\d])(\d{1,2})(?![\d])")

# Fetches a URL, returning its body.
Fetch = typing.Callable[[str], bytes]


class LookupError(Exception):
    """A lookup failed, rather than finding nothing."""


@dataclasses.dataclass
class Results:
    """What a lookup found: books, with an edition, and the first's the
    one for an ISBN."""

    isbn: str = ""
    books: list[Book] = dataclasses.field(default_factory=list)


def isbn_url(isbn: str, base: str = BASE) -> str:
    return f"{base}/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"


def search_url(text: str, base: str = BASE) -> str:
    query = urllib.parse.urlencode({"q": text, "fields": SEARCH_FIELDS, "limit": MAX_RESULTS})
    return f"{base}/search.json?{query}"


def parse_date(text: str) -> str:
    """Return a date as a card's typed, to what it says, or an empty
    string."""
    year = YEAR.search(text)
    if year is None:
        return ""
    rest = text[:year.start()] + " " + text[year.end():]
    month = next((MONTHS[m.lower()] for m in MONTH.findall(rest) if m.lower() in MONTHS), None)
    if month is None:
        return year[1]
    day = DAY.search(rest)
    return f"{year[1]}-{month:02d}-{int(day[1]):02d}" if day is not None else f"{year[1]}-{month:02d}"


def names(values: typing.Any) -> list[str]:
    """Return the names of a list of {"name": ...}, each once: an author
    can be listed under two records."""
    found = (v.get("name") for v in values if isinstance(v, dict)) if isinstance(values, list) else ()
    return list(dict.fromkeys(n.strip() for n in found if isinstance(n, str) and n.strip()))


def first(values: typing.Any) -> str:
    if isinstance(values, list) and values and isinstance(values[0], str):
        return values[0].strip()
    return ""


def load(body: bytes) -> typing.Any:
    try:
        return json.loads(body)
    except ValueError as e:
        raise LookupError(f"Open Library's answer can't be read: {e}") from None


def parse_isbn(body: bytes, isbn: str) -> Book | None:
    """Return the book an answer about an ISBN gives, or None, if it has
    none, or it's not for that ISBN."""
    answer = load(body)
    record = answer.get(f"ISBN:{isbn}") if isinstance(answer, dict) else None
    if not isinstance(record, dict) or not isinstance(record.get("title"), str):
        return None

    identifiers = record.get("identifiers")
    if not isinstance(identifiers, dict):
        return None
    isbn_13 = first(identifiers.get("isbn_13"))
    isbn_10 = first(identifiers.get("isbn_10"))
    wanted = {isbn, books.isbn_13_of(isbn), books.isbn_10_of(isbn)} - {""}
    if not wanted & {*_strings(identifiers.get("isbn_13")), *_strings(identifiers.get("isbn_10"))}:
        return None

    pages = record.get("number_of_pages")
    publishers = names(record.get("publishers"))
    edition = Edition(publisher=publishers[0] if publishers else "",
                      date=parse_date(str(record.get("publish_date", ""))),
                      isbn_13=isbn_13 or books.isbn_13_of(isbn), isbn_10=isbn_10 or books.isbn_10_of(isbn),
                      pages=str(pages) if isinstance(pages, int) and pages > 0 else "",
                      open_library=first(identifiers.get("openlibrary")), copies=[Copy()],
                      cover_url=cover_url(record.get("cover")))
    subtitle = record.get("subtitle")
    return Book(title=record["title"].strip(), subtitle=subtitle.strip() if isinstance(subtitle, str) else "",
                authors=names(record.get("authors")), editions=[edition])


def cover_url(covers: typing.Any) -> str:
    """Return the URL of the largest of a record's covers, or an empty
    string: it's only an https URL of Open Library's that's taken."""
    if not isinstance(covers, dict):
        return ""
    for size in ("large", "medium", "small"):
        url = covers.get(size)
        if isinstance(url, str) and url.startswith("https://covers.openlibrary.org/"):
            return url
    return ""


def _strings(values: typing.Any) -> list[str]:
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def parse_search(body: bytes) -> list[Book]:
    """Return the books an answer to a search gives: only the work's
    details, as Search doesn't know which edition is meant."""
    answer = load(body)
    docs = answer.get("docs") if isinstance(answer, dict) else None
    found = []
    for doc in docs if isinstance(docs, list) else ():
        if isinstance(doc, dict) and isinstance(doc.get("title"), str):
            subtitle = doc.get("subtitle")
            found.append(Book(title=doc["title"].strip(),
                              subtitle=subtitle.strip() if isinstance(subtitle, str) else "",
                              authors=list(dict.fromkeys(_strings(doc.get("author_name")))),
                              editions=[Edition(copies=[Copy()])]))
    return found


def lookup(text: str, fetch: Fetch, base: str = BASE) -> Results:
    """Look up an ISBN, or else a title and whatever else was typed.

    :param fetch: Fetches a URL.
    :raises LookupError: if it can't be done, or what's typed is an ISBN
                         with a mistake in it."""
    text = text.strip()
    digits = re.sub(r"[\s-]", "", text).upper()
    if re.fullmatch(r"\d{9}[\dX]|\d{13}", digits):
        isbn = books.clean_isbn(digits)
        if not isbn:
            raise LookupError(f"{text} isn't a valid ISBN: check its digits")
        found = parse_isbn(fetch(isbn_url(isbn, base)), isbn)
        return Results(isbn, [found] if found is not None else [])
    return Results("", parse_search(fetch(search_url(text, base)))[:MAX_RESULTS])
