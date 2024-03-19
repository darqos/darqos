# DarqOS
# Copyright (C) 2019-2023

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

import base64
import logging
import os
import sqlite3
import sys

import darq


class StorageService(darq.Service):
    """A simple persistent key:value store.

    All system storage (aside from early bootstrap) should use this service.

    Large values should probably be memory-mapped and paged into working
    set as required, rather than being completely loaded all at once."""

    def __init__(self, file: str = None):
        """Constructor.

        :param file: Specify a file name for the blob starage."""

        # Initialise runtime.
        darq.init_callbacks(darq.SelectEventLoop(), self)

        logging.info("Starting Storage service.")

        # Initialise service.
        super().__init__(11001)  # FIXME: hardcoded ports?!?

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

        # Open port.
        darq.open_port(self.port)
        return

    def run(self):
        return darq.loop().run()

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "storage"

    def set(self, key: str, value):
        """Set the value for a key.

        :param key: Key string.
        :param value: Value data."""

        print(f"set({key}, {value})")

        cursor = self.db.cursor()
        cursor.execute("insert into storage values (?, ?)",
                       (key, value))
        self.db.commit()
        return

    def update(self, key: str, value):
        """Update the value for a key.

        :param key: Key string.
        :param value: Value data.

        If 'key' is not already set, return an error."""

        logging.debug(f"update({key}, {value})")

        cursor = self.db.cursor()
        cursor.execute("update storage set value = ? where key = ?",
                       (value, key))
        self.db.commit()
        return

    def exists(self, key: str) -> bool:
        """Check whether key is set.

        :param key: String eky.
        :returns: True if set, False otherwise."""

        cursor = self.db.cursor()
        cursor.execute("select count(key) from storage where key = ?", (key,))
        row = cursor.fetchone()
        cursor.close()

        key_exists = True
        if row is None:
            key_exists = False
        if row[0] != 1:
            key_exists = False

        logging.debug(f"exists({key}) -> {key_exists}")
        return key_exists

    def get(self, key: str):
        """Returns the value for key.

        :param key: String key."""

        cursor = self.db.cursor()
        cursor.execute("select value from storage where key = ?", (key,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            print(f"get({key}) -> None")
            return None
        value = row[0]

        logging.debug(f"get({key}) -> {value}")
        return value

    def delete(self, key: str):
        """Deletes the value for key."""

        logging.debug(f"delete({key})")

        cursor = self.db.cursor()
        cursor.execute("delete from storage where key = ?", (key,))
        self.db.commit()
        return

    def handle_request(self, request: dict):
        """Handle requests."""

        method = request.get("method")
        if method == "set":
            self.set(request["key"], base64.b64decode(request["value"]))
            self.send_reply(request, result=True)
            return

        elif method == "update":
            self.update(request["key"], base64.b64decode(request["value"]))
            self.send_reply(request, result=True)
            return

        elif method == "exists":
            rpc_result = self.exists(request["key"])
            self.send_reply(request, result=rpc_result)
            return

        elif method == "get":
            value = self.get(request["key"])
            self.send_reply(
                request,
                result=value is not None,
                value=base64.b64encode(value).decode() if value else '')
            return

        elif method == "delete":
            self.delete(request["key"])
            self.send_reply(request, result=True)
            return

        else:
            super().handle_request(request)
        return

    def handle_shutdown(self):
        # Clean up database connection.
        self.db.close()
        self.db = None

        super().handle_shutdown()

        logging.info("Storage Service shutdown handled successfully.")
        return


if __name__ == "__main__":
    if os.getenv("INVOCATION_ID") is not None:
        # Running under systemd
        logging.basicConfig(stream=sys.stdout,
                            format='%(levelname)8s %(message)s',
                            level=logging.DEBUG)
    else:
        # Likely being run manually
        logging.basicConfig(stream=sys.stderr,
                            format='%(asctime)s p-Kernel %(levelname)8s %(message)s',
                            level=logging.DEBUG)

    logging.info("Starting storage service.")

    service = StorageService()
    result = service.run()

    logging.info("Exiting storage service.")
    sys.exit(result)
