#

# Blob storage service.
#
# Initially, all blobs will be stored in SQLite.  As a subsequent
# optimisation, it might make sense to store larger objects in simple
# files instead.
#
# The ideal scenario would be for the stored data to be mapped into the
# memory of the client process, and then fetched from disc as required
# (and using a suitable read-ahead heuristic to improve performance).
# This is largely an issue for the IPC system, however.
#
# For now, let's have a simple API, and a runtime library to match.

from darq.os.base import Service

import base64
import hashlib
import orjson
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

        print("set(%s, %s)" % (key, str(value)))

        cursor = self.db.cursor()
        cursor.execute("insert into storage values (?, ?)",
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

    def exists(self, key: str) -> bool:
        """Check whether key is set.

        :param key: String eky.
        :returns: True if set, False otherwise."""

        cursor = self.db.cursor()
        cursor.execute("select count(key) from storage where key = ?",
                       (self._hash(key),))
        row = cursor.fetchone()
        cursor.close()
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
        cursor.close()
        if row is None:
            return None
        value = row[0]
        return value

    def delete(self, key: str):
        """Deletes the value for key."""

        cursor = self.db.cursor()
        cursor.execute("delete from storage where key = ?",
                       (self._hash(key),))
        self.db.commit()
        return

    def listen(self):

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind("tcp://*:11001")
        self.active = True

        while self.active:
            message = self.socket.recv_json()
            self.handle_request(message)

        return

    def handle_request(self, request):
        if "method" not in request:
            self.socket.send_json({"error": "No method name specified"})
            return

        print("Request: ", request)

        method = request["method"]
        if method == "set":
            self.set(request["key"], base64.b64decode(request["value"]))

            self.socket.send_json({"method": request["method"],
                                   "xid": request["xid"],
                                   "result": True})
            return

        elif method == "update":
            print("update")

        elif method == "exists":
            rpc_result = self.exists(request["key"])
            reply = {"method": "exists",
                     "xid": request["xid"],
                     "result": rpc_result}
            buf = orjson.dumps(reply)
            self.socket.send(buf)
            return

        elif method == "get":
            value = self.get(request["key"])
            reply = {"method": request["method"],
                     "xid": request["xid"],
                     "result": value is not None,
                     "value": base64.b64encode(value).decode()}
            buf = orjson.dumps(reply)
            self.socket.send(buf)
            return

        elif method == "delete":
            self.delete(request["key"])
            reply = {"method": request["method"],
                     "xid": request["xid"],
                     "result": True}
            buf = orjson.dumps(reply)
            self.socket.send(buf)
            return

        else:
            self.socket.send_json({"method": request["method"],
                                   "result": False})
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
