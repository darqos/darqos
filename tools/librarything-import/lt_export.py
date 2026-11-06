# darqos
# Copyright (C) 2026 David Arnold

"""Reading a LibraryThing JSON export (see
doc/librarything-import-tool.rst): one object, of books by their
identifiers.  It turns a record into a Record, of plain values, so that
what the export's quirks are stays here: it knows nothing of Darq.

Every string has its HTML entities resolved.  What can't be understood
is left out, and said so: the publication's text is always kept."""

import dataclasses
import html
import json
import re
import typing


# Roles, in the languages they're found in, of those that matter: others
# are contributors, with their role as it is.  An unknown role's that of
# an author.
AUTHOR = "author"
EDITOR = "editor"
TRANSLATOR = "translator"
ROLES = {
    "": AUTHOR,
    "author": AUTHOR,
    "autor": AUTHOR,
    "auteur": AUTHOR,
    "editor": EDITOR,
    "herausgeber": EDITOR,
    "translator": TRANSLATOR,
    "übersetzer": TRANSLATOR,
    "traducteur": TRANSLATOR,
}

# LibraryThing's formats that say something about a book's binding.
BINDINGS = {"Paperback": "paperback", "Hardcover": "hardcover"}

# A language that isn't one.
NOT_LANGUAGES = frozenset({"", "All"})

# "Publisher (1999), Edition: Reprint, 300 pages": the first of two
# forms, with the publisher's own brackets allowed for.
ONLINE = re.compile(r"^(?P<publisher>.+?)\s*\((?P<when>[^()]*\d{4}[^()]*)\)\s*(?:,\s*(?P<rest>.*))?$")
EDITION = re.compile(r"Edition:\s*(?P<edition>[^,]+)")

# "London : Picador, 1982.", or "London, Picador, cop. 1982": a library's
# form.  Without a colon, what's before the last comma is a place.
LIBRARY = re.compile(r"^(?:(?P<place>[^:]+):\s*)?(?P<publisher>[^:]+?),+\s*(?:cop\.?\s*|c\.?|©)?\d{4}\b.*$")

# A year, in a date, and a number, as of pages, in what might be "xii; 300".
YEAR = re.compile(r"\d{4}")
NUMBER = re.compile(r"\d+")
ACQUIRED = re.compile(r"\d{4}(-\d\d(-\d\d)?)?")

# Books in a series can have their volume after it.
VOLUME = re.compile(r"^(?P<series>.+?)\s*(?:;\s*|\()(?P<volume>\d[\d.]*)\)?$")


@dataclasses.dataclass
class Person:
    name: str
    role: str


@dataclasses.dataclass
class Record:
    """A book of an export.  Levels are its keys: its work's identifier,
    its text's language, and an edition's ISBN."""

    book_id: str
    work_code: str
    title: str
    subtitle: str = ""
    original_title: str = ""
    people: list[Person] = dataclasses.field(default_factory=list)
    series: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    ddc: str = ""
    lcc: str = ""
    subjects: list[str] = dataclasses.field(default_factory=list)
    language: str = ""
    publication: str = ""
    publisher: str = ""
    edition: str = ""
    year: str = ""
    binding: str = ""
    isbn_13: str = ""
    isbn_10: str = ""
    asin: str = ""
    lccn: str = ""
    pages: str = ""
    physical: str = ""
    copies: int = 1
    acquired: str = ""
    acquired_from: str = ""
    price: str = ""
    tags: list[str] = dataclasses.field(default_factory=list)


def clean(value: typing.Any) -> str:
    """Return a value as text, with its HTML entities resolved."""
    return html.unescape(value).strip() if isinstance(value, str) else ""


