# darqos
# Copyright (C) 2026 David Arnold

"""Items' names, as the Terminal and the lenses show them (see
doc/item.rst): every item has an entity, and its name's
its entity's label, or, if it has none, its kind and when it was made,
such as "Text File, 8 Nov 2026"."""

import datetime
import typing

import darq
import darq.items
import darq.kb as model

from .links import type_name


# Control exports
__all__ = ["display_name", "item_name", "item_names"]

# Entities read at a time.
BATCH = 200


def made_on(entity: model.Entity) -> str:
    """Return when an item was made, as a date, or empty if it's not
    known."""
    for statement in entity.get_statements(darq.items.CREATED):
        value = statement.get_value()
        if isinstance(value, model.TimeValue) and value.month and value.day:
            moment = datetime.datetime(value.year, value.month, value.day, value.hour or 0, value.minute or 0,
                                       tzinfo=datetime.timezone.utc).astimezone()
            return f"{moment.day} {moment:%b %Y}"
    return ""


def display_name(entity: model.Entity, kind: str = "", language: str = "en") -> str:
    """Return an item's name: its entity's label, in a language, or any, or
    else its kind's name, and when it was made, if that's known.

    :param kind: The item's type, if its entity doesn't record it."""
    label = entity.labels.get(language) or next(iter(entity.labels.values()), "")
    if label:
        return label
    kind_name = type_name(kind or model.recorded_type(entity))
    made = made_on(entity)
    return f"{kind_name}, {made}" if made else kind_name


def item_names(kb: model.KB | None, item_ids: typing.Iterable[str]) -> dict[str, str]:
    """Return items' names, by identifier, reading their entities a batch
    at a time: an item that has no entity, or whose entity can't be read,
    as when the KB isn't there, has none."""
    wanted: dict[str, str] = {}
    for item_id in item_ids:
        try:
            wanted.setdefault(darq.split_item_id(item_id)[1], item_id)
        except darq.InvalidIdentifierError:
            continue
    if kb is None or not wanted:
        return {}
    names: dict[str, str] = {}
    ids = list(wanted)
    try:
        for start in range(0, len(ids), BATCH):
            entities, _ = kb.get_entities(ids[start:start + BATCH], follow=False)
            for entity in entities:
                item_id = wanted.get(entity.id)
                if item_id is None or entity.destroyed:
                    continue
                if entity.redirect is not None:
                    # Merged, as into Wikidata's: it's what it was merged
                    # into's name.
                    merged = kb.get(entity.id)
                    if merged is None:
                        continue
                    entity = merged
                names[item_id] = display_name(entity, darq.split_item_id(item_id)[0])
    except (darq.DarqError, TimeoutError, LookupError, ValueError):
        return names
    return names


def item_name(item_id: str, kb: model.KB | None = None) -> str:
    """Return an item's name, or its identifier, if it has none, or it
    can't be read: with the KB, if it's running, when none's given."""
    if kb is None:
        try:
            handle = darq.lookup_name("darq.kb", blocking=False)
        except darq.DarqError:
            return item_id
        if handle is None:
            return item_id
        kb = model.KB(model.wire.KB.Client(handle, timeout=5.0))
    return item_names(kb, [item_id]).get(item_id, item_id)
