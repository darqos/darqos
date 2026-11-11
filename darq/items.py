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
import pathlib
import typing

from . import kb as model
from . import message
from .protocols import kb as wire
from .kb import Datatype, Entity, EntityValue, Item, Property, Reference, StringValue, TimePrecision, TimeValue


# Control exports
__all__ = ["CREATED", "DATA_TYPE", "DEPICTS", "FILE_EXTENSION", "FILE_NAME", "HAS_TAG", "IMAGE", "INVERSE_LABELS",
           "KINDS", "MEDIA_TYPE", "TAG_TYPE", "Kind", "create", "destroy", "is_content", "item_of", "label_for", "made",
           "names", "read", "rename", "tag", "vocabulary"]


# Properties: when an item was made, in DarqOS, and the name of the file
# it was made from, where it was: the same as a message's attachment's.
CREATED = "4ef65274-cfb1-4d05-8789-f7575e03f5cc"
FILE_NAME = message.FILE_NAME

# The type of a file that's of no type there's an implementation for: its
# bytes, kept as they came (see doc/data-type.rst).
DATA_TYPE = "public.data"

# Wikidata's properties of a file: its media type, and its extension.
MEDIA_TYPE = "P1163"
FILE_EXTENSION = "P1195"

# Facts about items (see doc/entity-vs-item.rst, step 6): an item's tags,
# entities of their own kind, what a picture depicts (P180), and the
# picture that represents something (P18), Wikidata's.
TAG_TYPE = "darq.tag"
HAS_TAG = "abfb41d9-3a08-440e-bcec-8e899542f45f"
DEPICTS = "P180"
IMAGE = "P18"

# Their inverse labels, reading them backwards, from what they link to.
INVERSE_LABELS = [
    (HAS_TAG, "f2295096-8f72-4dbe-8c1a-116a2424c45e", "tag of"),
    (DEPICTS, "f7524c95-4c70-4095-ae42-77521959959a", "depicted in"),
    (IMAGE, "e07939b2-289b-4bb2-9cdd-07f0ac9c232a", "image of"),
]

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
    DATA_TYPE: Kind(COMPUTER_FILE, "File", "Files"),
}


def is_content(kind: str) -> bool:
    """Return whether a kind's items keep their content in Storage."""
    return kind in KINDS


def label_for(title: str = "", file_name: str = "") -> str:
    """Return a new item's label: the title its content gives, such as a
    PDF's, or else the name of the file it came from, without its
    extension, or else none."""
    if title.strip():
        return " ".join(title.split())
    stem = pathlib.PurePath(file_name.strip()).stem if file_name.strip() else ""
    return stem.strip()


def tag(reader: model.KB, writer: model.KB, name: str, context: wire.Context | None = None) -> str:
    """Return the tag with a name, ignoring case, making it if there's
    none, and return its entity's identifier.

    :param writer: The KB, written through the KB type implementation,
                   which makes items of its kinds.
    :param context: Describes what makes one, for History."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("A tag needs a name")
    for entity_id in sorted(reader.find_by_label(name, "en", words=True, class_id=model.TAG_CLASS)):
        found = reader.get(entity_id)
        if found is not None and any(model.fold(label) == model.fold(name)
                                     for label in [*found.labels.values(), *found.get_aliases()]):
            return found.id
    made = Item()
    made.set_label(name)
    writer.create(TAG_TYPE, made, context=context)
    return made.id


def item_of(kb: model.KB, entity_id: str) -> str:
    """Return the identifier of an entity's item, from its recorded kind,
    as a link to one gives only the entity's: empty if there's none."""
    entity = kb.get(entity_id)
    return model.item_id(entity.id, model.recorded_type(entity)) if entity is not None else ""


def names(entity: Entity | None) -> list[str]:
    """Return what an item's found by, from its entity, for the Index: its
    labels and aliases, in every language, each once."""
    if entity is None:
        return []
    return list(dict.fromkeys([*entity.labels.values(),
                               *(a for aliases in entity.aliases.values() for a in aliases)]))


def time_value(moment: datetime.datetime) -> TimeValue:
    """Return a moment as a value, in UTC, to the second."""
    moment = moment.astimezone(datetime.timezone.utc)
    return TimeValue(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
                     precision=TimePrecision.Seconds)


def made(kind: str, entity_id: str, label: str = "", file_name: str = "",
         created: datetime.datetime | None = None, source: str = model.DARQOS,
         statements: typing.Iterable[tuple[str, model.Value]] = ()) -> Item:
    """Return a new item's entity, not yet written: its kind, its label,
    in English, when it was made, the file it was made from, and any other
    statements its type implementation makes, such as its media type.

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
    for property_id, value in statements:
        entity.claim(property_id, value).add_reference(Reference().add(model.STATED_IN, EntityValue(model.DARQOS)))
    return entity


def create(kb: model.KB, kind: str, entity_id: str, label: str = "", file_name: str = "",
           created: datetime.datetime | None = None, source: str = model.DARQOS,
           statements: typing.Iterable[tuple[str, model.Value]] = ()) -> Entity:
    """Make an item's entity, after its blob, and return it.  If it's there
    already, as it is when it's made again after a failure, it's returned
    as it is.

    :raises ValueError: if the kind's items aren't kept in Storage.
    :raises darq.DarqError, TimeoutError, model.ConflictError: if the KB
                                                               won't."""
    existing = kb.get(entity_id, follow=False)
    if existing is not None:
        return existing
    return kb.put(made(kind, entity_id, label, file_name, created, source, statements))[0]


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
        prop(MEDIA_TYPE, "media type", Datatype.String, "IANA-registered identifier for a file type"),
        prop(FILE_EXTENSION, "file extension", Datatype.String,
             "filename suffix that indicates the format of the file's contents"),
        prop(HAS_TAG, "has tag", Datatype.Item, "A tag on anything: an entity of its own, which lists what has it"),
        prop(DEPICTS, "depicts", Datatype.Item, "entity visually depicted in an image, literarily described in a work, "
             "or otherwise incorporated into an audiovisual or other medium"),
        prop(IMAGE, "image", Datatype.Item, "image of relevant illustration of the subject"),
        *(item(item_id, label) for _, item_id, label in INVERSE_LABELS),
        item(TEXT_FILE, "text file"),
        item(IMAGE_FILE, "image file"),
        item(COMPUTER_FILE, "computer file"),
    ]
