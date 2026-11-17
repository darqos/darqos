# darqos
# Copyright (C) 2026 David Arnold

"""The Knowledge Base's vocabulary: the properties and items DarqOS's
code relies on, its services and type implementations, and what's said
of them, such as which DarqOS type a class's instances' items are, and
how a property reads backwards.  See doc/kb-service.rst.

The KB service sets it up when it starts, with setup(), before it
registers darq.kb, so no process ever sees the KB without it: it's
found, or created, and what's been renamed, or has a new label, is
changed, as data's migrated when new code starts.  The imports' own
vocabularies are theirs, ensured as they run."""

import logging

import darq.book
import darq.history
import darq.items
import darq.kb as model
import darq.message
import darq.sessions
import darq.url
import darq.wikidata


# Control exports
__all__ = ["relabel", "rename_types", "setup", "vocabulary"]

# Entities read and written at a time, renaming types.
RENAME_BATCH = 200


def vocabulary() -> list[model.Entity]:
    """Return the properties and items the implementation needs."""
    def prop(entity_id: str, label: str, datatype: model.Datatype, desc: str | None = None) -> model.Property:
        p = model.Property(datatype, entity_id)
        p.set_label(label)
        if desc is not None:
            p.set_description(desc)
        return p

    def item(entity_id: str, label: str) -> model.Item:
        i = model.Item(entity_id)
        i.set_label(label)
        return i

    return [
        prop(model.INSTANCE_OF, "instance of", model.Datatype.Item),
        prop(model.SUBCLASS_OF, "subclass of", model.Datatype.Item),
        prop(model.STATED_IN, "stated in", model.Datatype.Item),
        prop(model.DARQOS_TYPE, "DarqOS type", model.Datatype.String,
             "Type of an entity's item, recorded when it was created"),
        prop(model.DARQOS_INSTANCE_TYPE, "DarqOS instance type", model.Datatype.String,
             "Type of the items of a class's instances"),
        prop(model.START_TIME, "start time", model.Datatype.Time),
        prop(model.END_TIME, "end time", model.Datatype.Time),
        prop(model.POINT_IN_TIME, "point in time", model.Datatype.Time),
        prop(model.INVERSE_LABEL, "inverse label item", model.Datatype.Item,
             "Item with a label that reads a property backwards"),
        prop(model.REASON_FOR_DEPRECATED_RANK, "reason for deprecated rank", model.Datatype.Item),
        prop(model.REASON_FOR_PREFERRED_RANK, "reason for preferred rank", model.Datatype.Item),
        *darq.message.vocabulary(),
        *darq.url.vocabulary(), *darq.book.vocabulary(),
        *darq.wikidata.vocabulary(),
        *darq.items.vocabulary(),
        *darq.sessions.vocabulary(),
        item(model.DARQOS, "DarqOS"),
        item(model.REPLACED, "replaced by hand"),
        item(model.REMOVED, "removed by hand"),
        item(model.ENTITY_EDITOR, "Darq Entity Editor"),
        *(item(class_id, label) for class_id, label, _ in model.DEFAULT_INSTANCE_TYPES),
    ]


def setup(kb: model.KB):
    """Find or create the vocabulary, give what's been renamed its new
    label or type, and state the default instance types, and inverse
    labels.  Once it's done, doing it again changes nothing."""
    kb.ensure(*vocabulary())
    relabel(kb)
    rename_types(kb)
    for class_id, _, kind in model.DEFAULT_INSTANCE_TYPES:
        kb.state(class_id, model.DARQOS, [
            model.Statement(model.Snak(model.DARQOS_INSTANCE_TYPE, model.StringValue(kind)))])
    for property_id, item_id, _ in (*darq.message.INVERSE_LABELS, *darq.url.INVERSE_LABELS,
                                    *darq.book.INVERSE_LABELS, *darq.items.INVERSE_LABELS):
        kb.state(property_id, model.DARQOS, [
            model.Statement(model.Snak(model.INVERSE_LABEL, model.EntityValue(item_id)))])


def rename_types(kb: model.KB):
    """Give items of a type that's been renamed the new one, in their
    DarqOS type statements, and classes in their instance types (see
    darq.history.RENAMED_TYPES)."""
    for old, new in darq.history.RENAMED_TYPES.items():
        for property_id in (model.DARQOS_TYPE, model.DARQOS_INSTANCE_TYPE):
            ids = sorted(kb.find_by_value(property_id, old))
            for start in range(0, len(ids), RENAME_BATCH):
                entities, _ = kb.get_entities(ids[start:start + RENAME_BATCH], follow=False)
                changed = [e for e in entities if not e.destroyed and e.redirect is None]
                for entity in changed:
                    for s in entity.get_statements(property_id):
                        value = s.get_value()
                        if isinstance(value, model.StringValue) and value.value == old:
                            value.value = new
                if changed:
                    kb.put(*changed)
            if ids:
                logging.info(f"Renamed {old} to {new} in {len(ids)} entities' {property_id}")


def relabel(kb: model.KB):
    """Give vocabulary that's been renamed its new label, unless it's
    been changed by hand."""
    for entity_id, old, new in (*darq.book.RELABELLED, *darq.url.RELABELLED, *darq.message.RELABELLED):
        entity = kb.get(entity_id, follow=False)
        if entity is not None and entity.get_label() == old:
            entity.set_label(new)
            kb.put(entity)
