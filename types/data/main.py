# darqos
# Copyright (C) 2026 David Arnold

# Data type implementation: items of public.data, each a file of a type
# DarqOS has no implementation for, its bytes kept as they came, in a
# Storage blob, with their media type (see doc/data-type.rst).
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Data protocol
# (lib/didl/data.didl) on a port for each client of an item.
#
# An item's identifier is its type and its blob's Storage key, in hex.
# The blob is a header, where it's marked destroyed, its media type, and
# then the bytes.  It takes anything, so it's what's made of what no
# other type takes, by darq.launch.import_data and the launcher.
#
# Every item's an entity (see doc/entity-vs-item.rst): it inherits the
# entity type, darq.entity_type's EntityType, which serves an item's
# entity on a port from OpenEntity.  An item's entity is made with it,
# identified by its specifier, named from the file it came from, with
# its file name, media type (P1163) and extension (P1195).  Its entity's
# names are published to the Index: there's nothing in it to read.

import logging
import mimetypes
import pathlib
import re
import struct

import darq
import darq.index
import darq.items
import darq.kb as model
from darq.entity_type import EntityType
from darq.protocols import storage
from darq.protocols.data import CloseRequest, CloseResponse, Data, GetRequest, GetResponse, StatRequest, StatResponse
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


TYPE_ID = darq.items.DATA_TYPE

# The media type of bytes that are no more than that.
OCTET_STREAM = "application/octet-stream"

# Blob header: magic, format version, flags, and the length of the media
# type, which comes next, and then the bytes.
HEADER = struct.Struct(">4sHHH")
MAGIC = b"DDAT"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")


class FormatError(ValueError):
    """A blob isn't a data item."""


def encode_blob(data: bytes, media_type: str, flags: int = 0) -> bytes:
    encoded = media_type.encode()
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags, len(encoded)) + encoded + data


def decode_blob(value: bytes) -> tuple[bytes, str, int]:
    """Return a blob's bytes, media type and flags.

    :raises FormatError: if it isn't a data item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags, length = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    start = HEADER.size + length
    try:
        media_type = value[HEADER.size:start].decode()
    except UnicodeDecodeError as e:
        raise FormatError(str(e)) from None
    return value[start:], media_type, flags


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


def extension_of(name: str) -> str:
    """Return a file name's extension, without its dot."""
    return pathlib.PurePath(name).suffix.removeprefix(".") if name else ""


def media_type_of(given: str, name: str) -> str:
    """Return the media type of data: the one it came with, without its
    parameters' case changed, or else the one its name's extension
    suggests, or else no more than bytes."""
    given = given.strip()
    if given:
        return given
    guessed, _ = mimetypes.guess_type(name) if name else (None, None)
    return guessed or OCTET_STREAM


class Document:
    """An open item."""

    def __init__(self, key: storage.Key, generation: int, data: bytes, media_type: str, flags: int = 0):
        self.key = key
        self.generation = generation
        self.data = data
        self.media_type = media_type
        self.flags = flags

        # The ports of the clients that have it open.
        self.ports: set[int] = set()

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.key)


