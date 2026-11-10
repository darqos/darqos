# darqos
# Copyright (C) 2026 David Arnold

# A stand-in for darq.kb.KB, in memory, for the tests of the types whose
# items' content is in Storage, which make their entities (see
# darq.items): what they ask of the KB, and no more.  The real thing's
# tested in test_items.py and test_entity_type.py.

import copy

import darq.kb as model


class FakeKB:
    """Entities, by identifier, with revisions."""

    def __init__(self):
        self.entities: dict[str, model.Entity] = {}
        self.watched: list[tuple[list[str], int]] = []

        # When set, every write fails, as a KB that isn't there would.
        self.failing: Exception | None = None

    def get(self, entity_id: str, follow: bool = True) -> model.Entity | None:
        """Return an entity, following a merge, as the KB does, unless
        it's not to be."""
        entity = self.entities.get(entity_id)
        while follow and entity is not None and entity.redirect is not None:
            entity = self.entities.get(entity.redirect)
        return copy.deepcopy(entity) if entity is not None else None

    def put(self, *entities: model.Entity) -> list[model.Entity]:
        if self.failing is not None:
            raise self.failing
        written = []
        for entity in entities:
            current = self.entities.get(entity.id)
            if current is not None and entity.revision != current.revision:
                raise model.ConflictError("changed", entity.id)
            stored = copy.deepcopy(entity)
            stored.revision = (current.revision if current is not None else 0) + 1
            self.entities[entity.id] = stored
            written.append(copy.deepcopy(stored))
        return written

    def destroy(self, entity_id: str):
        if self.failing is not None:
            raise self.failing
        self.entities[entity_id].destroyed = True
        self.entities[entity_id].revision += 1

    def get_entities(self, ids, follow: bool = True, languages=()) -> tuple[list[model.Entity], dict[str, str]]:
        found = [self.get(i, follow) for i in ids]
        return [e for e in found if e is not None], {}

    @property
    def client(self) -> "FakeClient":
        """The protocol's client, as an entity's port writes with."""
        return FakeClient(self)

    def watch(self, ids, listener: int) -> int:
        self.watched.append((list(ids), listener))
        return 1

    def label(self, entity_id: str) -> str | None:
        entity = self.entities.get(entity_id)
        return entity.get_label() if entity is not None else None


class FakeClient:
    """What an entity's port asks of the KB's client."""

    def __init__(self, kb: FakeKB):
        self.kb = kb

    def put_entities(self, entities):
        from darq.protocols import kb as wire
        try:
            written = self.kb.put(*(model.entity_from_wire(e) for e in entities))
        except model.ConflictError as e:
            return wire.PutEntitiesResponse(failed_id=e.entity_id, status=wire.Status.CONFLICT)
        return wire.PutEntitiesResponse(entities=[model.entity_to_wire(e) for e in written], status=wire.Status.OK)


def rename(call, implementation, implementation_port: int, item_id: str, label: str):
    """Rename an item, as the entity lens does: through a port for its
    entity, from OpenEntity on its type implementation."""
    from darq.protocols import kb as wire
    from darq.protocols.kb import GetEntityRequest, PutEntityRequest
    from darq.protocols.type import OpenEntityRequest, Status
    opened = call(implementation_port, OpenEntityRequest(item_id=item_id))
    assert opened.status == Status.OK, opened
    handler = implementation.entity_ports[opened.item]
    entity = model.entity_from_wire(handler.get_entity(GetEntityRequest(languages=["en"]), 0).entity)
    entity.set_label(label)
    assert handler.put_entity(PutEntityRequest(entity=model.entity_to_wire(entity)), 0).status == wire.Status.OK


def file_name(entity: model.Entity) -> str:
    import darq.items
    values = [s.get_value() for s in entity.get_statements(darq.items.FILE_NAME)]
    return next((v.value for v in values if isinstance(v, model.StringValue)), "")
