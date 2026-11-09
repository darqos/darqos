# darqos
# Copyright (C) 2021-2026 David Arnold

# Image type implementation: items of the image types (public.png,
# public.jpeg, and the others in type.json), each its original data and
# a list of edits, kept in a Storage blob.
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst).  It serves the Implementation protocol
# (lib/didl/type.didl) on its own port, and the Image protocol
# (lib/didl/image.didl) on a port for each client of an item.
#
# An item's identifier is its type and its blob's Storage key, in hex.
# The blob is a header, the edits, as JSON, and then the original data.
# The image is the original with the edits made (see imaging.py), which
# is rendered when it's asked for, and kept while the item's open.
#
# An open item is shared by its clients' ports.  Changes are saved a
# little after the last one, as text items' are, and when the last
# client closes it.
#
# Each time an item's saved, and when it's created, its metadata fields
# are published to the Index (see doc/search-service.rst), and it's
# withdrawn when it's destroyed: its entity's names, and its title, artist
# and copyright, as names, and its comment, date taken, and format as its
# body.
#
# Every item's an entity (see doc/entity-vs-item.rst): it inherits the
# entity type, darq.entity_type's EntityType, which serves an item's
# entity on a port from OpenEntity.  An item's entity is made with it,
# identified by its specifier, and named by the image's title, or else the
# file it came from; a change to it is published to the Index.

import json
import logging
import re
import struct
import time

import darq
import darq.index
import darq.kb as model
from darq.entity_type import EntityType
from darq.protocols import image as wire
from darq.protocols import storage
from darq.protocols.image import (
    CloseRequest,
    CloseResponse,
    GetMetadataRequest,
    GetMetadataResponse,
    GetRequest,
    GetResponse,
    Image,
    SetEditsRequest,
    SetEditsResponse,
    StatRequest,
    StatResponse,
)
from darq.protocols.image import Status as ImageStatus
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

import imaging


# Blob header: magic, format version, flags, and the edits' length.
HEADER = struct.Struct(">4sHHI")
MAGIC = b"DIMG"
FORMAT_VERSION = 1

# Header flag: the item is destroyed (see doc/types.rst).
DESTROYED = 0x0001

# Seconds after the last change that an item is saved, and at most
# after its first unsaved change.
SAVE_DELAY = 2.0
MAX_SAVE_DELAY = 30.0

# Item specifier: the Storage key, as 32 lowercase hex digits.
SPECIFIER = re.compile(r"[0-9a-f]{32}")

# Edit types and fields, as the protocol's and imaging's.
EDIT_TYPES = {
    wire.EditType.CROP: imaging.CROP,
    wire.EditType.SCALE: imaging.SCALE,
    wire.EditType.ROTATE: imaging.ROTATE,
    wire.EditType.FLIP: imaging.FLIP,
    wire.EditType.SET_FIELD: imaging.SET_FIELD,
    wire.EditType.REMOVE_LOCATION: imaging.REMOVE_LOCATION,
}
FIELDS = {
    wire.Field.TITLE: imaging.TITLE,
    wire.Field.ARTIST: imaging.ARTIST,
    wire.Field.COPYRIGHT: imaging.COPYRIGHT,
    wire.Field.DATE_TAKEN: imaging.DATE_TAKEN,
    wire.Field.COMMENT: imaging.COMMENT,
}
FROM_EDIT_TYPES = {v: k for k, v in EDIT_TYPES.items()}
FROM_FIELDS = {v: k for k, v in FIELDS.items()}


class FormatError(ValueError):
    """A blob isn't an image item."""


def encode_blob(original: bytes, edits: list[imaging.Edit], flags: int = 0) -> bytes:
    text = json.dumps([e.to_dict() for e in edits], separators=(",", ":")).encode("utf-8")
    return HEADER.pack(MAGIC, FORMAT_VERSION, flags, len(text)) + text + original


