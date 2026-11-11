# darqos
# Copyright (C) 2026 David Arnold

"""Importing a LibraryThing export (see lt_export.py) into the KB, as a
book's four levels (see doc/book.rst and
doc/librarything-import-tool.rst).

It's a client of the Knowledge Base service, which it reads, and writes
the vocabulary to; and of the KB type implementation, through which
items are written (see doc/types.rst):

1. The vocabulary's found or created, with LibraryThing, which every
   statement here cites.
2. Editions already here are found, by ISBN, so that a book that's been
   entered by hand gains what's new, and isn't made twice.
3. Names are found, or created: people found by name are listed, to be
   checked.
4. Each level's created, or restated if it exists, in batches, from the
   top, as a link needs its target.

Identifiers come from what things are: a work's LibraryThing's, a text
its work and language, an edition its work and ISBN, and a copy its book
identifier.  So importing again finds the same ones, and changes nothing
unless the export has.  Only statements citing LibraryThing are changed
by that, so what's been edited by hand stays (see model.restate).  A
batch that fails is logged, and the import goes on, and importing again
finishes it."""

import dataclasses
import logging
import typing
import uuid

import darq
import darq.book
import darq.history
import darq.items
import darq.kb as model
from darq.book import EXPRESSION_TYPE, ITEM_TYPE, MANIFESTATION_TYPE, WORK_TYPE, Edition

import lt_export
from lt_export import Record


log = logging.getLogger("librarything-import")

# History source for its events.
SOURCE = "librarything-import"

# Entities read and written in each request.
BATCH = 200

# Where its identifiers come from.
NAMESPACE = uuid.UUID("3f1b7a52-3f0e-4a5e-9f3c-5b1d6f0c2a11")

# Qualifiers it states, so that what's added by hand, such as a reason
# for deprecating a statement, doesn't make a statement a different one,
# which would have an import state it again (see model.restate).
QUALIFIERS = frozenset({model.SERIES_ORDINAL, darq.book.CONTRIBUTOR_ROLE})

# Classes of what's found by name: none for what can be anything.
PERSON_CLASS = "Q5"
ORGANISATION_CLASS = "Q43229"


class History(typing.Protocol):
    def add_event(self, item_id: str, event: str, source: str) -> typing.Any: ...


def identifier(kind: str, key: str) -> str:
    """Return an entity's identifier, from what it is."""
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{key}"))


def edition_key(record: Record) -> str:
    """Return what makes a record's edition: copies of one are records
    with one ISBN, of one work."""
    return record.isbn_13 or record.isbn_10 or f"book:{record.book_id}"


def statement(property_id: str, value: model.Value, *qualifiers: tuple[str, model.Value]) -> model.Statement:
    made = model.Statement(model.Snak(property_id, value))
    for qualifier, qualified in qualifiers:
        made.add_qualifier(qualifier, qualified)
    return made


def string(property_id: str, text: str, *qualifiers: tuple[str, model.Value]) -> list[model.Statement]:
    """Return a statement of a string, or none if it's empty."""
    return [statement(property_id, model.StringValue(text), *qualifiers)] if text else []


def time(property_id: str, text: str) -> list[model.Statement]:
    return [statement(property_id, model.TimeValue.parse(text))] if text else []


def unique(statements: typing.Iterable[model.Statement]) -> list[model.Statement]:
    """Return statements, without those that say the same thing, as
    records of a work can."""
    kept: dict[str, model.Statement] = {}
    for made in statements:
        kept.setdefault(repr((made.get_property_id(), str(made.get_value()), [str(q) for q in made.qualifiers])), made)
    return list(kept.values())


@dataclasses.dataclass
class Level:
    """Entities of a level to write: what's wanted of each, by identifier,
    with the label each gets, if it's new."""

    type_id: str
    labels: dict[str, str] = dataclasses.field(default_factory=dict)
    wanted: dict[str, list[model.Statement]] = dataclasses.field(default_factory=dict)
    aliases: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    def add(self, entity_id: str, label: str, statements: list[model.Statement]):
        self.labels.setdefault(entity_id, label)
        self.wanted.setdefault(entity_id, []).extend(statements)


