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

from darq.os.base import Service

import hashlib
import sqlite3
import sys
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

        self.context = None
        self.socket = None
        self.active = False

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

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind("tcp://*:11001")
        self.active = True

        while self.active:
            message = self.socket.recv()
            self.handle_request(message)

        return

    def handle_request(self, message):
        return

    def send_response(self):
        self.socket.send()
        return

    def close(self):
        self.socket.close()
        self.socket = None

        self.context.destroy()
        self.context = None
        return

    def run(self) -> int:
        self.listen()
        self.close()
        return 0


if __name__ == "__main__":
    service = StorageService()
    result = service.run()
    sys.exit(result)
