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

import logging
import re
import struct
import time

import darq
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
)
from darq.protocols.text import Status as TextStatus
from darq.protocols.type import (
    CreateRequest,
    CreateResponse,
    DestroyRequest,
    DestroyResponse,
    Implementation,
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


def item_id(key: storage.Key) -> str:
    return f"{TYPE_ID}:{key.high:016x}{key.low:016x}"


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

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low


class TextImplementation(Implementation.Handler):
    """Answers the Implementation protocol, and keeps the open items."""

    def __init__(self, client: storage.Storage.Client):
        self.storage = client
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
        return True

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        response = self.storage.create(value=encode_blob(""))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create a text blob: {response.status.name}")
            return CreateResponse(status=Status.FAILED)

        buffer = Buffer(response.key, response.generation, "")
        logging.info(f"Created {item_id(buffer.key)}")
        return CreateResponse(item_id=item_id(buffer.key), item=self.attach(buffer), status=Status.OK)

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
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class TextItem(Text.Handler):
    """Answers the Text protocol, on one client's port for an item."""

    def __init__(self, implementation: TextImplementation, buffer: Buffer, port: int):
        self.implementation = implementation
        self.buffer = buffer
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.buffer, self.port)
        return CloseResponse()

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
        self.implementation.changed(buffer)
        return ReplaceResponse(version=buffer.version, status=TextStatus.OK)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        return StatResponse(length=len(self.buffer.text), version=self.buffer.version)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = TextImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10))

    port = darq.open_port()
    server = darq.Server()
    server.add(port, implementation)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {TYPE_ID} on port {port}")
    server.run()
