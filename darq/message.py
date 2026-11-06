# darqos
# Copyright (C) 2026 David Arnold

"""Messages, as Knowledge Base entities (see doc/message-type.rst).

A message is an entity of type darq.message, and a conversation of
darq.conversation: darq.kb has their classes, and types/kb implements
them.  This is their vocabulary, which is the same in every knowledge
base, and how a message's identifier is made.

The identifiers from Wikidata are those that have a clean match.  The
others are local properties, with fixed UUIDs."""

import datetime
import hashlib
import re
import typing
import uuid

from .kb import Datatype, Entity, EntityValue, Item, Property, TextValue, TimePrecision, TimeValue


# Control exports
__all__ = [
    "ATTACHMENT",
    "BODY",
    "CONVERSATION",
    "FILE_NAME",
    "FILE_SIZE",
    "INVERSE_LABELS",
    "LAST_EDITED",
    "MEDIA_TYPE",
    "MENTIONS",
    "PARTICIPANT",
    "PLATFORM",
    "REACTED_BY",
    "REACTION",
    "REPLY_TO",
    "SENDER",
    "TIME",
    "UNDETERMINED",
    "body_of",
    "message_id",
    "plain",
    "sender_of",
    "summary",
    "time_value",
    "vocabulary",
]


# A message's sender (creator), the conversation it's part of, when it
# was sent (publication date), and the message it replies to.
SENDER = "P170"
CONVERSATION = "P361"
TIME = "P577"
REPLY_TO = "P2675"

# A conversation's participants, and the service it's on (platform).
PARTICIPANT = "P710"
PLATFORM = "P400"

# A message's text, in a small subset of Markdown (a Text).  A service
# doesn't say what language it's in, so it's in UNDETERMINED.
BODY = "1e8a1b19-2b71-421f-aabe-cdcdca78725c"
UNDETERMINED = "und"

# Who a message mentions.
MENTIONS = "9426522c-0c0d-4f35-b3c9-1021805dd6ea"

# A reaction to a message, by its emoji's name (a String), with who
# reacted (qualifiers).
REACTION = "d13ae5ee-c74b-4890-a2b5-ebb3923ae35a"
REACTED_BY = "ba8becd6-91d3-449e-8028-03ef03fac69c"

# When a message was last edited.
LAST_EDITED = "04fe322a-5920-46e3-a805-d600cb20904c"

# An attachment: an item's identifier (a String), or empty if the file
# isn't an item, with the file's name, media type and size (qualifiers).
ATTACHMENT = "b3373473-423f-4977-8290-6667f4e1ca7a"
FILE_NAME = "f5bad6df-d25d-4243-846f-f71f50e71f26"
MEDIA_TYPE = "53825878-ddc3-4ae6-93f7-cfb5bffc63ab"
FILE_SIZE = "776f7074-8e35-45f4-98f8-d2169f1ad9c4"

# Items with labels that read these properties backwards, for properties
# that are read from the other end (see darq.kb.INVERSE_LABEL): each
# property, its item, and the item's label.
INVERSE_LABELS = [
    (SENDER, "d49e2b9b-3ca7-457c-ba79-78ad48a4bf60", "created"),
    (CONVERSATION, "9ff582cd-bfd4-4b0b-bc45-351baf41c8af", "has part"),
    (REPLY_TO, "a2c7dc7a-30b6-4cfa-9c0b-8f9433e7def8", "replied to by"),
    (PARTICIPANT, "aa2efa6b-bc87-4247-9c53-386faca819ff", "participant in"),
    (PLATFORM, "bb5ca2e4-94b4-41bf-9a4a-dbe407785038", "platform of"),
]

# Most characters of a message's text in its summary.
SUMMARY_LENGTH = 100

# What's marked up in a line of a body: bold, inline code, and italics,
# which aren't within a word, so 2*3*4 isn't.
MARKUP = re.compile(r"\*\*([^*]+)\*\*|`([^`]+)`|(?<![\w*])\*([^*]+)\*(?![\w*])")