class Result:
    """What an import did."""

    def __init__(self):
        self.records = 0
        self.created: dict[str, int] = {}
        self.updated = 0
        self.unchanged = 0
        self.failed = 0

        # What needs checking: people found by name, editions that were
        # here already, and publications that weren't understood.
        self.matches: list[tuple[str, str]] = []
        self.editions_found: list[tuple[str, str]] = []
        self.unparsed: list[str] = []

    def lines(self) -> list[str]:
        made = ", ".join(f"{self.created.get(t, 0)} {name}" for t, name in (
            (WORK_TYPE, "books"), (EXPRESSION_TYPE, "texts"), (MANIFESTATION_TYPE, "editions"), (ITEM_TYPE, "copies")))
        return [f"{self.records} records: {made} created",
                f"{self.updated} updated, {self.unchanged} unchanged, {self.failed} failed",
                f"{len(self.matches)} people found by name, {len(self.editions_found)} editions were here already",
                f"{len(self.unparsed)} publications not understood: they're kept as text"]


class Importer:
    def __init__(self, kb: model.KB, items: model.KB, history: History | None, records: list[Record],
                 progress: typing.Callable[[str, int, int], None]):
        """Constructor.

        :param kb: Client of the KB service.
        :param items: Client that writes through the implementation.
        :param progress: Told the step, how much is done, and the total."""
        self.kb = kb
        self.items = items
        self.history = history
        self.records = records
        self.progress = progress
        self.result = Result()

        # Identifiers of what's here already, overriding those made.
        self.works: dict[str, str] = {}
        self.expressions: dict[tuple[str, str], str] = {}
        self.editions: dict[tuple[str, str], str] = {}

        # Entities of names, by kind and name.
        self.names: dict[tuple[str, str], str] = {}

        # Tags, by name, folded, as they're found or made.
        self.tags: dict[str, str] = {}

    def run(self) -> Result:
        self.result.records = len(self.records)
        self.ensure_vocabulary()
        self.find_editions()
        levels = self.levels()
        for level in levels:
            self.write(level)
        return self.result

    def record(self, item_id: str, event: str):
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            log.warning(f"Can't record {event} for {item_id}: {e!r}")

    def ensure_vocabulary(self):
        self.kb.ensure(*darq.book.vocabulary())

    # What's here already

    def parent(self, entity: model.Entity, property_id: str) -> str:
        for s in entity.get_statements(property_id):
            value = s.get_value()
            if isinstance(value, model.EntityValue):
                return value.entity_id
        return ""

    def find_edition(self, record: Record) -> str:
        """Return an edition with a record's ISBN, if there is one."""
        for property_id, isbn in ((darq.book.ISBN_13, record.isbn_13), (darq.book.ISBN_10, record.isbn_10)):
            if isbn:
                found = sorted(self.kb.find_by_value(property_id, isbn))
                if found:
                    return found[0]
        return ""

    def find_editions(self):
        """Find what a book that's here is: its edition, and what that's an
        edition of, so that what's new goes on them."""
        for n, record in enumerate(self.records):
            self.progress("Looking for books that are here", n, len(self.records))
            found = self.find_edition(record)
            if not found:
                continue
            key = (record.work_code, edition_key(record))
            self.editions[key] = found
            if found != identifier("edition", f"{record.work_code}:{edition_key(record)}"):
                self.result.editions_found.append((record.title, model.item_id(found, MANIFESTATION_TYPE)))
            edition = self.kb.get(found, follow=False)
            text = self.parent(edition, darq.book.VERSION_OF) if edition is not None else ""
            if not text:
                continue
            self.expressions.setdefault((record.work_code, record.language), text)
            expression = self.kb.get(text, follow=False)
            work = self.parent(expression, darq.book.VERSION_OF) if expression is not None else ""
            if work:
                self.works.setdefault(record.work_code, work)

    # Names

    def name(self, text: str, type_id: str, class_id: str = "") -> str:
        """Return the entity with a name, making one if there isn't."""
        key = (type_id, text)
        if key in self.names:
            return self.names[key]
        found = sorted(self.kb.find_by_label(text, "en", class_id=class_id))
        if found:
            entity_id = identifier(type_id, text) if identifier(type_id, text) in found else found[0]

            # One an earlier import made isn't news.
            if type_id == darq.book.PERSON_TYPE and entity_id != identifier(type_id, text):
                item_id = model.item_id(entity_id, type_id)
                self.result.matches.append((text, item_id))
                log.info(f"Matched a person by name: {item_id}")
        else:
            entity_id = identifier(type_id, text)
            if self.items.get(entity_id, follow=False) is None:
                made = model.Item(entity_id)
                made.set_label(text)
                self.items.create(type_id, made)
                self.record(model.item_id(entity_id, type_id), darq.history.CREATED)
        # One that's been merged, as into Wikidata's, is what it was merged
        # into, so that its links aren't to the old one.
        current = self.items.get(entity_id)
        if current is not None:
            entity_id = current.id
        self.names[key] = entity_id
        return entity_id

    def person(self, text: str) -> model.EntityValue:
        return model.EntityValue(self.name(text, darq.book.PERSON_TYPE, PERSON_CLASS))

    def thing(self, text: str) -> model.EntityValue:
        return model.EntityValue(self.name(text, model.ENTITY_TYPE))

    # Levels

    def work_id(self, record: Record) -> str:
        return self.works.get(record.work_code) or identifier("work", record.work_code)

    def expression_id(self, record: Record) -> str:
        key = (record.work_code, record.language)
        return self.expressions.get(key) or identifier("expression", f"{record.work_code}:{record.language}")

    def edition_id(self, record: Record) -> str:
        key = edition_key(record)
        return self.editions.get((record.work_code, key)) or identifier("edition", f"{record.work_code}:{key}")

    def work_statements(self, record: Record) -> list[model.Statement]:
        made = [statement(darq.book.AUTHOR, self.person(p.name)) for p in record.people
                if p.role == lt_export.AUTHOR]
        if record.subtitle:
            made.append(statement(darq.book.SUBTITLE, model.TextValue(record.subtitle)))
        for series, volume in record.series:
            ordinal = [(model.SERIES_ORDINAL, model.StringValue(volume))] if volume else []
            made.append(statement(darq.book.SERIES, self.thing(series), *ordinal))
        made += string(darq.book.DDC, record.ddc)
        made += string(darq.book.LCC, record.lcc)
        made += string(darq.book.LIBRARYTHING_WORK, record.work_code)
        for heading in record.subjects:
            made += string(darq.book.SUBJECT_HEADING, heading)
        return made

    def expression_statements(self, record: Record) -> list[model.Statement]:
        made = [statement(darq.book.VERSION_OF, model.EntityValue(self.work_id(record)))]
        if record.language:
            made.append(statement(darq.book.LANGUAGE, self.thing(record.language)))
        for person in record.people:
            if person.role == lt_export.TRANSLATOR:
                made.append(statement(darq.book.TRANSLATOR, self.person(person.name)))
            elif person.role == lt_export.EDITOR:
                made.append(statement(darq.book.EDITOR, self.person(person.name)))
            elif person.role != lt_export.AUTHOR:
                made.append(statement(darq.book.CONTRIBUTOR, self.person(person.name),
                                      (darq.book.CONTRIBUTOR_ROLE, model.StringValue(person.role))))
        return made

    def edition_statements(self, record: Record) -> list[model.Statement]:
        made = [statement(darq.book.VERSION_OF, model.EntityValue(self.expression_id(record)))]
        if record.publisher:
            publisher = self.name(record.publisher, darq.book.ORGANISATION_TYPE, ORGANISATION_CLASS)
            made.append(statement(darq.book.PUBLISHER, model.EntityValue(publisher)))
        made += time(darq.book.PUBLICATION_DATE, record.year)
        made += string(darq.book.EDITION, record.edition)
        made += string(darq.book.BINDING, record.binding)
        made += string(darq.book.ISBN_13, record.isbn_13)
        made += string(darq.book.ISBN_10, record.isbn_10)
        made += string(darq.book.ASIN, record.asin)
        made += string(darq.book.LCCN, record.lccn)
        made += string(darq.book.PHYSICAL_DESCRIPTION, record.physical)
        made += string(darq.book.PUBLICATION_TEXT, record.publication)
        if record.pages:
            made.append(statement(darq.book.PAGES, model.QuantityValue(int(record.pages))))
        return made

    def copy_statements(self, record: Record) -> list[model.Statement]:
        made = [statement(darq.book.EXEMPLAR_OF, model.EntityValue(self.edition_id(record)))]
        made += string(darq.book.LIBRARYTHING_BOOK, record.book_id)
        made += time(darq.book.ACQUIRED, record.acquired)
        made += string(darq.book.ACQUIRED_FROM, record.acquired_from)
        made += string(darq.book.PRICE, record.price)
        for name in record.tags:
            # A tag's an entity, found or made by its name.
            made.append(statement(darq.items.HAS_TAG, model.EntityValue(self.tag(name))))
        return made

    def tag(self, name: str) -> str:
        """Return the tag with a name, making it if there's none."""
        key = model.fold(" ".join(name.split()))
        if key not in self.tags:
            self.tags[key] = darq.items.tag(self.kb, self.items, name)
        return self.tags[key]

    def levels(self) -> list[Level]:
        works = Level(WORK_TYPE)
        expressions = Level(EXPRESSION_TYPE)
        editions = Level(MANIFESTATION_TYPE)
        copies = Level(ITEM_TYPE)
        for n, record in enumerate(self.records):
            self.progress("Reading books", n, len(self.records))
            if record.publication and not record.publisher:
                self.result.unparsed.append(record.title)
            try:
                works.add(self.work_id(record), record.title, self.work_statements(record))
                if record.original_title:
                    works.aliases.setdefault(self.work_id(record), []).append(record.original_title)
                expressions.add(self.expression_id(record), darq.book.expression_label(record.language),
                                self.expression_statements(record))
                label = darq.book.edition_label(Edition(publisher=record.publisher, binding=record.binding,
                                                        date=record.year))
                editions.add(self.edition_id(record), label, self.edition_statements(record))
                for number in range(record.copies):
                    key = f"{record.book_id}:{number}" if number else record.book_id
                    copies.add(identifier("copy", key), darq.book.COPY_LABEL, self.copy_statements(record))
            except (darq.DarqError, TimeoutError, ValueError, LookupError, model.ConflictError) as e:
                log.error(f"Can't read book {record.book_id}: {e!r}")
                self.result.failed += 1
        return [works, expressions, editions, copies]

    # Writing

    def write(self, level: Level):
        ids = list(level.wanted)
        for start in range(0, len(ids), BATCH):
            self.progress(f"Writing {level.type_id}", start, len(ids))
            self.write_batch(level, ids[start:start + BATCH])

    def write_batch(self, level: Level, ids: list[str]):
        """Create or restate a batch, all or none."""
        try:
            found, _ = self.items.get_entities(ids, follow=False)
            existing = {e.id: e for e in found}
            new, changed = [], []
            for entity_id in ids:
                wanted = unique(level.wanted[entity_id])
                entity = existing.get(entity_id)
                if entity is None:
                    entity = model.Item(entity_id)
                    entity.set_label(level.labels[entity_id])
                    for alias in dict.fromkeys(level.aliases.get(entity_id, ())):
                        if alias != level.labels[entity_id]:
                            entity.add_alias(alias)
                    model.restate(entity, darq.book.LIBRARYTHING, wanted, QUALIFIERS)
                    new.append(entity)
                elif model.restate(entity, darq.book.LIBRARYTHING, wanted, QUALIFIERS):
                    changed.append(entity)
            if new:
                self.items.create(level.type_id, *new)
            if changed:
                self.items.put(*changed)
        except (darq.DarqError, TimeoutError, ValueError, LookupError, model.ConflictError) as e:
            log.error(f"Can't import {len(ids)} of {level.type_id}: {e!r}")
            self.result.failed += len(ids)
            return
        if new:
            self.result.created[level.type_id] = self.result.created.get(level.type_id, 0) + len(new)
        self.result.updated += len(changed)
        self.result.unchanged += len(ids) - len(new) - len(changed)
        for entity in new:
            self.record(model.item_id(entity.id, level.type_id), darq.history.IMPORTED)
