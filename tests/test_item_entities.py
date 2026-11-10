# darqos
# Copyright (C) 2026 David Arnold

# Tests for Give Items Entities, tools/item-entities (see
# doc/entity-vs-item.rst, step 3): finding the items made before every
# item was an entity, from History and what the KB refers to, with a KB
# service in this process (see test_kb_type.py), and giving them their
# entities, named from what referred to them.

import importlib.util
import pathlib
import sys
import typing

import pytest

import darq.book
import darq.items as items
import darq.kb as model
import darq.message
import darq.url
from darq.protocols import kb as wire
from darq.protocols.history import Event, GetEventsResponse
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.kb import GetEntityResponse, PutEntityResponse
from darq.protocols.type import ActivateResponse, OpenEntityResponse, Status

from test_kb_type import implementation, index, kb, kb_service, ports  # noqa: F401


TOOL = pathlib.Path(__file__).parent.parent / "tools" / "item-entities"


def load():
    sys.path.insert(0, str(TOOL))
    try:
        spec = importlib.util.spec_from_file_location("item_entities", TOOL / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOL))
    return module


tool = load()

JPEG = "public.jpeg:03d1824dc667bb6b343e27a5a610b06b"
PDF = "com.adobe.pdf:07b4683b886355916637b2359c058fb8"
PNG = "public.png:0348e75710eaf008796f266d7a03618c"
TEXT = "public.utf8-plain-text:7d194113e73a1e5d30a8ec8c7affdd1c"


@pytest.fixture
def store(kb, implementation):  # noqa: F811
    """The KB, with the vocabulary the KB type implementation ensures."""
    return kb


def entity_id(item_id: str) -> str:
    return item_id.partition(":")[2]


################################################################
# Finding them
################################################################

def test_what_the_kb_refers_to(store):
    message = model.Item("message")
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue(JPEG)).add_qualifier(
        darq.message.FILE_NAME, model.StringValue("IMG_0001.JPG"))
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue("")).add_qualifier(
        darq.message.FILE_NAME, model.StringValue("too big.mov"))
    url = model.Item(darq.url.entity_id("https://example.com/papers/On%20Computable.pdf"))
    url.claim(darq.url.URL, model.StringValue("https://example.com/papers/On%20Computable.pdf"))
    url.claim(darq.url.OLD_SNAPSHOT, model.StringValue(PDF))
    copy = model.Item("copy")
    copy.claim(darq.book.OLD_CONTENT, model.StringValue(TEXT))
    edition = model.Item("edition")
    edition.claim(darq.book.OLD_COVER, model.StringValue(PNG))
    store.put(message, url, copy, edition)
    assert tool.referred(store) == {JPEG: "IMG_0001.JPG", PDF: "On Computable.pdf", TEXT: "", PNG: ""}


def test_a_snapshots_name_is_where_it_ended_up(store):
    url = model.Item("url")
    url.claim(darq.url.URL, model.StringValue("https://example.com/latest"))
    url.claim(darq.url.OLD_SNAPSHOT, model.StringValue(PDF)).add_qualifier(
        darq.url.FINAL_URL, model.StringValue("https://example.com/v2/paper.pdf"))
    store.put(url)
    assert tool.referred(store) == {PDF: "paper.pdf"}


class FakeHistory:
    def __init__(self, events: list[Event]):
        self.events = events
        self.asked: list[dict] = []

    def get_events(self, **query):
        self.asked.append(query)
        found = [e for e in self.events if not query["end"] or e.time < query["end"]][:query["max"]]
        return GetEventsResponse(events=found, status=HistoryStatus.OK)


def test_what_history_has(monkeypatch):
    monkeypatch.setattr(tool, "PAGE", 2)
    history = FakeHistory([Event(3, JPEG, "opened", "image-lens", 1), Event(2, PDF, "imported", "terminal", 1),
                           Event(1, JPEG, "imported", "terminal", 1)])
    assert tool.history_items(typing.cast(typing.Any, history)) == [JPEG, PDF]
    assert set(history.asked[0]["type_ids"]) == set(items.KINDS)


################################################################
# Giving them entities
################################################################

class FakeImplementation:
    """A content type's implementation: OpenEntity gives an item that's
    there its entity, as adopt does."""

    def __init__(self, store: model.KB, gone: set[str]):
        self.kb = store
        self.gone = gone

    def open_entity(self, item_id: str):
        if item_id in self.gone:
            return OpenEntityResponse(status=Status.NOT_FOUND)
        kind, specifier = item_id.split(":")
        items.create(self.kb, kind, specifier)
        return OpenEntityResponse(item=hash(specifier) & 0xffff, status=Status.OK)


class FakeEntityItem:
    """An entity's port, on the KB."""

    def __init__(self, store: model.KB, entity_id: str):
        self.kb = store
        self.entity_id = entity_id
        self.closed = False

    def get_entity(self, languages):
        entity = self.kb.get(self.entity_id)
        assert entity is not None
        return GetEntityResponse(entity=model.entity_to_wire(entity), status=wire.Status.OK)

    def put_entity(self, entity):
        written = self.kb.put(model.entity_from_wire(entity))[0]
        return PutEntityResponse(revision=written.revision, status=wire.Status.OK)

    def close(self):
        self.closed = True