def message_id(key: str, microsecond: int) -> str:
    """Return a message's entity identifier, from what identifies it on
    its service, and when it was sent, in microseconds since 1970.

    It's a UUID of version 7's form, with the bits after the milliseconds
    holding the microseconds, as RFC 9562 allows, so identifiers sort by
    time, which is what lets a conversation be paged in order.  The rest
    is a hash of the key, not random, so the same message always has the
    same identifier."""
    millisecond, rest = divmod(microsecond, 1000)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    value = bytearray(millisecond.to_bytes(6, "big") + (0x7000 | rest).to_bytes(2, "big") + digest[:8])
    value[8] = 0x80 | value[8] & 0x3f
    return str(uuid.UUID(bytes=bytes(value)))


def time_value(microsecond: int) -> TimeValue:
    """Return a time, in microseconds since 1970, as a value, in UTC."""
    seconds, rest = divmod(microsecond, 1_000_000)
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return TimeValue(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
                     rest * 1000, precision=TimePrecision.Nanoseconds)


def body_of(entity: Entity) -> str:
    """Return a message's text."""
    for statement in entity.get_statements(BODY):
        value = statement.get_value()
        if isinstance(value, TextValue):
            return next(iter(value.text.values()), "")
    return ""


def sender_of(entity: Entity) -> str:
    """Return the entity that sent a message, or an empty string."""
    for statement in entity.get_statements(SENDER):
        value = statement.get_value()
        if isinstance(value, EntityValue):
            return value.entity_id
    return ""


def plain(line: str) -> str:
    """Return a line of a body without its markup."""
    return MARKUP.sub(lambda m: next(g for g in m.groups() if g is not None), line)


def summary(entity: Entity, sender: str) -> str:
    """Return a line about a message, for listing it, since it has no
    label: who sent it, when, and how it starts.

    :param sender: The sender's name, or an empty string."""
    parts = [sender] if sender else []
    for statement in entity.get_statements(TIME):
        value = statement.get_value()
        if isinstance(value, TimeValue):
            parts.append(f"{value.year:04d}-{value.month or 1:02d}-{value.day or 1:02d}")
            break
    line = next((line.strip() for line in body_of(entity).splitlines() if line.strip() != "```" and line.strip()), "")
    line = plain(line)
    if len(line) > SUMMARY_LENGTH:
        line = line[:SUMMARY_LENGTH - 1].rstrip() + "…"
    start = ", ".join(parts)
    return f"{start}: {line}" if start and line else start or line


def vocabulary() -> typing.List[Entity]:
    """Return the properties and items messages need."""
    def prop(entity_id: str, label: str, datatype: Datatype, description: str = "") -> Property:
        p = Property(datatype, entity_id)
        p.set_label(label)
        if description:
            p.set_description(description)
        return p

    def inverse(entity_id: str, label: str) -> Item:
        item = Item(entity_id)
        item.set_label(label)
        return item

    return [
        prop(SENDER, "creator", Datatype.Item),
        prop(CONVERSATION, "part of", Datatype.Item),
        prop(TIME, "publication date", Datatype.Time),
        prop(REPLY_TO, "reply to", Datatype.Item),
        prop(PARTICIPANT, "participant", Datatype.Item),
        prop(PLATFORM, "platform", Datatype.Item),
        prop(BODY, "message body", Datatype.Text, "The text of a message"),
        prop(MENTIONS, "mentions", Datatype.Item, "Who a message mentions"),
        prop(REACTION, "reaction", Datatype.String, "A reaction to a message, by its emoji's name"),
        prop(REACTED_BY, "reacted by", Datatype.Item, "Who reacted to a message"),
        prop(LAST_EDITED, "last edited", Datatype.Time, "When a message was last edited"),
        prop(ATTACHMENT, "attachment", Datatype.String,
             "An item attached to a message, by its identifier: empty for a file that isn't an item"),
        prop(FILE_NAME, "file name", Datatype.String, "The name of an attached file"),
        prop(MEDIA_TYPE, "media type", Datatype.String, "The media type of an attached file"),
        prop(FILE_SIZE, "file size", Datatype.String, "The size of an attached file, in bytes"),
        *(inverse(item_id, label) for _, item_id, label in INVERSE_LABELS),
    ]
