# darqos
# Copyright (C) 2026 David Arnold

# Tests for the entity type, which every type implementation inherits
# (darq.entity_type, and doc/entity-vs-item.rst): a stand-in for a type
# whose content's in Storage, inheriting EntityType, against a KB service
# in this process (see test_kb_type.py); types/kb, the plain
# implementation, opening its items' entities; and the entity lens
# opening them with OpenEntity.

import pathlib
import typing

import darq
import darq.items as items
import darq.launch
import darq.kb as model
from darq.entity_type import EntityType
from darq.protocols import kb as wire
from darq.protocols.kb import GetEntityRequest, GetLinkSummaryRequest, PutEntityRequest
from darq.protocols.type import OpenEntityRequest, OpenEntityResponse, OpenRequest, Status

import pytest

from test_kb_type import implementation, index, kb, kb_service, ports  # noqa: F401
import test_kb_type
from test_wikidata import entity_lens


KEY = "03d1824dc667bb6b343e27a5a610b06b"


class Images(EntityType):
    """A type implementation for images, inheriting the entity type, and
    remembering what it's told has changed."""

    def __init__(self, store: model.KB):
        super().__init__(store, ["public.jpeg", "public.png"])
        self.changes: list[tuple[str | None, str | None]] = []

    def entity_changed(self, before: model.Entity, after: model.Entity):
        self.changes.append((before.get_label(), after.get_label()))


@pytest.fixture
def images(kb, ports, implementation):  # noqa: F811
    """Images, beside the KB type implementation, which has made the
    vocabulary."""
    made = Images(kb)
    made.add(implementation.server, 60)
    return made


def open_entity(implementer: EntityType, item_id: str) -> typing.Any:
    """Return the handler of an item's entity's port, or why there's none."""
    response = implementer.open_entity(OpenEntityRequest(item_id=item_id), 0)
    if response.status != Status.OK:
        return response.status
    assert response.item is not None
    return implementer.entity_ports[response.item]


################################################################
# A type that inherits it
################################################################

def test_an_items_entity_is_opened(kb, images):  # noqa: F811
    items.create(kb, "public.jpeg", KEY, label="Uluru")
    handler = open_entity(images, f"public.jpeg:{KEY}")
    response = handler.get_entity(GetEntityRequest(languages=["en"]), 0)
    assert response.status == wire.Status.OK and model.entity_from_wire(response.entity).get_label() == "Uluru"


def test_its_links_are_found(kb, images):  # noqa: F811
    items.create(kb, "public.jpeg", KEY)
    handler = open_entity(images, f"public.jpeg:{KEY}")
    response = handler.get_link_summary(GetLinkSummaryRequest(languages=["en"]), 0)
    assert response.status == wire.Status.OK


@pytest.mark.parametrize("item_id", [f"public.png:{KEY}", f"public.jpeg:{KEY}", f"darq.person:{KEY}",
                                     "not an item"])
def test_one_that_isnt_its_isnt_opened(kb, images, item_id):  # noqa: F811
    """Of another kind than its identifier's, with no entity, of a type
    that's not its own, and not an identifier."""
    if item_id.startswith("public.png"):
        items.create(kb, "public.jpeg", KEY)
    assert open_entity(images, item_id) in (Status.NOT_FOUND, Status.INVALID_ID)


def test_a_destroyed_ones_not_opened(kb, images):  # noqa: F811
    items.create(kb, "public.jpeg", KEY)
    items.destroy(kb, KEY)
    assert open_entity(images, f"public.jpeg:{KEY}") == Status.NOT_FOUND


def test_a_change_is_told_to_the_layer_above(kb, images):  # noqa: F811
    items.create(kb, "public.jpeg", KEY, label="Uluru")
    handler = open_entity(images, f"public.jpeg:{KEY}")
    entity = kb.get(KEY)
    entity.set_label("Uluru at dusk")
    assert handler.put_entity(PutEntityRequest(entity=model.entity_to_wire(entity)), 0).status == wire.Status.OK
    assert images.changes == [("Uluru", "Uluru at dusk")]
    assert kb.get(KEY).get_label() == "Uluru at dusk"


