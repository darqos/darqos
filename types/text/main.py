# darqos
# Copyright (C) 2018-2026 David Arnold

# Text type implementation: items of public.utf8-plain-text, each a
# string of Unicode text kept in a Storage blob.
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Text protocol
# (lib/didl/text.didl) on a port for each client of an item.
#
# An item's specifier is its blob's Storage key, in hex, so there's no
# table of items to keep.  The blob is a header, then the text in UTF-8.
#
# An open item's text is kept in memory, shared by its clients' ports.
# Changes are saved a little after the last one (SAVE_DELAY), or at
# most MAX_SAVE_DELAY after the first unsaved one, and when the last
# client closes it.
#
# Each time an item's saved, its text is published to the Index (see
# doc/search-service.rst), with its entity's names, and it's withdrawn
# when it's destroyed; its entry's also given by IndexEntry, for a
# reindex.
#
# Every item has an entity (see doc/item.rst): it inherits
# ItemType, from darq.item_type, which serves an item's
# entity on a port from OpenEntity.  An item's entity is made with it,
# identified by its specifier, and named from the file it came from, if
# it did; a change to it is published to the Index.

import logging
import re
import struct
import time

import darq
import darq.history
import darq.index
import darq.kb as model
from darq.item_type import ItemType
from darq.protocols import storage
from darq.protocols.text import (
    CloseRequest,
    CloseResponse,
    GetRequest,
    GetResponse,
    ReplaceRequest,
    ReplaceResponse,
    StatRequest,
    StatResponse,
    Text,
    Viewed,
)
from darq.protocols.text import Status as TextStatus
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

# The type implemented.
TYPE_ID = "public.utf8-plain-text"

# Blob header: magic, format version, and flags.
HEADER = struct.Struct(">4sHH")
MAGIC = b"DTXT"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Seconds after the last change that an item is saved, and at most
# after its first unsaved change.
SAVE_DELAY = 2.0
MAX_SAVE_DELAY = 30.0

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")

# The type's MIME type, for Export.
MIME_TYPE = "text/plain;charset=utf-8"


class FormatError(ValueError):
    """A blob isn't a text item."""


def encode_blob(text: str, flags: int = 0) -> bytes:
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags) + text.encode("utf-8")


def decode_blob(value: bytes) -> tuple[str, int]:
    """Return a blob's text and flags.

    :raises FormatError: if it isn't a text item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    try:
        return value[HEADER.size:].decode("utf-8"), flags
    except UnicodeDecodeError as e:
        raise FormatError(str(e)) from None


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


class Buffer:
    """An open item: its text, shared by its clients' ports."""

    def __init__(self, key: storage.Key, generation: int, text: str, flags: int = 0):
        self.key = key

        # Generation of the blob last loaded or saved.
        self.generation = generation

        self.text = text
        self.flags = flags

        # Version of the text, for clients: counts changes.
        self.version = 1

        # Its clients' ports.
        self.ports: set[int] = set()

        # When its first unsaved change was made, if it has one, and the
        # timer that'll save it.
        self.dirty_since: float | None = None
        self.timer: darq.Timer | None = None

        # The source of its latest change: whoever made it, recorded as
        # its editor when it's saved.
        self.editor = ""

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low


