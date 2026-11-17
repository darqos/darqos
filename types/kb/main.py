# darqos
# Copyright (C) 2026 David Arnold

# Knowledge Base type implementation: items of darq.thing, and of the
# other kinds, darq.person, darq.organisation, darq.organisational-unit,
# darq.place, darq.group, darq.conversation, darq.message and public.url,
# and the book levels, each an entity in the Knowledge Base, whose
# content is all knowledge.
#
# It's the plainest type implementation: ItemType, which every type
# implementation inherits (see darq.item_type, and doc/item.rst), with
# these kinds' vocabulary,
# creating them, writing them for imports, and publishing them to the
# Index, as the layer above it.
#
# The Type service spawns it, with a bootstrap port it reports Ready on
# (see doc/types.rst), once for all of these types.  It serves the
# Implementation protocol (lib/didl/type.didl) on its own port, and the
# EntityItem protocol (lib/didl/kb.didl) on a port for each client of an
# item, from Open or OpenEntity, which are the same for these kinds.
# Entities of its kinds are written through it, not to the KB
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
# Things, and properties, aren't indexed.  One that's destroyed, merged into
# another, or no longer of its kind is withdrawn.

import logging

import darq
import darq.book
import darq.history
import darq.index
import darq.items
import darq.kb as model
import darq.message
import darq.url
import darq.wikidata
from darq.item_type import KB_ERRORS, ClassListener, EntityItemHandler, ItemType  # noqa: F401
from darq.protocols import kb as wire
from darq.protocols.kb import (
    CreateEntitiesRequest,
    CreateEntitiesResponse,
    DestroyEntityRequest,
    DestroyEntityResponse,
    EntityWriter,
    MergeEntityRequest,
    MergeEntityResponse,
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
    OpenRequest,
    OpenResponse,
    Ready,
    Status,
)


# A book's numbers, which are in its Index body, so that Search finds a
# book by them: an edition's ISBNs, LCCN, ASIN and Open Library ID, a
# work's LibraryThing number and classification numbers, and a copy's
# LibraryThing ID.
BOOK_NUMBERS = (darq.book.ISBN_13, darq.book.ISBN_10, darq.book.LCCN, darq.book.ASIN, darq.book.OPEN_LIBRARY,
                darq.book.LIBRARYTHING_WORK, darq.book.DDC, darq.book.LCC, darq.book.LIBRARYTHING_BOOK)