def test_its_kind_cant_be_changed_through_it(kb, images):  # noqa: F811
    items.create(kb, "public.jpeg", KEY)
    handler = open_entity(images, f"public.jpeg:{KEY}")
    entity = kb.get(KEY)
    model.stamp(entity, "public.png")
    handler.put_entity(PutEntityRequest(entity=model.entity_to_wire(entity)), 0)
    assert model.recorded_type(kb.get(KEY)) == "public.jpeg"


def test_closing_it_forgets_the_port(kb, images, ports):  # noqa: F811
    items.create(kb, "public.jpeg", KEY)
    handler = open_entity(images, f"public.jpeg:{KEY}")
    handler.close(None, 0)
    assert handler.port not in images.entity_ports


################################################################
# types/kb, the plain implementation
################################################################

def test_a_kb_items_entity_is_opened_as_the_item_is(kb, implementation):  # noqa: F811
    item_id, _ = test_kb_type.create(implementation, "darq.person")
    opened = implementation.open(OpenRequest(item_id=item_id), 0)
    entity = implementation.open_entity(OpenEntityRequest(item_id=item_id), 0)
    assert opened.status == entity.status == Status.OK
    assert type(implementation.entity_ports[opened.item]) is type(implementation.entity_ports[entity.item])


def test_a_kb_item_changed_through_its_entity_is_published(kb, implementation, index):  # noqa: F811
    item_id, _ = test_kb_type.create(implementation, "darq.person")
    handler = implementation.entity_ports[implementation.open_entity(OpenEntityRequest(item_id=item_id), 0).item]
    entity = kb.get(darq.split_item_id(item_id)[1])
    entity.set_label("Ada Lovelace")
    handler.put_entity(PutEntityRequest(entity=model.entity_to_wire(entity)), 0)
    assert index.sent[-1][:2] == ("publish", item_id) and index.names[-1] == ["Ada Lovelace"]


def test_the_kb_implementation_doesnt_open_content_items(kb, implementation):  # noqa: F811
    items.create(kb, "public.jpeg", KEY)
    assert implementation.open_entity(OpenEntityRequest(item_id=f"public.jpeg:{KEY}"), 0).status == \
        Status.NOT_FOUND


################################################################
# Opening an entity
################################################################

ROOT = pathlib.Path(__file__).parent.parent


class FakeImplementation:
    def __init__(self, status: Status):
        self.status = status
        self.asked: list[str] = []

    def open_entity(self, item_id: str):
        self.asked.append(item_id)
        return OpenEntityResponse(item=7 if self.status == Status.OK else None, status=self.status)


def test_launch_opens_an_entity_through_its_type(monkeypatch):
    fake = FakeImplementation(Status.OK)
    activated: list[str] = []
    monkeypatch.setattr(darq.launch, "_activate", lambda type_id: activated.append(type_id) or fake)
    assert darq.launch.open_entity(f"public.jpeg:{KEY}") == 7
    assert activated == ["public.jpeg"] and fake.asked == [f"public.jpeg:{KEY}"]


def test_launch_says_why_it_cant(monkeypatch):
    monkeypatch.setattr(darq.launch, "_activate", lambda type_id: FakeImplementation(Status.UNSUPPORTED))
    with pytest.raises(darq.LaunchError, match="UNSUPPORTED"):
        darq.launch.open_entity(f"public.jpeg:{KEY}")


def test_the_entity_lens_opens_entities(monkeypatch):
    monkeypatch.setattr(darq.launch, "open_entity", lambda item_id: 42)
    monkeypatch.setattr(darq.launch, "open_item", lambda item_id: 0)
    opener = entity_lens.LaunchOpener(typing.cast(typing.Any, None))
    assert opener.open("darq.person:x") == 42

