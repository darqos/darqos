# darqos
# Copyright (C) 2026 David Arnold

# Knowledge Base type implementation: items of darq.entity, and of its
# kinds, darq.person, darq.organisation, darq.organisational-unit,
# darq.place and darq.group, each an entity in the Knowledge Base.
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst), once for all of these types.  It serves the
# Implementation protocol (lib/didl/type.didl) on its own port, and the
# EntityItem protocol (lib/didl/kb.didl) on a port for each client of an
# item.  It's a client of the Knowledge Base service, darq.kb, and keeps
# nothing itself: each request is answered from the KB.
#
# An item's identifier is its kind's type and its entity's identifier,
# as darq.person:Q42.  An entity's kind is recorded when it's created
# here, and otherwise found from its classes (see darq.kb.kind_of), by
# their instance types: the defaults are stated by DarqOS when it
# starts, and kept up to date in the KB, so a preferred-rank statement
# of the operator's overrides them.  The classes it reads are cached,
# and the cache kept current by watching the KB.

import logging
import typing

import darq
import darq.kb as model
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    CloseRequest,
    CloseResponse,
    EntityItem,
    GetEntityRequest,
    GetEntityResponse,
    GetLinksRequest,
    GetLinksResponse,
    Linked,
    Listener,
    PutEntityRequest,
    PutEntityResponse,
    WatchEntityRequest,
    WatchEntityResponse,
)
from darq.protocols.type import (
    CreateRequest,
    CreateResponse,
    DestroyRequest,
    DestroyResponse,
    Implementation,
    OpenRequest,
    OpenResponse,
    Ready,
    Status,
)


# Errors talking to the KB service.
KB_ERRORS = (darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError)


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
        item(model.DARQOS, "DarqOS"),
        *(item(class_id, label) for class_id, label, _ in model.DEFAULT_INSTANCE_TYPES),
    ]


class EntityImplementation(Implementation.Handler):
    """Creates, opens and destroys KB items."""

    def __init__(self, kb: model.KB):
        self.kb = kb

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

    def linked(self, ids: typing.Iterable[str], languages: list[str]) -> list[Linked]:
        """Return entities as Linked, with labels in a chain of languages."""
        entities, _ = self.kb.get_entities(sorted(set(ids)))
        result = []
        for entity in entities:
            language, label = next(((lang, entity.labels[lang]) for lang in languages if lang in entity.labels),
                                   ("", ""))
            result.append(Linked(entity.id, self.item_id(entity), language, label))
        return result

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
        classes = dict(model.KINDS)
        if type_id not in model.KIND_TYPES:
            return CreateResponse(status=Status.NOT_FOUND)

        entity = model.Item()
        if type_id in classes:
            entity.claim(model.INSTANCE_OF, model.EntityValue(classes[type_id]))
        entity.claim(model.DARQOS_TYPE, model.StringValue(type_id))
        try:
            self.kb.put(entity)
        except KB_ERRORS as e:
            logging.error(f"Can't create a {type_id}: {e}")
            return CreateResponse(status=Status.FAILED)

        assert self.server is not None
        iid = model.item_id(entity.id, type_id)
        logging.info(f"Created {iid}")
        return CreateResponse(item_id=iid, item=self.attach(self.server, entity.id), status=Status.OK)

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
            self.kb.destroy(entity.id)
        except KB_ERRORS as e:
            logging.error(f"Can't destroy {request.item_id}: {e}")
            return DestroyResponse(status=Status.FAILED)
        logging.info(f"Destroyed {request.item_id}")
        return DestroyResponse(status=Status.OK)


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
        return PutEntityResponse(revision=response.entities[0].revision, status=wire.Status.OK)

    def get_links(self, request: GetLinksRequest, source: int) -> GetLinksResponse:
        try:
            response = self.kb.client.find_links(self.entity_id, request.property_id, request.role,
                                                 request.max, request.after)
            if response.status != wire.Status.OK:
                return GetLinksResponse(status=response.status)
            linked = self.implementation.linked(response.ids, request.languages)
        except KB_ERRORS as e:
            logging.error(f"Can't find links to {self.entity_id}: {e}")
            return GetLinksResponse(status=wire.Status.NOT_FOUND)
        return GetLinksResponse(linked=linked, status=wire.Status.OK)

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

    implementation = EntityImplementation(model.KB.connect(timeout=10))
    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    implementation.start()
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {', '.join(model.KIND_TYPES)} on port {port}")
    server.run()
