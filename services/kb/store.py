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
import heapq
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
# 1 had no snaks table, and no ranks or roles for links and strings;
# version 2 kept the full-text rows apart from labels, found by an
# unindexed column, so each write scanned them all, and its link
# indexes didn't end in entity_id, so a batch of links or instances
# was found by sorting them all.  An older
# database's index tables are rebuilt.
VERSION = 3

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
    id integer primary key,
    entity_id text not null,
    language text not null,
    role text not null,
    text text not null,
    folded text not null);
create index if not exists labels_by_text on labels (language, text);
create index if not exists labels_by_entity on labels (entity_id);

-- The full-text index of labels, its text read from them, kept in step
-- by triggers.  The explicit integer key keeps each label's rowid,
-- which the index refers to, stable through a vacuum.
create virtual table if not exists labels_fts using fts5 (
    entity_id unindexed,
    language unindexed,
    text,
    content = 'labels',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2');
create trigger if not exists labels_inserted after insert on labels begin
    insert into labels_fts (rowid, entity_id, language, text)
    values (new.id, new.entity_id, new.language, new.text);
end;
create trigger if not exists labels_deleted after delete on labels begin
    insert into labels_fts (labels_fts, rowid, entity_id, language, text)
    values ('delete', old.id, old.entity_id, old.language, old.text);
end;

create table if not exists links (
    entity_id text not null,
    statement_id text not null,
    property_id text not null,
    target_id text not null,
    role text not null,
    rank text not null);
-- Each ends in entity_id, so the entities linking to one, or the
-- instances of a class, are read in order, a batch at a time.
create index if not exists links_by_target on links (target_id, property_id, role, entity_id);
create index if not exists links_by_property on links (property_id, target_id, role, entity_id);
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

# Rows ANALYZE reads from each index, so it stays quick however large
# the database grows.
ANALYSIS_LIMIT = 1000

# Writes between refreshing the planner's statistics.
OPTIMIZE_WRITES = 10_000

# Analyse every table that needs it, whether or not this connection has
# queried it: a connection may only write, or have only just opened.
OPTIMIZE = "pragma optimize = 0x10002"

# Index tables, each with an entity_id column, other than labels_fts,
# which follows labels.
INDEXES = ("labels", "links", "strings", "snaks")

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
        if 0 < version < VERSION:
            self._upgrade()
        else:
            self.db.executescript(SCHEMA)
            self.db.execute(f"pragma user_version = {VERSION}")

        # Entities written in the current transaction, if there is one.
        self.active = False
        self.written: Set[str] = set()

        # Entity revisions written, in all, and at the last refresh of
        # the planner's statistics.
        self.writes = 0
        self.optimized = 0

        self.db.execute(f"pragma analysis_limit = {ANALYSIS_LIMIT}")
        self.optimize()

    def close(self):
        self.db.close()

    def optimize(self):
        """Refresh the query planner's statistics, if they need it.

        Without them, it can start a join from the wrong end: from every
        link of a property, not one entity's.  SQLite only analyses what
        has changed a lot since it was last analysed, so it's cheap when
        there's nothing to do."""
        self.db.execute(OPTIMIZE)
        self.optimized = self.writes

    def _upgrade(self):
        """Upgrade the schema from an earlier version, rebuilding the
        index tables from the documents, all or none."""
        drops = "".join(f"drop table if exists {table}; " for table in ("labels_fts", *INDEXES))
        self.db.executescript(f"begin immediate; {drops}{SCHEMA}")
        try:
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
            if self.writes - self.optimized >= OPTIMIZE_WRITES:
                self.optimize()

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
        # The triggers on labels keep labels_fts in step.
        self.db.executemany("insert into labels (entity_id, language, "
                            "role, text, folded) values (?, ?, ?, ?, ?)",
                            [(entity.id, language, role, text, fold(text))
                             for language, role, text in names])
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
                   role: str,
                   after: str = "",
                   limit: Optional[int] = None) -> List[str]:
        """Return the identifiers of entities linking to one, in order:
        those after the identifier after, and at most limit, if given.

        With a property, they're read in order from links_by_target, so
        a batch costs its own size, however many link to the entity;
        without one, they're sorted."""
        condition = "" if property_id is None else "and l.property_id = ? "
        args = [target_id, *([] if property_id is None else [property_id]),
                role, after, -1 if limit is None else limit]
        return self._ids("select l.entity_id from links l "
                         "join entities e on e.id = l.entity_id "
                         f"where l.target_id = ? {condition}and l.role = ? "
                         f"and l.entity_id > ? and {LIVE} "
                         "group by l.entity_id order by l.entity_id limit ?",
                         *args)

    def subclasses(self, class_id: str) -> List[str]:
        """Return a class, and its subclasses, at any depth."""
        return self._ids("with recursive classes (id) as ("
                         "  select ? union"
                         "  select l.entity_id from links l"
                         "  join classes c on l.target_id = c.id"
                         "  where l.property_id = ? and l.role = 'main') "
                         "select id from classes order by 1",
                         class_id, SUBCLASS_OF)

    def instances(self,
                  class_id: str,
                  after: str = "",
                  limit: Optional[int] = None) -> List[str]:
        """Return the identifiers of instances of a class, or of any of
        its subclasses, in order: those after the identifier after, and
        at most limit, if given.

        Each class's are read in order from links_by_property, at most
        limit of them, and merged, so a batch costs at most its size
        for each class, however many instances there are."""
        batches = [self._ids("select l.entity_id from links l "
                             "join entities e on e.id = l.entity_id "
                             "where l.property_id = ? and l.target_id = ? "
                             "and l.role = 'main' and l.entity_id > ? "
                             f"and {LIVE} group by l.entity_id "
                             "order by l.entity_id limit ?",
                             INSTANCE_OF, c, after,
                             -1 if limit is None else limit)
                   for c in self.subclasses(class_id)]
        found: List[str] = []
        for entity_id in heapq.merge(*batches):
            if found and found[-1] == entity_id:
                continue
            if limit is not None and len(found) == limit:
                break
            found.append(entity_id)
        return found

    def instances_among(self, ids: Sequence[str], class_id: str) -> List[str]:
        """Return those of some identifiers that are of instances of a
        class, or of any of its subclasses, in order."""
        classes = self.subclasses(class_id)
        marks = ", ".join("?" * len(classes))
        found: Set[str] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            found.update(self._ids(
                "select l.entity_id from links l "
                "join entities e on e.id = l.entity_id "
                f"where l.property_id = ? and l.role = 'main' and {LIVE} "
                f"and l.target_id in ({marks}) "
                f"and l.entity_id in ({', '.join('?' * len(chunk))})",
                INSTANCE_OF, *classes, *chunk))
        return sorted(found)

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
