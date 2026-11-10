# darqos
# Copyright (C) 2026 David Arnold

# Give Items Entities: give the items made before every item was an
# entity theirs, and make what refers to them by identifier link to them
# (see doc/entity-vs-item.rst, steps 3 and 4).
#
# An item of a type whose content is in Storage, text, an image, a PDF or
# an e-book, is given its entity by its type implementation when it's
# opened, or its entity is.  This finds the rest: Storage can't list
# its blobs, so they're those History has events for, and those the KB
# refers to, by identifier, as a message's attachment, a URL's snapshot,
# a copy of a book's content, or an edition's cover.  Each is opened
# with OpenEntity, on its type's implementation, which gives it its
# entity, named by its content's title, if it has one.  One that's still
# unnamed is named from what referred to it, if it can be: an
# attachment's file name, or the last part of a snapshot's URL.
#
# Then what referred to an item by its identifier, a String, links to its
# entity: the statement's made again, with the property that links, and
# the same qualifiers, references and rank, written through the KB type
# implementation.  One whose item's gone, or that's of a file that isn't
# an item, is left as it is.
#
# It's a tool (see tool.json), started from the development Terminal's
# Tools menu, and can be run again: an item that has its entity is left
# as it is, as is a link.  It logs what it did, as counts, and exits.

import logging
import pathlib
import typing
import urllib.parse

import darq
import darq.book
import darq.items
import darq.kb as model
import darq.message
import darq.url
from darq.protocols import kb as wire
from darq.protocols.history import History
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.kb import EntityItem
from darq.protocols.type import Implementation, Status, Type
from darq.ui import kb_writer


# Seconds to wait for services' replies.
TIMEOUT = 30

# Events read from History at a time, and entities from the KB.
PAGE = 1000
BATCH = 100

# The properties whose values were items, by identifier, with those that
# link to them now.
LINKS = {darq.message.OLD_ATTACHMENT: darq.message.ATTACHMENT, darq.url.OLD_SNAPSHOT: darq.url.SNAPSHOT,
         darq.book.OLD_CONTENT: darq.book.CONTENT, darq.book.OLD_COVER: darq.book.COVER}
REFERENCES = list(LINKS)


class Counts(typing.NamedTuple):
    had: int = 0
    made: int = 0
    named: int = 0
    unnamed: int = 0
    gone: int = 0
    failed: int = 0


def is_content(item_id: str) -> bool:
    try:
        return darq.items.is_content(darq.split_item_id(item_id)[0])
    except darq.InvalidIdentifierError:
        return False


def history_items(history: History.Client) -> list[str]:
    """Return the items of the content types History has events for, newest
    first, each once."""
    found: dict[str, None] = {}
    end = 0
    while True:
        response = history.get_events(start=0, end=end, type_ids=list(darq.items.KINDS), max=PAGE)
        if response.status != HistoryStatus.OK:
            raise darq.DarqError(f"get_events: {response.status.name}")
        for event in response.events:
            found.setdefault(event.item_id)
        if not response.events:
            return list(found)
        end = response.events[-1].time


def name_of(url: str) -> str:
    """Return the name of what's at a URL: the last part of its path."""
    return urllib.parse.unquote(pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).name)


def string(value: model.Value | None) -> str:
    return value.value if isinstance(value, model.StringValue) else ""


def referred(kb: model.KB) -> dict[str, str]:
    """Return the content items the KB refers to, with what each can be
    named, from what refers to it, or empty."""
    found: dict[str, str] = {}
    for property_id in REFERENCES:
        query = model.Query()
        subject = query.variable()
        query.match(subject, property_id, model.ANY)
        query.select(subject)
        ids = sorted({row[0] for row in kb.query(query)})
        for start in range(0, len(ids), BATCH):
            entities, _ = kb.get_entities(ids[start:start + BATCH], follow=False)
            for entity in entities:
                for statement in entity.get_statements(property_id):
                    item_id = string(statement.get_value())
                    if not is_content(item_id):
                        continue
                    name = ""
                    if property_id == darq.message.OLD_ATTACHMENT:
                        name = darq.url.qualifier(statement, darq.message.FILE_NAME)
                    elif property_id == darq.url.OLD_SNAPSHOT:
                        at = darq.url.qualifier(statement, darq.url.FINAL_URL) or next(
                            (string(s.get_value()) for s in entity.get_statements(darq.url.URL)), "")
                        name = name_of(at) if at else ""
                    if name or item_id not in found:
                        found[item_id] = name
    return found


