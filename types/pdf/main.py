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
# holds up other requests, but it's only done then, and for a reindex.

import logging
import re
import struct

from PyQt6.QtCore import QCoreApplication

import darq
import darq.index
from darq.protocols import storage
from darq.protocols.pdf import (
    CloseRequest,
    CloseResponse,
    GetRequest,
    GetResponse,
    Pdf,
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
    Implementation,
    OpenRequest,
    OpenResponse,
    Ready,
    Status,
)

import pdfdoc


TYPE_ID = "com.adobe.pdf"
MIME_TYPE = "application/pdf"

# Blob header: magic, format version, and flags.
HEADER = struct.Struct(">4sHH")
MAGIC = b"DPDF"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")


class FormatError(ValueError):
    """A blob isn't a PDF item."""


def encode_blob(data: bytes, flags: int = 0) -> bytes:
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags) + data


def decode_blob(value: bytes) -> tuple[bytes, int]:
    """Return a blob's PDF and flags.

    :raises FormatError: if it isn't a PDF item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    return value[HEADER.size:], flags


def item_id(key: storage.Key) -> str:
    return f"{TYPE_ID}:{key.high:016x}{key.low:016x}"


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

    def __init__(self, key: storage.Key, generation: int, data: bytes, info: pdfdoc.Info, flags: int = 0):
        self.key = key
        self.generation = generation
        self.data = data
        self.info = info
        self.flags = flags

        # The ports of the clients that have it open.
        self.ports: set[int] = set()

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.key)


class PdfImplementation(Implementation.Handler):
    """Answers the Implementation protocol, and keeps the open items."""

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher):
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
        data, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        try:
            info = pdfdoc.inspect(data)
        except pdfdoc.PdfError as e:
            raise FormatError(str(e)) from None
        return Document(key, response.generation, data, info, flags)

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
        self.publish(document)
        return CreateFromResponse(item_id=document.iid, item=self.attach(document), status=Status.OK)

    def publish(self, document: Document):
        """Publish an item's entry to the Index."""
        try:
            names, body = index_entry(document.data, document.info)
        except pdfdoc.PdfError as e:
            logging.error(f"Can't index {document.iid}: {e}")
            return
        self.index.publish(document.iid, names, body)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return OpenResponse(status=status)
        return OpenResponse(item=self.attach(document), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return IndexEntryResponse(status=status)
        try:
            names, body = index_entry(document.data, document.info)
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
                                        value=encode_blob(document.data, flags))
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't destroy {request.item_id}: {e!r}")
            return DestroyResponse(status=Status.FAILED)
        if response.status != storage.Status.OK:
            logging.error(f"Can't destroy {request.item_id}: {response.status.name}")
            return DestroyResponse(status=Status.FAILED)
        document.flags = flags
        document.generation = response.generation
        self.index.withdraw(document.iid)
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

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(data=self.document.data)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        return StatResponse(size=len(self.document.data), pages=self.document.info.pages)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    # Qt PDF needs an application, but this isn't a GUI: it has no display.
    app = QCoreApplication([])

    implementation = PdfImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                       darq.index.Publisher())

    port = darq.open_port()
    server = darq.Server()
    server.add(port, implementation)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
    del app
