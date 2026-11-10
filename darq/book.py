# darqos
# Copyright (C) 2026 David Arnold

"""Books, as Knowledge Base entities (see doc/book.rst).

A book is four levels of entities, of the kinds darq.kb has: a work,
its expressions, their manifestations, and their copies, which are the
LRM's items.  This is their vocabulary, which is the same in every
knowledge base, and what makes them one book: a Book is read from any of
them, shown as one, and written back with each field at its own level.
It knows nothing of how entities are reached: see Store.

Properties from Wikidata are those with a clean match, which is most of
them.  The others are local properties, with fixed UUIDs."""

import dataclasses
import re
import typing

from . import identifier
from . import kb as model
from .kb import Datatype, Entity, EntityValue, Item, Property, QuantityValue, StringValue, TextValue, TimeValue


# Control exports
__all__ = [
    "AUTHOR",
    "BINDING",
    "Book",
    "BookError",
    "CONTRIBUTOR",
    "CONTENT",
    "OLD_CONTENT",
    "OLD_COVER",
    "Copy",
    "DDC",
    "EDITION",
    "EDITION_LABEL",
    "EDITOR",
    "EXEMPLAR_OF",
    "EXPRESSION_TYPE",
    "Edition",
    "FINISHED",
    "INVERSE_LABELS",
    "ACQUIRED",
    "ACQUIRED_FROM",
    "ASIN",
    "CONTRIBUTOR_ROLE",
    "IMPRINT",
    "ISBN_10",
    "ISBN_13",
    "ITEM_TYPE",
    "LANGUAGE",
    "LANGUAGE_KIND",
    "LCC",
    "LCCN",
    "LIBRARYTHING",
    "LIBRARYTHING_BOOK",
    "LIBRARYTHING_WORK",
    "LOCATION",
    "MANIFESTATION_TYPE",
    "OWN_LANGUAGE",
    "PAGES",
    "PHYSICAL_DESCRIPTION",
    "PRICE",
    "PUBLICATION_TEXT",
    "RELABELLED",
    "SUBJECT_HEADING",
    "TAG",
    "OPEN_LIBRARY",
    "ORGANISATION_TYPE",
    "PARTS",
    "PERSON_TYPE",
    "PRINTED",
    "PRINTING",
    "PUBLICATION_DATE",
    "PUBLISHER",
    "SERIES",
    "STARTED",
    "SUBTITLE",
    "Store",
    "TEXT_LABEL",
    "TEXT_TITLE",
    "TRANSLATOR",
    "Text",
    "VERSION_OF",
    "WORK_TYPE",
    "NEW",
    "ANOTHER_COPY",
    "ANOTHER_EDITION",
    "Match",
    "add",
    "add_copy",
    "COVER",
    "HAS_CHARACTERISTIC",
    "PREFERENCE",
    "move",
    "prefer",
    "preferred_content",
    "set_cover",
    "add_edition",
    "add_text",
    "date_of",
    "language_name",
    "check",
    "clean_isbn",
    "content_format",
    "edition_label",
    "expression_label",
    "find_match",
    "find_isbn",
    "isbn_10_of",
    "isbn_13_of",
    "item_of",
    "read",
    "remove",
    "save",
    "vocabulary",
]


# A book's kinds.  A new book is a work: what's under it is made as it's
# needed, so the others aren't offered as new items.
WORK_TYPE = "darq.book.work"
EXPRESSION_TYPE = "darq.book.expression"
MANIFESTATION_TYPE = "darq.book.manifestation"
ITEM_TYPE = "darq.book.item"
PARTS = frozenset({EXPRESSION_TYPE, MANIFESTATION_TYPE, ITEM_TYPE})

# The kinds of what a book's names refer to.  A language is looked for
# among Wikidata's, by its name or code, and then among those made here.
PERSON_TYPE = "darq.person"
ORGANISATION_TYPE = "darq.organisation"
LANGUAGE_KIND = "language"

# What ties the levels together: an expression to its work, and a
# manifestation to its expression, are both Wikidata's version, edition
# or translation of; a copy to its manifestation, exemplar of.
VERSION_OF = "P629"
EXEMPLAR_OF = "P1574"

# On a work: its subtitle (a Text), authors, and series, with the volume
# as a series ordinal qualifier, its classifications, its subject
# headings, as text, and LibraryThing's identifier.
SUBTITLE = "P1680"
AUTHOR = "P50"
SERIES = "P179"
DDC = "P1036"
LCC = "P1149"
SUBJECT_HEADING = "8ca9ae0a-6fa2-449c-aa74-722e4b89e1d9"
LIBRARYTHING_WORK = "P1085"

# On an expression: its title, if it has its own, as a translation does
# (a Text), its language, translators and editors, others who
# contributed, with their role as text (a qualifier), and when it was
# started and finished, which are knowledge, unlike a place in a file
# (see doc/book.rst).
TEXT_TITLE = "P1476"
LANGUAGE = "P407"
TRANSLATOR = "P655"
EDITOR = "P98"
CONTRIBUTOR = "P767"
CONTRIBUTOR_ROLE = "7b770f8c-30a5-4f86-87cb-f489e90c50a2"
STARTED = "854cec02-7950-476e-ae4b-56c0ced34157"
FINISHED = "af1551ce-d8bc-40c0-bae6-c407bf124da2"

# On a manifestation: its publisher, date, edition, ISBNs and pages, and
# what form it takes, as text: paperback, hardcover, and so on, and the
# publisher's imprint, as text.  Other identifiers, its physical
# description, and what a source said of its publication, as it said it.
PUBLISHER = "P123"
PUBLICATION_DATE = "P577"  # Messages have it in their vocabulary.
EDITION = "P393"
ISBN_13 = "P212"
ISBN_10 = "P957"
PAGES = "P1104"
ASIN = "P5749"
OPEN_LIBRARY = "P648"
LCCN = "P1144"
PHYSICAL_DESCRIPTION = "8299e292-2411-498c-b34d-5536ba8b93b2"
PUBLICATION_TEXT = "6cf68a4e-7257-43af-ad50-497c6e94833f"
COVER = "5d15b0b4-c1ea-4451-a55b-eab35224899f"
IMPRINT = "2f20d2aa-65a0-4486-86c4-56facb477bf6"
BINDING = "2eabea67-5f8d-4d35-aec1-878da88f077d"

