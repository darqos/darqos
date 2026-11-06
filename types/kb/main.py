# darqos
# Copyright (C) 2026 David Arnold

# Knowledge Base type implementation: items of darq.entity, and of its
# kinds, darq.person, darq.organisation, darq.organisational-unit,
# darq.place, darq.group, darq.conversation, darq.message and public.url,
# each an entity in the Knowledge Base.
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst), once for all of these types.  It serves the
# Implementation protocol (lib/didl/type.didl) on its own port, and the
# EntityItem protocol (lib/didl/kb.didl) on a port for each client of an
# item.  Entities of its kinds are written through it, not to the KB
# directly, so it also serves EntityWriter, on its Implementation port.
# It's a client of the Knowledge Base service, darq.kb, and keeps
# nothing itself: each request is answered from the KB.
#
# An item's identifier is its kind's type and its entity's identifier,
# as darq.person:Q42.  An entity's kind is recorded when it's created
# here, and otherwise found from its classes (see darq.kb.kind_of), by
# their instance types: the defaults are stated by DarqOS when it
# starts, and kept up to date in the KB, so a preferred-rank statement
# of the operator's overrides them.  The classes it reads are cached,
# and the cache kept current by watching the KB.
#
# Clients, such as the entity lens, have only an item's port, so it's
# there that they search the KB too, for entities to refer to.
#
# Each entity of a kind it writes is published to the Index (see
# doc/search-service.rst): its labels and aliases, in every language, as
# names, and its descriptions as its body, with a message's text.
# Entities of darq.entity, and properties, aren't indexed.  One that's destroyed, merged into
# another, or no longer of its kind is withdrawn.

import heapq
import itertools
import logging
import typing

import darq
import darq.index
import darq.kb as model
import darq.message
import darq.book
import darq.url
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    CloseRequest,
    CloseResponse,
    CreateEntitiesRequest,
    CreateEntitiesResponse,
    DestroyEntityRequest,
    DestroyEntityResponse,
    EntityItem,
    EntityWriter,
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
    MergeEntityRequest,
    MergeEntityResponse,
    PutEntityRequest,
    PutEntityResponse,
    SuggestPropertiesRequest,
    SuggestPropertiesResponse,
    WatchEntityRequest,
    WatchEntityResponse,
    WriteEntitiesRequest,
    WriteEntitiesResponse,
)
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
    Implementation,
    OpenRequest,
    OpenResponse,
    Ready,
    Status,
)


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
        item(model.DARQOS, "DarqOS"),
        item(model.REPLACED, "replaced by hand"),
        item(model.REMOVED, "removed by hand"),
        item(model.ENTITY_EDITOR, "Darq Entity Editor"),
        *(item(class_id, label) for class_id, label, _ in model.DEFAULT_INSTANCE_TYPES),
    ]


