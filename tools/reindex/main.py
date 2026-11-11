# darqos
# Copyright (C) 2026 David Arnold

# Reindex: publish every item's entry to the Index again (see
# doc/search-service.rst), for an index that's been lost, or publishes
# dropped while the Index wasn't running.
#
# The items are those History says were created or imported, and the
# Knowledge Base's entities of its kinds (people, organisations, and the
# others), which an import writes without recording them: those of a
# class with a kind as its instance type, or with a recorded kind.  Each
# is asked for its entry by its type's implementation, with IndexEntry,
# and published; one that's been destroyed is withdrawn; and one whose
# type isn't indexed, or whose implementation can't be reached, is
# skipped.
#
# It's a tool (see tool.json), started from the development Terminal's
# Tools menu, and by the Index when it starts with a new database.  It
# logs what it did, as counts, and exits.

import logging
import typing

import darq
import darq.history
import darq.index
import darq.kb as model
from darq.protocols.history import History
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.type import Implementation, Status, Type


# Seconds to wait for services' replies.
TIMEOUT = 30.0

# Events read from History at a time.
PAGE = 1000

# The events that say an item exists.
EVENTS = [darq.history.CREATED, darq.history.IMPORTED]

# Entities read from the KB at a time.
BATCH = 100


class Counts(typing.NamedTuple):
    published: int = 0
    withdrawn: int = 0
    skipped: int = 0


def item_ids(history: History.Client) -> list[str]:
    """Return the items History says were created or imported, newest
    first, each once."""
    found: dict[str, None] = {}
    end = 0
    while True:
        response = history.get_events(start=0, end=end, events=EVENTS, max=PAGE)
        if response.status != HistoryStatus.OK:
            raise darq.DarqError(f"get_events: {response.status.name}")
        for event in response.events:
            found.setdefault(event.item_id)
        if not response.events:
            return list(found)
        end = response.events[-1].time


def kb_item_ids(kb: model.KB) -> list[str]:
    """Return the KB's items of a kind, other than things, darq.thing, in
    order of identifier."""
    ids: set[str] = set()
    for kind, _ in model.KINDS:
        ids.update(kb.find_by_value(model.DARQOS_TYPE, kind))
        for class_id in list(kb.find_by_value(model.DARQOS_INSTANCE_TYPE, kind)):
            ids.update(kb.instances(class_id))

    classes: dict[str, model.Entity | None] = {}

    def get_class(class_id: str) -> model.Entity | None:
        if class_id not in classes:
            classes[class_id] = kb.get(class_id)
        return classes[class_id]

    found = []
    ordered = sorted(ids)
    for n in range(0, len(ordered), BATCH):
        entities, _ = kb.get_entities(ordered[n:n + BATCH], follow=False)
        for entity in entities:
            kind = model.kind_of(entity, get_class)
            if kind != model.THING_TYPE:
                found.append(model.item_id(entity.id, kind))
    return found


def reindex(history: History.Client, types: Type.Client, index: darq.index.Publisher,
            kb: model.KB | None = None,
            connect: typing.Callable[[int], Implementation.Client] =
            lambda port: Implementation.Client(port, TIMEOUT)) -> Counts:
    """Publish every item's entry again: History's, and, given the KB,
    its entities'."""
    published = withdrawn = skipped = 0

    # Implementations, by type, or None for a type that can't be.
    implementations: dict[str, Implementation.Client | None] = {}

    def implementation(type_id: str) -> Implementation.Client | None:
        if type_id not in implementations:
            try:
                activated = types.activate(type_id=type_id)
            except (darq.DarqError, TimeoutError) as e:
                logging.warning(f"Can't activate {type_id}: {e!r}")
                activated = None
            if activated is not None and activated.status == Status.OK and activated.implementation is not None:
                implementations[type_id] = connect(activated.implementation)
            else:
                if activated is not None:
                    logging.warning(f"Can't activate {type_id}: {activated.status.name}")
                implementations[type_id] = None
        return implementations[type_id]

    items = item_ids(history)
    if kb is not None:
        items = list(dict.fromkeys(items + kb_item_ids(kb)))
    for item_id in items:
        type_id, _ = darq.split_item_id(item_id)
        client = implementation(type_id)
        if client is None:
            skipped += 1
            continue
        try:
            response = client.index_entry(item_id=item_id)
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't get the entry for {item_id}: {e!r}")
            skipped += 1
            continue
        if response.status == Status.OK:
            index.publish(item_id, response.names, response.body)
            published += 1
        elif response.status == Status.NOT_FOUND:
            index.withdraw(item_id)
            withdrawn += 1
        else:
            if response.status != Status.UNSUPPORTED:
                logging.warning(f"Can't get the entry for {item_id}: {response.status.name}")
            skipped += 1
    return Counts(published, withdrawn, skipped)


def darq_main():
    history = History.Client(darq.connect("darq.history"), timeout=TIMEOUT)
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    kb = model.KB.connect(timeout=TIMEOUT)
    # Waiting for the Index, which its publishes don't.
    darq.connect(darq.index.NAME)
    counts = reindex(history, types, darq.index.Publisher(), kb)
    logging.info(f"Reindexed: {counts.published} published, {counts.withdrawn} withdrawn, "
                 f"{counts.skipped} skipped")
    return 0