# On a copy: where it is, as text for now, or the item that's its content,
# a link to its entity, as every item's an entity, and its printing, and
# when it was printed, and where it came from: when, from where, and for
# how much, as text.  Its identifier and tags on LibraryThing.
LOCATION = "2cf1b758-7379-418f-9ea6-049d74518bbe"
CONTENT = "5a75f2fa-b42e-4da9-9987-f1dddbe7671f"
PRINTING = "20450dc5-a708-4ca1-8729-3db3e8d90836"
PRINTED = "6e9db545-11e2-452b-a2ef-97ef7da9b182"

ACQUIRED = "3206ba38-9a22-4396-8c1e-391cd34033bd"
ACQUIRED_FROM = "946e5920-9ab3-4348-827b-72145068153d"
PRICE = "3fd7d449-6e7a-4e27-9070-289ad1edbd15"
LIBRARYTHING_BOOK = "3467b6af-1d3a-4773-8b01-4ae3c208cbc5"
TAG = "8b300fc8-4c53-4805-9390-63864dfdafe2"

# A copy's content, and an edition's cover, as they were, by an item's
# identifier, a String, before every item was an entity: kept for what
# can't be linked, as an item that's gone (see doc/entity-vs-item.rst).
OLD_CONTENT = "0cd7fdd5-f6c8-4346-91b8-b5d674244ea4"
OLD_COVER = "c7ef4020-e876-4650-80b5-616cd386b717"

# Labels of the vocabulary that have changed, with what they were: what's
# there's left alone by ensure, so an existing KB is told.
RELABELLED = [(OLD_CONTENT, "file", "content (identifier, old)"),
              (OLD_CONTENT, "content", "content (identifier, old)"),
              (OLD_COVER, "cover", "cover (identifier, old)")]

# LibraryThing, as a source.
LIBRARYTHING = "7659380e-57eb-4c0e-aba6-644f197824a7"

# A copy that's the one to read, of a book's: has characteristic (P1552),
# preference (Q908656).
HAS_CHARACTERISTIC = "P1552"
PREFERENCE = "Q908656"

# Items with labels that read these properties backwards (see
# darq.kb.INVERSE_LABEL): each property, its item, and the item's label.
INVERSE_LABELS = [
    (VERSION_OF, "8fe38994-d1de-4940-ae2e-7748e1410708", "has version"),
    (EXEMPLAR_OF, "e2997042-874d-4ea1-84a5-82cf5516c9da", "has copy"),
    (AUTHOR, "014bae63-c863-4fa3-b41f-e487989d409d", "author of"),
    (TRANSLATOR, "2fca48d4-7992-4a5d-8511-a65c0391ad96", "translator of"),
    (EDITOR, "35b8561e-d042-45d1-bd1c-3d2b602b63ad", "editor of"),
    (PUBLISHER, "b0790383-2b4a-402d-9937-e5cf37932076", "publisher of"),
    (CONTRIBUTOR, "b8874f9d-1089-48bd-9232-472086c67a9c", "contributor to"),
    (SERIES, "4493219b-a0f1-433b-869a-4133b2495471", "has part"),
    (CONTENT, "521b74f9-0acd-489c-8bdd-251a2da7aa8b", "content of"),
    (COVER, "0d815ad0-dc98-441a-bb62-dd3984dbde7a", "cover of"),
]

# Formats, by the type of a copy's content.
CONTENT_FORMATS = {
    "com.adobe.pdf": "PDF",
    "darq.book.epub": "EPUB",
    "darq.book.mobi": "MOBI",
    "darq.book.azw": "AZW",
}

# What a date can be typed as: a year, a month, or a day.
DATE = re.compile(r"\d{1,4}(-\d\d(-\d\d)?)?")

# Labels of levels that have no better one.
TEXT_LABEL = "Text"
EDITION_LABEL = "Edition"
COPY_LABEL = "Copy"

# The language of labels the card sets.
OWN_LANGUAGE = "en"


class BookError(Exception):
    """A book can't be read or saved."""


class Store(typing.Protocol):
    """How a book reaches its entities: through items' ports, in a lens."""

    def get(self, item_id: str) -> Entity | None:
        """Return an item's entity, or None."""
        ...

    def children(self, item_id: str, property_id: str, type_id: str) -> list[Entity]:
        """Return the entities of a kind that link to an item by a
        property."""
        ...

    def label(self, entity_id: str) -> str:
        """Return the label of an entity that's been referred to, or an
        empty string."""
        ...

    def item_of(self, entity_id: str) -> str:
        """Return the item identifier of an entity that's been referred
        to, or an empty string."""
        ...

    def find(self, label: str, type_id: str) -> str:
        """Return the identifier of an entity of a kind with exactly this
        label, or an empty string."""
        ...

    def edition_with(self, property_id: str, isbn: str) -> str:
        """Return the item of an edition with an ISBN, or an empty string."""
        ...

    def works_titled(self, title: str) -> list[str]:
        """Return the items of the works with a title."""
        ...

    def create(self, type_id: str, label: str) -> Entity:
        """Make an entity of a kind, with a label, and return it.

        :raises BookError: if it can't be done."""
        ...

    def change(self, entity: Entity, edits: list[model.Edit]) -> Entity:
        """Make edits to an entity, and return it as it now is.

        :raises BookError: if it can't be done."""
        ...

    def destroy(self, item_id: str) -> None:
        """Destroy an item.

        :raises BookError: if it can't be done."""
        ...


