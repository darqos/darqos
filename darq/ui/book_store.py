# darqos
# Copyright (C) 2026 David Arnold

# How a lens reaches a book's entities: through their items'
# ports, so it needs no access to the KB's writer (see darq.book.Store).
# A lens can only write an entity through its own port, and a book is
# several, so it holds one for each, closed with the lens.
#
# Each level's an item of its own, so what's made and changed here is
# recorded in History against that item, and not only the one that's
# open, along with any people and publishers that are made: by their
# type, with the store's source (see doc/history.rst).  What's filled in
# as it's made isn't an edit.

import itertools
import logging
import typing

import darq
import darq.book
import darq.kb as model
import darq.launch
import darq.wikidata
from darq.book import BookError
from darq.protocols import type as type_protocol
from darq.protocols.kb import Context, EntityItem, Linked, Role
from darq.protocols.kb import Status as KBStatus


# Seconds to wait for items' replies.
TIMEOUT = 10.0

# Most entities to ask for at a time.
BATCH = 500

# Tries at a write that keeps conflicting.
SAVE_ATTEMPTS = 3

# Most names to offer, as what's typed is completed.
MAX_NAMES = 12

# The classes of kinds, to find entities of, and of languages: Wikidata's
# language (see darq.wikidata).
CLASSES = dict(model.KINDS) | {darq.book.LANGUAGE_KIND: darq.wikidata.LANGUAGE_CLASS}

# Failures of a port.
ERRORS = (darq.DarqError, TimeoutError, ValueError)

# Opens an item, returning a port for it.
Open = typing.Callable[[str], int]

# Creates an item of a type, as a context describes it, returning its
# identifier and a port for it.
Create = typing.Callable[[str, type_protocol.Context | None], tuple[str, int]]


