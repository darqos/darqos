#

# Blob storage service.
#
# Initially, all blobs will be stored as files.  As a subsequent optimisation,
# it might make sense to store smaller objects in a SQLite database or
# something similar instead.
#
# The ideal scenario would be for the stored data to be mapped into the
# memory of the client process, and then fetched from disc as required
# (and using a suitable read-ahead heuristic to improve performance).
# This is largely an issue for the IPC system, however.
#
# For now, let's have a simple API, and a runtime library to match.

from .base import Service

import hashlib
import sqlite3
import zmq


class StorageService(Service):
    """A simple persistent key:value store.

    All system storage (aside from early bootstrap) should use this service.

    Large values should probably be memory-mapped and paged into working
    set as required, rather than being completely loaded all at once."""

    def __init__(self, file: str = None):
        super().__init__()

        if file is None:
            file = "storage.sqlite"

        self.db = sqlite3.connect(file)
        cursor = self.db.cursor()
        cursor.execute("create table if not exists storage ("
                       "key text not null primary key, "
                       "value blob)")
        self.db.commit()
        return

    @staticmethod
    def _hash(key: str):
        """(Internal) Return a prepared key."""
        k = hashlib.sha256(key.encode())
        return k.digest()

    def set(self, key: str, value):
        """Set the value for a key.

        :param key: Key string.
        :param value: Value data."""

        cursor = self.db.cursor()
        cursor.execute("insert into storage value (?, ?)",
                       (self._hash(key), value))
        self.db.commit()
        return

    def update(self, key: str, value):
        """Update the value for a key.

        :param key: Key string.
        :param value: Value data.

        If 'key' is not already set, return an error."""

        cursor = self.db.cursor()
        cursor.execute("update storage set value = ? where key = ?",
                       (self._hash(key), value))
        self.db.commit()
        return

    def has_key(self, key: str) -> bool:
        """Check whether key is set.

        :param key: String eky.
        :returns: True if set, False otherwise."""

        cursor = self.db.cursor()
        cursor.execute("select count(key) from storage where key = ?",
                       (self._hash(key),))
        row = cursor.fetchone()
        if row is None:
            return False
        if row[0] != 1:
            return False
        return True

    def get(self, key: str):
        """Returns the value for key.

        :param key: String key."""

        cursor = self.db.cursor()
        cursor.execute("select value from storage where key = ?",
                       (self._hash(key),))
        row = cursor.fetchone()
        if row is None:
            return None
        value = row[0]
        return value

    def listen(self):
        self.zmq_context = zmq.Context()
        self.socket = self.zmq_context.socket(zmq.REP)
        self.socket.bind("tcp://*:11001")
        return

    def handle_request(self):
        return

    def send_response(self):
        return

    def close(self):
        self.socket.close()
        self.zmq_context.destroy()
        return