@dataclasses.dataclass
class Copy:
    """A copy, on a shelf, or as an item: entity is None until it's saved."""

    entity: Entity | None = None
    location: str = ""
    content: str = ""
    printing: str = ""
    printed: str = ""

    # Whether it's to be made with nothing known: a book you have, in no
    # particular place yet.
    wanted: bool = False

    # Whether it's the one to read, of a book's formats.
    preferred: bool = False


@dataclasses.dataclass
class Edition:
    """A manifestation, with its copies: entity is None until it's saved."""

    entity: Entity | None = None
    publisher: str = ""
    imprint: str = ""
    date: str = ""
    edition: str = ""
    binding: str = ""
    isbn_13: str = ""
    isbn_10: str = ""
    pages: str = ""
    open_library: str = ""
    copies: list[Copy] = dataclasses.field(default_factory=list)

    # The item of its cover, if it has one, and where one can be fetched
    # from, which isn't kept.
    cover: str = ""
    cover_url: str = ""


@dataclasses.dataclass
class Text:
    """An expression, as the card shows it."""

    entity: Entity | None = None
    language: str = ""
    translators: list[str] = dataclasses.field(default_factory=list)
    editors: list[str] = dataclasses.field(default_factory=list)
    contributors: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    started: str = ""
    finished: str = ""
    editions: list[Edition] = dataclasses.field(default_factory=list)
    title: str = ""


@dataclasses.dataclass
class Book:
    """A book, as the card shows it: the fields can be changed, and saved
    with save().  They're of one expression, which is the one it was opened
    on, or its work's first: others is the rest, and texts shows them, to
    be opened and changed on their own."""

    work: Entity | None = None
    expression: Entity | None = None
    title: str = ""
    subtitle: str = ""
    authors: list[str] = dataclasses.field(default_factory=list)
    series: str = ""
    volume: str = ""
    text_title: str = ""
    language: str = ""
    translators: list[str] = dataclasses.field(default_factory=list)
    editors: list[str] = dataclasses.field(default_factory=list)

    # Others who contributed, shown but not edited: name, and role.
    contributors: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    started: str = ""
    finished: str = ""
    editions: list[Edition] = dataclasses.field(default_factory=list)
    others: list[Entity] = dataclasses.field(default_factory=list)
    texts: list[Text] = dataclasses.field(default_factory=list)

    # The entity it was opened on.
    opened: str = ""

    # The items of the names it refers to, by name.
    links: dict[str, str] = dataclasses.field(default_factory=dict)


# Reading

def _statements(entity: Entity | None, property_id: str) -> list[model.Statement]:
    """Return an entity's statements that aren't deprecated, in order."""
    if entity is None:
        return []
    return [s for s in model.in_order(entity.get_statements(property_id)) if s.rank != model.Rank.Deprecated]


def _text(entity: Entity | None, property_id: str) -> str:
    """Return the first value of a property, as text."""
    for statement in _statements(entity, property_id):
        return _as_text(statement.get_value())
    return ""


def _as_text(value: model.Value) -> str:
    if isinstance(value, StringValue):
        return value.value
    if isinstance(value, TextValue):
        return next(iter(value.text.values()), "")
    if isinstance(value, QuantityValue):
        return str(value.amount)
    if isinstance(value, TimeValue):
        return _date_text(value)
    return ""


def _date_text(value: TimeValue) -> str:
    """Return a time, to its precision, as it's typed."""
    if value.precision.value <= model.TimePrecision.Years.value or value.month is None:
        return f"{value.year:04d}"
    if value.precision.value == model.TimePrecision.Months.value or value.day is None:
        return f"{value.year:04d}-{value.month:02d}"
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


def _names(store: Store, entity: Entity | None, property_id: str,
           links: dict[str, str] | None = None) -> list[str]:
    """Return the labels of the entities a property refers to, adding
    their items to links, if it's given."""
    names = []
    for statement in _statements(entity, property_id):
        value = statement.get_value()
        if isinstance(value, EntityValue):
            name = store.label(value.entity_id) or value.entity_id
            names.append(name)
            if links is not None:
                links[name] = store.item_of(value.entity_id)
    return names


def _parent(store: Store, entity: Entity, property_id: str, type_id: str) -> Entity | None:
    """Return the entity of a kind an entity links to by a property."""
    for statement in _statements(entity, property_id):
        value = statement.get_value()
        if isinstance(value, EntityValue):
            return store.get(model.item_id(value.entity_id, type_id))
    return None


def _preferred(entity: Entity) -> bool:
    return any(isinstance(s.get_value(), EntityValue) and typing.cast(EntityValue, s.get_value()).entity_id
               == PREFERENCE for s in _statements(entity, HAS_CHARACTERISTIC))


def _item(store: Store, entity: Entity | None, property_id: str, old: str) -> str:
    """Return the item a property links to, by its identifier, or else its
    old String's, from before every item was an entity, or empty."""
    for statement in _statements(entity, property_id) if entity is not None else []:
        value = statement.get_value()
        if isinstance(value, EntityValue):
            item_id = store.item_of(value.entity_id)
            if item_id:
                return item_id
    return _text(entity, old)


def _read_copy(store: Store, entity: Entity) -> Copy:
    return Copy(entity, _text(entity, LOCATION), _item(store, entity, CONTENT, OLD_CONTENT), _text(entity, PRINTING),
                _text(entity, PRINTED), preferred=_preferred(entity))


def _read_edition(store: Store, entity: Entity, links: dict[str, str]) -> Edition:
    publisher = _names(store, entity, PUBLISHER, links)
    copies = store.children(model.item_id(entity.id, MANIFESTATION_TYPE), EXEMPLAR_OF, ITEM_TYPE)
    return Edition(entity, publisher[0] if publisher else "", _text(entity, IMPRINT),
                   _text(entity, PUBLICATION_DATE), _text(entity, EDITION),
                   _text(entity, BINDING), _text(entity, ISBN_13), _text(entity, ISBN_10), _text(entity, PAGES),
                   _text(entity, OPEN_LIBRARY), [_read_copy(store, c) for c in copies],
                   _item(store, entity, COVER, OLD_COVER))


