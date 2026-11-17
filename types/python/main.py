# darqos
# Copyright (C) 2026 David Arnold

# Python package type implementation: items of darq.python, each a Python
# package, a zip of its modules, in a Storage blob, which can be imported
# (see darq.packages), and run, and is a REPL session's code (see
# doc/python-type.rst).
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Python protocol
# (lib/didl/python.didl) on a port for each client of an item.
#
# An item's identifier is its type and its blob's Storage key, in hex.
# The blob is a header, where it's marked destroyed, and then the zip.
#
# Every item has an entity (see doc/item.rst): it inherits ItemType,
# from darq.item_type, which serves an item's entity on a port from
# OpenEntity.  An item's entity is made with it, identified by its
# specifier, and its label is the package's name, as it's imported.
# Its entity's names are published to the Index.

import logging
import re
import struct

import darq
import darq.history
import darq.index
import darq.items
import darq.kb as model
import darq.packages as packages
from darq.item_type import ItemType
from darq.protocols import storage
from darq.protocols.python import CloseRequest, CloseResponse, GetRequest, GetResponse, PutRequest, PutResponse, Python
from darq.protocols.python import Status as PythonStatus
from darq.protocols.python import Viewed
from darq.protocols.type import (
    CreateFromRequest,
    CreateFromResponse,
    CreateRequest,
    CreateResponse,
    DestroyRequest,
    DestroyResponse,
    ExportRequest,
    ExportResponse,
    IndexEntryRequest,
    IndexEntryResponse,
    OpenRequest,
    OpenResponse,
    Ready,
    Status,
)


TYPE_ID = packages.TYPE_ID

# Its media type, as exported.
MEDIA_TYPE = "application/vnd.darq.python+zip"

# Blob header: magic, format version, and flags; then the zip.
HEADER = struct.Struct(">4sHH")
MAGIC = b"DPYP"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")


class FormatError(ValueError):
    """A blob isn't a package item."""


def encode_blob(package: bytes, flags: int = 0) -> bytes:
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags) + package


def decode_blob(value: bytes) -> tuple[bytes, int]:
    """Return a blob's package and flags.

    :raises FormatError: if it isn't a package item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    return value[HEADER.size:], flags


def specifier(key: storage.Key) -> str:
    """Return an item's specifier, which is its entity's identifier too."""
    return f"{key.high:016x}{key.low:016x}"


def item_id(key: storage.Key) -> str:
    return f"{TYPE_ID}:{specifier(key)}"


def parse_item_id(iid: str) -> storage.Key | None:
    """Return an item's Storage key, or None if it isn't one of ours.

    :raises InvalidIdentifierError: if it isn't a valid item identifier."""
    type_id, spec = darq.split_item_id(iid)
    if type_id != TYPE_ID or not SPECIFIER.fullmatch(spec):
        return None
    return storage.Key(int(spec[:16], 16), int(spec[16:], 16))


class Package:
    """An open item."""

    def __init__(self, key: storage.Key, generation: int, data: bytes, flags: int = 0):
        self.key = key
        self.generation = generation
        self.data = data
        self.flags = flags

        # The ports of the clients that have it open.
        self.ports: set[int] = set()

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.key)