def decode_blob(value: bytes) -> tuple[bytes, list[imaging.Edit], int]:
    """Return a blob's original data, edits, and flags.

    :raises FormatError: if it isn't an image item."""
    if len(value) < HEADER.size:
        raise FormatError(f"{len(value)} bytes is too short")
    magic, version, flags, length = HEADER.unpack_from(value)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise FormatError(f"header {magic!r} version {version}, expected {MAGIC!r} version {FORMAT_VERSION}")
    start = HEADER.size
    try:
        edits = [imaging.Edit.from_dict(d) for d in json.loads(value[start:start + length])]
    except (ValueError, TypeError) as e:
        raise FormatError(f"edits: {e}") from None
    return value[start + length:], edits, flags


def specifier(key: storage.Key) -> str:
    """Return an item's specifier, which is its entity's identifier too."""
    return f"{key.high:016x}{key.low:016x}"


def item_id(type_id: str, key: storage.Key) -> str:
    return f"{type_id}:{specifier(key)}"


def title_of(type_id: str, original: bytes) -> str:
    """Return an image's own title, from its metadata, or empty."""
    try:
        return imaging.field_values(type_id, original, []).get(imaging.TITLE, "") if original else ""
    except imaging.ImageError:
        return ""


def parse_item_id(iid: str) -> tuple[str, storage.Key] | None:
    """Return an item's type and Storage key, or None if it isn't one of
    ours.

    :raises InvalidIdentifierError: if it isn't a valid item identifier."""
    type_id, specifier = darq.split_item_id(iid)
    if type_id not in imaging.FORMATS or not SPECIFIER.fullmatch(specifier):
        return None
    return type_id, storage.Key(int(specifier[:16], 16), int(specifier[16:], 16))


def edit_to_wire(e: imaging.Edit) -> wire.Edit:
    return wire.Edit(FROM_EDIT_TYPES[e.type], e.x, e.y, e.width, e.height, e.turns, e.vertical,
                     FROM_FIELDS.get(e.field, wire.Field.NONE), e.value)


def edit_from_wire(e: wire.Edit) -> imaging.Edit:
    return imaging.Edit(EDIT_TYPES[e.type], e.x, e.y, e.width, e.height, e.turns, e.vertical,
                        FIELDS.get(e.field, ""), e.value)


def index_entry(picture: "Picture") -> tuple[list[str], str]:
    """Return an item's names and body, for the Index.

    :raises imaging.ImageError: if its original can't be read."""
    values = imaging.field_values(picture.type_id, picture.original, picture.edits)
    names = [values[f] for f in (imaging.TITLE, imaging.ARTIST, imaging.COPYRIGHT) if values.get(f)]
    subtype = imaging.FORMATS[picture.type_id].mime_type.partition("/")[2]
    body = [values[f] for f in (imaging.COMMENT, imaging.DATE_TAKEN) if values.get(f)]
    return names, "\n".join(body + [f"{subtype.upper()} image"])


class Picture:
    """An open item: its original, edits, and rendering, shared by its
    clients' ports."""

    def __init__(self, type_id: str, key: storage.Key, generation: int, original: bytes,
                 edits: list[imaging.Edit], flags: int = 0):
        self.type_id = type_id
        self.key = key

        # Generation of the blob last loaded or saved.
        self.generation = generation

        self.original = original
        self.edits = edits
        self.flags = flags

        # Version of the image, for clients: counts changes.
        self.version = 1

        # The image rendered, and its size, once it's been asked for.
        self.rendered: bytes | None = None
        self.info: imaging.Info | None = None

        # Its clients' ports.
        self.ports: set[int] = set()

        # When its first unsaved change was made, if it has one, and the
        # timer that'll save it.
        self.dirty_since: float | None = None
        self.timer: darq.Timer | None = None

    @property
    def id(self) -> tuple[int, int]:
        return self.key.high, self.key.low

    @property
    def iid(self) -> str:
        return item_id(self.type_id, self.key)

    def original_info(self) -> imaging.Info:
        """Return the original's size and frames."""
        return imaging.check(self.type_id, self.original)

    def render(self) -> tuple[bytes, imaging.Info]:
        """Return the image, with its edits made, and its size.

        :raises imaging.ImageError: if it can't be rendered."""
        if self.rendered is None or self.info is None:
            data = imaging.render(self.type_id, self.original, self.edits)
            self.info = imaging.check(self.type_id, data)
            self.rendered = data
        return self.rendered, self.info