def _edition_order(edition: Edition) -> tuple[bool, str]:
    """Return what orders editions: by date, with undated ones last.  A
    link's order means nothing."""
    return not edition.date, edition.date


def _read_text(store: Store, expression: Entity, links: dict[str, str]) -> Text:
    """Read an expression, and its editions."""
    text = Text(expression)
    text.title = _text(expression, TEXT_TITLE)
    text.language = next(iter(_names(store, expression, LANGUAGE, links)), "")
    text.translators = _names(store, expression, TRANSLATOR, links)
    text.editors = _names(store, expression, EDITOR, links)
    for statement, name in zip(_statements(expression, CONTRIBUTOR), _names(store, expression, CONTRIBUTOR, links)):
        role = next((q.value.value for q in statement.qualifiers if q.property_id == CONTRIBUTOR_ROLE
                     and isinstance(q.value, StringValue)), "")
        text.contributors.append((name, role))
    text.started = _text(expression, STARTED)
    text.finished = _text(expression, FINISHED)
    editions = store.children(model.item_id(expression.id, EXPRESSION_TYPE), VERSION_OF, MANIFESTATION_TYPE)
    text.editions = sorted((_read_edition(store, e, links) for e in editions), key=_edition_order)
    return text


def read(store: Store, item_id: str) -> Book:
    """Read the book an item of any of its levels is part of.

    :raises BookError: if the item can't be read."""
    type_id, _ = identifier.split_item_id(item_id)
    entity = store.get(item_id)
    if entity is None:
        raise BookError(f"Can't read {item_id}")

    manifestation = expression = work = None
    if type_id == ITEM_TYPE:
        manifestation = _parent(store, entity, EXEMPLAR_OF, MANIFESTATION_TYPE)
    elif type_id == MANIFESTATION_TYPE:
        manifestation = entity
    if manifestation is not None:
        expression = _parent(store, manifestation, VERSION_OF, EXPRESSION_TYPE)
    elif type_id == EXPRESSION_TYPE:
        expression = entity
    if expression is not None:
        work = _parent(store, expression, VERSION_OF, WORK_TYPE)
    elif type_id == WORK_TYPE:
        work = entity

    book = Book(work=work, opened=entity.id)
    expressions = []
    if work is not None:
        expressions = store.children(model.item_id(work.id, WORK_TYPE), VERSION_OF, EXPRESSION_TYPE)
        book.title = work.get_label() or ""
        book.subtitle = _text(work, SUBTITLE)
        book.authors = _names(store, work, AUTHOR, book.links)
        series = _statements(work, SERIES)
        if series:
            book.series = next(iter(_names(store, work, SERIES, book.links)), "")
            book.volume = next((q.value.value for q in series[0].qualifiers if q.property_id == model.SERIES_ORDINAL
                                and isinstance(q.value, StringValue)), "")
    if expression is None and expressions:
        expression = expressions[0]
    book.expression = expression
    book.others = [e for e in expressions if expression is None or e.id != expression.id]
    book.texts = [_read_text(store, e, book.links) for e in book.others]
    if expression is not None:
        text = _read_text(store, expression, book.links)
        book.text_title = text.title
        book.language, book.translators, book.editors = text.language, text.translators, text.editors
        book.contributors, book.started, book.finished = text.contributors, text.started, text.finished
        book.editions = text.editions
    elif manifestation is not None:
        book.editions = [_read_edition(store, manifestation, book.links)]
    return book


def item_of(expression: Entity) -> str:
    """Return the item identifier of an expression."""
    return model.item_id(expression.id, EXPRESSION_TYPE)


def content_format(copy: Copy) -> str:
    """Return the name of the format of a copy's content, or an empty string
    if it hasn't one."""
    if not copy.content:
        return ""
    type_id = copy.content.partition(":")[0]
    return CONTENT_FORMATS.get(type_id, type_id)


# ISBNs

def _digit(value: int) -> str:
    return "X" if value == 10 else str(value)


def _check_10(digits: str) -> str:
    return _digit((11 - sum((10 - n) * int(d) for n, d in enumerate(digits[:9])) % 11) % 11)


def _check_13(digits: str) -> str:
    return str((10 - sum((3 if n % 2 else 1) * int(d) for n, d in enumerate(digits[:12])) % 10) % 10)


def clean_isbn(text: str) -> str:
    """Return an ISBN, without spaces and hyphens, or an empty string if
    it isn't a valid one.  One digit repeated passes its check, but is a
    placeholder, and can find a stray record."""
    isbn = re.sub(r"[\s-]", "", text).upper()
    if len(set(isbn)) < 2:
        return ""
    if re.fullmatch(r"\d{9}[\dX]", isbn):
        return isbn if isbn[-1] == _check_10(isbn) else ""
    if re.fullmatch(r"97[89]\d{10}", isbn):
        return isbn if isbn[-1] == _check_13(isbn) else ""
    return ""


def isbn_13_of(isbn: str) -> str:
    """Return the ISBN-13 of an ISBN."""
    if len(isbn) == 13:
        return isbn
    return "978" + isbn[:9] + _check_13("978" + isbn[:9]) if len(isbn) == 10 else ""


def isbn_10_of(isbn: str) -> str:
    """Return the ISBN-10 of an ISBN, or an empty string: only 978's have
    one."""
    if len(isbn) == 10:
        return isbn
    return isbn[3:12] + _check_10(isbn[3:12]) if isbn.startswith("978") and len(isbn) == 13 else ""


# Languages, by the codes files give them in, for those likely here.
LANGUAGES = {
    "ar": "Arabic", "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "ga": "Irish", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "la": "Latin", "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "pt": "Portuguese", "ru": "Russian", "sv": "Swedish", "tr": "Turkish", "uk": "Ukrainian", "zh": "Chinese",
}


def language_name(code: str) -> str:
    """Return the name of a language, by a code such as en-AU or the
    English it says itself, or an empty string for one that isn't known."""
    text = code.strip()
    if text.title() in LANGUAGES.values():
        return text.title()
    return LANGUAGES.get(re.split(r"[-_]", text.lower())[0], "")