class ItemStore:
    def __init__(self, chain: list[str], open_item: Open = darq.launch.open_item,
                 create_item: Create = darq.launch.create_item, source: str = ""):
        """Constructor.

        :param chain: Languages, best first.
        :param source: What's using it, such as "book-lens", as History's
                       source of what's made and changed with it."""
        self.chain = chain
        self.open_item = open_item
        self.create_item = create_item
        self.source = source

        # The items of open entities, and those made since settled(), which
        # are still being filled in: that's not an edit.
        self.items: dict[str, str] = {}
        self.fresh: set[str] = set()

        # Open items: ports by item, and clients by port and by entity.
        self.ports: dict[str, int] = {}
        self.clients: dict[int, EntityItem.Client] = {}
        self.by_entity: dict[str, EntityItem.Client] = {}

        # What's been referred to.
        self.linked: dict[str, Linked] = {}

        # Client of the KB service, once one's needed.
        self.kb: model.KB | None = None

    def adopt(self, item_id: str, port: int) -> EntityItem.Client:
        """Take over a port for an item."""
        client = EntityItem.Client(port, timeout=TIMEOUT)
        self.ports[item_id] = port
        self.clients[port] = client
        self.by_entity[darq.split_item_id(item_id)[1]] = client
        self.items[darq.split_item_id(item_id)[1]] = item_id
        return client

    def client(self, item_id: str) -> EntityItem.Client:
        port = self.ports.get(item_id)
        return self.clients[port] if port is not None else self.adopt(item_id, self.open_item(item_id))

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

                # They've no ports of their own yet: see writer().
                self.items.update((e.id, model.item_id(e.id, type_id)) for e in entities)
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

    def labelled(self, label: str, type_id: str) -> list[str]:
        """Return the entities of a kind with a label, ignoring case: a
        lookup's exact, or by words, and only words ignore case."""
        try:
            kb = self.reader()
            ids = sorted(kb.find_by_label(label, darq.book.OWN_LANGUAGE, words=True,
                                          class_id=CLASSES.get(type_id, "")))
            found = []
            for start in range(0, len(ids), BATCH):
                entities, _ = kb.get_entities(ids[start:start + BATCH], follow=False)
                found += [e.id for e in entities if label.casefold() in
                          {n.casefold() for n in (e.get_label() or "", *self.codes(e, type_id))}]
        except ERRORS as e:
            raise BookError(f"Can't look up {label}: {e}") from None
        return sorted(found)

    @staticmethod
    def codes(entity: model.Entity, type_id: str) -> list[str]:
        """Return what else a language's known by: its aliases, such as en,
        and English language.  Other kinds go by their labels only."""
        return entity.get_aliases() if type_id == darq.book.LANGUAGE_KIND else []

    def names_like(self, text: str, type_id: str, most: int = MAX_NAMES) -> list[str]:
        """Return the names of entities of a kind starting with some
        words, for completing what's being typed: an empty list if they
        can't be had, as it's only a convenience."""
        try:
            kb = self.reader()
            ids = list(itertools.islice(kb.find_by_label(text, darq.book.OWN_LANGUAGE, words=True,
                                                         class_id=CLASSES.get(type_id, "")), most * 4))
            entities, _ = kb.get_entities(ids, follow=False) if ids else ([], None)
        except ERRORS as e:
            logging.info(f"Can't look up names like {text}: {e!r}")
            return []
        if type_id == model.THING_TYPE:
            entities = [e for e in entities if model.recorded_type(e) == model.THING_TYPE]
        if not entities and type_id == darq.book.LANGUAGE_KIND:
            return self.names_like(text, model.THING_TYPE, most)
        names = (e.get_label() for e in entities if isinstance(e, model.Item) and not e.destroyed)
        return sorted(dict.fromkeys(n for n in names if n), key=str.casefold)[:most]

    def works_like(self, text: str) -> list[tuple[str, str]]:
        """Return works whose titles start with some words: their items,
        and titles."""
        try:
            kb = self.reader()
            ids = list(itertools.islice(kb.find_by_label(text, darq.book.OWN_LANGUAGE, words=True,
                                                         class_id=CLASSES[darq.book.WORK_TYPE]), MAX_NAMES))
            entities, _ = kb.get_entities(ids, follow=False) if ids else ([], None)
        except ERRORS as e:
            raise BookError(f"Can't look up {text}: {e}") from None
        return sorted(((model.item_id(e.id, darq.book.WORK_TYPE), e.get_label() or e.id) for e in entities
                       if not e.destroyed), key=lambda w: w[1].casefold())

    def find(self, label: str, type_id: str) -> str:
        found = self.labelled(label, type_id)

        # Before Wikidata's languages are brought, a language's one made here.
        if not found and type_id == darq.book.LANGUAGE_KIND:
            found = self.labelled(label, model.THING_TYPE)
            type_id = model.THING_TYPE
        if not found:
            return ""
        self.linked[found[0]] = Linked(entity_id=found[0], item_id=model.item_id(found[0], type_id), label=label)
        return found[0]

    def reader(self) -> model.KB:
        """Return a client of the KB service, for lookups."""
        if self.kb is None:
            self.kb = model.KB.connect(TIMEOUT)
        return self.kb

    def edition_with(self, property_id: str, isbn: str) -> str:
        try:
            found = sorted(self.reader().find_by_value(property_id, isbn))
        except ERRORS as e:
            raise BookError(f"Can't look up {isbn}: {e}") from None
        return model.item_id(found[0], darq.book.MANIFESTATION_TYPE) if found else ""

    def works_titled(self, title: str) -> list[str]:
        return [model.item_id(found, darq.book.WORK_TYPE) for found in self.labelled(title, darq.book.WORK_TYPE)]

    def create(self, type_id: str, label: str) -> model.Entity:
        try:
            item_id, port = self.create_item(type_id, type_protocol.Context(source=self.source))
        except ERRORS as e:
            raise BookError(f"Can't make a {type_id}: {e}") from None
        self.adopt(item_id, port)
        self.fresh.add(darq.split_item_id(item_id)[1])
        entity = self.get(item_id)
        if entity is None:
            raise BookError(f"Can't read the new {item_id}")
        entity = self.change(entity, [model.SetText("labels", darq.book.OWN_LANGUAGE, None, label)])
        self.linked[entity.id] = Linked(entity_id=entity.id, item_id=item_id, label=label)
        return entity

    def writer(self, entity: model.Entity) -> EntityItem.Client:
        """Return a client to write an entity with: one that was read with
        its parent, and not by itself, is opened now.

        :raises BookError: if it can't be."""
        client = self.by_entity.get(entity.id)
        if client is not None:
            return client
        item_id = self.items.get(entity.id)
        if item_id is None:
            raise BookError(f"{entity.id} isn't known here")
        try:
            return self.client(item_id)
        except ERRORS as e:
            raise BookError(f"Can't open {item_id}: {e}") from None

    def change(self, entity: model.Entity, edits: list[model.Edit]) -> model.Entity:
        if not edits:
            return entity
        client = self.writer(entity)
        for _ in range(SAVE_ATTEMPTS):
            try:
                changed = model.apply_edits(entity, edits)
                # Filling in what's just been made isn't an edit.
                context = Context(quiet=True) if entity.id in self.fresh else Context(source=self.source)
                response = client.put_entity(entity=model.entity_to_wire(changed), context=context)
                if response.status == KBStatus.CONFLICT:
                    read = client.get_entity(languages=self.chain)
                    entity = model.entity_from_wire(read.entity)
                    continue
                if response.status == KBStatus.INVALID_STATEMENT:
                    raise BookError("A value doesn't fit its property")
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

    def settled(self):
        """What was being made is finished: changes are edits from now on."""
        self.fresh.clear()

    def destroy(self, item_id: str):
        """Close an item, and destroy it."""
        port = self.ports.pop(item_id, None)
        if port is not None:
            client = self.clients.pop(port)
            self.by_entity.pop(darq.split_item_id(item_id)[1], None)
            try:
                client.close()
            except ERRORS as e:
                logging.warning(f"Can't close {item_id}: {e!r}")
            darq.close_port(port)
        try:
            darq.launch.destroy_item(item_id, type_protocol.Context(source=self.source))
        except ERRORS as e:
            raise BookError(f"Can't destroy {item_id}: {e}") from None

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
