# darqos
# Copyright (C) 2026 David Arnold

"""The entity type, which every type implementation inherits (see
doc/entity-vs-item.rst): every item is a KB entity, so every
implementation serves the EntityItem protocol (lib/didl/kb.didl) for its
items, on a port from OpenEntity, beside the port from Open that serves
its type's own protocol.

EntityType is the base class, the lower layer: it reads and writes an
item's entity, in the KB, gives the names of what it links to, finds
its links a batch at a time, and searches the KB for entities to link
to.  It keeps nothing itself: each request is answered from the KB.

It calls entity_changed after each change made through an item's port,
which an implementation overrides, as the layer above, to publish what's
changed to the Index: types/kb, for the KB's own kinds, publishes their
names and descriptions, and a type whose content is in Storage what's in
it too.  So everything about an item is written through its type
implementation, and its Index entry kept up to date by it alone.

An entity's kind is its recorded DarqOS type, or else the one its
classes give (see darq.kb.kind_of): the classes read are cached, and the
cache kept current by watching the KB."""

import heapq
import itertools
import logging
import typing

import darq
import darq.kb as model
import darq.message
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    CloseRequest,
    CloseResponse,
    EntityItem,
    FindEntitiesRequest,
    FindEntitiesResponse,
    GetEntityRequest,
    GetEntityResponse,
    GetLinkedEntitiesRequest,
    GetLinkedEntitiesResponse,
    GetLinksRequest,
    GetLinksResponse,
    GetLinkSummaryRequest,
    GetLinkSummaryResponse,
    Linked,
    LinkGroup,
    Listener,
    PutEntityRequest,
    PutEntityResponse,
    SuggestPropertiesRequest,
    SuggestPropertiesResponse,
    WatchEntityRequest,
    WatchEntityResponse,
)
from darq.protocols.type import Implementation, OpenEntityRequest, OpenEntityResponse, Status


# Control exports
__all__ = ["ClassListener", "EntityItemHandler", "EntityType", "KB_ERRORS"]


# The type of a message, which has a summary, since it has no label.
MESSAGE_TYPE = "darq.message"

# Errors talking to the KB service.
KB_ERRORS = (darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError)

# Most entities found by FindEntities, and suggested by
# SuggestProperties: and how many are read at a time, to see if they're
# of the kind wanted.
MAX_FOUND = 200
FIND_BATCH = 100

# The qualifiers for annotating statements by hand, in the order they're
# suggested.
ANNOTATIONS = [model.START_TIME, model.END_TIME, model.POINT_IN_TIME, model.REASON_FOR_DEPRECATED_RANK,
               model.REASON_FOR_PREFERRED_RANK]


