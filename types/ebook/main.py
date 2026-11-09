# darqos
# Copyright (C) 2026 David Arnold

# E-book type implementation: items of darq.book.epub, darq.book.mobi and
# darq.book.azw, each a file, read-only, kept in a Storage blob (see
# doc/book.rst).
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Ebook protocol
# (lib/didl/ebook.didl) on a port for each client of an item.
#
# An item's identifier is its type and its blob's Storage key, in hex.
# The blob is a header, where it's marked destroyed, the reader's
# position, and then the file.  An open item is shared by its clients'
# ports.  Files are parsed here, and not in a lens, since it's C reading
# untrusted data: bookfile.py and mobifile.py hide the format.
#
# Every file of the right format is kept, whether or not it can be read:
# one with DRM can still be catalogued.  When an item's created, its
# title and author are published to the Index as names, and its text as
# its body, and it's withdrawn when it's destroyed.
#
# Every item's an entity (see doc/entity-vs-item.rst): it inherits the
# entity type, darq.entity_type's EntityType, which serves an item's
# entity on a port from OpenEntity.  An item's entity is made with it,
# identified by its specifier, and named by the book's title, or else the
# file it came from; its names are published to the Index with its own.

import logging
import re
import struct

import darq
import darq.index
import darq.kb as model
from darq.entity_type import EntityType
from darq.protocols import storage
from darq.protocols.ebook import (
    CloseRequest,
    CloseResponse,
    ContentsEntry,
    ContentsRequest,
    ContentsResponse,
    Ebook,
    GetPartRequest,
    GetPartResponse,
    GetPositionRequest,
    GetPositionResponse,
    GetRequest,
    GetResponse,
    SetPositionRequest,
    SetPositionResponse,
)
from darq.protocols.ebook import Status as EbookStatus
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

import bookfile


# The types, and their media types.
TYPES = {
    "darq.book.epub": "application/epub+zip",
    "darq.book.mobi": "application/x-mobipocket-ebook",
    "darq.book.azw": "application/vnd.amazon.ebook",
}

# Blob header: magic, format version, flags, and the position's length.
HEADER = struct.Struct(">4sHHI")
MAGIC = b"DBOK"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")

# Tries at writing a position, which can lose to nothing but a destroy.
SAVE_ATTEMPTS = 3


class FormatError(ValueError):
    """A blob isn't an e-book item."""


def encode_blob(data: bytes, position: str = "", flags: int = 0) -> bytes:
    encoded = position.encode()
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags, len(encoded)) + encoded + data


def decode_blob(value: bytes) -> tuple[bytes, str, int]:
    """Return a blob's file, position and flags.

    :raises FormatError: if it isn't an e-book item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags, length = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    start = HEADER.size + length
    return value[start:], value[HEADER.size:start].decode("utf-8", "replace"), flags


def specifier(key: storage.Key) -> str:
    """Return an item's specifier, which is its entity's identifier too."""
    return f"{key.high:016x}{key.low:016x}"


def item_id(type_id: str, key: storage.Key) -> str:
    return f"{type_id}:{specifier(key)}"


def parse_item_id(iid: str) -> tuple[str, storage.Key] | None:
    """Return an item's type and Storage key, or None if it isn't one of
    ours.

    :raises InvalidIdentifierError: if it isn't a valid item identifier."""
    type_id, specifier = darq.split_item_id(iid)
    if type_id not in TYPES or not SPECIFIER.fullmatch(specifier):
        return None
    return type_id, storage.Key(int(specifier[:16], 16), int(specifier[16:], 16))


class Document:
    """An open item, or one being looked at."""

    def __init__(self, type_id: str, key: storage.Key, generation: int, data: bytes, position: str = "",
                 flags: int = 0):
        self.type_id = type_id
        self.key = key
        self.generation = generation
        self.data = data
        self.position = position
        self.flags = flags

        # The ports of the clients that have it open.
        self.ports: set[int] = set()

        # The file, parsed when it's first wanted, or why it can't be.
        self.parsed: bookfile.Book | None = None
        self.reason = ""

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.type_id, self.key)

    def book(self) -> bookfile.Book | None:
        """Return the file, parsed, or None, with reason set."""
        if self.parsed is None and not self.reason:
            try:
                self.parsed = bookfile.open_book(self.type_id, self.data)
            except (bookfile.Unreadable, bookfile.NotABook) as e:
                self.reason = str(e)
        return self.parsed