class ImageImplementation(EntityType):
    """Answers the Implementation protocol, and keeps the open items."""

    def __init__(self, client: storage.Storage.Client, index: darq.index.Publisher, kb: model.KB):
        super().__init__(kb, imaging.FORMATS)
        self.storage = client
        self.index = index
        self.pictures: dict[tuple[int, int], Picture] = {}

    # Items

    def attach(self, picture: Picture) -> int:
        """Return a new port for a client of an item."""
        assert self.server is not None, "ImageImplementation isn't being served"
        port = darq.open_port()
        self.pictures[picture.id] = picture
        picture.ports.add(port)
        self.server.add(port, ImageItem(self, picture, port))
        return port

    def detach(self, picture: Picture, port: int):
        """Forget a client's port, once it's closed the item, and the
        item, if that was its last client."""
        assert self.server is not None
        picture.ports.discard(port)
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

        if not picture.ports:
            if picture.dirty_since is not None:
                self.save(picture)
            if picture.timer is not None:
                picture.timer.cancel()
            del self.pictures[picture.id]

    def load(self, type_id: str, key: storage.Key) -> Picture | None:
        """Return an open item, or load it, or None if it isn't found or
        is destroyed.

        :raises FormatError: if its blob isn't an image item."""
        picture = self.pictures.get((key.high, key.low))
        if picture is not None:
            return picture if not picture.flags & DESTROYED else None

        response = self.storage.get(key=key)
        if response.status != storage.Status.OK:
            return None
        original, edits, flags = decode_blob(response.value)
        if flags & DESTROYED:
            return None
        return Picture(type_id, key, response.generation, original, edits, flags)

    def find(self, iid: str) -> tuple[Picture | None, Status]:
        """Return an item, by its identifier, or why it can't be."""
        try:
            parsed = parse_item_id(iid)
        except darq.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if parsed is None:
            return None, Status.NOT_FOUND
        try:
            picture = self.load(*parsed)
        except FormatError as e:
            logging.error(f"Can't load {iid}: {e}")
            return None, Status.FAILED
        return picture, Status.OK if picture is not None else Status.NOT_FOUND

    def changed(self, picture: Picture):
        """Schedule an item's save, after a change."""
        assert self.server is not None
        now = time.monotonic()
        if picture.dirty_since is None:
            picture.dirty_since = now
        if picture.timer is not None:
            picture.timer.cancel()
        delay = max(0.0, min(SAVE_DELAY, picture.dirty_since + MAX_SAVE_DELAY - now))
        picture.timer = self.server.call_later(delay, lambda: self.autosave(picture))

    def autosave(self, picture: Picture):
        """Save an item, from its timer: a failure is just logged."""
        self.save(picture)

    def save(self, picture: Picture) -> bool:
        """Save an item's edits, over the generation last loaded or saved.

        :returns: True if it was saved."""
        if picture.timer is not None:
            picture.timer.cancel()
            picture.timer = None
        try:
            response = self.storage.put(key=picture.key, expected=picture.generation,
                                        value=encode_blob(picture.original, picture.edits, picture.flags))
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't save {picture.iid}: {e!r}")
            return False
        if response.status != storage.Status.OK:
            logging.error(f"Can't save {picture.iid}: {response.status.name}")
            return False
        picture.generation = response.generation
        picture.dirty_since = None
        self.publish(picture)
        return True

    def publish(self, picture: Picture):
        """Publish an item's entry to the Index, as saved, or withdraw it
        if it's destroyed."""
        if picture.flags & DESTROYED:
            self.index.withdraw(picture.iid)
            return
        names = self.entity_names(specifier(picture.key))
        if not picture.original:
            # Empty: there's nothing to find it by but its entity's names.
            self.index.publish(picture.iid, names)
            return
        try:
            own, body = index_entry(picture)
        except imaging.ImageError as e:
            logging.error(f"Can't index {picture.iid}: {e}")
            return
        self.index.publish(picture.iid, list(dict.fromkeys([*names, *own])), body)

    def adopt(self, item_id: str) -> bool:
        """Give an item made before there were entities its entity, named by
        the image's title, if it has one."""
        picture, _ = self.find(item_id)
        if picture is None:
            return False
        return self.adopt_entity(picture.type_id, specifier(picture.key), title_of(picture.type_id, picture.original))

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Its entity's names may have changed: publish it again."""
        picture, _ = self.find(model.item_id(after.id, model.recorded_type(after)))
        if picture is not None:
            self.publish(picture)

    def new(self, type_id: str, original: bytes, name: str = "") -> Picture | None:
        """Create an item's blob, and its entity, named by the image's
        title, or else the file it came from, and return it, or None if it
        can't be."""
        response = self.storage.create(value=encode_blob(original, []))
        if response.status != storage.Status.OK:
            logging.error(f"Can't create an image blob: {response.status.name}")
            return None
        picture = Picture(type_id, response.key, response.generation, original, [])
        logging.info(f"Created {picture.iid}")
        self.make_entity(type_id, specifier(picture.key), title_of(type_id, original), name)
        self.publish(picture)
        return picture

    @staticmethod
    def check_type(type_id: str) -> tuple[str, Status]:
        try:
            canonical = darq.canonical_type_id(type_id)
        except darq.InvalidIdentifierError:
            return "", Status.INVALID_ID
        return canonical, Status.OK if canonical in imaging.FORMATS else Status.NOT_FOUND

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        """Create an empty item: it has an image once it's created from
        data (see CreateFrom)."""
        type_id, status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateResponse(status=status)
        picture = self.new(type_id, b"")
        if picture is None:
            return CreateResponse(status=Status.FAILED)
        return CreateResponse(item_id=picture.iid, item=self.attach(picture), status=Status.OK)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        type_id, status = self.check_type(request.type_id)
        if status != Status.OK:
            return CreateFromResponse(status=status)
        try:
            imaging.check(type_id, request.data)
        except imaging.ImageError as e:
            logging.info(f"Not a {type_id} image: {e}")
            return CreateFromResponse(status=Status.INVALID_DATA)
        picture = self.new(type_id, request.data, request.name)
        if picture is None:
            return CreateFromResponse(status=Status.FAILED)
        return CreateFromResponse(item_id=picture.iid, item=self.attach(picture), status=Status.OK)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        picture, status = self.find(request.item_id)
        if picture is None:
            return OpenResponse(status=status)
        self.adopt_entity(picture.type_id, specifier(picture.key), title_of(picture.type_id, picture.original))
        return OpenResponse(item=self.attach(picture), status=Status.OK)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        picture, status = self.find(request.item_id)
        if picture is None:
            return IndexEntryResponse(status=status)
        names = self.entity_names(specifier(picture.key))
        if not picture.original:
            return IndexEntryResponse(names=names, status=Status.OK)
        try:
            own, body = index_entry(picture)
        except imaging.ImageError as e:
            logging.error(f"Can't index {request.item_id}: {e}")
            return IndexEntryResponse(status=Status.FAILED)
        return IndexEntryResponse(names=list(dict.fromkeys([*names, *own])), body=body, status=Status.OK)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        picture, status = self.find(request.item_id)
        if picture is None:
            return ExportResponse(status=status)
        mime_type = imaging.FORMATS[picture.type_id].mime_type
        if not picture.original:
            return ExportResponse(mime_type=mime_type, status=Status.OK)
        try:
            data, _ = picture.render()
        except imaging.ImageError as e:
            logging.error(f"Can't render {request.item_id}: {e}")
            return ExportResponse(status=Status.FAILED)
        return ExportResponse(data=data, mime_type=mime_type, status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        """Mark an item destroyed.  Its open clients can go on using it,
        but it can't be opened again."""
        picture, status = self.find(request.item_id)
        if picture is None:
            return DestroyResponse(status=status)
        picture.flags |= DESTROYED
        if not self.save(picture):
            picture.flags &= ~DESTROYED
            return DestroyResponse(status=Status.FAILED)
        self.destroy_item_entity(specifier(picture.key))
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


class ImageItem(Image.Handler):
    """Answers the Image protocol, on one client's port for an item."""

    def __init__(self, implementation: ImageImplementation, picture: Picture, port: int):
        self.implementation = implementation
        self.picture = picture
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.picture, self.port)
        return CloseResponse()

    def render(self) -> tuple[bytes, imaging.Info] | ImageStatus:
        """Return the image and its size, or why it can't be."""
        if not self.picture.original:
            return ImageStatus.EMPTY
        try:
            return self.picture.render()
        except imaging.ImageError as e:
            logging.error(f"Can't render {self.picture.iid}: {e}")
            return ImageStatus.FAILED

    def get(self, request: GetRequest, source: int) -> GetResponse:
        picture = self.picture
        edits = [edit_to_wire(e) for e in picture.edits]
        rendered = self.render()
        if isinstance(rendered, ImageStatus):
            return GetResponse(edits=edits, version=picture.version, status=rendered)
        data, info = rendered
        return GetResponse(data=data, width=info.width, height=info.height, frames=info.frames, edits=edits,
                           version=picture.version, status=ImageStatus.OK)

    def stat(self, request: StatRequest, source: int) -> StatResponse:
        rendered = self.render()
        if isinstance(rendered, ImageStatus):
            return StatResponse(version=self.picture.version, status=rendered)
        data, info = rendered
        return StatResponse(width=info.width, height=info.height, frames=info.frames, size=len(data),
                            version=self.picture.version, status=ImageStatus.OK)

    def set_edits(self, request: SetEditsRequest, source: int) -> SetEditsResponse:
        picture = self.picture
        if request.expected != 0 and request.expected != picture.version:
            return SetEditsResponse(version=picture.version, status=ImageStatus.CONFLICT)
        if not picture.original:
            return SetEditsResponse(version=picture.version, status=ImageStatus.EMPTY)
        try:
            info = picture.original_info()
        except imaging.ImageError as e:
            logging.error(f"Can't read {picture.iid}: {e}")
            return SetEditsResponse(version=picture.version, status=ImageStatus.FAILED)
        edits = [edit_from_wire(e) for e in request.edits]
        if edits and info.frames > 1:
            return SetEditsResponse(version=picture.version, status=ImageStatus.ANIMATED)
        try:
            imaging.check_edits(picture.type_id, info, edits)
        except imaging.InvalidEdit as e:
            logging.info(f"Edits to {picture.iid} refused: {e}")
            return SetEditsResponse(version=picture.version, status=ImageStatus.INVALID_EDIT)

        picture.edits = edits
        picture.rendered = picture.info = None
        picture.version += 1
        self.implementation.changed(picture)
        return SetEditsResponse(version=picture.version, status=ImageStatus.OK)

    def get_metadata(self, request: GetMetadataRequest, source: int) -> GetMetadataResponse:
        rendered = self.render()
        if isinstance(rendered, ImageStatus):
            return GetMetadataResponse(status=rendered)
        try:
            entries = imaging.metadata(self.picture.type_id, rendered[0])
        except imaging.ImageError as e:
            logging.error(f"Can't read {self.picture.iid}'s metadata: {e}")
            return GetMetadataResponse(status=ImageStatus.FAILED)
        return GetMetadataResponse(entries=[wire.Entry(e.group, e.name, e.value,
                                                       FROM_FIELDS.get(e.field, wire.Field.NONE))
                                            for e in entries], status=ImageStatus.OK)


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = ImageImplementation(storage.Storage.Client(darq.connect("darq.storage"), timeout=10),
                                         darq.index.Publisher(), model.KB.connect(timeout=10))

    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving the image types on port {port}")
    server.run()
