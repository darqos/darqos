# darqos
# Copyright (C) 2026 David Arnold

"""A knowledge base's entities, kept in an SQLite database.

Each entity is a row holding its JSON document (see darq.kb.document) and
revision, and every revision is kept.  Index tables of labels, links,
string values and snaks are derived from the documents, and updated in
the same transaction as each change; queries are answered from them
(see query.py).  The design is in doc/kb-service.rst.

A Store is used by a KnowledgeBase:

    kb = KnowledgeBase(Store("kb.sqlite"))
    with kb.transaction():
        ...
"""

import datetime
import json
import os
import re
import sqlite3
import time

from typing import List, Optional, Sequence, Set, Tuple

from darq.kb import (INSTANCE_OF, SUBCLASS_OF, ConflictError, Entity,
                     EntityValue, Property, StringValue, decode_entity,
                     entity_snaks, fold, normalise)

from query import Plan, TooCostly, compile


# Version of the schema, kept as the database's user_version.  Version
# 1 had no snaks table, and no ranks or roles for links and strings:
# its index tables are rebuilt.
VERSION = 2

SCHEMA = """
create table if not exists entities (
    id text primary key,
    kind text not null,
    revision integer not null,
    document text not null,
    redirect text,
    destroyed integer not null default 0,
    modified text not null);

create table if not exists revisions (
    id text not null,
    revision integer not null,
    document text not null,
    modified text not null,
    primary key (id, revision));

create table if not exists labels (
    entity_id text not null,
    language text not null,
    role text not null,
    text text not null,
    folded text not null);
create index if not exists labels_by_text on labels (language, text);
create index if not exists labels_by_entity on labels (entity_id);

create virtual table if not exists labels_fts using fts5 (
    entity_id unindexed,
    language unindexed,
    text,
    tokenize = 'unicode61 remove_diacritics 2');

create table if not exists links (
    entity_id text not null,
    statement_id text not null,
    property_id text not null,
    target_id text not null,
    role text not null,
    rank text not null);
create index if not exists links_by_target on links (target_id, property_id);
create index if not exists links_by_property on links (property_id, target_id);
create index if not exists links_by_entity on links (entity_id);
create index if not exists links_by_statement on links (statement_id);

create table if not exists strings (
    entity_id text not null,
    statement_id text not null,
    property_id text not null,
    value text not null,
    text text not null,
    role text not null,
    rank text not null);
create index if not exists strings_by_value on strings (property_id, value);
create index if not exists strings_by_entity on strings (entity_id);
create index if not exists strings_by_statement on strings (statement_id);

create table if not exists snaks (
    entity_id text not null,
    statement_id text not null,
    property_id text not null,
    role text not null,
    rank text not null);
create index if not exists snaks_by_statement on snaks (statement_id, property_id);
create index if not exists snaks_by_property on snaks (property_id);
create index if not exists snaks_by_entity on snaks (entity_id);
"""

# Index tables, each with an entity_id column.
INDEXES = ("labels", "labels_fts", "links", "strings", "snaks")

# The index tables version 1 had, which have changed.
CHANGED = ("links", "strings")

# Condition for an entity, e, that isn't destroyed or merged.
LIVE = "e.destroyed = 0 and e.redirect is null"