def give(items: dict[str, str], kb: model.KB, types: Type.Client,
         connect: typing.Callable[[int], Implementation.Client] =
         lambda port: Implementation.Client(port, TIMEOUT),
         entity: typing.Callable[[int], EntityItem.Client] = lambda port: EntityItem.Client(port, TIMEOUT)) -> Counts:
    """Give items their entities, and name those that are unnamed from what
    referred to them, if that's known."""
    counts = dict(Counts()._asdict())
    implementations: dict[str, Implementation.Client | None] = {}

    def implementation(type_id: str) -> Implementation.Client | None:
        if type_id not in implementations:
            try:
                activated = types.activate(type_id=type_id)
            except (darq.DarqError, TimeoutError) as e:
                logging.warning(f"Can't activate {type_id}: {e!r}")
                activated = None
            port = activated.implementation if activated is not None and activated.status == Status.OK else None
            implementations[type_id] = connect(port) if port is not None else None
        return implementations[type_id]

    for item_id, name in items.items():
        if not is_content(item_id):
            continue
        type_id, entity_id = darq.split_item_id(item_id)
        client = implementation(type_id)
        if client is None:
            counts["failed"] += 1
            continue
        try:
            had = darq.items.read(kb, entity_id) is not None
            response = client.open_entity(item_id=item_id)
        except (darq.DarqError, TimeoutError, LookupError, ValueError) as e:
            logging.warning(f"Can't open {item_id}'s entity: {e!r}")
            counts["failed"] += 1
            continue
        if response.status == Status.NOT_FOUND:
            counts["gone"] += 1
            continue
        if response.status != Status.OK or response.item is None:
            logging.warning(f"Can't open {item_id}'s entity: {response.status.name}")
            counts["failed"] += 1
            continue
        counts["had" if had else "made"] += 1
        try:
            if name_from(entity(response.item), name):
                counts["named"] += 1
            elif not had:
                counts["unnamed"] += 1
        except (darq.DarqError, TimeoutError, LookupError, ValueError) as e:
            logging.warning(f"Can't name {item_id}: {e!r}")
        finally:
            darq.close_port(response.item)
    return Counts(**counts)


def name_from(item: EntityItem.Client, name: str) -> bool:
    """Name an item's entity from a file name, through its port, if it's
    unnamed: return whether it was.  The port's closed."""
    try:
        got = item.get_entity(languages=["en"])
        if got.status != wire.Status.OK:
            return False
        current = model.entity_from_wire(got.entity)
        label = darq.items.label_for(file_name=name)
        if current.labels or not label:
            return False
        current.set_label(label)
        if not current.get_statements(darq.items.FILE_NAME):
            current.claim(darq.items.FILE_NAME, model.StringValue(name)).add_reference(
                model.Reference().add(model.STATED_IN, model.EntityValue(model.DARQOS)))
        return item.put_entity(model.entity_to_wire(current)).status == wire.Status.OK
    finally:
        item.close()


class Relinked(typing.NamedTuple):
    linked: int = 0
    left: int = 0


def relink(kb: model.KB, writer: model.KB) -> Relinked:
    """Make what refers to an item by its identifier link to its entity, if
    it has one, and return how many were, and were left.

    :param writer: The KB, written through the KB type implementation."""
    linked = left = 0
    for old, new in LINKS.items():
        query = model.Query()
        subject = query.variable()
        query.match(subject, old, model.ANY)
        query.select(subject)
        ids = sorted({row[0] for row in kb.query(query)})
        for start in range(0, len(ids), BATCH):
            entities, _ = kb.get_entities(ids[start:start + BATCH], follow=False)
            changed = []
            for entity in entities:
                touched = False
                for statement in list(entity.get_statements(old)):
                    item_id = string(statement.get_value())
                    target = darq.split_item_id(item_id)[1] if is_content(item_id) else ""
                    if not target or darq.items.read(kb, target) is None:
                        left += 1
                        continue
                    entity.remove_statement(statement)
                    entity.add_statement(model.Statement(model.Snak(new, model.EntityValue(target)),
                                                         statement.qualifiers, statement.references, statement.rank))
                    linked += 1
                    touched = True
                if touched:
                    changed.append(entity)
            if changed:
                writer.put(*changed)
    return Relinked(linked, left)


def darq_main():
    history = History.Client(darq.connect("darq.history"), timeout=TIMEOUT)
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    kb = model.KB.connect(timeout=TIMEOUT)
    items = {item_id: "" for item_id in history_items(history)}
    for item_id, name in referred(kb).items():
        if name or item_id not in items:
            items[item_id] = name
    counts = give(items, kb, types)
    logging.info(f"Gave items entities: {counts.made} made, {counts.had} had one, {counts.named} named from what "
                 f"refers to them, {counts.unnamed} unnamed, {counts.gone} gone, {counts.failed} failed")
    relinked = relink(kb, kb_writer.writer(kb))
    logging.info(f"Linked to items: {relinked.linked} linked, {relinked.left} left, as their items are gone, "
                 f"or they're of files that aren't items")
    return 0