def date_of(text: str) -> str:
    """Return a file's date as a card's typed, or an empty string."""
    found = re.match(r"\d{4}(-\d\d(-\d\d)?)?", text.strip())
    return found.group() if found is not None and _valid_date(found.group()) else ""


def find_isbn(text: str) -> str:
    """Return the first valid ISBN in some text, such as a title page,
    or an empty string: those labelled as one come first."""
    labelled = re.findall(r"ISBN(?:[-\s]1[03])?[:\s]*([\dXx][\dXx\s-]{8,20})", text)
    for candidate in (*labelled, *re.findall(r"\b97[89][\d\s-]{10,16}\b", text)):
        isbn = clean_isbn(candidate.strip())
        if isbn:
            return isbn
    return ""


# Adding

# What a book being added is to what's here.
NEW = "new"
ANOTHER_EDITION = "edition"
ANOTHER_COPY = "copy"


@dataclasses.dataclass
class Match:
    """What a book being added is: book is the one that's here, if it's
    of one, and edition which of its editions, if it's a copy of one."""

    kind: str = NEW
    book: Book | None = None
    edition: Edition | None = None

    def language_differs(self, book: Book) -> bool:
        """Return whether a book being added isn't in this one's language,
        so it's a text of its own, which isn't for guessing."""
        if self.book is None or not book.language.strip() or not self.book.language.strip():
            return False
        return book.language.strip().casefold() != self.book.language.strip().casefold()


def _same_people(first: list[str], second: list[str]) -> bool:
    """Return whether two lists of names overlap, or either's empty."""
    return not first or not second or bool({n.casefold() for n in first} & {n.casefold() for n in second})


def find_match(store: Store, book: Book) -> Match:
    """Return what a book being added is, by ISBN, or by title and author:
    formats often have ISBNs of their own, and older books have none."""
    edition = book.editions[0] if book.editions else Edition()
    isbns = [(ISBN_13, edition.isbn_13 or isbn_13_of(edition.isbn_10)),
             (ISBN_10, edition.isbn_10 or isbn_10_of(edition.isbn_13))]
    for property_id, isbn in isbns:
        found = store.edition_with(property_id, isbn) if isbn else ""
        if found:
            here = read(store, found)
            mine = identifier.split_item_id(found)[1]
            target = next((e for e in here.editions if e.entity is not None and e.entity.id == mine), None)
            return Match(ANOTHER_COPY, here, target)

    title = book.title.strip()
    for item_id in store.works_titled(title) if title else ():
        here = read(store, item_id)
        if here.title.casefold() == title.casefold() and _same_people(here.authors, book.authors):
            return Match(ANOTHER_EDITION, here)
    return Match()


def add(store: Store, book: Book, match: Match) -> Book:
    """Save a book being added, as a match says, and return it as saved.
    The first of its editions and copies is what's added: what's here
    otherwise stays as it is.

    :raises BookError: if it can't be done."""
    edition = book.editions[0] if book.editions else Edition(copies=[Copy()])
    copies = [c for c in edition.copies if c.location.strip() or c.printing.strip() or c.printed.strip()
              or c.content.strip()] or [Copy(wanted=True)]
    if match.kind == ANOTHER_COPY and match.book is not None and match.edition is not None:
        match.edition.copies.extend(copies)
        target = match.book
    elif match.kind == ANOTHER_EDITION and match.book is not None:
        edition.copies = copies
        match.book.editions.append(edition)
        target = match.book
    else:
        edition.copies = copies
        target = book
        target.editions = [edition]
    save(store, target)
    return target


# Saving

def check(book: Book) -> str:
    """Return what's wrong with a book's fields, or an empty string."""
    if not book.title.strip():
        return "A book needs a title"
    dated = [("started", book.started), ("finished", book.finished)]
    for edition in book.editions:
        dated.append(("date", edition.date))
        dated += [("printed", copy.printed) for copy in edition.copies]
        if edition.pages.strip() and not edition.pages.strip().isdigit():
            return f"Pages should be a number, not {edition.pages!r}"
    for name, text in dated:
        text = text.strip()
        if text and (DATE.fullmatch(text) is None or not _valid_date(text)):
            return f"The {name} date should be a year, YYYY-MM or YYYY-MM-DD, not {text!r}"
    return ""


def _valid_date(text: str) -> bool:
    try:
        TimeValue.parse(text)
    except ValueError:
        return False
    return True


def _value_edits(entity: Entity, property_id: str, value: model.Value | None) -> list[model.Edit]:
    """Return the edits that make a value a property's only one that
    counts: the first statement is changed, or a statement added.  Any
    others are left, as this only knows of the first."""
    statements = _statements(entity, property_id)
    if statements:
        if value is None:
            return model.remove_statement(entity, statements[0].id)
        return model.change_value(entity, statements[0].id, value, model.ENTITY_EDITOR)
    if value is None:
        return []
    return model.add_statement(entity, property_id, value, model.ENTITY_EDITOR)


def _link_edits(entity: Entity, property_id: str, old: str, item_id: str) -> list[model.Edit]:
    """Return the edits that make a property link to an item, by its
    identifier, or to none, if it's empty, and take away the old String
    of it, from before every item was an entity.

    :raises BookError: if it isn't an item's identifier."""
    value = None
    if item_id.strip():
        try:
            value = EntityValue(identifier.split_item_id(item_id.strip())[1])
        except ValueError:
            raise BookError(f"{item_id.strip()} isn't an item") from None
    edits = []
    for statement in _statements(entity, old):
        edits += model.remove_statement(entity, statement.id)
    return edits + _value_edits(entity, property_id, value)


def _string(text: str) -> model.Value | None:
    return StringValue(text.strip()) if text.strip() else None


def _time(text: str) -> model.Value | None:
    return TimeValue.parse(text.strip()) if text.strip() else None


def _quantity(text: str) -> model.Value | None:
    return QuantityValue(int(text)) if text.strip() else None


