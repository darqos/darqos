# darqos
# Copyright (C) 2026 David Arnold

# PDF type implementation: items of com.adobe.pdf, each a PDF, read-only,
# kept in a Storage blob (see doc/pdf-type.rst).
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Pdf protocol
# (lib/didl/pdf.didl) on a port for each client of an item.
#
# An item's identifier is its type and its blob's Storage key, in hex.
# The blob is a header and then the PDF.  An open item is shared by its
# clients' ports.
#
# When an item's created, its title and author are published to the
# Index as names, and its text as its body, and it's withdrawn when it's
# destroyed.  Reading a long PDF's text takes up to a second or so, which
# holds up other requests, but it's only done then, when its entity's
# changed, and for a reindex.
#
# Every item's an entity (see doc/entity-vs-item.rst): it inherits the
# entity type, darq.entity_type's EntityType, which serves an item's
# entity on a port from OpenEntity.  An item's entity is made with it,
# identified by its specifier, and named by the PDF's title, or else the
# file it came from; its names are published to the Index with its own.

import logging
import re
import struct

from PyQt6.QtCore import QCoreApplication

import darq
import darq.history
import darq.index
import darq.kb as model
from darq.entity_type import EntityType
from darq.protocols import storage
from darq.protocols.pdf import (
    Viewed,
    CloseRequest,
    CloseResponse,
    GetPositionRequest,
    GetPositionResponse,
    GetRequest,
    GetResponse,
    Pdf,
    SetPositionRequest,
    SetPositionResponse,
    StatRequest,
    StatResponse,
)
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

import pdfdoc


TYPE_ID = "com.adobe.pdf"
MIME_TYPE = "application/pdf"

# Blob header: magic, format version, and flags.  The second version has
# the length of the reader's position next, and then the position: the
# first's are still read, and written as the second when they change.
HEADER = struct.Struct(">4sHH")
POSITION_LENGTH = struct.Struct(">I")
MAGIC = b"DPDF"
FORMAT_VERSION = 2

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Tries at writing a position.
SAVE_ATTEMPTS = 3

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")


class FormatError(ValueError):
    """A blob isn't a PDF item."""


def encode_blob(data: bytes, flags: int = 0, position: str = "") -> bytes:
    encoded = position.encode()
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags) + POSITION_LENGTH.pack(len(encoded)) + encoded + data


def decode_blob(value: bytes) -> tuple[bytes, int, str]:
    """Return a blob's PDF, flags and position.

    :raises FormatError: if it isn't a PDF item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags = HEADER.unpack_from(value)
    if magic != MAGIC or version not in (1, FORMAT_VERSION):
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    if version == 1:
        return value[HEADER.size:], flags, ""
    start = HEADER.size + POSITION_LENGTH.size
    if len(value) < start:
        raise FormatError(f"{len(value)} bytes is too short")
    [length] = POSITION_LENGTH.unpack_from(value, HEADER.size)
    return value[start + length:], flags, value[start:start + length].decode("utf-8", "replace")


def specifier(key: storage.Key) -> str:
    """Return an item's specifier, which is its entity's identifier too."""
    return f"{key.high:016x}{key.low:016x}"


def item_id(key: storage.Key) -> str:
    return f"{TYPE_ID}:{specifier(key)}"


def parse_item_id(iid: str) -> storage.Key | None:
    """Return an item's Storage key, or None if it isn't one of ours.

    :raises InvalidIdentifierError: if it isn't a valid item identifier."""
    type_id, specifier = darq.split_item_id(iid)
    if type_id != TYPE_ID or not SPECIFIER.fullmatch(specifier):
        return None
    return storage.Key(int(specifier[:16], 16), int(specifier[16:], 16))


def index_entry(data: bytes, info: pdfdoc.Info) -> tuple[list[str], str]:
    """Return an item's names and body, for the Index.

    :raises pdfdoc.PdfError: if its data can't be read."""
    return [name for name in (info.title, info.author) if name], pdfdoc.text(data)


class Document:
    """An open item, or one being looked at."""

    def __init__(self, key: storage.Key, generation: int, data: bytes, info: pdfdoc.Info, flags: int = 0,
                 position: str = ""):
        self.key = key
        self.generation = generation
        self.data = data
        self.info = info
        self.flags = flags
        self.position = position

        # The ports of the clients that have it open.
        self.ports: set[int] = set()

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.key)