class TextImplementation(ItemType):
    """Answers the Implementation protocol, and keeps the open items."""

    SOURCE = "text-type"

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, [TYPE_ID])
        self.storage = client
        self.index = index
        self.buffers: dict[tuple[int, int], Buffer] = {}

    # Items

    def attach(self, buffer: Buffer) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "TextImplementation isn't being served"
        port = darq.open_port()
        self.buffers[buffer.id] = buffer
        buffer.ports.add(port)
        self.server.add(port, TextItem(self, buffer, port))
        return port

    def detach(self, buffer: Buffer, port: int):
        """Forget a client's port, once it's closed the item, and the
        item, if that was its last client."""
        assert self.server is not None
        buffer.ports.discard(port)
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

        if not buffer.ports:
            if buffer.dirty_since is not None:
                self.save(buffer)
            if buffer.timer is not None:
                buffer.timer.cancel()
            del self.buffers[buffer.id]

    def load(self, key: storage.Key) -> Buffer | None:
        """Return an open item's buffer, or load it, or None if it isn't
        found or is destroyed.

        :raises FormatError: if its blob isn't a text item."""
        buffer = self.buffers.get((key.high, key.low))
        if buffer is not None:
            return buffer if not buffer.flags & DESTROYED else None

        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        text, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        return Buffer(key, response.generation, text, flags)

    def changed(self, buffer: Buffer):
        """Schedule an item's save, after a change."""
        assert self.server is not None
        now = time.monotonic()
        if buffer.dirty_since is None:
            buffer.dirty_since = now
        if buffer.timer is not None:
            buffer.timer.cancel()
        delay = max(0.0, min(SAVE_DELAY, buffer.dirty_since + MAX_SAVE_DELAY - now))
        buffer.timer = self.server.call_later(delay, lambda: self.autosave(buffer))

    def autosave(self, buffer: Buffer):
        """Save an item, from its timer: a failure is just logged."""
        self.save(buffer)

    def save(self, buffer: Buffer) -> bool:
        """Save an item's text.

        It's only saved over the generation last loaded or saved: if
        something else has changed the blob, it fails.

        :returns: True if it was saved."""
        if buffer.timer is not None:
            buffer.timer.cancel()
            buffer.timer = None

        iid = item_id(buffer.key)
        edited = buffer.dirty_since is not None
        try:
            response = self.storage.put(key=buffer.key, expected=buffer.generation,
                                        value=encode_blob(buffer.text, buffer.flags))
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't save {iid}: {e!r}")
            return False
        if response.status != storage.Status.OK:
            logging.error(f"Can't save {iid}: {response.status.name}")
            return False

        buffer.generation = response.generation
        buffer.dirty_since = None
        self.publish(buffer)
        if edited:
            self.record(iid, darq.history.EDITED, source=buffer.editor)
        return True

    def publish(self, buffer: Buffer):
        """Publish an item's text to the Index, as saved, with its entity's
        names, or withdraw it if it's destroyed."""
        if buffer.flags & DESTROYED:
            self.index.withdraw(item_id(buffer.key))
        else:
            self.index.publish(item_id(buffer.key), self.entity_names(specifier(buffer.key)), buffer.text)

    def adopt(self, item_id: str) -> bool:
        """Give an item made before there were entities its entity: a text
        has no title, so it's unnamed."""
        try:
            key = parse_item_id(item_id)
            buffer = self.load(key) if key is not None else None
        except (darq.InvalidIdentifierError, FormatError):
            return False
        return buffer is not None and self.adopt_entity(TYPE_ID, specifier(buffer.key))

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        key = parse_item_id(model.item_id(after.id, TYPE_ID))
        try:
            buffer = self.load(key) if key is not None else None
        except FormatError as e:
            logging.error(f"Can't publish {after.id}: {e}")
            return
        if buffer is not None:
            self.publish(buffer)

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateResponse(status=status)
        buffer = self.new("")
        if buffer is None:
            return CreateResponse(status=Status.FAILED)
        self.record(item_id(buffer.key), darq.history.CREATED, request.context)
        return CreateResponse(item_id=item_id(buffer.key), item=self.attach(buffer), status=Status.OK)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        """Create an item from UTF-8 text, without any byte order mark."""
        status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        try:
            text = request.data.decode("utf-8").removeprefix("\ufeff")
        except UnicodeDecodeError:
            return CreateFromResponse(status=Status.INVALID_DATA)

        # DIDL's strings can't hold one, so it's binary, not text.
        if "\0" in text:
            return CreateFromResponse(status=Status.INVALID_DATA)
        buffer = self.new(text, request.name)
        if buffer is None:
            return CreateFromResponse(status=Status.FAILED)
        self.record(item_id(buffer.key), darq.history.CREATED, request.context)
        return CreateFromResponse(item_id=item_id(buffer.key), item=self.attach(buffer), status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        try:
            key = parse_item_id(request.item_id)
        except darq.InvalidIdentifierError:
            return ExportResponse(status=Status.INVALID_ID)
        if key is None:
            return ExportResponse(status=Status.NOT_FOUND)
        try:
            buffer = self.load(key)
        except FormatError as e:
            logging.error(f"Can't export {request.item_id}: {e}")
            return ExportResponse(status=Status.FAILED)
        if buffer is None:
            return ExportResponse(status=Status.NOT_FOUND)
        return ExportResponse(data=buffer.text.encode("utf-8"), mime_type=MIME_TYPE, status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        try:
            key = parse_item_id(request.item_id)
        except darq.InvalidIdentifierError:
            return IndexEntryResponse(status=Status.INVALID_ID)
        if key is None:
            return IndexEntryResponse(status=Status.NOT_FOUND)
        try:
            buffer = self.load(key)
        except FormatError as e:
            logging.error(f"Can't index {request.item_id}: {e}")
            return IndexEntryResponse(status=Status.FAILED)
        if buffer is None:
            return IndexEntryResponse(status=Status.NOT_FOUND)
        return IndexEntryResponse(names=self.entity_names(specifier(key)), body=buffer.text, status=Status.OK)

    @staticmethod
    def check_type(type_id: str) -> Status:
        try:
            return Status.OK if darq.canonical_type_id(type_id) == TYPE_ID else Status.NOT_FOUND
        except darq.InvalidIdentifierError:
            return Status.INVALID_ID

    def new(self, text: str, name: str = "") -> Buffer | None:
        """Create an item's blob, and its entity, named from the file it came
        from, and return its buffer, or None if it can't be."""
        response = self.storage.create(value=encode_blob(text))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create a text blob: {response.status.name}")
            return None
        buffer = Buffer(response.key, response.generation, text)
        logging.info(f"Created {item_id(buffer.key)}")
        self.make_entity(TYPE_ID, specifier(buffer.key), file_name=name)
        self.publish(buffer)
        return buffer

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        try:
            key = parse_item_id(request.item_id)
        except darq.InvalidIdentifierError:
            return OpenResponse(status=Status.INVALID_ID)
        if key is None:
            return OpenResponse(status=Status.NOT_FOUND)

        try:
            buffer = self.load(key)
        except FormatError as e:
            logging.error(f"Can't open {request.item_id}: {e}")
            return OpenResponse(status=Status.FAILED)
        if buffer is None:
            return OpenResponse(status=Status.NOT_FOUND)
        self.adopt_entity(TYPE_ID, specifier(buffer.key))
        return OpenResponse(item=self.attach(buffer), status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        try:
            key = parse_item_id(request.item_id)
        except darq.InvalidIdentifierError:
            return DestroyResponse(status=Status.INVALID_ID)
        if key is None:
            return DestroyResponse(status=Status.NOT_FOUND)

        try:
            buffer = self.load(key)
        except FormatError as e:
            logging.error(f"Can't destroy {request.item_id}: {e}")
            return DestroyResponse(status=Status.FAILED)
        if buffer is None:
            return DestroyResponse(status=Status.NOT_FOUND)

        buffer.flags |= DESTROYED
        if not self.save(buffer):
            buffer.flags &= ~DESTROYED
            return DestroyResponse(status=Status.FAILED)
        self.destroy_item_entity(specifier(key))
        self.record(item_id(key), darq.history.DESTROYED, request.context)
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class TextItem(Text.Handler):
    """Answers the Text protocol, on one client's port for an item."""

    def __init__(self, implementation: TextImplementation, buffer: Buffer, port: int):
        self.implementation = implementation
        self.buffer = buffer
        self.port = port

        # The client's name, from Viewed: its changes are recorded with it.
        self.source = ""

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.buffer, self.port)
        return CloseResponse()

    def viewed(self, request: Viewed, source: int) -> None:
        self.source = request.source
        self.implementation.record(item_id(self.buffer.key), darq.history.VIEWED, source=request.source,
                                   note=request.note)

    def get(self, request: GetRequest, source: int) -> GetResponse:
        return GetResponse(text=self.buffer.text, version=self.buffer.version)

    def replace(self, request: ReplaceRequest, source: int) -> ReplaceResponse:
        buffer = self.buffer
        if request.expected != 0 and request.expected != buffer.version:
            return ReplaceResponse(version=buffer.version, status=TextStatus.CONFLICT)
        if not request.start <= request.end <= len(buffer.text):
            return ReplaceResponse(version=buffer.version, status=TextStatus.OUT_OF_RANGE)

        buffer.text = buffer.text[:request.start] + request.text + buffer.text[request.end:]
        buffer.version += 1
        buffer.editor = self.source
        self.implementation.changed(buffer)
        return ReplaceResponse(version=buffer.version, status=TextStatus.OK)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        return StatResponse(length=len(self.buffer.text), version=self.buffer.version)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = TextImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                        darq.index.Publisher(), model.KB.connect(timeout=10))

    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