def _subtitle(text: str) -> model.Value | None:
    return TextValue(text.strip()) if text.strip() else None


def _name_id(store: Store, name: str, type_id: str) -> str:
    """Return the entity with a name, or make one: a language that isn't
    one of Wikidata's is an entity like any other."""
    made = model.ENTITY_TYPE if type_id == LANGUAGE_KIND else type_id
    return store.find(name, type_id) or store.create(made, name).id


def _names_edits(store: Store, entity: Entity, property_id: str, names: list[str], type_id: str) -> list[model.Edit]:
    """Return the edits that make a property's values the entities with
    some names, in order of their first appearance: those that are there
    are kept, whichever entity they are, so two people with one name
    aren't confused, and only a new name is looked up."""
    wanted = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    edits: list[model.Edit] = []
    have: list[str] = []
    for statement in _statements(entity, property_id):
        value = statement.get_value()
        if not isinstance(value, EntityValue):
            continue
        name = store.label(value.entity_id) or value.entity_id
        if name in wanted:
            have.append(name)
        else:
            edits += model.remove_statement(entity, statement.id)
    for name in wanted:
        if name not in have:
            value = EntityValue(_name_id(store, name, type_id))
            edits += model.add_statement(entity, property_id, value, model.ENTITY_EDITOR)
    return edits


def _label_edits(entity: Entity, label: str) -> list[model.Edit]:
    old = entity.get_label()
    return [] if old == label else [model.SetText("labels", OWN_LANGUAGE, old, label)]


def _series_edits(store: Store, work: Entity, book: Book) -> list[model.Edit]:
    """Return the edits for a series, and its volume, which go together."""
    name = book.series.strip()
    volume = book.volume.strip()
    statements = _statements(work, SERIES)
    now = next(iter(_names(store, work, SERIES)), "")
    numbered = next((q.value.value for s in statements[:1] for q in s.qualifiers
                     if q.property_id == model.SERIES_ORDINAL and isinstance(q.value, StringValue)), "")
    if (name, volume) == (now, numbered):
        return []
    edits: list[model.Edit] = []
    if statements:
        edits += model.remove_statement(work, statements[0].id)
    if name:
        target = _statements(work, SERIES)[0].get_value() if statements and name == now else None
        value = target if isinstance(target, EntityValue) else EntityValue(_name_id(store, name, model.ENTITY_TYPE))
        added = model.add_statement(work, SERIES, value, model.ENTITY_EDITOR)
        if volume:
            typing.cast(model.AddStatement, added[0]).statement.add_qualifier(model.SERIES_ORDINAL, StringValue(volume))
        edits += added
    return edits


def edition_label(edition: Edition) -> str:
    """Return the label of an edition: a title isn't in it, so that
    Search finds a book once."""
    year = edition.date.strip()[:4]
    parts = (edition.publisher.strip(), edition.imprint.strip(), edition.binding.strip(), year)
    return ", ".join(p for p in parts if p) or EDITION_LABEL


def _save_copy(store: Store, copy: Copy, manifestation: Entity):
    label = copy.location.strip() or COPY_LABEL
    if copy.entity is None:
        if not (copy.wanted or copy.location.strip() or copy.content.strip() or copy.printing.strip()
                or copy.printed.strip()):
            return
        copy.entity = store.create(ITEM_TYPE, label)
        link = model.add_statement(copy.entity, EXEMPLAR_OF, EntityValue(manifestation.id), model.ENTITY_EDITOR)
    else:
        link = []
    entity = copy.entity
    copy.entity = store.change(entity, [
        *link,
        *_label_edits(entity, label),
        *_value_edits(entity, LOCATION, _string(copy.location)),
        *_link_edits(entity, CONTENT, OLD_CONTENT, copy.content),
        *_value_edits(entity, PRINTING, _string(copy.printing)),
        *_value_edits(entity, PRINTED, _time(copy.printed)),
        *_preference_edits(entity, copy.preferred),
    ])


def _preference_edits(entity: Entity, preferred: bool) -> list[model.Edit]:
    """Return the edits that make a copy preferred, or not, leaving any
    other characteristic it has."""
    have = [s for s in _statements(entity, HAS_CHARACTERISTIC) if isinstance(s.get_value(), EntityValue)
            and typing.cast(EntityValue, s.get_value()).entity_id == PREFERENCE]
    if preferred and not have:
        return model.add_statement(entity, HAS_CHARACTERISTIC, EntityValue(PREFERENCE), model.ENTITY_EDITOR)
    if not preferred and have:
        return [e for s in have for e in model.remove_statement(entity, s.id)]
    return []


def _all_copies(book: Book) -> list[Copy]:
    """Return every copy of a book, of every text."""
    editions = [*book.editions, *(e for text in book.texts for e in text.editions)]
    return [c for e in editions for c in e.copies]


def preferred_content(book: Book) -> str:
    """Return the item to read of a book: the preferred copy's, or the
    first, or an empty string if there's none."""
    copies = [c for c in _all_copies(book) if c.content]
    return next((c.content for c in copies if c.preferred), copies[0].content if copies else "")


def prefer(store: Store, book: Book, chosen: Copy) -> None:
    """Make a copy the one to read, of all of a book's.

    :raises BookError: if it can't be done."""
    for copy in _all_copies(book):
        if copy.entity is not None and copy.preferred != (copy is chosen):
            copy.entity = store.change(copy.entity, _preference_edits(copy.entity, copy is chosen))
            copy.preferred = copy is chosen


def set_cover(store: Store, manifestation: Entity, item_id: str) -> Entity:
    """Give a manifestation a cover.

    :raises BookError: if it can't be done."""
    return store.change(manifestation, _link_edits(manifestation, COVER, OLD_COVER, item_id))


