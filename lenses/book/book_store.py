# darqos
# Copyright (C) 2026 David Arnold

# How the Book lens reaches a book's entities: through their items'
# ports, so it needs no access to the KB's writer (see darq.book.Store).
# A lens can only write an entity through its own port, and a book is
# several, so it holds one for each, closed with the lens.

import logging
import typing

import darq
import darq.book
import darq.kb as model
import darq.launch
from darq.book import BookError
from darq.protocols.kb import EntityItem, Kind, Linked, Role
from darq.protocols.kb import Status as KBStatus


# Seconds to wait for items' replies.
TIMEOUT = 10.0

# Most entities to ask for at a time.
BATCH = 500

# Tries at a write that keeps conflicting.
SAVE_ATTEMPTS = 3

# Most matches to look through for a name.
MAX_MATCHES = 200

# The classes of kinds, to find entities of.
CLASSES = dict(model.KINDS)

# Failures of a port.
ERRORS = (darq.DarqError, TimeoutError, ValueError)

# Opens an item, returning a port for it.
Open = typing.Callable[[str], int]

# Creates an item of a type, returning its identifier and a port for it.
Create = typing.Callable[[str], tuple[str, int]]


class ItemStore:
    def __init__(self, chain: list[str], open_item: Open = darq.launch.open_item,
                 create_item: Create = darq.launch.create_item):
        """Constructor.

        :param chain: Languages, best first."""
        self.chain = chain
        self.open_item = open_item
        self.create_item = create_item

        # Open items: ports by item, and clients by port and by entity.
        self.ports: dict[str, int] = {}
        self.clients: dict[int, EntityItem.Client] = {}
        self.by_entity: dict[str, EntityItem.Client] = {}

        # What's been referred to.
        self.linked: dict[str, Linked] = {}

    def adopt(self, item_id: str, port: int) -> EntityItem.Client:
        """Take over a port for an item."""
        client = EntityItem.Client(port, timeout=TIMEOUT)
        self.ports[item_id] = port
        self.clients[port] = client
        self.by_entity[darq.split_item_id(item_id)[1]] = client
        return client

    def client(self, item_id: str) -> EntityItem.Client:
        port = self.ports.get(item_id)
        return self.clients[port] if port is not None else self.adopt(item_id, self.open_item(item_id))

    def any_client(self) -> EntityItem.Client:
        """Return a client for lookups, which any item's port does."""
        return next(iter(self.clients.values()))

    def remember(self, linked: typing.Iterable[Linked]):
        self.linked.update((link.entity_id, link) for link in linked)

    # As a darq.book.Store

    def get(self, item_id: str) -> model.Entity | None:
        try:
            response = self.client(item_id).get_entity(languages=self.chain)
            if response.status != KBStatus.OK:
                return None
            self.remember(response.linked)
            return model.entity_from_wire(response.entity)
        except ERRORS as e:
            logging.error(f"Can't read {item_id}: {e!r}")
            return None

    def children(self, item_id: str, property_id: str, type_id: str) -> list[model.Entity]:
        found: list[model.Entity] = []
        after = ""
        try:
            client = self.client(item_id)
            while True:
                response = client.get_linked_entities(property_id=property_id, role=Role.MAIN, type_id=type_id,
                                                      languages=self.chain, max=BATCH, after=after, reverse=False)
                if response.status != KBStatus.OK:
                    raise BookError(f"Can't read what links to {item_id}: {response.status.name}")
                self.remember(response.linked)
                entities = [model.entity_from_wire(e) for e in response.entities]
                found += entities
                if len(entities) < BATCH:
                    return found
                after = entities[-1].id
        except ERRORS as e:
            raise BookError(f"Can't read what links to {item_id}: {e}") from None

    def label(self, entity_id: str) -> str:
        link = self.linked.get(entity_id)
        return link.label if link is not None else ""

    def item_of(self, entity_id: str) -> str:
        link = self.linked.get(entity_id)
        return link.item_id if link is not None else ""

    def find(self, label: str, type_id: str) -> str:
        try:
            response = self.any_client().find_entities(text=label, languages=self.chain, kind=Kind.ITEM,
                                                       class_id=CLASSES.get(type_id, ""), max=MAX_MATCHES, after="")
        except ERRORS as e:
            raise BookError(f"Can't look up {label}: {e}") from None
        if response.status != KBStatus.OK:
            raise BookError(f"Can't look up {label}: {response.status.name}")
        for found in response.found:
            if found.label.casefold() == label.casefold():
                self.linked[found.entity_id] = found
                return found.entity_id
        return ""

    def create(self, type_id: str, label: str) -> model.Entity:
        try:
            item_id, port = self.create_item(type_id)
        except ERRORS as e:
            raise BookError(f"Can't make a {type_id}: {e}") from None
        self.adopt(item_id, port)
        entity = self.get(item_id)
        if entity is None:
            raise BookError(f"Can't read the new {item_id}")
        entity = self.change(entity, [model.SetText("labels", darq.book.OWN_LANGUAGE, None, label)])
        self.linked[entity.id] = Linked(entity_id=entity.id, item_id=item_id, label=label)
        return entity

    def change(self, entity: model.Entity, edits: list[model.Edit]) -> model.Entity:
        if not edits:
            return entity
        client = self.by_entity.get(entity.id)
        if client is None:
            raise BookError(f"{entity.id} isn't open")
        for _ in range(SAVE_ATTEMPTS):
            try:
                changed = model.apply_edits(entity, edits)
                response = client.put_entity(entity=model.entity_to_wire(changed))
                if response.status == KBStatus.CONFLICT:
                    read = client.get_entity(languages=self.chain)
                    entity = model.entity_from_wire(read.entity)
                    continue
                if response.status != KBStatus.OK:
                    raise BookError(f"Can't save {entity.id}: {response.status.name}")
                read = client.get_entity(languages=self.chain)
                self.remember(read.linked)
                return model.entity_from_wire(read.entity)
            except model.EditConflict as e:
                raise BookError(f"Can't save {entity.id}: {e}") from None
            except ERRORS as e:
                raise BookError(f"Can't save {entity.id}: {e}") from None
        raise BookError(f"{entity.id} keeps being changed")

    def close(self):
        """Close the items, and their ports."""
        for port, client in self.clients.items():
            try:
                client.close()
            except ERRORS as e:
                logging.warning(f"Can't close an item: {e!r}")
            darq.close_port(port)
        self.ports.clear()
        self.clients.clear()
        self.by_entity.clear()