class EntityImplementation(Implementation.Handler, EntityWriter.Handler):
    """Creates, opens, writes and destroys KB items."""

    METHODS = {**Implementation.Handler.METHODS, **EntityWriter.Handler.METHODS}

    def __init__(self, kb: model.KB, index: darq.index.Publisher):
        self.kb = kb
        self.index = index

        # Classes read to find entities' kinds, by identifier, or None
        # for one that doesn't exist.  An entity's dropped when the KB
        # says it's changed.
        self.classes: dict[str, model.Entity | None] = {}

        # Item ports, by port.
        self.items: dict[int, "EntityItemHandler"] = {}

    def add(self, server: darq.Server, port: int):
        """Answer requests on a port, with a server, and watch the KB for
        changes to classes."""
        server.add(port, self)
        listener = darq.open_port()
        server.add(listener, ClassListener(self))
        self.kb.watch([], listener)

    def start(self):
        """Find or create the vocabulary, and state the default instance
        types."""
        self.kb.ensure(*vocabulary())
        for class_id, _, kind in model.DEFAULT_INSTANCE_TYPES:
            self.kb.state(class_id, model.DARQOS, [
                model.Statement(model.Snak(model.DARQOS_INSTANCE_TYPE, model.StringValue(kind)))])
        for property_id, item_id, _ in (*darq.message.INVERSE_LABELS, *darq.url.INVERSE_LABELS,
                                        *darq.book.INVERSE_LABELS):
            self.kb.state(property_id, model.DARQOS, [
                model.Statement(model.Snak(model.INVERSE_LABEL, model.EntityValue(item_id)))])

    def get_class(self, class_id: str) -> model.Entity | None:
        if class_id not in self.classes:
            self.classes[class_id] = self.kb.get(class_id)
        return self.classes[class_id]

    def kind_of(self, entity: model.Entity) -> str:
        return model.kind_of(entity, self.get_class)

    def indexed_kind(self, entity: model.Entity) -> str | None:
        """Return an entity's kind, if it's indexed, or None."""
        if isinstance(entity, model.Property) or entity.destroyed or entity.redirect is not None:
            return None
        kind = self.kind_of(entity)
        return kind if kind != model.ENTITY_TYPE else None

    def publish(self, entity: model.Entity, was: str | None = None):
        """Publish an entity to the Index, if it's of an indexed kind, and
        withdraw it as the kind it was, if that's changed."""
        kind = self.indexed_kind(entity)
        if was is not None and was != kind:
            self.index.withdraw(model.item_id(entity.id, was))
        if kind is not None:
            names, body = index_entry(entity)
            self.index.publish(model.item_id(entity.id, kind), names, body)

    def item_id(self, entity: model.Entity) -> str:
        """Return an entity's item identifier, or empty for a property,
        which isn't an item."""
        if isinstance(entity, model.Property):
            return ""
        return model.item_id(entity.id, self.kind_of(entity))

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
        chain of languages, in the order of the identifiers."""
        entities, _ = self.kb.get_entities(list(dict.fromkeys(ids)))
        return self.as_linked(entities, languages)

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

    def attach(self, server: darq.Server, entity_id: str) -> int:
        """Return a new port for an entity's item."""
        port = darq.open_port()
        handler = EntityItemHandler(self, entity_id, port)
        self.items[port] = handler
        server.add(port, handler)
        return port

    def detach(self, port: int):
        """Forget an item port, once its client has closed the item."""
        del self.items[port]
        assert self.server is not None
        self.server.remove(port)

        # The Close reply is sent from the port, so it's closed after.
        self.server.call_later(0, lambda: darq.close_port(port))

    def find(self, item_id: str) -> tuple[model.Entity | None, Status]:
        """Return an item's entity, following a merge, or why there's none.

        It's NOT_FOUND if it doesn't exist, is destroyed, or is of
        another kind than the item identifier's type."""
        try:
            type_id, entity_id = darq.split_item_id(item_id)
        except darq.InvalidIdentifierError:
            return None, Status.INVALID_ID
        if type_id not in model.KIND_TYPES:
            return None, Status.NOT_FOUND

        entity = self.kb.get(entity_id, follow=False)
        if entity is None or entity.destroyed:
            return None, Status.NOT_FOUND
        if entity.redirect is not None:
            return self.kb.get(entity_id), Status.OK
        if self.kind_of(entity) != type_id:
            return None, Status.NOT_FOUND
        return entity, Status.OK

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        try:
            type_id = darq.canonical_type_id(request.type_id)
        except darq.InvalidIdentifierError:
            return CreateResponse(status=Status.INVALID_ID)
        if type_id not in model.KIND_TYPES:
            return CreateResponse(status=Status.NOT_FOUND)

        entity = model.Item()
        stamp(entity, type_id)
        try:
            self.kb.put(entity)
        except KB_ERRORS as e:
            logging.error(f"Can't create a {type_id}: {e}")
            return CreateResponse(status=Status.FAILED)

        try:
            self.publish(entity)
        except KB_ERRORS as e:
            logging.error(f"Can't index the new {type_id}: {e}")
        assert self.server is not None
        iid = model.item_id(entity.id, type_id)
        logging.info(f"Created {iid}")
        return CreateResponse(item_id=iid, item=self.attach(self.server, entity.id), status=Status.OK)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        # An entity has no form as data: a vCard file, say, makes many,
        # which is a tool's job.
        return CreateFromResponse(status=Status.UNSUPPORTED)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        return ExportResponse(status=Status.UNSUPPORTED)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        try:
            entity, status = self.find(request.item_id)
        except KB_ERRORS as e:
            logging.error(f"Can't index {request.item_id}: {e}")
            return IndexEntryResponse(status=Status.FAILED)
        if entity is None:
            return IndexEntryResponse(status=status)

        # A merged entity's item is the one it was merged into's now.
        if entity.id != darq.split_item_id(request.item_id)[1]:
            return IndexEntryResponse(status=Status.NOT_FOUND)
        if self.indexed_kind(entity) is None:
            return IndexEntryResponse(status=Status.UNSUPPORTED)
        names, body = index_entry(entity)
        return IndexEntryResponse(names=names, body=body, status=Status.OK)

    def open(self, request: OpenRequest, source: int) -> OpenResponse:
        try:
            entity, status = self.find(request.item_id)
        except KB_ERRORS as e:
            logging.error(f"Can't open {request.item_id}: {e}")
            return OpenResponse(status=Status.FAILED)
        if entity is None:
            return OpenResponse(status=status)
        assert self.server is not None
        return OpenResponse(item=self.attach(self.server, entity.id), status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        try:
            entity, status = self.find(request.item_id)
            if entity is None:
                return DestroyResponse(status=status)
            kind = self.indexed_kind(entity)
            self.kb.destroy(entity.id)
        except KB_ERRORS as e:
            logging.error(f"Can't destroy {request.item_id}: {e}")
            return DestroyResponse(status=Status.FAILED)
        if kind is not None:
            self.index.withdraw(model.item_id(entity.id, kind))
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)

    def write_entities(self, request: WriteEntitiesRequest, source: int) -> WriteEntitiesResponse:
        try:
            entities = [model.entity_from_wire(e) for e in request.entities]
        except (KeyError, ValueError):
            return WriteEntitiesResponse(status=wire.Status.INVALID_ARGUMENT)

        try:
            current, _ = self.kb.get_entities([e.id for e in entities if e.id], follow=False)

            # Kinds stay as they were: a new entity has none.
            kinds = {e.id: e.get_statements(model.DARQOS_TYPE) for e in current}
            was = {e.id: self.indexed_kind(e) for e in current}
            for entity in entities:
                for s in entity.get_statements(model.DARQOS_TYPE):
                    entity.remove_statement(s)
                for s in kinds.get(entity.id, []):
                    entity.add_statement(s)

            response = self.kb.client.put_entities([model.entity_to_wire(e) for e in entities])
        except KB_ERRORS as e:
            logging.error(f"Can't write {len(entities)} entities: {e}")
            return WriteEntitiesResponse(status=wire.Status.NOT_FOUND)
        if response.status == wire.Status.OK:
            try:
                for written in response.entities:
                    entity = model.entity_from_wire(written)
                    self.publish(entity, was.get(entity.id))
            except KB_ERRORS as e:
                logging.error(f"Can't index the entities written: {e}")
        return WriteEntitiesResponse(entities=response.entities, failed_id=response.failed_id,
                                     status=response.status)

    def create_entities(self, request: CreateEntitiesRequest, source: int) -> CreateEntitiesResponse:
        try:
            type_id = darq.canonical_type_id(request.type_id)
        except darq.InvalidIdentifierError:
            return CreateEntitiesResponse(status=wire.Status.INVALID_ARGUMENT)
        if type_id not in model.KIND_TYPES:
            return CreateEntitiesResponse(status=wire.Status.NOT_FOUND)
        try:
            entities = [model.entity_from_wire(e) for e in request.entities]
        except (KeyError, ValueError):
            return CreateEntitiesResponse(status=wire.Status.INVALID_ARGUMENT)

        for entity in entities:
            if entity.revision != 0 or isinstance(entity, model.Property):
                return CreateEntitiesResponse(failed_id=entity.id, status=wire.Status.INVALID_ARGUMENT)
            stamp(entity, type_id)

        try:
            response = self.kb.client.put_entities([model.entity_to_wire(e) for e in entities])
        except KB_ERRORS as e:
            logging.error(f"Can't create {len(entities)} entities: {e}")
            return CreateEntitiesResponse(status=wire.Status.NOT_FOUND)
        if response.status == wire.Status.OK:
            try:
                for written in response.entities:
                    self.publish(model.entity_from_wire(written))
            except KB_ERRORS as e:
                logging.error(f"Can't index the entities created: {e}")
        return CreateEntitiesResponse(entities=response.entities, failed_id=response.failed_id,
                                      status=response.status)

    def merge_entity(self, request: MergeEntityRequest, source: int) -> MergeEntityResponse:
        try:
            old = self.kb.get(request.old_id, follow=False)
            was = self.indexed_kind(old) if old is not None else None
            response = self.kb.client.merge(request.old_id, request.new_id)
        except KB_ERRORS as e:
            logging.error(f"Can't merge {request.old_id} into {request.new_id}: {e}")
            return MergeEntityResponse(status=wire.Status.NOT_FOUND)
        if response.status == wire.Status.OK:
            if was is not None:
                self.index.withdraw(model.item_id(request.old_id, was))
            try:
                new = self.kb.get(request.new_id)
                if new is not None:
                    self.publish(new)
            except KB_ERRORS as e:
                logging.error(f"Can't index {request.new_id}: {e}")
        return MergeEntityResponse(status=response.status)

    def destroy_entity(self, request: DestroyEntityRequest, source: int) -> DestroyEntityResponse:
        try:
            entity = self.kb.get(request.entity_id, follow=False)
            was = self.indexed_kind(entity) if entity is not None else None
            response = self.kb.client.destroy(request.entity_id)
        except KB_ERRORS as e:
            logging.error(f"Can't destroy {request.entity_id}: {e}")
            return DestroyEntityResponse(status=wire.Status.NOT_FOUND)
        if response.status == wire.Status.OK and was is not None:
            self.index.withdraw(model.item_id(request.entity_id, was))
        return DestroyEntityResponse(status=response.status)