def move(store: Store, type_id: str, entity: Entity, parent: Entity) -> None:
    """Move a level to another parent: an expression to another work, a
    manifestation to another expression, or a copy to another manifestation.

    :raises BookError: if it can't be done."""
    property_id = EXEMPLAR_OF if type_id == ITEM_TYPE else VERSION_OF
    statements = _statements(entity, property_id)
    if statements:
        edits = model.change_value(entity, statements[0].id, EntityValue(parent.id), model.ENTITY_EDITOR)
    else:
        edits = model.add_statement(entity, property_id, EntityValue(parent.id), model.ENTITY_EDITOR)
    store.change(entity, edits)


def _empty(edition: Edition) -> bool:
    fields = (edition.publisher, edition.imprint, edition.date, edition.edition, edition.binding, edition.isbn_13,
              edition.isbn_10, edition.pages)
    return not any(f.strip() for f in fields) and not any(c.entity or c.wanted or c.location.strip()
                                                          or c.content.strip() or c.printing.strip()
                                                          or c.printed.strip() for c in edition.copies)


def _save_edition(store: Store, edition: Edition, expression: Entity | None):
    if edition.entity is None and _empty(edition):
        return
    label = edition_label(edition)
    link: list[model.Edit] = []
    if edition.entity is None:
        edition.entity = store.create(MANIFESTATION_TYPE, label)
        if expression is not None:
            link = model.add_statement(edition.entity, VERSION_OF, EntityValue(expression.id), model.ENTITY_EDITOR)
    entity = edition.entity
    names = [edition.publisher] if edition.publisher.strip() else []
    edition.entity = store.change(entity, [
        *link,
        *_label_edits(entity, label),
        *_names_edits(store, entity, PUBLISHER, names, ORGANISATION_TYPE),
        *_value_edits(entity, PUBLICATION_DATE, _time(edition.date)),
        *_value_edits(entity, EDITION, _string(edition.edition)),
        *_value_edits(entity, IMPRINT, _string(edition.imprint)),
        *_value_edits(entity, BINDING, _string(edition.binding)),
        *_value_edits(entity, ISBN_13, _string(edition.isbn_13)),
        *_value_edits(entity, ISBN_10, _string(edition.isbn_10)),
        *_value_edits(entity, PAGES, _quantity(edition.pages)),
        *_value_edits(entity, OPEN_LIBRARY, _string(edition.open_library)),
        *_link_edits(entity, COVER, OLD_COVER, edition.cover),
    ])
    for copy in edition.copies:
        _save_copy(store, copy, edition.entity)


def expression_label(language: str, title: str = "") -> str:
    """Return the label of an expression: its own title, if it has one,
    which isn't its work's, so that Search finds a book once."""
    if title.strip():
        return title.strip()
    return f"{language.strip()} text" if language.strip() else TEXT_LABEL


def save(store: Store, book: Book) -> None:
    """Write a book's fields to their levels, making what's new.  What
    isn't a field of the card is left as it is, and a source's statements
    stay the source's (see model.change_value).

    A work and its expression are made for a book, and so nobody needs to
    think about them.  Each level is written on its own, so a failure can
    leave some written: saving again finishes the job, as what's made is
    kept in the book.

    :raises BookError: if it can't be done."""
    problem = check(book)
    if problem:
        raise BookError(problem)

    title = book.title.strip()
    if book.work is None:
        book.work = store.create(WORK_TYPE, title)
    work = book.work
    book.work = store.change(work, [
        *_label_edits(work, title),
        *_value_edits(work, SUBTITLE, _subtitle(book.subtitle)),
        *_names_edits(store, work, AUTHOR, book.authors, PERSON_TYPE),
        *_series_edits(store, work, book),
    ])

    link: list[model.Edit] = []
    if book.expression is None:
        book.expression = store.create(EXPRESSION_TYPE, expression_label(book.language, book.text_title))
        link = model.add_statement(book.expression, VERSION_OF, EntityValue(book.work.id), model.ENTITY_EDITOR)
    expression = book.expression
    languages = [book.language] if book.language.strip() else []
    book.expression = store.change(expression, [
        *link,
        *_label_edits(expression, expression_label(book.language, book.text_title)),
        *_value_edits(expression, TEXT_TITLE, _subtitle(book.text_title)),
        *_names_edits(store, expression, LANGUAGE, languages, LANGUAGE_KIND),
        *_names_edits(store, expression, TRANSLATOR, book.translators, PERSON_TYPE),
        *_names_edits(store, expression, EDITOR, book.editors, PERSON_TYPE),
        *_value_edits(expression, STARTED, _time(book.started)),
        *_value_edits(expression, FINISHED, _time(book.finished)),
    ])
    for edition in book.editions:
        _save_edition(store, edition, book.expression)


def add_text(store: Store, work: Entity, text: Text) -> Entity:
    """Add an expression to a work, and return it.

    :raises BookError: if it can't be done."""
    if not (text.title.strip() or text.language.strip()):
        raise BookError("An Expression needs a title or a language")
    label = expression_label(text.language, text.title)
    entity = store.create(EXPRESSION_TYPE, label)
    languages = [text.language] if text.language.strip() else []
    try:
        return store.change(entity, [
            *model.add_statement(entity, VERSION_OF, EntityValue(work.id), model.ENTITY_EDITOR),
            *_value_edits(entity, TEXT_TITLE, _subtitle(text.title)),
            *_names_edits(store, entity, LANGUAGE, languages, LANGUAGE_KIND),
            *_names_edits(store, entity, TRANSLATOR, text.translators, PERSON_TYPE),
            *_names_edits(store, entity, EDITOR, text.editors, PERSON_TYPE),
        ])
    except BookError:
        _clear_away(store, entity, EXPRESSION_TYPE)
        raise


def add_edition(store: Store, expression: Entity, edition: Edition) -> None:
    """Add a manifestation to an expression, with no copies: having one
    is said on its own.

    :raises BookError: if it can't be done."""
    edition.copies = []
    if _empty(edition):
        raise BookError("A Manifestation needs some details")
    problem = check(Book(title="-", editions=[edition]))
    if problem:
        raise BookError(problem)
    try:
        _save_edition(store, edition, expression)
    except BookError:
        if edition.entity is not None:
            _clear_away(store, edition.entity, MANIFESTATION_TYPE)
        raise


