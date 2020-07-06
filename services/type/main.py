#! /usr/bin/env python3

from darq.os.base import Service, TypeDefinition
from darq.rt.storage import Storage

import orjson
import os
import sys

KEY = "system.typeservice.db"


class TypeService(Service):

    def __init__(self):
        super().__init__()

        # Server endpoint.
        self.context = None
        self.socket = None
        self.active = False

        # Look up database.
        self.storage = Storage.api()
        buf = self.storage.get(KEY)
        typedefs = orjson.loads(buf)

        self.db = {}
        for type_data in typedefs:
            typedef = TypeDefinition.from_dict(type_data)
            self.db[typedef.get_uti()] = typedef
        return

    def persist(self):
        buf = orjson.dumps(self.db.items())
        self.storage.set(KEY, buf)
        return

    def register(self, uti: str, name: str, description: str, url: str):
        """Register the factory implementation for a type.

        :param uti:
        :param name:
        :param description:
        :param url: Storage URL for type implementation"""

        td = TypeDefinition()
        td.uti = uti
        td.name = name
        td.description = description
        td.implementation = url

        self.db[uti] = td
        self.persist()
        return

    def deregister(self, uti: str):
        """Deregister the factory implementation for a type."""

        del self.db[uti]
        self.persist()
        return

    def create(self, uti: str):
        """Create a new instance of a type."""

        td = self.db.get(uti)
        if td is None:
            raise KeyError("No such type")

        buf = self.storage.get(td.implementation)

        path = f"/tmp/{td.uti}"
        f = open(path)
        f.write(buf)
        f.close()

        pid = os.fork()
        if pid == 0:
            os.execv("python", [path])

        return

    def open(self, object_id: str):
        """Open an existing instance of a type."""
        pass

    def handle_request(self, request: dict):

        method = request.get("method")
        if method == "register":
            self.register()

        elif method == "deregister":
            self.deregister()

        elif method == "create":
            self.create()

        elif method == "open":
            self.open()

        else:
            super().handle_request(request)
        return


def main():
    service = TypeService()
    result = service.run()
    sys.exit(result)


if __name__ == "__main__":
    main()

