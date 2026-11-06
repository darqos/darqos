# darqos
# Copyright (C) 2026 David Arnold

"""URLs, as Knowledge Base entities (see doc/url-type.rst).

A URL item, of type public.url, is a place on the web, with no actions
of its own: darq.kb has its class, and types/kb implements it.  There's
one for a URL, whose identifier comes from it.  What's been fetched from
there is recorded on it, as snapshots: this is that vocabulary."""

import typing
import uuid

from .identifier import serialize_url
from .kb import (END_TIME, Datatype, Entity, EntityValue, Item, Property, Snak, Statement, StringValue, TimeValue,
                 Value)
from .kb import item_id as make_item_id
from .message import MEDIA_TYPE


# Control exports
__all__ = [
    "ETAG",
    "FINAL_URL",
    "HASH",
    "HTTP_SERVICE",
    "HTTP_STATUS",
    "INVERSE_LABELS",
    "LAST_MODIFIED",
    "MOVED_TO",
    "RETRIEVED",
    "SNAPSHOT",
    "URL",
    "URL_TYPE",
    "Snapshot",
    "entity_id",
    "item_id",
    "latest_snapshot",
    "moved_to",
    "snapshot_statement",
    "vocabulary",
]


# The type of a URL's item.
URL_TYPE = "public.url"

# What's at it (a URL), and when a snapshot was fetched.
URL = "P2699"
RETRIEVED = "P813"

# A snapshot: its item's identifier (a String), with when it was
# retrieved, its media type, and these, as qualifiers: its SHA-256, and
# what the server said to ask about it next time.
SNAPSHOT = "dd17dc90-4f1c-4e36-9ef6-2c1b4d8f1c7a"
HASH = "0a4d6a1e-6d3b-4d3c-8b7e-5b2f7c0b9e11"
ETAG = "3f9f6c52-8f7d-4f26-9e3f-7a1d2b6e4c55"
LAST_MODIFIED = "6b1e2d44-2c8a-4b11-a7d6-9c3e8f5a1d02"

# Where a snapshot really came from, if a temporary redirect led
# elsewhere (a String, a qualifier).
FINAL_URL = "efe90e0f-afc6-4da0-b9d0-4287a8e70e4b"

# Where a URL's resource has moved to, by a permanent redirect, with when
# it was retrieved, and the status (a String), as qualifiers.  A move
# that's over has an end time.  Wikidata's closest, replaced by (P1366),
# is for people and offices.
MOVED_TO = "4a005e23-c460-4b4d-8abe-cfa984fe6638"
HTTP_STATUS = "9e0c6d3b-2c6f-4c58-8f0e-5d1a7b3c2e41"

# Labels that read these backwards (see darq.kb.INVERSE_LABEL).
INVERSE_LABELS = [(MOVED_TO, "624977dd-f503-4da7-9dbb-6bae1d050049", "moved from")]

# The HTTP service, as the source of what's stated here.
HTTP_SERVICE = "c2a84b1e-77e0-4a55-8d21-0e6f3b9d4a18"


class Snapshot(typing.NamedTuple):
    """What's known of a snapshot."""

    item_id: str
    hash: str
    etag: str
    last_modified: str


def entity_id(url: str) -> str:
    """Return the identifier of a URL's entity, the same wherever it's
    made, which is what makes it a singleton.

    :raises InvalidURLError: if it isn't a URL."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, serialize_url(url)))


def item_id(url: str) -> str:
    """Return the identifier of a URL's item."""
    return make_item_id(entity_id(url), URL_TYPE)


def qualifier(statement: Statement, property_id: str) -> str:
    """Return a statement's string qualifier, or an empty string."""
    for snak in statement.qualifiers:
        if snak.property_id == property_id and isinstance(snak.value, StringValue):
            return snak.value.value
    return ""


def latest_snapshot(entity: Entity) -> Snapshot | None:
    """Return a URL's latest snapshot, the last recorded, or None."""
    for statement in reversed(entity.get_statements(SNAPSHOT)):
        value = statement.get_value()
        if isinstance(value, StringValue):
            return Snapshot(value.value, qualifier(statement, HASH), qualifier(statement, ETAG),
                            qualifier(statement, LAST_MODIFIED))
    return None


def moved_to(entity: Entity) -> str:
    """Return the entity a URL's moved to, if it has, or an empty string:
    the latest, if it's not over."""
    for statement in reversed(entity.get_statements(MOVED_TO)):
        value = statement.get_value()
        if isinstance(value, EntityValue) and not any(q.property_id == END_TIME for q in statement.qualifiers):
            return value.entity_id
    return ""


def snapshot_statement(snapshot: Snapshot, retrieved: TimeValue, media_type: str, final_url: str = "") -> Statement:
    """Return the statement of a snapshot."""
    made = Statement(Snak(SNAPSHOT, StringValue(snapshot.item_id)))
    qualifiers: list[tuple[str, Value]] = [(RETRIEVED, retrieved), (HASH, StringValue(snapshot.hash))]
    for property_id, text in ((MEDIA_TYPE, media_type), (ETAG, snapshot.etag),
                              (LAST_MODIFIED, snapshot.last_modified), (FINAL_URL, final_url)):
        if text:
            qualifiers.append((property_id, StringValue(text)))
    for property_id, value in qualifiers:
        made.add_qualifier(property_id, value)
    return made


def vocabulary() -> list[Entity]:
    """Return the properties and items URLs need."""
    def prop(property_id: str, label: str, datatype: Datatype, description: str = "") -> Property:
        p = Property(datatype, property_id)
        p.set_label(label)
        if description:
            p.set_description(description)
        return p

    def item(entity_id: str, label: str) -> Item:
        made = Item(entity_id)
        made.set_label(label)
        return made

    return [
        prop(URL, "URL", Datatype.Url),
        prop(RETRIEVED, "retrieved", Datatype.Time),
        prop(SNAPSHOT, "snapshot", Datatype.String, "An item holding what was at a URL, by its identifier"),
        prop(HASH, "SHA-256 hash", Datatype.String, "The SHA-256 of a snapshot"),
        prop(ETAG, "ETag", Datatype.String, "The ETag of a snapshot, from the server"),
        prop(LAST_MODIFIED, "Last-Modified", Datatype.String, "The Last-Modified of a snapshot, from the server"),
        prop(FINAL_URL, "final URL", Datatype.String, "Where a temporary redirect led a snapshot's fetch"),
        prop(MOVED_TO, "moved to", Datatype.Item, "Where a URL's resource has moved to, by a permanent redirect"),
        prop(HTTP_STATUS, "HTTP status", Datatype.String, "The status of a redirect"),
        item(HTTP_SERVICE, "Darq HTTP Service"),
        *(item(item_id, label) for _, item_id, label in INVERSE_LABELS),
    ]