def check_file(type_id: str, data: bytes) -> str:
    """Return why a file can't be read, or an empty string.

    :raises bookfile.NotABook: if it isn't of its type."""
    try:
        bookfile.open_book(type_id, data)
    except bookfile.Unreadable as e:
        return str(e)
    return ""


def index_entry(document: Document) -> tuple[list[str], str]:
    """Return an item's names and body, for the Index: empty if it can't
    be read."""
    book = document.book()
    if book is None:
        return [], ""
    return [name for name in (book.title, *book.authors) if name], bookfile.text_of(book)


class EbookImplementation(EntityType):
    """Answers the Implementation protocol, and keeps the open items."""

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, [t for t in TYPES])
        self.storage = client
        self.index = index
        self.documents: dict[tuple[int, int], Document] = {}

    # Items

    def attach(self, document: Document) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "EbookImplementation isn't being served"
        port = darq.open_port()
        self.documents[document.id] = document
        document.ports.add(port)
        self.server.add(port, EbookItem(self, document, port))
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

    def load(self, type_id: str, key: storage.Key) -> Document | None:
        """Return an open item, or load it, or None if it isn't found or
        is destroyed.

        :raises FormatError: if its blob isn't an e-book item."""
        document = self.documents.get((key.high, key.low))
        if document is not None:
            return document if not document.flags & DESTROYED else None

        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        data, position, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        return Document(type_id, key, response.generation, data, position, flags)

    def find(self, iid: str) -> tuple[Document | None, Status]:
        """Return an item, by its identifier, or why it can't be."""
        try:
            parsed = parse_item_id(iid)
        except darq.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if parsed is None:
            return None, Status.NOT_FOUND
        try:
            document = self.load(*parsed)
        except FormatError as e:
            logging.error(f"Can't load {iid}: {e}")
            return None, Status.FAILED
        return document, Status.OK if document is not None else Status.NOT_FOUND

    @staticmethod
    def check_type(type_id: str) -> tuple[str, Status]:
        """Return a type's canonical form, or why it isn't one of these."""
        try:
            canonical = darq.canonical_type_id(type_id)
        except darq.InvalidIdentifierError:
            return "", Status.INVALID_ID
        return canonical, Status.OK if canonical in TYPES else Status.NOT_FOUND

    def write(self, document: Document, position: str, flags: int) -> storage.Status:
        """Write an item's blob."""
        response = self.storage.put(key=document.key, expected=document.generation,
                                    value=encode_blob(document.data, position, flags))
        if response.status == storage.Status.OK:
            document.generation = response.generation
            document.position = position
            document.flags = flags
        return response.status

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        """A book can't be empty: it's only made from data (see CreateFrom)."""
        _, status = self.check_type(request.type_id)
        return CreateResponse(status=status if status != Status.OK else Status.UNSUPPORTED)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        type_id, status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        try:
            reason = check_file(type_id, request.data)
        except bookfile.NotABook as e:
            logging.info(f"Not a {type_id}: {e}")
            return CreateFromResponse(status=Status.INVALID_DATA)
        if reason:
            logging.info(f"Keeping a {type_id} that can't be read: {reason}")
        response = self.storage.create(value=encode_blob(request.data))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create an e-book blob: {response.status.name}")
            return CreateFromResponse(status=Status.FAILED)
        document = Document(type_id, response.key, response.generation, request.data)
        logging.info(f"Created {document.iid}")
        book = document.book()
        self.make_entity(type_id, specifier(document.key), book.title if book is not None else "", request.name)
        self.publish(document)
        return CreateFromResponse(item_id=document.iid, item=self.attach(document), status=Status.OK)

    def entry(self, document: Document) -> tuple[list[str], str]:
        """Return an item's entry for the Index: its entity's names, with
        its own, and its text."""
        own, body = index_entry(document)
        return list(dict.fromkeys([*self.entity_names(specifier(document.key)), *own])), body

    def publish(self, document: Document):
        """Publish an item's entry to the Index."""
        names, body = self.entry(document)
        self.index.publish(document.iid, names, body)

    def adopt(self, item_id: str) -> bool:
        """Give an item made before there were entities its entity, named by
        the book's title, if it can be read."""
        document, _ = self.find(item_id)
        if document is None:
            return False
        book = document.book()
        return self.adopt_entity(document.type_id, specifier(document.key), book.title if book is not None else "")

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        document, _ = self.find(model.item_id(after.id, model.recorded_type(after)))
        if document is not None:
            self.publish(document)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return OpenResponse(status=status)
        book = document.book()
        self.adopt_entity(document.type_id, specifier(document.key), book.title if book is not None else "")
        return OpenResponse(item=self.attach(document), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return IndexEntryResponse(status=status)
        names, body = self.entry(document)
        return IndexEntryResponse(names=names, body=body, status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        document, status = self.find(request.item_id)
        if document is None:
            return ExportResponse(status=status)
        return ExportResponse(data=document.data, mime_type=TYPES[document.type_id], status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        document, status = self.find(request.item_id)
        if document is None:
            return DestroyResponse(status=status)
        try:
            written = self.write(document, document.position, document.flags | DESTROYED)
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't destroy {request.item_id}: {e!r}")
            return DestroyResponse(status=Status.FAILED)
        if written != storage.Status.OK:
            logging.error(f"Can't destroy {request.item_id}: {written.name}")
            return DestroyResponse(status=Status.FAILED)
        self.index.withdraw(document.iid)
        self.destroy_item_entity(specifier(document.key))
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class EbookItem(Ebook.Handler):
    """Answers the Ebook protocol, on one client's port for an item."""

    def __init__(self, implementation: EbookImplementation, document: Document, port: int):
        self.implementation = implementation
        self.document = document
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.document, self.port)
        return CloseResponse()

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(data=self.document.data)

    def contents(self, request: ContentsRequest, source: int) -> ContentsResponse:
        book = self.document.book()
        if book is None:
            return ContentsResponse(reason=self.document.reason, status=EbookStatus.UNREADABLE)
        entries = [ContentsEntry(title=e.title, target=e.target, level=e.level) for e in book.contents]
        return ContentsResponse(title=book.title, authors=book.authors, isbn=book.isbn, publisher=book.publisher,
                                date=book.date, language=book.language, cover=book.cover, parts=book.parts,
                                contents=entries)

    def get_part(self, request: GetPartRequest, source: int) -> GetPartResponse:
        book = self.document.book()
        if book is None:
            return GetPartResponse(status=EbookStatus.UNREADABLE)
        try:
            found = book.get(request.name)
        except bookfile.Unreadable as e:
            logging.warning(f"Can't read a part of {self.document.iid}: {e}")
            return GetPartResponse(status=EbookStatus.UNREADABLE)
        if found is None:
            return GetPartResponse(status=EbookStatus.NOT_FOUND)
        return GetPartResponse(media_type=found[0], data=found[1])

    def get_position(self, request: GetPositionRequest, source: int) -> GetPositionResponse:
        return GetPositionResponse(position=self.document.position)

    def set_position(self, request: SetPositionRequest, source: int) -> SetPositionResponse:
        """Record a position: it's rewriting the whole blob, so one that's
        lost isn't worth failing for."""
        document = self.document
        if request.position == document.position:
            return SetPositionResponse()
        for _ in range(SAVE_ATTEMPTS):
            try:
                status = self.implementation.write(document, request.position, document.flags)
            except (darq.DarqError, TimeoutError) as e:
                logging.warning(f"Can't save the position of {document.iid}: {e!r}")
                break
            if status == storage.Status.OK:
                break
            logging.warning(f"Can't save the position of {document.iid}: {status.name}")
            current = self.implementation.storage.stat(key=document.key)
            document.generation = current.generation
        return SetPositionResponse()


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = EbookImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                         darq.index.Publisher(), model.KB.connect(timeout=10))

    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {', '.join(TYPES)} on port {port}")
    server.run()
    return 0