class DataImplementation(EntityType):
    """Answers the Implementation protocol, and keeps the open items."""

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, [TYPE_ID])
        self.storage = client
        self.index = index
        self.documents: dict[tuple[int, int], Document] = {}

    # Items

    def attach(self, document: Document) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "DataImplementation isn't being served"
        port = darq.open_port()
        self.documents[document.id] = document
        document.ports.add(port)
        self.server.add(port, DataItem(self, document, port))
        return port

    def detach(self, document: Document, port: int):
        """Forget a client's port, once it's closed the item, and the
        item, if that was its last client."""
        assert self.server is not None
        document.ports.discard(port)
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

        if not document.ports:
            del self.documents[document.id]

    def load(self, key: storage.Key) -> Document | None:
        """Return an open item, or load it, or None if it isn't found or
        is destroyed.

        :raises FormatError: if its blob isn't a data item."""
        document = self.documents.get((key.high, key.low))
        if document is not None:
            return document if not document.flags & DESTROYED else None
        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        data, media_type, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        return Document(key, response.generation, data, media_type, flags)

    def find(self, iid: str) -> tuple[Document | None, Status]:
        """Return an item, by its identifier, or why it can't be."""
        try:
            key = parse_item_id(iid)
        except darq.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if key is None:
            return None, Status.NOT_FOUND
        try:
            document = self.load(key)
        except FormatError as e:
            logging.error(f"Can't load {iid}: {e}")
            return None, Status.FAILED
        return document, Status.OK if document is not None else Status.NOT_FOUND

    def publish(self, document: Document):
        """Publish an item's entity's names to the Index."""
        self.index.publish(document.iid, self.entity_names(specifier(document.key)))

    @staticmethod
    def check_type(type_id: str) -> Status:
        try:
            return Status.OK if darq.canonical_type_id(type_id) == TYPE_ID else Status.NOT_FOUND
        except darq.InvalidIdentifierError:
            return Status.INVALID_ID

    # The entity

    def adopt(self, item_id: str) -> bool:
        document, _ = self.find(item_id)
        if document is None:
            return False
        return self.adopt_entity(TYPE_ID, specifier(document.key),
                                 statements=[(darq.items.MEDIA_TYPE, model.StringValue(document.media_type))])

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        document, _ = self.find(model.item_id(after.id, TYPE_ID))
        if document is not None:
            self.publish(document)

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        """Data's only made from data (see CreateFrom)."""
        status = self.check_type(request.type_id)
        return CreateResponse(status=status if status != Status.OK else Status.UNSUPPORTED)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        """Keep any data at all, with its media type, and the file it came
        from as its entity's name."""
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        media_type = media_type_of(request.media_type, request.name)
        response = self.storage.create(value=encode_blob(request.data, media_type))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create a data blob: {response.status.name}")
            return CreateFromResponse(status=Status.FAILED)
        document = Document(response.key, response.generation, request.data, media_type)
        logging.info(f"Created {document.iid}: {media_type}, {len(request.data)} bytes")
        statements: list[tuple[str, model.Value]] = [(darq.items.MEDIA_TYPE, model.StringValue(media_type))]
        if extension_of(request.name):
            statements.append((darq.items.FILE_EXTENSION, model.StringValue(extension_of(request.name))))
        self.make_entity(TYPE_ID, specifier(document.key), file_name=request.name, statements=statements)
        self.publish(document)
        return CreateFromResponse(item_id=document.iid, item=self.attach(document), status=Status.OK)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return OpenResponse(status=status)
        self.adopt_entity(TYPE_ID, specifier(document.key),
                          statements=[(darq.items.MEDIA_TYPE, model.StringValue(document.media_type))])
        return OpenResponse(item=self.attach(document), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return IndexEntryResponse(status=status)
        return IndexEntryResponse(names=self.entity_names(specifier(document.key)), status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return ExportResponse(status=status)
        return ExportResponse(data=document.data, mime_type=document.media_type, status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        document, status = self.find(request.item_id)
        if document is None:
            return DestroyResponse(status=status)
        flags = document.flags | DESTROYED
        try:
            response = self.storage.put(key=document.key, expected=document.generation,
                                        value=encode_blob(document.data, document.media_type, flags))
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't destroy {request.item_id}: {e!r}")
            return DestroyResponse(status=Status.FAILED)
        if response.status != storage.Status.OK:
            logging.error(f"Can't destroy {request.item_id}: {response.status.name}")
            return DestroyResponse(status=Status.FAILED)
        document.flags = flags
        document.generation = response.generation
        self.index.withdraw(document.iid)
        self.destroy_item_entity(specifier(document.key))
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class DataItem(Data.Handler):
    """Answers the Data protocol, on one client's port for an item."""

    def __init__(self, implementation: DataImplementation, document: Document, port: int):
        self.implementation = implementation
        self.document = document
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.document, self.port)
        return CloseResponse()

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(data=self.document.data, media_type=self.document.media_type)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        return StatResponse(size=len(self.document.data), media_type=self.document.media_type)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = DataImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                        darq.index.Publisher(), model.KB.connect(timeout=10))
    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