def add_copy(store: Store, manifestation: Entity, copy: Copy) -> None:
    """Add a copy to a manifestation: one with nothing known is still one.

    :raises BookError: if it can't be done."""
    problem = check(Book(title="-", editions=[Edition(copies=[copy])]))
    if problem:
        raise BookError(problem)
    copy.wanted = True
    try:
        _save_copy(store, copy, manifestation)
    except BookError:
        if copy.entity is not None:
            _clear_away(store, copy.entity, ITEM_TYPE)
        raise


def _clear_away(store: Store, entity: Entity, type_id: str):
    """Destroy what was made for something that failed, so that nothing's
    left of it.  The failure's what matters if this can't be done."""
    try:
        store.destroy(model.item_id(entity.id, type_id))
    except BookError:
        pass


def remove(store: Store, type_id: str, entity: Entity) -> None:
    """Remove a level, if it has nothing under it: what's on the card is
    what you'd expect to go, so nothing goes with it.  A copy's content
    isn't touched: it's an item in its own right.

    :raises BookError: if there's something under it, or it can't be done."""
    item_id = model.item_id(entity.id, type_id)
    below = {WORK_TYPE: (VERSION_OF, EXPRESSION_TYPE, "Expressions"),
             EXPRESSION_TYPE: (VERSION_OF, MANIFESTATION_TYPE, "Manifestations"),
             MANIFESTATION_TYPE: (EXEMPLAR_OF, ITEM_TYPE, "copies")}.get(type_id)
    if below is not None and store.children(item_id, below[0], below[1]):
        raise BookError(f"Remove its {below[2]} first")
    store.destroy(item_id)


def vocabulary() -> list[Entity]:
    """Return the properties and items books need."""
    def prop(property_id: str, label: str, datatype: Datatype, description: str = "") -> Property:
        p = Property(datatype, property_id)
        p.set_label(label)
        if description:
            p.set_description(description)
        return p

    def item(entity_id: str, label: str) -> Item:
        made = Item(entity_id)
        made.set_label(label)
        return made

    return [
        prop(VERSION_OF, "version, edition or translation of", Datatype.Item),
        prop(EXEMPLAR_OF, "exemplar of", Datatype.Item),
        prop(SUBTITLE, "subtitle", Datatype.Text),
        prop(TEXT_TITLE, "title", Datatype.Text),
        prop(AUTHOR, "author", Datatype.Item),
        prop(SERIES, "part of the series", Datatype.Item),
        prop(DDC, "Dewey Decimal Classification", Datatype.String),
        prop(LCC, "Library of Congress Classification", Datatype.String),
        prop(LANGUAGE, "language of work or name", Datatype.Item),
        prop(TRANSLATOR, "translator", Datatype.Item),
        prop(EDITOR, "editor", Datatype.Item),
        prop(PUBLISHER, "publisher", Datatype.Item),
        prop(EDITION, "edition number", Datatype.String),
        prop(ISBN_13, "ISBN-13", Datatype.String),
        prop(ISBN_10, "ISBN-10", Datatype.String),
        prop(PAGES, "number of pages", Datatype.Quantity),
        prop(CONTRIBUTOR, "contributor to the creative work or subject", Datatype.Item),
        prop(CONTRIBUTOR_ROLE, "contribution", Datatype.String, "What someone contributed to a book"),
        prop(OPEN_LIBRARY, "Open Library ID", Datatype.String),
        prop(ASIN, "Amazon Standard Identification Number", Datatype.String),
        prop(LCCN, "Library of Congress item ID", Datatype.String),
        prop(LIBRARYTHING_WORK, "LibraryThing work ID", Datatype.String),
        prop(LIBRARYTHING_BOOK, "LibraryThing book ID", Datatype.String, "A copy's identifier on LibraryThing"),
        prop(SUBJECT_HEADING, "subject heading", Datatype.String, "A book's subject heading, as text"),
        prop(PHYSICAL_DESCRIPTION, "physical description", Datatype.String, "An edition's size and extent, as text"),
        prop(PUBLICATION_TEXT, "publication details", Datatype.String, "What a source said of a book's publication"),
        prop(ACQUIRED, "date acquired", Datatype.Time, "When a copy of a book was acquired"),
        prop(ACQUIRED_FROM, "acquired from", Datatype.String, "Where a copy of a book came from"),
        prop(PRICE, "price paid", Datatype.String, "What a copy of a book cost, as text"),
        prop(TAG, "tag", Datatype.String, "A tag on a copy of a book"),
        item(LIBRARYTHING, "LibraryThing"),
        prop(STARTED, "started reading", Datatype.Time, "When a book's text was started"),
        prop(FINISHED, "finished reading", Datatype.Time, "When a book's text was finished"),
        prop(IMPRINT, "imprint", Datatype.String, "The imprint of a publisher an edition of a book is under"),
        prop(BINDING, "binding", Datatype.String, "The form an edition of a book takes"),
        prop(LOCATION, "location", Datatype.String, "Where a copy of a book is kept"),
        prop(HAS_CHARACTERISTIC, "has characteristic", Datatype.Item),
        item(PREFERENCE, "preference"),
        prop(COVER, "cover", Datatype.Item, "The image item of an edition's cover"),
        prop(CONTENT, "content", Datatype.Item, "The item that's a copy of a book, such as an e-book"),
        prop(OLD_COVER, "cover (identifier, old)", Datatype.String,
             "An edition's cover, by its item's identifier, before every item was an entity"),
        prop(OLD_CONTENT, "content (identifier, old)", Datatype.String,
             "A copy's content, by its item's identifier, before every item was an entity"),
        prop(PRINTING, "printing", Datatype.String, "Which printing a copy of a book is"),
        prop(PRINTED, "date of printing", Datatype.Time, "When a copy of a book was printed"),
        *(item(item_id, label) for _, item_id, label in INVERSE_LABELS),
    ]