class PythonImplementation(ItemType):
    """Answers the Implementation protocol, and keeps the open items."""

    SOURCE = "python-type"

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, [TYPE_ID])
        self.storage = client
        self.index = index
        self.packages: dict[tuple[int, int], Package] = {}

    # Items

    def attach(self, package: Package) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "PythonImplementation isn't being served"
        port = darq.open_port()
        self.packages[package.id] = package
        package.ports.add(port)
        self.server.add(port, PythonItem(self, package, port))
        return port

    def detach(self, package: Package, port: int):
        """Forget a client's port, once it's closed the item, and the
        item, if that was its last client."""
        assert self.server is not None
        package.ports.discard(port)
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

        if not package.ports:
            del self.packages[package.id]

    def load(self, key: storage.Key) -> Package | None:
        """Return an open item, or load it, or None if it isn't found or
        is destroyed.

        :raises FormatError: if its blob isn't a package item."""
        package = self.packages.get((key.high, key.low))
        if package is not None:
            return package if not package.flags & DESTROYED else None
        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        data, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        return Package(key, response.generation, data, flags)

    def find(self, iid: str) -> tuple[Package | None, Status]:
        """Return an item, by its identifier, or why it can't be."""
        try:
            key = parse_item_id(iid)
        except darq.errors.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if key is None:
            return None, Status.NOT_FOUND
        try:
            package = self.load(key)
        except FormatError as e:
            logging.error(f"Can't load {iid}: {e}")
            return None, Status.FAILED
        return package, Status.OK if package is not None else Status.NOT_FOUND

    def publish(self, package: Package):
        """Publish an item's entity's names to the Index."""
        self.index.publish(package.iid, self.entity_names(specifier(package.key)))

    def put(self, package: Package, expected: int, data: bytes, source: str) -> PutResponse:
        """Replace a package, if it's still the generation expected."""
        try:
            packages.check(data)
        except packages.PackageError as e:
            return PutResponse(status=PythonStatus.INVALID, message=str(e))
        if expected != package.generation:
            return PutResponse(generation=package.generation, status=PythonStatus.CONFLICT,
                               message=f"It's generation {package.generation} now, not {expected}")
        try:
            response = self.storage.put(key=package.key, expected=package.generation,
                                        value=encode_blob(data, package.flags))
        except (darq.errors.DarqError, TimeoutError) as e:
            logging.error(f"Can't keep {package.iid}: {e!r}")
            return PutResponse(status=PythonStatus.FAILED, message=str(e))
        if response.status == storage.Status.CONFLICT:
            return PutResponse(generation=package.generation, status=PythonStatus.CONFLICT,
                               message="It was changed elsewhere")
        if response.status != storage.Status.OK:
            logging.error(f"Can't keep {package.iid}: {response.status.name}")
            return PutResponse(status=PythonStatus.FAILED, message=response.status.name)
        package.data = data
        package.generation = response.generation
        self.record(package.iid, darq.history.EDITED, source=source)
        return PutResponse(generation=package.generation, status=PythonStatus.OK)

    @staticmethod
    def check_type(type_id: str) -> Status:
        try:
            return Status.OK if darq.canonical_type_id(type_id) == TYPE_ID else Status.NOT_FOUND
        except darq.errors.InvalidIdentifierError:
            return Status.INVALID_ID

    def keep(self, data: bytes, name: str, context) -> tuple[Package | None, Status]:
        """Keep a new package, named, and make its entity."""
        response = self.storage.create(value=encode_blob(data))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create a package blob: {response.status.name}")
            return None, Status.FAILED
        package = Package(response.key, response.generation, data)
        logging.info(f"Created {package.iid}: {name or 'unnamed'}, {len(data)} bytes")
        self.make_entity(TYPE_ID, specifier(package.key), title=name)
        self.publish(package)
        self.record(package.iid, darq.history.CREATED, context)
        return package, Status.OK

    # The entity

    def adopt(self, item_id: str) -> bool:
        package, _ = self.find(item_id)
        return package is not None and self.adopt_entity(TYPE_ID, specifier(package.key))

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        package, _ = self.find(model.item_id(after.id, TYPE_ID))
        if package is not None:
            self.publish(package)

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        """A new, empty, package, with no name, so it can't be imported
        till it's named."""
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateResponse(status=status)
        package, status = self.keep(packages.make({}), "", request.context)
        if package is None:
            return CreateResponse(status=status)
        return CreateResponse(item_id=package.iid, item=self.attach(package), status=Status.OK)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        """A package, from its zip, named as it's to be imported."""
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        try:
            packages.check(request.data)
        except packages.PackageError as e:
            logging.info(f"Not a package: {e}")
            return CreateFromResponse(status=Status.FAILED)
        package, status = self.keep(request.data, request.name, request.context)
        if package is None:
            return CreateFromResponse(status=status)
        return CreateFromResponse(item_id=package.iid, item=self.attach(package), status=Status.OK)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        package, status = self.find(request.item_id)
        if package is None:
            return OpenResponse(status=status)
        self.adopt_entity(TYPE_ID, specifier(package.key))
        return OpenResponse(item=self.attach(package), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        package, status = self.find(request.item_id)
        if package is None:
            return IndexEntryResponse(status=status)
        return IndexEntryResponse(names=self.entity_names(specifier(package.key)), status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        package, status = self.find(request.item_id)
        if package is None:
            return ExportResponse(status=status)
        return ExportResponse(data=package.data, mime_type=MEDIA_TYPE, status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        package, status = self.find(request.item_id)
        if package is None:
            return DestroyResponse(status=status)
        flags = package.flags | DESTROYED
        try:
            response = self.storage.put(key=package.key, expected=package.generation,
                                        value=encode_blob(package.data, flags))
        except (darq.errors.DarqError, TimeoutError) as e:
            logging.error(f"Can't destroy {request.item_id}: {e!r}")
            return DestroyResponse(status=Status.FAILED)
        if response.status != storage.Status.OK:
            logging.error(f"Can't destroy {request.item_id}: {response.status.name}")
            return DestroyResponse(status=Status.FAILED)
        package.flags = flags
        package.generation = response.generation
        self.index.withdraw(package.iid)
        self.destroy_item_entity(specifier(package.key))
        self.record(package.iid, darq.history.DESTROYED, request.context)
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class PythonItem(Python.Handler):
    """Answers the Python protocol, on one client's port for an item."""

    def __init__(self, implementation: PythonImplementation, package: Package, port: int):
        self.implementation = implementation
        self.package = package
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.package, self.port)
        return CloseResponse()

    def viewed(self, request: Viewed, source: int) -> None:
        self.implementation.record(self.package.iid, darq.history.VIEWED, source=request.source, note=request.note)

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(package=self.package.data, generation=self.package.generation)

    def put(self, request: PutRequest, source: int) -> PutResponse:
        return self.implementation.put(self.package, request.expected, request.package, request.source)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = PythonImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                          darq.index.Publisher(), model.KB.connect(timeout=10))
    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
