# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Knowledge Base type implementation, types/kb/main.py:
# its handlers directly, with a KB client that calls a Knowledge Base
# service's handler (see test_kb_service.py) on a temporary database, in
# the same process; and then through a real p-kernel, activated by the
# Type service, from the type sync's registration of types/kb.

import importlib.util
import pathlib
import typing

import pytest

import darq
import darq.kb as model
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    CloseRequest,
    GetEntityRequest,
    GetLinksRequest,
    PutEntityRequest,
    Role,
    WatchEntityRequest,
)
from darq.protocols.type import CreateRequest, DestroyRequest, OpenRequest, Status

from test_kb_service import module as service_module
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


TYPE_PATH = pathlib.Path(__file__).parent.parent / "types" / "kb"


def load():
    spec = importlib.util.spec_from_file_location("kb_type", TYPE_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load()

IMPLEMENTATION_PORT = 50


class DirectClient(wire.KB.Client):
    """A KB client that calls a service's handler, in this process."""

    def __init__(self, handler):
        super().__init__(0)
        self.handler = handler

    def call(self, request, response_class):
        request.txid = 1
        data, handles = request.encode()
        reply = self.handler.dispatch(data, 0, tuple(handles))
        data, handles = reply.encode()
        response = response_class()
        response.decode(data, handles)
        return response


class Ports:
    """The p-calls made, faked: Changed messages sent to a listener the
    implementation serves are handed to its handler."""

    def __init__(self):
        self.next_port = 100
        self.closed: list[int] = []
        self.sent: list[tuple[int, str]] = []
        self.server: darq.Server | None = None

    def open_port(self):
        self.next_port += 1
        return self.next_port

    def close_port(self, port):
        self.closed.append(port)

    def send(self, port, message):
        self.sent.append((port, message.entity_id))
        handler: typing.Any = self.server._handlers.get(port) if self.server else None
        if isinstance(handler, module.ClassListener):
            handler.changed(message, 0)

    def notify_on_death(self, handle, port):
        pass


@pytest.fixture
def ports(monkeypatch):
    fake = Ports()
    for name in ("open_port", "close_port", "send", "notify_on_death"):
        monkeypatch.setattr(darq, name, getattr(fake, name))
    return fake


@pytest.fixture
def kb_service(tmp_path, ports):
    service = service_module.KBService(str(tmp_path / "kb.sqlite"))
    service.port = 1
    yield service
    service.close()


@pytest.fixture
def kb(kb_service):
    return model.KB(DirectClient(kb_service))


@pytest.fixture
def implementation(kb, ports):
    implementation = module.EntityImplementation(kb)
    server = darq.Server()
    ports.server = server
    implementation.add(server, IMPLEMENTATION_PORT)
    implementation.start()
    return implementation


def create(implementation, type_id):
    response = implementation.create(CreateRequest(type_id=type_id), 0)
    assert response.status == Status.OK
    return response.item_id, response.item


def open_item(implementation, item_id):
    response = implementation.open(OpenRequest(item_id=item_id), 0)
    return response.item if response.status == Status.OK else response.status


def item(implementation, port):
    return implementation.items[port]


def entity(entity_id, label, *classes):
    e = model.Item(entity_id)
    e.set_label(label)
    for c in classes:
        e.claim(model.INSTANCE_OF, model.EntityValue(c))
    return e


################################################################
# Kinds
################################################################

def test_start_states_the_defaults_once(kb, implementation, kb_service):
    human = kb.get("Q5")
    assert human is not None and human.get_label() == "human"
    [statement] = human.get_statements(model.DARQOS_INSTANCE_TYPE)
    assert statement.get_value().value == "darq.person"
    assert statement.references[0].snaks[0].value.get_id() == model.DARQOS

    writes = kb_service.store.writes
    implementation.start()
    assert kb_service.store.writes == writes


def test_kind_of(kb, implementation):
    business = entity("Q4830453", "business")
    business.claim(model.SUBCLASS_OF, model.EntityValue("Q43229"))

    # A region is under both geographic location and organization, on
    # Wikidata: place comes first.
    region = entity("Q56061", "administrative territorial entity")
    for c in ("Q2221906", "Q43229"):
        region.claim(model.SUBCLASS_OF, model.EntityValue(c))
    kb.put(business, region,
           entity("acme", "Acme", "Q4830453"),
           entity("qld", "Queensland", "Q56061"),
           entity("ada", "Ada", "Q5"),
           entity("dept", "Sales", "Q9261468"),
           entity("addr", "1 Example St", "Q319608"),
           entity("friends", "Friends", "Q16334295"),
           entity("Q202444", "given name"),
           entity("name", "Ada", "Q202444"))

    def kind(entity_id):
        return implementation.kind_of(kb.get(entity_id))

    assert [kind(i) for i in ("acme", "qld", "ada", "dept", "addr", "friends", "name")] == [
        "darq.organisation", "darq.place", "darq.person", "darq.organisational-unit", "darq.place",
        "darq.group", "darq.entity"]

    # A preferred instance type overrides the default, once the cache
    # hears of it.
    organization = kb.get("Q43229")
    organization.claim(model.DARQOS_INSTANCE_TYPE, model.StringValue("darq.group"), model.Rank.Preferred)
    kb.put(organization)
    assert kind("acme") == "darq.group"

    # A recorded kind wins.
    acme = kb.get("acme")
    acme.claim(model.DARQOS_TYPE, model.StringValue("darq.place"))
    kb.put(acme)
    assert kind("acme") == "darq.place"


################################################################
# Implementation
################################################################

@pytest.mark.parametrize("type_id, cls", [
    ("darq.person", "Q5"), ("Darq.Organisation", "Q43229"), ("darq.organisational-unit", "Q9261468"),
    ("darq.place", "Q2221906"), ("darq.group", "Q16334295"), ("darq.entity", None)])
def test_create_and_open(kb, implementation, type_id, cls):
    item_id, port = create(implementation, type_id)
    canonical, entity_id = darq.split_item_id(item_id)
    assert canonical == type_id.lower()

    created = kb.get(entity_id)
    assert [s.get_value().value for s in created.get_statements(model.DARQOS_TYPE)] == [canonical]
    assert [s.get_value().get_id() for s in created.get_statements(model.INSTANCE_OF)] == ([cls] if cls else [])
    assert item(implementation, port).entity_id == entity_id

    other = open_item(implementation, item_id)
    assert other != port and item(implementation, other).entity_id == entity_id


def test_create_refuses_other_types(implementation):
    assert implementation.create(CreateRequest(type_id="public.png"), 0).status == Status.NOT_FOUND
    assert implementation.create(CreateRequest(type_id="bad:id"), 0).status == Status.INVALID_ID


def test_open_is_strict(kb, implementation):
    item_id, _ = create(implementation, "darq.person")
    _, entity_id = darq.split_item_id(item_id)
    assert open_item(implementation, f"darq.entity:{entity_id}") == Status.NOT_FOUND
    assert open_item(implementation, f"darq.place:{entity_id}") == Status.NOT_FOUND
    assert open_item(implementation, "darq.person:missing") == Status.NOT_FOUND
    assert open_item(implementation, "public.png:x") == Status.NOT_FOUND
    assert open_item(implementation, "no colon") == Status.INVALID_ID

    # An imported entity, with no recorded kind, opens as its classes'.
    kb.put(entity("Q7259", "Ada", "Q5"))
    assert isinstance(open_item(implementation, "darq.person:Q7259"), int)


def test_merged_and_destroyed(kb, implementation):
    old_id, _ = create(implementation, "darq.person")
    new_id, _ = create(implementation, "darq.person")
    old, new = darq.split_item_id(old_id)[1], darq.split_item_id(new_id)[1]
    kb.merge(old, new)
    assert item(implementation, open_item(implementation, old_id)).entity_id == new

    assert implementation.destroy(DestroyRequest(item_id=new_id), 0).status == Status.OK
    assert open_item(implementation, new_id) == Status.NOT_FOUND
    assert implementation.destroy(DestroyRequest(item_id=new_id), 0).status == Status.NOT_FOUND


################################################################
# Item ports
################################################################

@pytest.fixture
def ada(kb, implementation):
    employer = model.Property(model.Datatype.Item, "P108")
    employer.set_label("employer")
    kb.put(entity("acme", "Acme", "Q43229"), employer)
    item_id, port = create(implementation, "darq.person")
    handler = item(implementation, port)
    person = kb.get(handler.entity_id)
    person.set_label("Ada")
    person.set_label("Ada", "fr")
    person.claim("P108", model.EntityValue("acme"))
    kb.put(person)
    return handler


def test_get_entity(ada):
    response = ada.get_entity(GetEntityRequest(languages=["fr", "en"]), 0)
    assert response.status == wire.Status.OK
    assert response.entity.id == ada.entity_id
    linked = {(link.entity_id, link.item_id, link.language, link.label) for link in response.linked}
    assert linked == {
        ("acme", "darq.organisation:acme", "en", "Acme"),
        ("Q5", "darq.entity:Q5", "en", "human"),
        ("P108", "", "en", "employer"),
        (model.INSTANCE_OF, "", "en", "instance of"),
        (model.DARQOS_TYPE, "", "en", "DarqOS type"),
    }


def test_put_entity(kb, ada):
    current = kb.get(ada.entity_id)
    current.set_label("Ada Lovelace")
    for s in current.get_statements(model.DARQOS_TYPE):
        current.remove_statement(s)
    current.claim(model.DARQOS_TYPE, model.StringValue("darq.place"))
    response = ada.put_entity(PutEntityRequest(entity=model.entity_to_wire(current)), 0)
    assert response.status == wire.Status.OK
    assert response.revision == current.revision + 1

    # Its kind stayed as it was.
    stored = kb.get(ada.entity_id)
    assert stored.get_label() == "Ada Lovelace"
    assert [s.get_value().value for s in stored.get_statements(model.DARQOS_TYPE)] == ["darq.person"]

    # Out of date, and not its entity.
    assert ada.put_entity(PutEntityRequest(entity=model.entity_to_wire(current)), 0).status == wire.Status.CONFLICT
    other = model.entity_to_wire(entity("acme", "Acme"))
    assert ada.put_entity(PutEntityRequest(entity=other), 0).status == wire.Status.INVALID_ARGUMENT


def test_get_links(kb, implementation, ada):
    acme = implementation.items[open_item(implementation, "darq.organisation:acme")]
    response = acme.get_links(GetLinksRequest(property_id="P108", role=Role.MAIN, languages=["en"]), 0)
    assert response.status == wire.Status.OK
    assert [(link.entity_id, link.item_id, link.label) for link in response.linked] == [
        (ada.entity_id, f"darq.person:{ada.entity_id}", "Ada")]


def test_watch_entity_and_close(kb, implementation, ports, ada):
    assert ada.watch_entity(WatchEntityRequest(listener=999), 0).status == wire.Status.OK
    assert 999 in ports.closed

    person = kb.get(ada.entity_id)
    person.add_alias("Augusta")
    ports.sent.clear()
    kb.put(person)
    assert (999, ada.entity_id) in ports.sent

    assert ada.watch_entity(WatchEntityRequest(), 0).status == wire.Status.INVALID_ARGUMENT

    port = ada.port
    ada.close(CloseRequest(), 0)
    assert port not in implementation.items
    assert port not in ports.closed
    for timer in implementation.server._timers:
        timer.fire()
    assert port in ports.closed


def test_changed_unknown_class_keeps_cache(implementation):
    implementation.get_class("Q5")
    module.ClassListener(implementation).changed(Changed(entity_id="unrelated", revision=1), 0)
    assert "Q5" in implementation.classes


################################################################
# Service
################################################################

def test_kb_type(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    client_path = write_app(tmp_path / "client", """
        import time
        import darq
        import darq.kb as model
        from darq.protocols.kb import EntityItem, Changed
        from darq.protocols import kb as wire
        from darq.protocols.type import Implementation, Status, Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)

            # Wait for the sync to register the KB's types.
            deadline = time.monotonic() + 20
            while types.describe(type_id="darq.person").status != Status.OK:
                if time.monotonic() > deadline:
                    return "darq.person never registered"
                time.sleep(0.05)

            person = types.activate(type_id="darq.person")
            place = types.activate(type_id="darq.place")
            if person.status != Status.OK or place.implementation != person.implementation:
                return f"activate: {person!r} {place!r}"

            implementation = Implementation.Client(person.implementation, timeout=30)
            created = implementation.create(type_id="darq.person")
            if created.status != Status.OK or not created.item_id.startswith("darq.person:"):
                return f"create: {created!r}"
            item = EntityItem.Client(created.item, timeout=30)

            listener = darq.open_port()
            if item.watch_entity(listener).status != wire.Status.OK:
                return "watch_entity failed"

            got = item.get_entity(languages=["en"])
            entity = model.entity_from_wire(got.entity)
            entity.set_label("Ada Lovelace")
            put = item.put_entity(model.entity_to_wire(entity))
            if put.status != wire.Status.OK:
                return f"put_entity: {put!r}"

            message = darq.receive_message(listener, timeout=10)
            if message is None:
                return "no change notification"
            changed = Changed()
            changed.decode(message[1], message[2])
            if (changed.entity_id, changed.revision) != (entity.id, put.revision):
                return f"changed: {changed.entity_id} {changed.revision}"
            item.close()

            opened = implementation.open(item_id=created.item_id)
            if opened.status != Status.OK:
                return f"open: {opened!r}"
            again = EntityItem.Client(opened.item, timeout=30).get_entity(languages=["en"])
            labels = {link.entity_id: link.label for link in again.linked}
            if model.entity_from_wire(again.entity).get_label() != "Ada Lovelace" or labels["Q5"] != "human":
                return f"reopened: {labels!r}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
