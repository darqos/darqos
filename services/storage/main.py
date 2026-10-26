# darqos
# Copyright (C) 2019-2026 David Arnold

# Storage service: a persistent blob store, registered as darq.storage.
#
# It serves the Storage protocol (lib/didl/storage.didl), as described
# in doc/storage.rst.  Blobs are kept in a SQLite database in the run
# directory.  As a later optimisation, it might make sense to store
# larger values in plain files instead, or to map them into clients'
# memory.

import logging
import os
import secrets
import sqlite3

import darq
from darq.protocols.storage import (
    CreateRequest,
    CreateResponse,
    DeleteRequest,
    DeleteResponse,
    GetRequest,
    GetResponse,
    Key,
    PutRequest,
    PutResponse,
    StatRequest,
    StatResponse,
    Status,
    Storage,
)


# Database file, in the run directory.
DATABASE = "storage.sqlite"

# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"


def key_bytes(key: Key) -> bytes:
    """Return a key as the database's primary key: 16 bytes, big-endian.

    SQLite integers are signed 64-bit, so they can't hold a uint64."""
    return key.high.to_bytes(8, "big") + key.low.to_bytes(8, "big")


class StorageService(Storage.Handler):
    """A persistent store of blobs, each identified by a random key.

    Keys are 128-bit random integers, allocated by the service: a
    collision with any key ever issued, including deleted ones, is
    vanishingly unlikely, so keys are effectively never reused.  Each
    blob has a generation, which starts at 1 and counts its changes.
    Every change is committed before it's answered."""

    def __init__(self, path: str):
        """Constructor.

        :param path: Database file, created if it doesn't exist."""
        self.db = sqlite3.connect(path)
        self.db.execute("pragma synchronous = full")
        with self.db:
            self.db.execute("create table if not exists blobs ("
                            "key blob not null primary key, "
                            "generation integer not null, "
                            "value blob not null)")
        logging.info(f"Storage database {path}")
        return

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        while True:
            key = Key(secrets.randbits(64), secrets.randbits(64))
            try:
                with self.db:
                    self.db.execute("insert into blobs values (?, 1, ?)", (key_bytes(key), request.value))
                break
            except sqlite3.IntegrityError:
                logging.warning("Random key collided: trying another")

        logging.debug(f"create() -> {key.high:016x}{key.low:016x}, {len(request.value)} bytes")
        return CreateResponse(key=key, generation=1, status=Status.OK)

    def get(self, request: GetRequest, source: int) -> GetResponse:
        row = self.db.execute("select value, generation from blobs where key = ?",
                              (key_bytes(request.key),)).fetchone()
        if row is None:
            return GetResponse(status=Status.NOT_FOUND)
        return GetResponse(value=row[0], generation=row[1], status=Status.OK)

    def put(self, request: PutRequest, source: int) -> PutResponse:
        key = key_bytes(request.key)
        with self.db:
            row = self.db.execute("select generation from blobs where key = ?", (key,)).fetchone()
            if row is None:
                return PutResponse(status=Status.NOT_FOUND)
            if request.expected not in (0, row[0]):
                # Someone else changed it: report the generation it's at.
                return PutResponse(generation=row[0], status=Status.CONFLICT)

            generation = row[0] + 1
            self.db.execute("update blobs set value = ?, generation = ? where key = ?",
                            (request.value, generation, key))
        return PutResponse(generation=generation, status=Status.OK)

    def delete(self, request: DeleteRequest, source: int) -> DeleteResponse:
        with self.db:
            cursor = self.db.execute("delete from blobs where key = ?", (key_bytes(request.key),))
        return DeleteResponse(status=Status.OK if cursor.rowcount == 1 else Status.NOT_FOUND)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        row = self.db.execute("select length(value), generation from blobs where key = ?",
                              (key_bytes(request.key),)).fetchone()
        if row is None:
            return StatResponse(status=Status.NOT_FOUND)
        return StatResponse(size=row[0], generation=row[1], status=Status.OK)


def darq_main():
    run_dir = os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    darq.serve("darq.storage", StorageService(os.path.join(run_dir, DATABASE)))