def stamp(entity: model.Entity, type_id: str):
    """Make an entity an item of a kind: its only DarqOS type is that,
    with the kind's class.

    These statements are DarqOS's: an import that says the same adds its
    own reference to them (see darq.kb.restate)."""
    for statement in entity.get_statements(model.DARQOS_TYPE):
        entity.remove_statement(statement)
    claims: list[tuple[str, model.Value]] = [(model.DARQOS_TYPE, model.StringValue(type_id))]
    cls = dict(model.KINDS).get(type_id)
    if cls is not None and not any(isinstance(value := s.get_value(), model.EntityValue) and value.entity_id == cls
                                   for s in entity.get_statements(model.INSTANCE_OF)):
        claims.insert(0, (model.INSTANCE_OF, model.EntityValue(cls)))
    for property_id, value in claims:
        entity.claim(property_id, value).add_reference(
            model.Reference().add(model.STATED_IN, model.EntityValue(model.DARQOS)))


def index_entry(entity: model.Entity) -> tuple[list[str], str]:
    """Return an entity's names and body, for the Index: its labels and
    aliases, and its descriptions, in every language, each once, with a
    message's text in its body."""
    names = list(dict.fromkeys([*entity.labels.values(),
                                *(a for aliases in entity.aliases.values() for a in aliases)]))
    texts = [text for s in entity.get_statements(darq.message.BODY)
             if isinstance(value := s.get_value(), model.TextValue)
             for text in value.text.values()]
    return names, "\n".join(dict.fromkeys([*entity.descriptions.values(), *texts]))


class ClassListener(Listener.Handler):
    """Forgets cached classes that have changed."""

    def __init__(self, implementation: EntityImplementation):
        self.implementation = implementation

    def changed(self, request: Changed, source: int) -> None:
        # A change to an entity's subclasses can change any kind that
        # depends on it, so a changed class empties the cache.
        if request.entity_id in self.implementation.classes:
            self.implementation.classes.clear()


class EntityItemHandler(EntityItem.Handler):
    """Answers the EntityItem protocol, on one client's port for an item."""

    def __init__(self, implementation: EntityImplementation, entity_id: str, port: int):
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
            was = self.implementation.indexed_kind(current)

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
            self.implementation.publish(model.entity_from_wire(response.entities[0]), was)
        except KB_ERRORS as e:
            logging.error(f"Can't index {self.entity_id}: {e}")
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


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = EntityImplementation(model.KB.connect(timeout=10), darq.index.Publisher())
    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    implementation.start()
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {', '.join(model.KIND_TYPES)} on port {port}")
    server.run()