class EntityType(Implementation.Handler):
    """The entity type's part of a type implementation: its items'
    entities, for the types it implements."""

    def __init__(self, kb: model.KB, types: typing.Iterable[str]):
        """Constructor.

        :param kb: The KB, read and written with.
        :param types: The types implemented: their items' entities are
                      opened, and others' aren't."""
        self.kb = kb
        self.types = frozenset(types)

        # Classes read to find entities' kinds, by identifier, or None
        # for one that doesn't exist.  An entity's dropped when the KB
        # says it's changed.
        self.classes: dict[str, model.Entity | None] = {}

        # Entity ports, by port.
        self.items: dict[int, "EntityItemHandler"] = {}

    def add(self, server: darq.Server, port: int):
        """Answer requests on a port, with a server, and watch the KB for
        changes to classes."""
        server.add(port, self)
        listener = darq.open_port()
        server.add(listener, ClassListener(self))
        self.kb.watch([], listener)

    # The layer above

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """An item's entity has been changed through its port: publish what's
        changed.  It's an implementation's to override: here, nothing's
        done."""

    # Kinds

    def get_class(self, class_id: str) -> model.Entity | None:
        if class_id not in self.classes:
            self.classes[class_id] = self.kb.get(class_id)
        return self.classes[class_id]

    def kind_of(self, entity: model.Entity) -> str:
        return model.kind_of(entity, self.get_class)

    def item_id(self, entity: model.Entity) -> str:
        """Return an entity's item identifier, or empty for a property,
        which isn't an item."""
        if isinstance(entity, model.Property):
            return ""
        return model.item_id(entity.id, self.kind_of(entity))

    def find(self, item_id: str) -> tuple[model.Entity | None, Status]:
        """Return an item's entity, following a merge, or why there's none.

        It's NOT_FOUND if it doesn't exist, is destroyed, or is of
        another kind than the item identifier's type, or one that's not
        implemented here."""
        try:
            type_id, entity_id = darq.split_item_id(item_id)
        except darq.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if type_id not in self.types:
            return None, Status.NOT_FOUND

        entity = self.kb.get(entity_id, follow=False)
        if entity is None or entity.destroyed:
            return None, Status.NOT_FOUND
        if entity.redirect is not None:
            return self.kb.get(entity_id), Status.OK
        if self.kind_of(entity) != type_id:
            return None, Status.NOT_FOUND
        return entity, Status.OK

    # Names

    def property_labels(self, ids: typing.Iterable[str], languages: list[str]) -> dict[str, tuple[str, str]]:
        """Return properties' labels and inverse labels, by identifier, in
        a chain of languages: empty for those that have none."""
        def label(entity: model.Entity | None) -> str:
            if entity is None:
                return ""
            return next((entity.labels[language] for language in languages if language in entity.labels), "")

        def inverse(entity: model.Entity) -> str:
            values = [s.get_value() for s in entity.get_statements(model.INVERSE_LABEL)]
            return next((v.entity_id for v in values if isinstance(v, model.EntityValue)), "")

        properties, _ = self.kb.get_entities(sorted(set(ids)), follow=False)
        items, _ = self.kb.get_entities(sorted({inverse(p) for p in properties} - {""}), follow=False)
        by_id = {item.id: item for item in items}
        return {i: ("", "") for i in ids} | {p.id: (label(p), label(by_id.get(inverse(p)))) for p in properties}

    def linked(self, ids: typing.Iterable[str], languages: list[str]) -> list[Linked]:
        """Return entities, by identifier, as Linked, with labels in a
        chain of languages, in the order of the identifiers.

        One that's been merged is listed by the identifier asked for, as
        that's what's linked to, with the label and item of what it
        redirects to: a statement made before the merge still names it."""
        wanted = list(dict.fromkeys(ids))
        found, _ = self.kb.get_entities(wanted, follow=False)
        resolved: list[tuple[str, model.Entity]] = []
        for entity in found:
            if entity.redirect is not None:
                target = self.kb.get(entity.id)
                if target is None:
                    continue
                resolved.append((entity.id, target))
            else:
                resolved.append((entity.id, entity))
        linked = self.as_linked([entity for _, entity in resolved], languages)
        for (asked, _), link in zip(resolved, linked):
            link.entity_id = asked
        return linked

    def as_linked(self, entities: typing.Iterable[model.Entity], languages: list[str]) -> list[Linked]:
        entities = list(entities)
        summaries = self.summaries(entities, languages)
        result = []
        for entity in entities:
            language, label = next(((lang, entity.labels[lang]) for lang in languages if lang in entity.labels),
                                   ("", ""))
            description = entity.descriptions.get(language, "") if language else ""
            result.append(Linked(entity.id, self.item_id(entity), language, label, description,
                                 model.datatype_to_wire(entity), summaries.get(entity.id, "")))
        return result

    def summaries(self, entities: list[model.Entity], languages: list[str]) -> dict[str, str]:
        """Return lines about the messages among some entities, by
        identifier, naming their senders in a chain of languages."""
        messages = [e for e in entities if model.recorded_type(e) == MESSAGE_TYPE]
        senders = {darq.message.sender_of(e) for e in messages} - {""}
        found, _ = self.kb.get_entities(sorted(senders)) if senders else ([], {})
        names = {e.id: next((e.labels[lang] for lang in languages if lang in e.labels), "") for e in found}
        return {e.id: darq.message.summary(e, names.get(darq.message.sender_of(e), "")) for e in messages}

    # Searching

    def labelled(self, text: str, language: str, class_id: str, after: str) -> typing.Iterator[str]:
        """Return the entities with words of a label or alias in a language
        starting with each of the text's, after an identifier, in order."""
        while True:
            response = self.kb.client.find_by_label(text, language, wire.Match.WORDS, class_id, 0, after)
            if response.status != wire.Status.OK:
                raise LookupError(f"Can't find {text!r}: {response.status.name}")
            yield from response.ids
            if not response.ids:
                return
            after = response.ids[-1]

    def search(self, text: str, languages: list[str], kind: wire.Kind, class_id: str, limit: int,
               after: str) -> list[Linked]:
        """Return the entities of a kind found by the words of their labels,
        in any of a chain of languages, in order of identifier."""
        if not text.strip():
            return []
        wanted = model.Property if kind == wire.Kind.PROPERTY else model.Item
        streams = [self.labelled(text, language, class_id, after) for language in languages or ["en"]]
        ids = (entity_id for entity_id, _ in itertools.groupby(heapq.merge(*streams)))
        found: list[model.Entity] = []
        while len(found) < limit:
            batch = list(itertools.islice(ids, FIND_BATCH))
            if not batch:
                break
            entities, _ = self.kb.get_entities(batch)
            found.extend(e for e in entities if isinstance(e, wanted) and not e.destroyed)
        return self.as_linked(found[:limit], languages)

    # Entity ports

    def attach(self, server: darq.Server, entity_id: str) -> int:
        """Return a new port for an item's entity."""
        port = darq.open_port()
        handler = EntityItemHandler(self, entity_id, port)
        self.items[port] = handler
        server.add(port, handler)
        return port

    def detach(self, port: int):
        """Forget an entity port, once its client has closed it."""
        del self.items[port]
        assert self.server is not None
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

    def open_entity(self, request: OpenEntityRequest, source: int) -> OpenEntityResponse:
        try:
            entity, status = self.find(request.item_id)
        except KB_ERRORS as e:
            logging.error(f"Can't open {request.item_id}'s entity: {e}")
            return OpenEntityResponse(status=Status.FAILED)
        if entity is None:
            return OpenEntityResponse(status=status)
        assert self.server is not None
        return OpenEntityResponse(item=self.attach(self.server, entity.id), status=Status.OK)


