# darqos
# Copyright (C) 2022-2026 David Arnold

# Index service: full-text search of items, registered as darq.index.
#
# It serves the Index protocol (lib/didl/index.didl), as described in
# doc/search-service.rst.  Type implementations publish their items'
# text, replacing what was indexed for each, every time they save one,
# and withdraw them when they're destroyed; anything can search.  The
# entries are kept in a SQLite FTS5 table, in the run directory, which
# only its owner can read: it holds the text of every item.

import logging
import os
import re
import sqlite3
import time
import typing

import darq
from darq.protocols.index import (
    Field,
    FieldKind,
    Index,
    Publish,
    Result,
    SearchRequest,
    SearchResponse,
    Status,
    Withdraw,
)


# Database file, in the run directory.
DATABASE = "index.sqlite"

# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"

# Most bytes of an item's text indexed, in UTF-8: the rest is left out.
MAX_TEXT = 1024 * 1024

# Most results Search returns, and when it's asked for no limit.
MAX_RESULTS = 100

# How much more a match in an item's names counts than one in its body.
NAMES_WEIGHT = 10.0

# Words of context in a result's snippet.
SNIPPET_WORDS = 16

SCHEMA = """
create table if not exists items (
    id integer primary key,
    item_id text not null unique,
    type_id text not null,
    updated integer not null);

create index if not exists items_by_type on items (type_id);

create virtual table if not exists entries using fts5 (
    names,
    body,
    tokenize = 'unicode61 remove_diacritics 2');
"""


def now() -> int:
    """Return the time, in UTC microseconds since the epoch."""
    return time.time_ns() // 1000


def truncate(text: str, limit: int) -> str:
    """Return text cut to at most limit bytes of UTF-8, at a character."""
    encoded = text.encode("utf-8", "surrogatepass")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")


def match_query(text: str) -> str | None:
    """Return the FTS5 query for the items with words starting with each
    of the text's words, or None if it has none."""
    words = re.findall(r"\w+", text)
    if not words:
        return None
    return " ".join(f'"{w}"*' for w in words)


class IndexService(Index.Handler):
    """The items' text, for searching."""

    def __init__(self, path: str, clock: typing.Callable[[], int] = now):
        """Constructor.

        :param path: Database file, created if it doesn't exist.
        :param clock: Returns the time, in UTC microseconds."""
        self.clock = clock

        # SQLite gives its log files the database's permissions.
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        self.db = sqlite3.connect(path)

        # An entry lost in a crash is published again when its item's
        # next saved, or by a reindex: syncing each isn't worth it.
        self.db.execute("pragma journal_mode = wal")
        self.db.execute("pragma synchronous = normal")
        with self.db:
            self.db.executescript(SCHEMA)
        logging.info(f"Index database {path}")

    def close(self):
        self.db.close()

    def publish(self, request: Publish, source: int) -> None:
        try:
            item_id = darq.canonical_item_id(request.item_id)
            type_id, _ = darq.split_item_id(item_id)
        except darq.InvalidIdentifierError as e:
            logging.warning(f"Dropped entry for {request.item_id!r}: {e}")
            return
        self.put(item_id, type_id, request.fields)

    def put(self, item_id: str, type_id: str, fields: typing.Iterable[Field]):
        """Replace an item's entry."""
        names = "\n".join(f.text for f in fields if f.kind == FieldKind.NAMES)
        body = "\n\n".join(f.text for f in fields if f.kind == FieldKind.BODY)
        names = truncate(names, MAX_TEXT)
        body = truncate(body, MAX_TEXT - len(names.encode("utf-8", "surrogatepass")))

        with self.db:
            self.remove(item_id)
            cursor = self.db.execute("insert into items (item_id, type_id, updated) values (?, ?, ?)",
                                     (item_id, type_id, self.clock()))
            self.db.execute("insert into entries (rowid, names, body) values (?, ?, ?)",
                            (cursor.lastrowid, names, body))

    def withdraw(self, request: Withdraw, source: int) -> None:
        try:
            item_id = darq.canonical_item_id(request.item_id)
        except darq.InvalidIdentifierError as e:
            logging.warning(f"Dropped withdrawal of {request.item_id!r}: {e}")
            return
        with self.db:
            self.remove(item_id)

    def remove(self, item_id: str):
        """Forget an item's entry, if it has one, in a transaction."""
        row = self.db.execute("select id from items where item_id = ?", (item_id,)).fetchone()
        if row is not None:
            self.db.execute("delete from entries where rowid = ?", row)
            self.db.execute("delete from items where id = ?", row)

    def search(self, request: SearchRequest, source: int) -> SearchResponse:
        try:
            type_ids = [darq.canonical_type_id(t) for t in request.type_ids]
        except darq.InvalidIdentifierError as e:
            logging.debug(f"search: {e}")
            return SearchResponse(status=Status.INVALID_ARGUMENT)

        query = match_query(request.text)
        if query is None:
            return SearchResponse(status=Status.OK)

        where = "entries match ?"
        parameters: list[str | int] = [query]
        if type_ids:
            where += f" and i.type_id in ({', '.join('?' * len(type_ids))})"
            parameters.extend(type_ids)
        limit = MAX_RESULTS if request.max == 0 else min(request.max, MAX_RESULTS)
        parameters.extend([limit, request.offset])

        rows = self.db.execute(
            f"select i.item_id, snippet(entries, -1, '', '', '...', {SNIPPET_WORDS}), i.updated "
            "from entries join items i on i.id = entries.rowid "
            f"where {where} order by bm25(entries, {NAMES_WEIGHT}, 1.0), i.updated desc "
            "limit ? offset ?", parameters).fetchall()
        return SearchResponse(results=[Result(*row) for row in rows], status=Status.OK)


def darq_main():
    run_dir = os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    darq.serve("darq.index", IndexService(os.path.join(run_dir, DATABASE)))