class EntityImplementation(ItemType, EntityWriter.Handler):
    """Creates, opens, writes and destroys KB items, and records them in
    History, but for things, darq.thing, such as
    properties, classes and given names, which aren't the operator's
    items: those changed through their ports, on their pages, are
    recorded, as any entity's are (see darq.item_type)."""

    SOURCE = "kb-type"

    METHODS = {**ItemType.METHODS, **EntityWriter.Handler.METHODS}

    def __init__(self, kb: model.KB, index: darq.index.Publisher):
        super().__init__(kb, model.KIND_TYPES)
        self.index = index

    def record_entity(self, entity: model.Entity, event: str, context: darq.history.Context | None):
        """Record an event about an entity, if it's of a kind."""
        kind = self.kind_of(entity)
        if kind != model.THING_TYPE:
            self.record(model.item_id(entity.id, kind), event, context)

    def indexed_kind(self, entity: model.Entity) -> str | None:
        """Return an entity's kind, if it's indexed, or None."""
        if isinstance(entity, model.Property) or entity.destroyed or entity.redirect is not None:
            return None
        kind = self.kind_of(entity)
        return kind if kind != model.THING_TYPE else None

    def publish(self, entity: model.Entity, was: str | None = None):
        """Publish an entity to the Index, if it's of an indexed kind, and
        withdraw it as the kind it was, if that's changed."""
        kind = self.indexed_kind(entity)
        if was is not None and was != kind:
            self.index.withdraw(model.item_id(entity.id, was))
        if kind is not None:
            names, body = index_entry(entity)
            self.index.publish(model.item_id(entity.id, kind), names, body)

    def entity_changed(self, before: model.Entity, after: model.Entity):
        """Publish an entity changed through its port, withdrawing it as the
        kind it was, if that's changed."""
        self.publish(after, self.indexed_kind(before))

    # Requests

    def create(self, request: CreateRequest, source: int) -> CreateResponse:
        try:
            type_id = darq.canonical_type_id(request.type_id)
        except darq.errors.InvalidIdentifierError:
            return CreateResponse(status=Status.INVALID_ID)
        if type_id not in model.KIND_TYPES:
            return CreateResponse(status=Status.NOT_FOUND)

        entity = model.Item()
        model.stamp(entity, type_id)
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
        if type_id != model.THING_TYPE:
            self.record(iid, darq.history.CREATED, request.context)
        logging.info(f"Created {iid}")
        return CreateResponse(item_id=iid, item=self.attach_entity(self.server, entity.id), status=Status.OK)

    def create_from(self, request: CreateFromRequest, source: int) -> CreateFromResponse:
        # An entity has no form as data: a vCard file, say, makes many,
        # which is a tool's job.
        return CreateFromResponse(status=Status.UNSUPPORTED)

    def export(self, request: ExportRequest, source: int) -> ExportResponse:
        return ExportResponse(status=Status.UNSUPPORTED)

    def index_entry(self, request: IndexEntryRequest, source: int) -> IndexEntryResponse:
        try:
            entity, status = self.find_entity(request.item_id)
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
            entity, status = self.find_entity(request.item_id)
        except KB_ERRORS as e:
            logging.error(f"Can't open {request.item_id}: {e}")
            return OpenResponse(status=Status.FAILED)
        if entity is None:
            return OpenResponse(status=status)
        assert self.server is not None
        return OpenResponse(item=self.attach_entity(self.server, entity.id), status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        try:
            entity, status = self.find_entity(request.item_id)
            if entity is None:
                return DestroyResponse(status=status)
            kind = self.indexed_kind(entity)
            self.kb.destroy(entity.id)
        except KB_ERRORS as e:
            logging.error(f"Can't destroy {request.item_id}: {e}")
            return DestroyResponse(status=Status.FAILED)
        if kind is not None:
            self.index.withdraw(model.item_id(entity.id, kind))
        self.record_entity(entity, darq.history.DESTROYED, request.context)
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
                    self.record_entity(entity, darq.history.EDITED if entity.id in was else darq.history.CREATED,
                                       request.context)
            except KB_ERRORS as e:
                logging.error(f"Can't index the entities written: {e}")
        return WriteEntitiesResponse(entities=response.entities, failed_id=response.failed_id,
                                     status=response.status)

    def create_entities(self, request: CreateEntitiesRequest, source: int) -> CreateEntitiesResponse:
        try:
            type_id = darq.canonical_type_id(request.type_id)
        except darq.errors.InvalidIdentifierError:
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
            model.stamp(entity, type_id)

        try:
            response = self.kb.client.put_entities([model.entity_to_wire(e) for e in entities])
        except KB_ERRORS as e:
            logging.error(f"Can't create {len(entities)} entities: {e}")
            return CreateEntitiesResponse(status=wire.Status.NOT_FOUND)
        if response.status == wire.Status.OK:
            try:
                for written in response.entities:
                    entity = model.entity_from_wire(written)
                    self.publish(entity)
                    self.record_entity(entity, darq.history.CREATED, request.context)
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
                    # On both: the old one's kind is its own still.
                    if old is not None:
                        self.record_entity(old, darq.history.MERGED, request.context)
                    self.record_entity(new, darq.history.MERGED, request.context)
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
        if response.status == wire.Status.OK:
            if was is not None:
                self.index.withdraw(model.item_id(request.entity_id, was))
            if entity is not None:
                self.record_entity(entity, darq.history.DESTROYED, request.context)
        return DestroyEntityResponse(status=response.status)


def index_entry(entity: model.Entity) -> tuple[list[str], str]:
    """Return an entity's names and body, for the Index: its labels and
    aliases, and its descriptions, in every language, each once, with a
    message's text in its body."""
    names = list(dict.fromkeys([*entity.labels.values(),
                                *(a for aliases in entity.aliases.values() for a in aliases)]))
    texts = [text for s in entity.get_statements(darq.message.BODY)
             if isinstance(value := s.get_value(), model.TextValue)
             for text in value.text.values()]
    numbers = [value.value for property_id in BOOK_NUMBERS for s in entity.get_statements(property_id)
               if isinstance(value := s.get_value(), model.StringValue)]
    return names, "\n".join(dict.fromkeys([*entity.descriptions.values(), *texts, *numbers]))


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the Type service starts this, with Activate")
        return 1

    implementation = EntityImplementation(model.KB.connect(timeout=10), darq.index.Publisher())
    port = darq.open_port()
    server = darq.Server()
    implementation.add(server, port)
    darq.send(bootstrap, Ready(implementation=port))
    logging.info(f"Serving {', '.join(model.KIND_TYPES)} on port {port}")
    server.run()