class ClassListener(Listener.Handler):
    """Forgets cached classes that have changed."""

    def __init__(self, implementation: EntityType):
        self.implementation = implementation

    def changed(self, request: Changed, source: int) -> None:
        # A change to an entity's subclasses can change any kind that
        # depends on it, so a changed class empties the cache.
        if request.entity_id in self.implementation.classes:
            self.implementation.classes.clear()


class EntityItemHandler(EntityItem.Handler):
    """Answers the EntityItem protocol, on one client's port for an item's
    entity."""

    def __init__(self, implementation: EntityType, entity_id: str, port: int):
        self.implementation = implementation
        self.kb = implementation.kb
        self.entity_id = entity_id
        self.port = port

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        self.implementation.detach(self.port)
        return CloseResponse()

    def get_entity(self, request: GetEntityRequest, source: int) -> GetEntityResponse:
        try:
            entities, _ = self.kb.get_entities([self.entity_id])
            if not entities:
                return GetEntityResponse(status=wire.Status.NOT_FOUND)
            entity = entities[0]
            linked = self.implementation.linked(model.referenced(entity), request.languages)
        except KB_ERRORS as e:
            logging.error(f"Can't get {self.entity_id}: {e}")
            return GetEntityResponse(status=wire.Status.NOT_FOUND)
        return GetEntityResponse(entity=model.entity_to_wire(entity), linked=linked, status=wire.Status.OK)

    def put_entity(self, request: PutEntityRequest, source: int) -> PutEntityResponse:
        if request.entity.id != self.entity_id:
            return PutEntityResponse(status=wire.Status.INVALID_ARGUMENT)
        try:
            entity = model.entity_from_wire(request.entity)
        except (KeyError, ValueError):
            return PutEntityResponse(status=wire.Status.INVALID_ARGUMENT)

        try:
            current = self.kb.get(self.entity_id, follow=False)
            if current is None:
                return PutEntityResponse(status=wire.Status.NOT_FOUND)

            # Its kind stays as it was.
            for s in entity.get_statements(model.DARQOS_TYPE):
                entity.remove_statement(s)
            for s in current.get_statements(model.DARQOS_TYPE):
                entity.add_statement(s)

            response = self.kb.client.put_entities([model.entity_to_wire(entity)])
        except KB_ERRORS as e:
            logging.error(f"Can't put {self.entity_id}: {e}")
            return PutEntityResponse(status=wire.Status.NOT_FOUND)
        if response.status != wire.Status.OK:
            return PutEntityResponse(status=response.status)
        try:
            self.implementation.entity_changed(current, model.entity_from_wire(response.entities[0]))
        except KB_ERRORS as e:
            logging.error(f"Can't publish {self.entity_id}: {e}")
        return PutEntityResponse(revision=response.entities[0].revision, status=wire.Status.OK)

    def get_links(self, request: GetLinksRequest, source: int) -> GetLinksResponse:
        try:
            response = self.kb.client.find_links(self.entity_id, request.property_id, request.role,
                                                 request.max, request.after, request.reverse, request.type_id)
            if response.status != wire.Status.OK:
                return GetLinksResponse(status=response.status)
            linked = self.implementation.linked(response.ids, request.languages)
        except KB_ERRORS as e:
            logging.error(f"Can't find links to {self.entity_id}: {e}")
            return GetLinksResponse(status=wire.Status.NOT_FOUND)
        return GetLinksResponse(linked=linked, status=wire.Status.OK)

    def get_link_summary(self, request: GetLinkSummaryRequest, source: int) -> GetLinkSummaryResponse:
        try:
            response = self.kb.client.link_counts(self.entity_id)
            if response.status != wire.Status.OK:
                return GetLinkSummaryResponse(status=response.status)
            labels = self.implementation.property_labels({c.property_id for c in response.counts}, request.languages)
        except KB_ERRORS as e:
            logging.error(f"Can't count links to {self.entity_id}: {e}")
            return GetLinkSummaryResponse(status=wire.Status.NOT_FOUND)
        groups = [LinkGroup(c.property_id, c.role, c.type_id, c.count, *labels[c.property_id])
                  for c in response.counts]
        return GetLinkSummaryResponse(groups=groups, status=wire.Status.OK)

    def get_linked_entities(self, request: GetLinkedEntitiesRequest, source: int) -> GetLinkedEntitiesResponse:
        try:
            response = self.kb.client.find_links(self.entity_id, request.property_id, request.role, request.max,
                                                 request.after, request.reverse, request.type_id)
            if response.status != wire.Status.OK:
                return GetLinkedEntitiesResponse(status=response.status)
            entities, _ = self.kb.get_entities(response.ids, follow=False)
            referred = {i for entity in entities for i in model.referenced(entity)}
            linked = self.implementation.linked(referred, request.languages)
        except KB_ERRORS as e:
            logging.error(f"Can't read links to {self.entity_id}: {e}")
            return GetLinkedEntitiesResponse(status=wire.Status.NOT_FOUND)
        return GetLinkedEntitiesResponse(entities=[model.entity_to_wire(e) for e in entities], linked=linked,
                                         status=wire.Status.OK)

    def find_entities(self, request: FindEntitiesRequest, source: int) -> FindEntitiesResponse:
        limit = min(request.max or MAX_FOUND, MAX_FOUND)
        try:
            found = self.implementation.search(request.text, request.languages, request.kind, request.class_id,
                                               limit, request.after)
        except KB_ERRORS as e:
            logging.error(f"Can't find {request.text!r}: {e}")
            return FindEntitiesResponse(status=wire.Status.NOT_FOUND)
        return FindEntitiesResponse(found=found, status=wire.Status.OK)

    def suggest_properties(self, request: SuggestPropertiesRequest, source: int) -> SuggestPropertiesResponse:
        try:
            entity = self.kb.get(self.entity_id)
            if entity is None:
                return SuggestPropertiesResponse(status=wire.Status.NOT_FOUND)
            ids = list(ANNOTATIONS)
            for s in entity.statements:
                if s.get_property_id() not in ids and s.get_property_id() != model.DARQOS_TYPE:
                    ids.append(s.get_property_id())
            ids = ids[:min(request.max or MAX_FOUND, MAX_FOUND)]
            entities, _ = self.kb.get_entities(ids)
            properties = [e for e in entities if isinstance(e, model.Property)]
        except KB_ERRORS as e:
            logging.error(f"Can't suggest properties for {self.entity_id}: {e}")
            return SuggestPropertiesResponse(status=wire.Status.NOT_FOUND)
        return SuggestPropertiesResponse(found=self.implementation.as_linked(properties, request.languages),
                                         status=wire.Status.OK)

    def watch_entity(self, request: WatchEntityRequest, source: int) -> WatchEntityResponse:
        if request.listener is None:
            return WatchEntityResponse(status=wire.Status.INVALID_ARGUMENT)

        # The KB tells the listener directly: this handle's no longer
        # needed, once it's been passed on.
        try:
            response = self.kb.client.watch([self.entity_id], request.listener)
        except KB_ERRORS as e:
            logging.error(f"Can't watch {self.entity_id}: {e}")
            return WatchEntityResponse(status=wire.Status.NOT_FOUND)
        finally:
            darq.close_port(request.listener)
        return WatchEntityResponse(status=response.status)
