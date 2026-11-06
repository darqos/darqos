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
    "Copy",
    "DDC",
    "EDITION",
    "EDITOR",
    "EXEMPLAR_OF",
    "EXPRESSION_TYPE",
    "Edition",
    "FILE",
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
    "SUBJECT_HEADING",
    "TAG",
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
    "TRANSLATOR",
    "VERSION_OF",
    "WORK_TYPE",
    "check",
    "edition_label",
    "expression_label",
    "file_format",
    "item_of",
    "read",
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

# The kinds of what a book's names refer to.
PERSON_TYPE = "darq.person"
ORGANISATION_TYPE = "darq.organisation"

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

# On an expression: its language, translators and editors, others who
# contributed, with their role as text (a qualifier), and when it was
# started and finished, which are knowledge, unlike a place in a file
# (see doc/book.rst).
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
LCCN = "P1144"
PHYSICAL_DESCRIPTION = "8299e292-2411-498c-b34d-5536ba8b93b2"
PUBLICATION_TEXT = "6cf68a4e-7257-43af-ad50-497c6e94833f"
IMPRINT = "2f20d2aa-65a0-4486-86c4-56facb477bf6"
BINDING = "2eabea67-5f8d-4d35-aec1-878da88f077d"

# On a copy: where it is, as text for now, or the item that's its file
# (an identifier, a String, as URL snapshots are), and its printing, and
# when it was printed, and where it came from: when, from where, and for
# how much, as text.  Its identifier and tags on LibraryThing.
LOCATION = "2cf1b758-7379-418f-9ea6-049d74518bbe"
FILE = "0cd7fdd5-f6c8-4346-91b8-b5d674244ea4"
PRINTING = "20450dc5-a708-4ca1-8729-3db3e8d90836"
PRINTED = "6e9db545-11e2-452b-a2ef-97ef7da9b182"

ACQUIRED = "3206ba38-9a22-4396-8c1e-391cd34033bd"
ACQUIRED_FROM = "946e5920-9ab3-4348-827b-72145068153d"
PRICE = "3fd7d449-6e7a-4e27-9070-289ad1edbd15"
LIBRARYTHING_BOOK = "3467b6af-1d3a-4773-8b01-4ae3c208cbc5"
TAG = "8b300fc8-4c53-4805-9390-63864dfdafe2"

# LibraryThing, as a source.
LIBRARYTHING = "7659380e-57eb-4c0e-aba6-644f197824a7"

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
]