class FakeTypes:
    def activate(self, type_id):
        return ActivateResponse(implementation=1, status=Status.OK)


def give(store, wanted: dict[str, str], gone: typing.Collection[str] = ()):
    implementer = FakeImplementation(store, set(gone))
    port_ids = {hash(entity_id(i)) & 0xffff: entity_id(i) for i in wanted}
    return tool.give(wanted, store, typing.cast(typing.Any, FakeTypes()),
                     connect=lambda port: typing.cast(typing.Any, implementer),
                     entity=lambda port: typing.cast(typing.Any, FakeEntityItem(store, port_ids[port])))


def file_name(entity: model.Entity) -> str:
    return next((v.value for v in (s.get_value() for s in entity.get_statements(items.FILE_NAME))
                 if isinstance(v, model.StringValue)), "")


def test_items_are_given_entities_and_named_from_what_referred_to_them(store):
    counts = give(store, {JPEG: "IMG_0001.JPG", TEXT: ""})
    assert (counts.made, counts.named, counts.unnamed) == (2, 1, 1)
    jpeg = store.get(entity_id(JPEG))
    assert (jpeg.get_label(), file_name(jpeg)) == ("IMG_0001", "IMG_0001.JPG")
    assert model.recorded_type(jpeg) == "public.jpeg"
    assert store.get(entity_id(TEXT)).labels == {}


def test_one_thats_named_keeps_its_name(store):
    items.create(store, "com.adobe.pdf", entity_id(PDF), label="A Title")
    counts = give(store, {PDF: "scan0001.pdf"})
    assert (counts.had, counts.made, counts.named) == (1, 0, 0)
    assert store.get(entity_id(PDF)).get_label() == "A Title"


def test_one_whose_blobs_gone_is_counted(store):
    counts = give(store, {PNG: ""}, gone={PNG})
    assert counts.gone == 1 and store.get(entity_id(PNG)) is None


def test_it_can_be_run_again(store):
    give(store, {JPEG: "IMG_0001.JPG"})
    before = store.get(entity_id(JPEG)).revision
    counts = give(store, {JPEG: "IMG_0001.JPG"})
    assert (counts.had, counts.made, counts.named) == (1, 0, 0)
    assert store.get(entity_id(JPEG)).revision == before


def test_only_content_items_are_given_them(store):
    assert give(store, {"darq.person:ada": ""}) == tool.Counts()


################################################################
# Linking to them
################################################################

def test_what_referred_to_an_item_links_to_it(store):
    items.create(store, "public.jpeg", entity_id(JPEG))
    message = model.Item("message")
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue(JPEG), rank=model.Rank.Preferred).add_qualifier(
        darq.message.FILE_NAME, model.StringValue("IMG_0001.JPG")).add_reference(
        model.Reference().add(model.STATED_IN, model.EntityValue(model.DARQOS)))
    store.put(message)
    assert tool.relink(store, store) == tool.Relinked(1, 0)

    message = store.get("message")
    assert message.get_statements(darq.message.OLD_ATTACHMENT) == []
    [attachment] = message.get_statements(darq.message.ATTACHMENT)
    assert attachment.get_value().entity_id == entity_id(JPEG) and attachment.rank == model.Rank.Preferred
    assert attachment.qualifiers[0].value.value == "IMG_0001.JPG"
    assert model.is_sourced(attachment, model.DARQOS)
    assert list(store.find_links(entity_id(JPEG), darq.message.ATTACHMENT)) == ["message"]


def test_what_cant_be_linked_is_left(store):
    """An item that's gone, and a file that isn't an item."""
    message = model.Item("message")
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue(JPEG))
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue("")).add_qualifier(
        darq.message.FILE_NAME, model.StringValue("too big.mov"))
    store.put(message)
    assert tool.relink(store, store) == tool.Relinked(0, 2)
    assert len(store.get("message").get_statements(darq.message.OLD_ATTACHMENT)) == 2


def test_each_kind_of_reference_is_linked(store):
    for item_id in (PDF, TEXT, PNG):
        items.create(store, item_id.partition(":")[0], entity_id(item_id))
    url = model.Item("url")
    url.claim(darq.url.OLD_SNAPSHOT, model.StringValue(PDF))
    copy = model.Item("copy")
    copy.claim(darq.book.OLD_CONTENT, model.StringValue(TEXT))
    edition = model.Item("edition")
    edition.claim(darq.book.OLD_COVER, model.StringValue(PNG))
    store.put(url, copy, edition)
    assert tool.relink(store, store).linked == 3
    assert [s.get_value().entity_id for s in store.get("url").get_statements(darq.url.SNAPSHOT)] == [entity_id(PDF)]
    assert [s.get_value().entity_id for s in store.get("copy").get_statements(darq.book.CONTENT)] == [entity_id(TEXT)]
    assert [s.get_value().entity_id for s in store.get("edition").get_statements(darq.book.COVER)] == [entity_id(PNG)]
    assert tool.relink(store, store) == tool.Relinked(0, 0)
