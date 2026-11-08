# darqos
# Copyright (C) 2026 David Arnold

"""Items' entities: every item is a KB entity (see doc/entity-vs-item.rst).

The KB's own kinds, such as people and messages, are made by types/kb.
This is for the type implementations that keep their items' content in
Storage, as text, images, PDFs and e-books do: making, reading, naming
and destroying an item's entity, alongside its blob.

An item's entity is identified by its specifier, its blob's Storage key,
in hex, so each is found from the other, and no item identifier changes.
Its kind is its DarqOS type statement, with a class, as for the KB's
kinds.  It has a label, its name, where it has one, and when it was made.

An item's content and kind are written only by its type implementation;
its knowledge, its label, description, aliases and other statements, by
anyone, through the KB (see doc/types.rst)."""

import dataclasses
import datetime

from . import kb as model
from .kb import Datatype, Entity, EntityValue, Item, Property, Reference, StringValue, TimePrecision, TimeValue


# Control exports
__all__ = ["CREATED", "FILE_NAME", "KINDS", "Kind", "create", "destroy", "is_content", "made", "read", "rename",
           "vocabulary"]


# Properties: when an item was made, in DarqOS, and the name of the file
# it was made from, where it was.
CREATED = "4ef65274-cfb1-4d05-8789-f7575e03f5cc"
FILE_NAME = "48adae83-63e6-4a50-8170-d23476ef84ad"

# Wikidata's classes of files: text file, image file, and computer file.
TEXT_FILE = "Q86920"
IMAGE_FILE = "Q860625"
COMPUTER_FILE = "Q82753"

# Tries at a change that conflicts with another, before giving up.
TRIES = 3


@dataclasses.dataclass(frozen=True)
class Kind:
    """A kind of item whose content is in Storage: its class, and its
    name, for one and for several."""
    class_id: str
    name: str
    plural: str


def _image(name: str) -> Kind:
    return Kind(IMAGE_FILE, f"{name} Image", f"{name} Images")


def _ebook(name: str) -> Kind:
    return Kind(COMPUTER_FILE, f"{name} E-book", f"{name} E-books")


# The kinds of item whose content's in Storage, by type identifier: those
# of types/text, types/image, types/pdf and types/ebook.
KINDS: dict[str, Kind] = {
    "public.utf8-plain-text": Kind(TEXT_FILE, "Text File", "Text Files"),
    "public.png": _image("PNG"),
    "public.jpeg": _image("JPEG"),
    "com.compuserve.gif": _image("GIF"),
    "org.webmproject.webp": _image("WebP"),
    "public.tiff": _image("TIFF"),
    "com.microsoft.bmp": _image("BMP"),
    "public.heif": _image("HEIF"),
    "public.heic": _image("HEIC"),
    "com.adobe.pdf": Kind(COMPUTER_FILE, "PDF", "PDFs"),
    "darq.book.epub": _ebook("EPUB"),
    "darq.book.mobi": _ebook("MOBI"),
    "darq.book.azw": _ebook("AZW"),
}


def is_content(kind: str) -> bool:
    """Return whether a kind's items keep their content in Storage."""
    return kind in KINDS


def time_value(moment: datetime.datetime) -> TimeValue:
    """Return a moment as a value, in UTC, to the second."""
    moment = moment.astimezone(datetime.timezone.utc)
    return TimeValue(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
                     precision=TimePrecision.Seconds)


def made(kind: str, entity_id: str, label: str = "", file_name: str = "",
         created: datetime.datetime | None = None, source: str = model.DARQOS) -> Item:
    """Return a new item's entity, not yet written: its kind, its label,
    in English, when it was made, and the file it was made from.

    :param source: What the file name's stated in: DarqOS, or an import.
    :raises ValueError: if the kind's items aren't kept in Storage."""
    if kind not in KINDS:
        raise ValueError(f"{kind}'s items aren't kept in Storage")
    entity = Item(entity_id)
    if label.strip():
        entity.set_label(label.strip())
    model.stamp(entity, kind, KINDS[kind].class_id)
    darqos = Reference().add(model.STATED_IN, EntityValue(model.DARQOS))
    moment = created if created is not None else datetime.datetime.now(datetime.timezone.utc)
    entity.claim(CREATED, time_value(moment)).add_reference(darqos)
    if file_name.strip():
        entity.claim(FILE_NAME, StringValue(file_name.strip())).add_reference(
            Reference().add(model.STATED_IN, EntityValue(source)))
    return entity


def create(kb: model.KB, kind: str, entity_id: str, label: str = "", file_name: str = "",
           created: datetime.datetime | None = None, source: str = model.DARQOS) -> Entity:
    """Make an item's entity, after its blob, and return it.  If it's there
    already, as it is when it's made again after a failure, it's returned
    as it is.

    :raises ValueError: if the kind's items aren't kept in Storage.
    :raises darq.DarqError, TimeoutError, model.ConflictError: if the KB
                                                               won't."""
    existing = kb.get(entity_id, follow=False)
    if existing is not None:
        return existing
    return kb.put(made(kind, entity_id, label, file_name, created, source))[0]


def read(kb: model.KB, entity_id: str) -> Entity | None:
    """Return an item's entity, or None if it has none, or it's been
    destroyed."""
    entity = kb.get(entity_id, follow=False)
    return None if entity is None or entity.destroyed else entity


def rename(kb: model.KB, entity_id: str, label: str, language: str = "en") -> Entity:
    """Give an item's entity a label, its name, in a language, or none, if
    it's empty, and return it.

    :raises LookupError: if it has no entity.
    :raises model.ConflictError: if it keeps being changed elsewhere."""
    for attempt in range(TRIES):
        entity = read(kb, entity_id)
        if entity is None:
            raise LookupError(f"{entity_id} has no entity")
        if label.strip():
            if entity.labels.get(language) == label.strip():
                return entity
            entity.set_label(label.strip(), language)
        elif language in entity.labels:
            del entity.labels[language]
        else:
            return entity
        try:
            return kb.put(entity)[0]
        except model.ConflictError:
            if attempt == TRIES - 1:
                raise
    raise AssertionError("unreachable")


def destroy(kb: model.KB, entity_id: str):
    """Mark an item's entity destroyed, as its blob is: it keeps its
    statements, and what links to it, but is left out of lookups.  One
    that has none, or is destroyed already, is left as it is."""
    entity = kb.get(entity_id, follow=False)
    if entity is not None and not entity.destroyed:
        kb.destroy(entity_id)


def vocabulary() -> list[Entity]:
    """Return the properties and classes items' entities need."""
    def prop(property_id: str, label: str, datatype: Datatype, description: str) -> Property:
        p = Property(datatype, property_id)
        p.set_label(label)
        p.set_description(description)
        return p

    def item(entity_id: str, label: str) -> Item:
        made = Item(entity_id)
        made.set_label(label)
        return made

    return [
        prop(CREATED, "created", Datatype.Time, "When an item was made, in DarqOS"),
        prop(FILE_NAME, "file name", Datatype.String, "The name of the file an item was made from"),
        item(TEXT_FILE, "text file"),
        item(IMAGE_FILE, "image file"),
        item(COMPUTER_FILE, "computer file"),
    ]