# File formats, by the type of a file's item.
FILE_FORMATS = {
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

    def create(self, type_id: str, label: str) -> Entity:
        """Make an entity of a kind, with a label, and return it.

        :raises BookError: if it can't be done."""
        ...

    def change(self, entity: Entity, edits: list[model.Edit]) -> Entity:
        """Make edits to an entity, and return it as it now is.

        :raises BookError: if it can't be done."""
        ...


@dataclasses.dataclass
class Copy:
    """A copy, on a shelf, or as a file: entity is None until it's saved."""

    entity: Entity | None = None
    location: str = ""
    file: str = ""
    printing: str = ""
    printed: str = ""


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
    copies: list[Copy] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Book:
    """A book, as the card shows it: the fields can be changed, and saved
    with save().  It's centred on one expression: others is the rest."""

    work: Entity | None = None
    expression: Entity | None = None
    title: str = ""
    subtitle: str = ""
    authors: list[str] = dataclasses.field(default_factory=list)
    series: str = ""
    volume: str = ""
    language: str = ""
    translators: list[str] = dataclasses.field(default_factory=list)
    editors: list[str] = dataclasses.field(default_factory=list)

    # Others who contributed, shown but not edited: name, and role.
    contributors: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    started: str = ""
    finished: str = ""
    editions: list[Edition] = dataclasses.field(default_factory=list)
    others: list[Entity] = dataclasses.field(default_factory=list)

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


def _read_copy(entity: Entity) -> Copy:
    return Copy(entity, _text(entity, LOCATION), _text(entity, FILE), _text(entity, PRINTING), _text(entity, PRINTED))


def _read_edition(store: Store, entity: Entity, links: dict[str, str]) -> Edition:
    publisher = _names(store, entity, PUBLISHER, links)
    copies = store.children(model.item_id(entity.id, MANIFESTATION_TYPE), EXEMPLAR_OF, ITEM_TYPE)
    return Edition(entity, publisher[0] if publisher else "", _text(entity, IMPRINT),
                   _text(entity, PUBLICATION_DATE), _text(entity, EDITION),
                   _text(entity, BINDING), _text(entity, ISBN_13), _text(entity, ISBN_10), _text(entity, PAGES),
                   [_read_copy(c) for c in copies])


def _edition_order(edition: Edition) -> tuple[bool, str]:
    """Return what orders editions: by date, with undated ones last.  A
    link's order means nothing."""
    return not edition.date, edition.date


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
    if expression is not None:
        book.language = next(iter(_names(store, expression, LANGUAGE, book.links)), "")
        book.translators = _names(store, expression, TRANSLATOR, book.links)
        book.editors = _names(store, expression, EDITOR, book.links)
        for statement, name in zip(_statements(expression, CONTRIBUTOR),
                                   _names(store, expression, CONTRIBUTOR, book.links)):
            role = next((q.value.value for q in statement.qualifiers if q.property_id == CONTRIBUTOR_ROLE
                         and isinstance(q.value, StringValue)), "")
            book.contributors.append((name, role))
        book.started = _text(expression, STARTED)
        book.finished = _text(expression, FINISHED)
        editions = store.children(model.item_id(expression.id, EXPRESSION_TYPE), VERSION_OF, MANIFESTATION_TYPE)
        book.editions = sorted((_read_edition(store, e, book.links) for e in editions), key=_edition_order)
    elif manifestation is not None:
        book.editions = [_read_edition(store, manifestation, book.links)]
    return book


def item_of(expression: Entity) -> str:
    """Return the item identifier of an expression."""
    return model.item_id(expression.id, EXPRESSION_TYPE)


def file_format(copy: Copy) -> str:
    """Return the name of the format of a copy's file, or an empty string
    if it hasn't one."""
    if not copy.file:
        return ""
    type_id = copy.file.partition(":")[0]
    return FILE_FORMATS.get(type_id, type_id)


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


def _string(text: str) -> model.Value | None:
    return StringValue(text.strip()) if text.strip() else None


def _time(text: str) -> model.Value | None:
    return TimeValue.parse(text.strip()) if text.strip() else None


def _quantity(text: str) -> model.Value | None:
    return QuantityValue(int(text)) if text.strip() else None


def _subtitle(text: str) -> model.Value | None:
    return TextValue(text.strip()) if text.strip() else None


def _name_id(store: Store, name: str, type_id: str) -> str:
    """Return the entity with a name, or make one."""
    return store.find(name, type_id) or store.create(type_id, name).id


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
        if not (copy.location.strip() or copy.file.strip() or copy.printing.strip() or copy.printed.strip()):
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
        *_value_edits(entity, FILE, _string(copy.file)),
        *_value_edits(entity, PRINTING, _string(copy.printing)),
        *_value_edits(entity, PRINTED, _time(copy.printed)),
    ])


def _empty(edition: Edition) -> bool:
    fields = (edition.publisher, edition.imprint, edition.date, edition.edition, edition.binding, edition.isbn_13,
              edition.isbn_10, edition.pages)
    return not any(f.strip() for f in fields) and not any(c.entity or c.location.strip() or c.file.strip()
                                                          or c.printing.strip() or c.printed.strip()
                                                          for c in edition.copies)


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
    ])
    for copy in edition.copies:
        _save_copy(store, copy, edition.entity)


def expression_label(language: str) -> str:
    """Return the label of an expression in a language."""
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
        book.expression = store.create(EXPRESSION_TYPE, expression_label(book.language))
        link = model.add_statement(book.expression, VERSION_OF, EntityValue(book.work.id), model.ENTITY_EDITOR)
    expression = book.expression
    languages = [book.language] if book.language.strip() else []
    book.expression = store.change(expression, [
        *link,
        *_label_edits(expression, expression_label(book.language)),
        *_names_edits(store, expression, LANGUAGE, languages, model.ENTITY_TYPE),
        *_names_edits(store, expression, TRANSLATOR, book.translators, PERSON_TYPE),
        *_names_edits(store, expression, EDITOR, book.editors, PERSON_TYPE),
        *_value_edits(expression, STARTED, _time(book.started)),
        *_value_edits(expression, FINISHED, _time(book.finished)),
    ])
    for edition in book.editions:
        _save_edition(store, edition, book.expression)


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
        prop(FILE, "file", Datatype.String, "The item that's a copy of a book, as an identifier"),
        prop(PRINTING, "printing", Datatype.String, "Which printing a copy of a book is"),
        prop(PRINTED, "date of printing", Datatype.Time, "When a copy of a book was printed"),
        *(item(item_id, label) for _, item_id, label in INVERSE_LABELS),
    ]