class Store:
    """Entities, in an SQLite database."""

    def __init__(self, path: str):
        """Constructor.

        :param path: Database file, created if it doesn't exist."""
        # A knowledge base can hold personal data, such as an address
        # book, so only its owner can read it.  SQLite gives its log
        # files the database's permissions.
        if path != ":memory:":
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))

        # Transactions are begun explicitly, by put().
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("pragma journal_mode = wal")
        self.db.execute("pragma synchronous = normal")

        version = self.db.execute("pragma user_version").fetchone()[0]
        if version > VERSION:
            self.db.close()
            raise ValueError(f"{path}: schema version {version} is newer "
                             f"than {VERSION}")
        if version == 1:
            self._upgrade()
        else:
            self.db.executescript(SCHEMA)
            self.db.execute(f"pragma user_version = {VERSION}")

        # Entities written in the current transaction, if there is one.
        self.active = False
        self.written: Set[str] = set()

        # Entity revisions written, in all.
        self.writes = 0

    def close(self):
        self.db.close()

    def _upgrade(self):
        """Upgrade the schema from version 1, rebuilding the index tables
        from the documents, all or none."""
        drops = "".join(f"drop table {table}; " for table in CHANGED)
        self.db.executescript(f"begin immediate; {drops}{SCHEMA}")
        try:
            for table in INDEXES:
                self.db.execute(f"delete from {table}")
            rows = self.db.execute("select document from entities").fetchall()
            for (text,) in rows:
                self._index(decode_entity(json.loads(text)))
            self.db.execute(f"pragma user_version = {VERSION}")
            self.db.execute("commit")
        except BaseException:
            self.db.execute("rollback")
            raise

    def begin(self):
        """Begin a transaction, unless one's begun."""
        if not self.active:
            self.db.execute("begin immediate")
            self.active = True

    def commit(self):
        """Commit the current transaction, if any."""
        if self.active:
            self.db.execute("commit")
            self._ended()

    def rollback(self):
        """Roll back the current transaction, if any."""
        if self.active:
            self.db.execute("rollback")
            self._ended()

    def _ended(self):
        self.active = False
        self.written.clear()

    def get(self, entity_id: str) -> Optional[Entity]:
        """Read an entity, or return None if there's none."""
        row = self.db.execute("select revision, document from entities "
                              "where id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        revision, text = row
        entity = decode_entity(json.loads(text))
        entity.revision = revision
        entity.stored = text
        return entity

    def put(self, entity: Entity, text: str):
        """Write an entity's document, and its index rows.

        The first write of an entity in a transaction is a new revision,
        which raises ConflictError unless the stored revision is the one
        the entity was read at.  Later writes in the same transaction
        replace it."""
        self.begin()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        kind = "property" if isinstance(entity, Property) else "item"
        destroyed = int(entity.destroyed)

        if entity.id in self.written:
            self.db.execute("update entities set document = ?, redirect = ?, "
                            "destroyed = ?, modified = ? where id = ?",
                            (text, entity.redirect, destroyed, now, entity.id))
            self.db.execute("update revisions set document = ?, modified = ? "
                            "where id = ? and revision = ?",
                            (text, now, entity.id, entity.revision))
        else:
            revision = entity.revision + 1
            if entity.revision == 0:
                try:
                    self.db.execute("insert into entities values "
                                    "(?, ?, ?, ?, ?, ?, ?)",
                                    (entity.id, kind, revision, text,
                                     entity.redirect, destroyed, now))
                except sqlite3.IntegrityError:
                    raise ConflictError(f"{entity.id} already exists",
                                        entity.id) from None
            else:
                cursor = self.db.execute(
                    "update entities set revision = ?, document = ?, "
                    "redirect = ?, destroyed = ?, modified = ? "
                    "where id = ? and revision = ?",
                    (revision, text, entity.redirect, destroyed, now,
                     entity.id, entity.revision))
                if cursor.rowcount == 0:
                    raise ConflictError(f"{entity.id} has changed since "
                                        f"revision {entity.revision}",
                                        entity.id)
            self.db.execute("insert into revisions values (?, ?, ?, ?)",
                            (entity.id, revision, text, now))
            entity.revision = revision
            self.written.add(entity.id)
            self.writes += 1

        self._index(entity)

    def _index(self, entity: Entity):
        """Replace an entity's rows in the index tables."""
        for table in INDEXES:
            self.db.execute(f"delete from {table} where entity_id = ?",
                            (entity.id,))

        names = [(language, "label", label)
                 for language, label in entity.labels.items()]
        names += [(language, "alias", alias)
                  for language, aliases in entity.aliases.items()
                  for alias in aliases]
        self.db.executemany("insert into labels values (?, ?, ?, ?, ?)",
                            [(entity.id, language, role, text, fold(text))
                             for language, role, text in names])
        self.db.executemany("insert into labels_fts values (?, ?, ?)",
                            [(entity.id, language, text)
                             for language, _, text in names])
        links, strings, snaks = [], [], []
        for s, snak, role in entity_snaks(entity):
            row = (entity.id, s.id, snak.property_id)
            rank = s.rank.name.lower()
            if isinstance(snak.value, EntityValue):
                links.append((*row, snak.value.entity_id, role, rank))
            elif isinstance(snak.value, StringValue):
                strings.append((*row, normalise(snak.value.value),
                                snak.value.value, role, rank))
            snaks.append((*row, role, rank))
        self.db.executemany("insert into links values (?, ?, ?, ?, ?, ?)",
                            links)
        self.db.executemany("insert into strings values "
                            "(?, ?, ?, ?, ?, ?, ?)", strings)
        self.db.executemany("insert into snaks values (?, ?, ?, ?, ?)",
                            snaks)

    def _ids(self, sql: str, *args) -> List[str]:
        return [row[0] for row in self.db.execute(sql, args)]

    def ids(self) -> List[str]:
        """Return the identifiers of entities not destroyed or merged."""
        return self._ids(f"select id from entities e where {LIVE} order by id")

    def find_label(self, label: str, language: str) -> List[str]:
        """Return the identifiers of entities with a label."""
        return self._ids("select distinct l.entity_id from labels l "
                         "join entities e on e.id = l.entity_id "
                         "where l.language = ? and l.role = 'label' "
                         f"and l.text = ? and {LIVE} order by 1",
                         language, label)

    def search(self, text: str, language: str) -> List[str]:
        """Return the identifiers of entities with a label or alias with
        words starting with each of the text's words."""
        words = re.findall(r"\w+", text)
        if not words:
            return []
        query = " ".join(f'"{w}"*' for w in words)
        return self._ids("select distinct f.entity_id from labels_fts f "
                         "join entities e on e.id = f.entity_id "
                         "where labels_fts match ? and f.language = ? "
                         f"and {LIVE} order by 1",
                         query, language)

    def find_value(self, property_id: str, value: str) -> List[str]:
        """Return the identifiers of entities with a normalised string
        value for a property."""
        return self._ids("select distinct s.entity_id from strings s "
                         "join entities e on e.id = s.entity_id "
                         "where s.property_id = ? and s.value = ? "
                         f"and s.role = 'main' and {LIVE} order by 1",
                         property_id, value)

    def find_links(self,
                   target_id: str,
                   property_id: Optional[str],
                   role: str) -> List[str]:
        """Return the identifiers of entities linking to one."""
        condition = "" if property_id is None else "and l.property_id = ? "
        args = [target_id, role] + ([] if property_id is None
                                    else [property_id])
        return self._ids("select distinct l.entity_id from links l "
                         "join entities e on e.id = l.entity_id "
                         "where l.target_id = ? and l.role = ? "
                         f"{condition}and {LIVE} order by 1", *args)

    def instances(self, class_id: str) -> List[str]:
        """Return the identifiers of instances of a class, or of any of
        its subclasses."""
        return self._ids("with recursive classes (id) as ("
                         "  select ? union"
                         "  select l.entity_id from links l"
                         "  join classes c on l.target_id = c.id"
                         "  where l.property_id = ? and l.role = 'main') "
                         "select distinct l.entity_id from links l "
                         "join classes c on l.target_id = c.id "
                         "join entities e on e.id = l.entity_id "
                         "where l.property_id = ? and l.role = 'main' "
                         f"and {LIVE} order by 1",
                         class_id, SUBCLASS_OF, INSTANCE_OF)

    def query(self,
              plan: Plan,
              after: Sequence[str],
              limit: Optional[int],
              deadline: float) -> List[Tuple[str, ...]]:
        """Return a query's rows, sorted: those after the row after, if
        it isn't empty, and at most limit, if given.

        :param deadline: When it's stopped, as a time.monotonic() time.
        :raises TooCostly: if it's stopped."""
        sql, args = compile(plan, after, limit)
        self.db.set_progress_handler(lambda: time.monotonic() > deadline,
                                     1000)
        try:
            return [tuple(row) for row in self.db.execute(sql, args)]
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise TooCostly() from None
            raise
        finally:
            self.db.set_progress_handler(None, 0)