def strings(value: typing.Any) -> list[str]:
    """Return a list's strings, cleaned, each once."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(c for c in (clean(v) for v in value) if c))


def split_title(title: str) -> tuple[str, str]:
    """Return a title, and its subtitle, which is after a colon."""
    main, colon, rest = title.partition(":")
    return (main.strip(), rest.strip()) if colon and main.strip() and rest.strip() else (title, "")


def parse_publication(text: str) -> tuple[str, str]:
    """Return the publisher and edition a publication's text gives:
    either's empty if it can't be understood."""
    online = ONLINE.match(text)
    if online is not None:
        edition = EDITION.search(online["rest"] or "")
        return online["publisher"].strip(), edition["edition"].strip() if edition is not None else ""
    library = LIBRARY.match(text)
    if library is not None:
        publisher = library["publisher"]
        return (publisher if library["place"] else publisher.rpartition(",")[2]).strip(), ""

    # "Penguin UK": with no date, it's only a name.
    return ("", "") if NUMBER.search(text) else (text, "")


def parse_people(record: dict) -> list[Person]:
    people = []
    seen = set()
    for author in record.get("authors") or ():
        if not isinstance(author, dict):
            continue
        name = clean(author.get("fl"))
        role = clean(author.get("role"))
        if name and (name, role) not in seen:
            seen.add((name, role))
            people.append(Person(name, ROLES.get(role.casefold(), role)))
    return people


def parse_isbns(record: dict) -> tuple[str, str]:
    """Return the ISBN-13 and ISBN-10, from where they're found: a list,
    an object, or the one that was entered."""
    found = record.get("isbn")
    values = list(found.values()) if isinstance(found, dict) else found if isinstance(found, list) else []
    values = [*values, record.get("originalisbn")]
    isbns = [re.sub(r"[\s-]", "", clean(v)).upper() for v in values]
    return next((i for i in isbns if len(i) == 13 and i.isdigit()), ""), \
        next((i for i in isbns if re.fullmatch(r"\d{9}[\dX]", i)), "")


def parse_series(record: dict) -> list[tuple[str, str]]:
    found = []
    for name in strings(record.get("series")):
        numbered = VOLUME.match(name)
        found.append((numbered["series"], numbered["volume"]) if numbered is not None else (name, ""))
    return found


def parse_subjects(record: dict) -> list[str]:
    """Return subject headings, each as words joined: a list of lists, or
    an object of them."""
    found = record.get("subject")
    headings = found.values() if isinstance(found, dict) else found if isinstance(found, list) else ()
    joined = (" -- ".join(strings(heading)) for heading in headings)
    return list(dict.fromkeys(h for h in joined if h))


def code(record: dict, field: str) -> str:
    """Return the first of a classification's codes."""
    found = record.get(field)
    codes = found.get("code") if isinstance(found, dict) else None
    if isinstance(codes, list):
        codes = codes[0] if codes else ""
    return clean(codes)


def parse_binding(record: dict) -> str:
    for found in record.get("format") or ():
        if isinstance(found, dict) and clean(found.get("text")) in BINDINGS:
            return BINDINGS[clean(found["text"])]
    return ""


def parse_language(record: dict) -> str:
    languages = [v for v in strings(record.get("language")) if v not in NOT_LANGUAGES]
    return languages[0] if languages else ""


def parse_record(book_id: str, record: dict) -> Record:
    title, subtitle = split_title(clean(record.get("title")))
    publication = clean(record.get("publication"))
    publisher, edition = parse_publication(publication)
    isbn_13, isbn_10 = parse_isbns(record)
    year = YEAR.search(clean(record.get("date")))
    pages = NUMBER.findall(clean(record.get("pages")))
    copies = clean(record.get("copies"))
    acquired = clean(record.get("dateacquired"))
    return Record(
        book_id=book_id,
        work_code=clean(record.get("workcode")) or book_id,
        title=title,
        subtitle=subtitle,
        original_title=clean(record.get("originaltitle")),
        people=parse_people(record),
        series=parse_series(record),
        ddc=code(record, "ddc"),
        lcc=code(record, "lcc"),
        subjects=parse_subjects(record),
        language=parse_language(record),
        publication=publication,
        publisher=publisher,
        edition=edition,
        year=year.group() if year is not None else "",
        binding=parse_binding(record),
        isbn_13=isbn_13,
        isbn_10=isbn_10,
        asin=clean(record.get("asin")),
        lccn=clean(record.get("lccn")),
        pages=pages[-1] if pages else "",
        physical=clean(record.get("physical_description")),
        copies=max(int(copies), 1) if copies.isdigit() else 1,
        acquired=acquired if ACQUIRED.fullmatch(acquired) else "",
        acquired_from=clean(record.get("fromwhere")),
        price=clean(record.get("price")),
        tags=strings(record.get("tags")),
    )


def read_export(text: str) -> list[Record]:
    """Return an export's records.

    :raises ValueError: if it isn't one."""
    books = json.loads(text)
    if not isinstance(books, dict) or not all(isinstance(b, dict) and "books_id" in b for b in books.values()):
        raise ValueError("This isn't a LibraryThing JSON export")
    return [parse_record(book_id, record) for book_id, record in books.items()]