class PdfImplementation(EntityType):
    """Answers the Implementation protocol, and keeps the open items."""

    SOURCE = "pdf-type"

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, [TYPE_ID])
        self.storage = client
        self.index = index
        self.documents: dict[tuple[int, int], Document] = {}

    # Items

    def attach(self, document: Document) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "PdfImplementation isn't being served"
        port = darq.open_port()
        self.documents[document.id] = document
        document.ports.add(port)
        self.server.add(port, PdfItem(self, document, port))
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

        :raises FormatError: if its blob isn't a PDF item."""
        document = self.documents.get((key.high, key.low))
        if document is not None:
            return document if not document.flags & DESTROYED else None

        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        data, flags, position = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        try:
            info = pdfdoc.inspect(data)
        except pdfdoc.PdfError as e:
            raise FormatError(str(e)) from None
        return Document(key, response.generation, data, info, flags, position)

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

    @staticmethod
    def check_type(type_id: str) -> Status:
        try:
            canonical = darq.canonical_type_id(type_id)
        except darq.InvalidIdentifierError:
            return Status.INVALID_ID
        return Status.OK if canonical == TYPE_ID else Status.NOT_FOUND

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        """A PDF can't be empty: it's only made from data (see CreateFrom)."""
        status = self.check_type(request.type_id)
        return CreateResponse(status=status if status != Status.OK else Status.UNSUPPORTED)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        try:
            info = pdfdoc.inspect(request.data)
        except pdfdoc.PdfError as e:
            logging.info(f"Not a PDF: {e}")
            return CreateFromResponse(status=Status.INVALID_DATA)
        response = self.storage.create(value=encode_blob(request.data))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create a PDF blob: {response.status.name}")
            return CreateFromResponse(status=Status.FAILED)
        document = Document(response.key, response.generation, request.data, info)
        logging.info(f"Created {document.iid}")
        self.make_entity(TYPE_ID, specifier(document.key), info.title, request.name)
        self.publish(document)
        self.record(document.iid, darq.history.CREATED, request.context)
        return CreateFromResponse(item_id=document.iid, item=self.attach(document), status=Status.OK)

    def entry(self, document: Document) -> tuple[list[str], str]:
        """Return an item's entry for the Index: its entity's names, with
        its own, and its text.

        :raises pdfdoc.PdfError: if its data can't be read."""
        own, body = index_entry(document.data, document.info)
        return list(dict.fromkeys([*self.entity_names(specifier(document.key)), *own])), body

    def publish(self, document: Document):
        """Publish an item's entry to the Index."""
        try:
            names, body = self.entry(document)
        except pdfdoc.PdfError as e:
            logging.error(f"Can't index {document.iid}: {e}")
            return
        self.index.publish(document.iid, names, body)

    def adopt(self, item_id: str) -> bool:
        """Give an item made before there were entities its entity, named by
        the PDF's title, if it has one."""
        document, _ = self.find(item_id)
        if document is None:
            return False
        return self.adopt_entity(TYPE_ID, specifier(document.key), document.info.title)

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        document, _ = self.find(model.item_id(after.id, TYPE_ID))
        if document is not None:
            self.publish(document)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return OpenResponse(status=status)
        self.adopt_entity(TYPE_ID, specifier(document.key), document.info.title)
        return OpenResponse(item=self.attach(document), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return IndexEntryResponse(status=status)
        try:
            names, body = self.entry(document)
        except pdfdoc.PdfError as e:
            logging.error(f"Can't index {request.item_id}: {e}")
            return IndexEntryResponse(status=Status.FAILED)
        return IndexEntryResponse(names=names, body=body, status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return ExportResponse(status=status)
        return ExportResponse(data=document.data, mime_type=MIME_TYPE, status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        document, status = self.find(request.item_id)
        if document is None:
            return DestroyResponse(status=status)
        flags = document.flags | DESTROYED
        try:
            response = self.storage.put(key=document.key, expected=document.generation,
                                        value=encode_blob(document.data, flags, document.position))
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
        self.record(document.iid, darq.history.DESTROYED, request.context)
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class PdfItem(Pdf.Handler):
    """Answers the Pdf protocol, on one client's port for an item."""

    def __init__(self, implementation: PdfImplementation, document: Document, port: int):
        self.implementation = implementation
        self.document = document
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.document, self.port)
        return CloseResponse()

    def viewed(self, request: Viewed, source: int) -> None:
        self.implementation.record(self.document.iid, darq.history.VIEWED, source=request.source, note=request.note)

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(data=self.document.data)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        return StatResponse(size=len(self.document.data), pages=self.document.info.pages)

    def get_position(self, request: GetPositionRequest, source: int) -> GetPositionResponse:
        return GetPositionResponse(position=self.document.position)

    def set_position(self, request: SetPositionRequest, source: int) -> SetPositionResponse:
        """Record a position: it's rewriting the whole blob, so one that's
        lost isn't worth failing for."""
        document = self.document
        if request.position == document.position:
            return SetPositionResponse()
        client = self.implementation.storage
        for _ in range(SAVE_ATTEMPTS):
            try:
                response = client.put(key=document.key, expected=document.generation,
                                      value=encode_blob(document.data, document.flags, request.position))
                if response.status == storage.Status.OK:
                    document.generation = response.generation
                    document.position = request.position
                    break
                logging.warning(f"Can't save the position of {document.iid}: {response.status.name}")
                document.generation = client.stat(key=document.key).generation
            except (darq.DarqError, TimeoutError) as e:
                logging.warning(f"Can't save the position of {document.iid}: {e!r}")
                break
        return SetPositionResponse()


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    # Qt PDF needs an application, but this isn't a GUI: it has no display.
    app = QCoreApplication([])

    implementation = PdfImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                       darq.index.Publisher(), model.KB.connect(timeout=10))

    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
    del app
