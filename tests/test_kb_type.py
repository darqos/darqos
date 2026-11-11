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
import darq.book
import darq.kb as model
import darq.message
import darq.url
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    CloseRequest,
    FindEntitiesRequest,
    GetEntityRequest,
    GetLinkedEntitiesRequest,
    GetLinksRequest,
    GetLinkSummaryRequest,
    Kind,
    PutEntityRequest,
    Role,
    SuggestPropertiesRequest,
    WatchEntityRequest,
)
from darq.protocols.type import (CreateFromRequest, CreateRequest, DestroyRequest, ExportRequest,
                                 IndexEntryRequest, OpenRequest, Status)

from test_kb_service import module as service_module
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_text_type import FakePublisher


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
def index():
    return FakePublisher()


@pytest.fixture
def implementation(kb, ports, index):
    implementation = module.EntityImplementation(kb, index)
    server = darq.Server()
    ports.server = server
    implementation.add(server, IMPLEMENTATION_PORT)
    implementation.start()
    return implementation


def create(implementation, type_id):
    response = implementation.create(CreateRequest(type_id=type_id), 0)
    assert response.status == Status.OK
    return response.item_id, response.item


def test_no_data_form(implementation):
    # An entity has no form as data, to create from or export.
    assert implementation.create_from(CreateFromRequest(type_id="darq.person", data=b"BEGIN:VCARD"), 0).status \
        == Status.UNSUPPORTED
    iid, _ = create(implementation, "darq.person")
    assert implementation.export(ExportRequest(item_id=iid), 0).status == Status.UNSUPPORTED


def index_entry(implementation, item_id):
    response = implementation.index_entry(IndexEntryRequest(item_id=item_id), 0)
    return (response.names, response.body) if response.status == Status.OK else response.status


def test_index_entry(kb, implementation):
    iid, _ = create(implementation, "darq.person")
    person = kb.get(darq.split_item_id(iid)[1])
    person.set_label("Ada Lovelace")
    person.set_label("Ada Lovelace", "fr")
    person.set_label("Ada", "de")
    person.add_alias("Augusta Ada King")
    person.set_description("mathematician")
    person.set_description("mathématicienne", "fr")
    kb.put(person)
    assert index_entry(implementation, iid) == (["Ada Lovelace", "Ada", "Augusta Ada King"],
                                                "mathematician\nmathématicienne")

    # Items of darq.thing aren't indexed.
    thing, _ = create(implementation, "darq.thing")
    assert index_entry(implementation, thing) == Status.UNSUPPORTED

    # A merged entity's item is the other's now; a destroyed one's gone.
    other, _ = create(implementation, "darq.person")
    kb.merge(person.id, darq.split_item_id(other)[1])
    assert index_entry(implementation, iid) == Status.NOT_FOUND
    kb.destroy(darq.split_item_id(other)[1])
    assert index_entry(implementation, other) == Status.NOT_FOUND
    assert index_entry(implementation, "public.png:x") == Status.NOT_FOUND
    assert index_entry(implementation, "bad id") == Status.INVALID_ID


def test_published_as_written(kb, implementation, index):
    items = model.KB(kb.client, Writer(implementation))
    iid, port = create(implementation, "darq.person")
    entity_id = darq.split_item_id(iid)[1]
    assert index.sent == [("publish", iid, "")]
    create(implementation, "darq.thing")
    assert len(index.sent) == 1

    person = kb.get(entity_id)
    person.set_label("Ada")
    person.set_description("mathematician")
    items.put(person)
    assert (index.sent[-1], index.names[-1]) == (("publish", iid, "mathematician"), ["Ada"])

    handler = item(implementation, port)
    current = kb.get(entity_id)
    current.add_alias("Augusta")
    assert handler.put_entity(PutEntityRequest(entity=model.entity_to_wire(current)), 0).status == wire.Status.OK
    assert index.names[-1] == ["Ada", "Augusta"]

    other, _ = create(implementation, "darq.person")
    items.merge(entity_id, darq.split_item_id(other)[1])
    assert index.sent[-2:] == [("withdraw", iid), ("publish", other, "mathematician")]
    assert index.names[-1] == ["Ada", "Augusta"]

    items.destroy(darq.split_item_id(other)[1])
    assert index.sent[-1] == ("withdraw", other)
    third, _ = create(implementation, "darq.place")
    assert implementation.destroy(DestroyRequest(item_id=third), 0).status == Status.OK
    assert index.sent[-1] == ("withdraw", third)


def test_kind_changed_is_withdrawn(kb, implementation, index):
    # An imported entity's kind is its classes': written with another
    # class, it's another kind of item.
    items = model.KB(kb.client, Writer(implementation))
    items.put(entity("Q7259", "Ada", "Q5"))
    assert index.sent[-1] == ("publish", "darq.person:Q7259", "")
    ada = kb.get("Q7259")
    for s in ada.get_statements(model.INSTANCE_OF):
        ada.remove_statement(s)
    ada.claim(model.INSTANCE_OF, model.EntityValue("Q43229"))
    items.put(ada)
    assert index.sent[-2:] == [("withdraw", "darq.person:Q7259"), ("publish", "darq.organisation:Q7259", "")]

    # Vocabulary, such as classes and properties, isn't indexed.
    sent = len(index.sent)
    items.put(entity("Q123", "a class"), model.Property(model.Datatype.String, "P999"))
    assert len(index.sent) == sent


def open_item(implementation, item_id):
    response = implementation.open(OpenRequest(item_id=item_id), 0)
    return response.item if response.status == Status.OK else response.status


def item(implementation, port):
    return implementation.entity_ports[port]


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


@pytest.mark.parametrize("property_id, before, after", [
    (darq.book.OLD_CONTENT, "file", "content (identifier, old)"),
    (darq.book.OLD_CONTENT, "content", "content (identifier, old)"),
    (darq.url.OLD_SNAPSHOT, "snapshot", "snapshot (identifier, old)"),
    (darq.message.OLD_ATTACHMENT, "attachment", "attached file"),
])
def test_a_renamed_property_gets_its_new_label(kb, implementation, property_id, before, after):
    entity = kb.get(property_id)
    entity.set_label(before)
    kb.put(entity)
    implementation.start()
    assert kb.get(property_id).get_label() == after


def test_a_label_changed_by_hand_isnt_renamed(kb, implementation):
    entity = kb.get(darq.book.OLD_CONTENT)
    entity.set_label("mine")
    kb.put(entity)
    implementation.start()
    assert kb.get(darq.book.OLD_CONTENT).get_label() == "mine"


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
        "darq.group", "darq.thing"]

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
    ("darq.place", "Q2221906"), ("darq.group", "Q16334295"), ("darq.conversation", model.CONVERSATION),
    ("darq.message", model.INSTANT_MESSAGE), ("darq.thing", None)])
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
    assert open_item(implementation, f"darq.thing:{entity_id}") == Status.NOT_FOUND
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


class Writer(wire.EntityWriter.Client):
    """An EntityWriter client that calls the implementation's handler, in
    this process."""

    def __init__(self, handler):
        super().__init__(0)
        self.handler = handler

    def call(self, request, response_class):
        _, name = self.handler.METHODS[request.METHOD]
        return getattr(self.handler, name)(request, 0)


def test_serves_entity_writer(implementation):
    # On its Implementation port, beside Implementation.
    assert {1, 2, 3, 4, 5, 16, 17, 18} <= set(implementation.METHODS)


def test_write_entities_keeps_kinds(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    item_id, _ = create(implementation, "darq.person")
    person = kb.get(darq.split_item_id(item_id)[1])
    stale = kb.get(person.id)
    person.set_label("Ada")
    for s in person.get_statements(model.DARQOS_TYPE):
        person.remove_statement(s)
    person.claim(model.DARQOS_TYPE, model.StringValue("darq.place"))

    # A new entity can't be given a kind.
    new = entity("acme", "Acme", "Q43229")
    new.claim(model.DARQOS_TYPE, model.StringValue("darq.person"))
    items.put(person, new)

    stored = kb.get(person.id)
    assert stored.get_label() == "Ada"
    assert [s.get_value().value for s in stored.get_statements(model.DARQOS_TYPE)] == ["darq.person"]
    assert kb.get("acme").get_statements(model.DARQOS_TYPE) == []
    assert implementation.kind_of(kb.get("acme")) == "darq.organisation"

    # All or none, as PutEntities.
    stale.set_label("Lovelace")
    with pytest.raises(model.ConflictError):
        items.put(stale, entity("other", "Other"))
    assert kb.get("other") is None


def message(entity_id: str, body: str = "Hello") -> model.Item:
    item = model.Item(entity_id)
    item.claim(darq.message.BODY, model.TextValue(body, darq.message.UNDETERMINED))
    return item


def test_create_entities(kb, implementation, index):
    items = model.KB(kb.client, Writer(implementation))
    first, second = message("m1", "Hello there"), message("m2", "Goodbye")
    items.create("darq.message", first, second)
    assert first.revision == second.revision == 1

    stored = kb.get("m1")
    assert implementation.kind_of(stored) == "darq.message"
    assert [s.get_value().value for s in stored.get_statements(model.DARQOS_TYPE)] == ["darq.message"]
    assert [s.get_value().get_id() for s in stored.get_statements(model.INSTANCE_OF)] == [model.INSTANT_MESSAGE]

    # Its text is its body, and it's found by nothing else.
    assert ("publish", "darq.message:m1", "Hello there") in index.sent
    assert index.names[-1] == []
    assert index_entry(implementation, "darq.message:m2") == ([], "Goodbye")

    # It's opened as any other item.
    assert item(implementation, open_item(implementation, "darq.message:m1")).entity_id == "m1"


def test_create_entities_keeps_a_class_and_its_kind_only(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    new = message("m1")
    new.claim(model.INSTANCE_OF, model.EntityValue(model.INSTANT_MESSAGE))
    new.claim(model.DARQOS_TYPE, model.StringValue("darq.person"))
    items.create("darq.message", new)

    stored = kb.get("m1")
    assert len(stored.get_statements(model.INSTANCE_OF)) == 1
    assert [s.get_value().value for s in stored.get_statements(model.DARQOS_TYPE)] == ["darq.message"]


def test_create_entities_is_all_or_none(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    items.create("darq.message", message("m1"))
    with pytest.raises(model.ConflictError):
        items.create("darq.message", message("m2"), message("m1"))
    assert kb.get("m2") is None


def test_create_entities_refused(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    with pytest.raises(LookupError):
        items.create("public.png", message("m1"))
    with pytest.raises(ValueError):
        items.create("bad:type", message("m1"))
    written = message("m1")
    written.revision = 1
    with pytest.raises(ValueError):
        items.create("darq.message", written)
    assert kb.get("m1") is None

    # Creating needs the implementation.
    with pytest.raises(ValueError):
        kb.create("darq.message", message("m1"))


def test_merge_and_destroy_through_writer(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    old_id, _ = create(implementation, "darq.person")
    new_id, _ = create(implementation, "darq.person")
    old, new = darq.split_item_id(old_id)[1], darq.split_item_id(new_id)[1]
    items.merge(old, new)
    assert kb.get(old).id == new

    items.destroy(new)
    assert open_item(implementation, new_id) == Status.NOT_FOUND
    with pytest.raises(LookupError):
        items.destroy("missing")


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
        ("Q5", "darq.thing:Q5", "en", "human"),
        ("P108", "", "en", "employer"),
        (model.INSTANCE_OF, "", "en", "instance of"),
        (model.DARQOS_TYPE, "", "en", "DarqOS type"),
        (model.STATED_IN, "", "en", "stated in"),
        (model.DARQOS, f"darq.thing:{model.DARQOS}", "en", "DarqOS"),
    }


def test_a_link_to_a_merged_entity_names_what_it_was_merged_into(kb, ada):
    """A merge relinks what links to the old identifier, but a statement
    made after it, as by an import, can still name it, and it redirects:
    it's listed by that, with the new one's name."""
    other = model.Item("Q999")
    other.set_label("Acme, from Wikidata")
    other.claim(model.INSTANCE_OF, model.EntityValue("Q43229"))
    kb.put(other)
    kb.merge("acme", "Q999")
    person = kb.get(ada.entity_id)
    assert person.get_statements("P108")[0].get_value().entity_id == "Q999"
    person.claim("P108", model.EntityValue("acme"))
    kb.put(person)
    response = ada.get_entity(GetEntityRequest(languages=["en"]), 0)
    linked = {link.entity_id: link for link in response.linked}
    assert (linked["acme"].label, linked["acme"].item_id) == ("Acme, from Wikidata", "darq.organisation:Q999")


def test_linked_have_descriptions_and_datatypes(kb, ada):
    acme = kb.get("acme")
    acme.set_description("a company")
    kb.put(acme)
    response = ada.get_entity(GetEntityRequest(languages=["en"]), 0)
    linked = {link.entity_id: link for link in response.linked}
    assert (linked["acme"].description, linked["acme"].datatype) == ("a company", wire.Datatype.NONE)
    assert linked["P108"].datatype == wire.Datatype.ITEM
    assert linked[model.DARQOS_TYPE].datatype == wire.Datatype.STRING


def test_find_entities(kb, implementation, ada):
    kb.put(entity("acme2", "Acme Anvils", "Q43229"), entity("other", "Acme", "Q5"))
    local = model.Property(model.Datatype.String, "acme-code")
    local.set_label("Acme code", "fr")
    kb.put(local)

    def find(text, kind=Kind.ITEM, languages=("en",), class_id="", max=0, after=""):
        response = ada.find_entities(FindEntitiesRequest(text=text, languages=list(languages), kind=kind,
                                                         class_id=class_id, max=max, after=after), 0)
        assert response.status == wire.Status.OK
        return [link.entity_id for link in response.found]

    assert find("acme") == ["acme", "acme2", "other"]
    assert find("acme", class_id="Q43229") == ["acme", "acme2"]
    assert find("acme", max=2) == ["acme", "acme2"] and find("acme", after="acme2") == ["other"]
    assert find("") == [] and find("nothing") == []

    # Properties, in any of a chain of languages.
    assert find("acme", Kind.PROPERTY) == []
    assert find("acme", Kind.PROPERTY, ["fr", "en"]) == ["acme-code"]
    assert find("employ", Kind.PROPERTY) == ["P108"]
    [found] = ada.find_entities(FindEntitiesRequest(text="time", languages=["en"], kind=Kind.PROPERTY), 0).found[:1]
    assert found.datatype == wire.Datatype.TIME and found.item_id == ""


def test_suggest_properties(ada):
    response = ada.suggest_properties(SuggestPropertiesRequest(languages=["en"]), 0)
    assert response.status == wire.Status.OK
    assert [link.entity_id for link in response.found] == [
        model.START_TIME, model.END_TIME, model.POINT_IN_TIME, model.REASON_FOR_DEPRECATED_RANK,
        model.REASON_FOR_PREFERRED_RANK, model.INSTANCE_OF, "P108"]
    assert response.found[0].label == "start time"
    assert len(ada.suggest_properties(SuggestPropertiesRequest(languages=["en"], max=2), 0).found) == 2


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
    acme = implementation.entity_ports[open_item(implementation, "darq.organisation:acme")]
    response = acme.get_links(GetLinksRequest(property_id="P108", role=Role.MAIN, languages=["en"]), 0)
    assert response.status == wire.Status.OK
    assert [(link.entity_id, link.item_id, link.label) for link in response.linked] == [
        (ada.entity_id, f"darq.person:{ada.entity_id}", "Ada")]


def test_get_links_newest_first_and_without_messages(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    iid, port = create(implementation, "darq.conversation")
    conversation = darq.split_item_id(iid)[1]
    for name in ("m1", "m2", "m3"):
        new = message(name)
        new.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
        items.create("darq.message", new)
    person = entity("ada", "Ada", "Q5")
    person.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
    kb.put(person)

    def links(**arguments):
        request = GetLinksRequest(role=Role.MAIN, languages=["en"], **arguments)
        response = item(implementation, port).get_links(request, 0)
        return [link.entity_id for link in response.linked]

    assert links(property_id=darq.message.CONVERSATION, reverse=True, max=2) == ["m3", "m2"]
    assert links(property_id=darq.message.CONVERSATION, reverse=True, max=2, after="m2") == ["m1", "ada"]
    assert links(type_id="darq.message") == ["m1", "m2", "m3"]
    assert links(type_id="darq.thing") == ["ada"]


def test_messages_are_summarised_when_listed(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    iid, port = create(implementation, "darq.conversation")
    conversation = darq.split_item_id(iid)[1]
    kb.put(entity("ada", "Ada", "Q5"))
    new = message("m1", "Hello there\nSecond line")
    new.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
    new.claim(darq.message.SENDER, model.EntityValue("ada"))
    new.claim(darq.message.TIME, darq.message.time_value(1_700_000_000_000_000))
    items.create("darq.message", new)

    response = item(implementation, port).get_links(GetLinksRequest(role=Role.MAIN, languages=["en"]), 0)
    assert [link.summary for link in response.linked] == ["Ada, 2023-11-14: Hello there"]


def test_get_link_summary(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    iid, port = create(implementation, "darq.conversation")
    conversation = darq.split_item_id(iid)[1]
    for name in ("m1", "m2"):
        new = message(name)
        new.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
        items.create("darq.message", new)
    person = entity("ada", "Ada", "Q5")
    person.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
    person.claim(darq.message.BODY, model.TextValue("Hello", "en"))
    kb.put(person)

    response = item(implementation, port).get_link_summary(GetLinkSummaryRequest(languages=["en"]), 0)
    assert response.status == wire.Status.OK
    assert [(g.property_id, g.role, g.type_id, g.count, g.label, g.inverse_label) for g in response.groups] == [
        (darq.message.CONVERSATION, Role.MAIN, "darq.message", 2, "part of", "has part"),
        (darq.message.CONVERSATION, Role.MAIN, "darq.thing", 1, "part of", "has part")]


def test_get_link_summary_without_an_inverse_label(kb, implementation, ada):
    # The fallback's up to the lens.
    response = item(implementation, open_item(implementation, "darq.organisation:acme")).get_link_summary(
        GetLinkSummaryRequest(languages=["en"]), 0)
    groups = [(g.property_id, g.count, g.inverse_label) for g in response.groups]
    assert ("P108", 1, "") in groups


def test_get_linked_entities(kb, implementation):
    items = model.KB(kb.client, Writer(implementation))
    iid, port = create(implementation, "darq.conversation")
    conversation = darq.split_item_id(iid)[1]
    kb.put(entity("ada", "Ada", "Q5"))
    for name in ("m1", "m2", "m3"):
        new = message(name, f"Hello from {name}")
        new.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
        new.claim(darq.message.SENDER, model.EntityValue("ada"))
        items.create("darq.message", new)

    response = item(implementation, port).get_linked_entities(GetLinkedEntitiesRequest(
        property_id=darq.message.CONVERSATION, role=Role.MAIN, type_id="darq.message", languages=["en"], max=2,
        reverse=True), 0)
    assert response.status == wire.Status.OK

    # Newest first, whole, with who they refer to by name.
    entities = [model.entity_from_wire(e) for e in response.entities]
    assert [e.id for e in entities] == ["m3", "m2"]
    assert [module.index_entry(e)[1] for e in entities] == ["Hello from m3", "Hello from m2"]
    assert {link.entity_id: link.label for link in response.linked}["ada"] == "Ada"


def test_start_states_inverse_labels_once(kb, implementation, kb_service):
    creator = kb.get(darq.message.SENDER)
    statements = creator.get_statements(model.INVERSE_LABEL)
    assert len(statements) == 1
    revision = creator.revision
    implementation.start()
    assert kb.get(darq.message.SENDER).revision == revision


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
    assert port not in implementation.entity_ports
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
        import darq.message
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

            # Its entity, as every item's, from OpenEntity: the same, here.
            entity_port = implementation.open_entity(item_id=created.item_id)
            if entity_port.status != Status.OK:
                return f"open_entity: {entity_port!r}"
            got = EntityItem.Client(entity_port.item, timeout=30).get_entity(languages=["en"])
            if model.entity_from_wire(got.entity).get_label() != "Ada Lovelace":
                return f"open_entity's entity: {got!r}"

            # A text's an entity too, made with it, and named from its file.
            text = types.activate(type_id="public.utf8-plain-text")
            if text.status != Status.OK:
                return f"activate text: {text!r}"
            texts = Implementation.Client(text.implementation, timeout=30)
            note = texts.create_from(type_id="public.utf8-plain-text", data=b"Hello", name="Shopping list.txt")
            if note.status != Status.OK:
                return f"text's create_from: {note!r}"
            darq.close_port(note.item)
            answer = texts.open_entity(item_id=note.item_id)
            if answer.status != Status.OK:
                return f"text's open_entity: {answer!r}"
            got = EntityItem.Client(answer.item, timeout=30).get_entity(languages=["en"])
            if model.entity_from_wire(got.entity).get_label() != "Shopping list":
                return f"text's entity: {got!r}"

            # A message, created with the identifier it's given.
            kb = model.KB.connect(30, wire.EntityWriter.Client(person.implementation, timeout=30))
            if types.describe(type_id="darq.message").status != Status.OK:
                return "darq.message isn't registered"
            message_id = darq.message.message_id("test:1", 1_700_000_000_123_456)
            new = model.Item(message_id)
            new.claim(darq.message.BODY, model.TextValue("Hello", darq.message.UNDETERMINED))
            new.claim(darq.message.TIME, darq.message.time_value(1_700_000_000_123_456))
            kb.create("darq.message", new)
            opened = implementation.open(item_id=f"darq.message:{message_id}")
            if opened.status != Status.OK:
                return f"open the message: {opened!r}"

            # And it's found through what it links to.
            created = implementation.create(type_id="darq.conversation")
            conversation = darq.split_item_id(created.item_id)[1]
            reply = model.Item(darq.message.message_id("test:2", 1_700_000_000_123_457))
            reply.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
            kb.create("darq.message", reply)
            item = EntityItem.Client(created.item, timeout=30)
            summary = item.get_link_summary(languages=["en"])
            if [(g.type_id, g.count, g.inverse_label) for g in summary.groups] != [("darq.message", 1, "has part")]:
                return f"summary: {summary!r}"
            found = item.get_linked_entities(property_id=darq.message.CONVERSATION, role=wire.Role.MAIN,
                                             type_id="darq.message", languages=["en"], max=10, reverse=True)
            if [e.id for e in found.entities] != [reply.id]:
                return f"linked entities: {found!r}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


################################################################
# History (see doc/history.rst, Who Records Events)
################################################################

def test_what_it_does_is_recorded(kb, implementation, recorded):
    ctx = wire.Context(source="vcard-import", note="Address book")
    old_id = implementation.create(CreateRequest(type_id="darq.person"), 0).item_id
    new_id, _ = create(implementation, "darq.person")
    old, new = darq.split_item_id(old_id)[1], darq.split_item_id(new_id)[1]

    # Created as messages, then a person renamed.
    created = implementation.create_entities(wire.CreateEntitiesRequest(
        type_id="darq.message", entities=[model.entity_to_wire(message("m1"))], context=ctx), 0)
    assert created.status == wire.Status.OK
    person = kb.get(new)
    person.set_label("Ada")
    written = implementation.write_entities(wire.WriteEntitiesRequest(
        entities=[model.entity_to_wire(person)], context=ctx), 0)
    assert written.status == wire.Status.OK
    implementation.merge_entity(wire.MergeEntityRequest(old_id=old, new_id=new), 0)
    implementation.destroy_entity(wire.DestroyEntityRequest(entity_id=new, context=wire.Context(quiet=True)), 0)
    assert recorded.events == [
        (old_id, "created", "kb-type", ""),
        (new_id, "created", "kb-type", ""),
        ("darq.message:m1", "created", "vcard-import", "Address book"),
        (new_id, "edited", "vcard-import", "Address book"),
        (old_id, "merged", "kb-type", ""),
        (new_id, "merged", "kb-type", ""),
    ]


def test_an_entity_of_no_kind_isnt_recorded_unless_its_edited_on_its_page(kb, implementation, recorded):
    kb.ensure(entity("given", "Ada"))
    given = kb.get("given")
    given.set_description("a given name")
    implementation.write_entities(wire.WriteEntitiesRequest(entities=[model.entity_to_wire(given)]), 0)
    implementation.destroy_entity(wire.DestroyEntityRequest(entity_id="given"), 0)
    assert recorded.events == []


def test_viewing_and_editing_an_entity_on_its_port(kb, implementation, recorded):
    item_id, _ = create(implementation, "darq.person")
    port = open_item(implementation, item_id)
    recorded.events.clear()
    handler = item(implementation, port)
    handler.viewed(wire.Viewed(source="entity-lens", note=""), 0)
    person = kb.get(darq.split_item_id(item_id)[1])
    person.set_label("Ada")
    assert handler.put_entity(wire.PutEntityRequest(entity=model.entity_to_wire(person)), 0).status == wire.Status.OK
    handler.put_entity(wire.PutEntityRequest(entity=model.entity_to_wire(kb.get(person.id)),
                                             context=wire.Context(source="wikidata-match", note="Q7259")), 0)
    assert recorded.events == [(item_id, "viewed", "entity-lens", ""), (item_id, "edited", "entity-lens", ""),
                               (item_id, "edited", "wikidata-match", "Q7259")]


def test_a_renamed_type_is_renamed_when_it_starts(kb, implementation):
    """Items of darq.entity, now darq.thing, have their DarqOS type
    statements renamed, once."""
    old = model.Item("old")
    old.set_label("A unit")
    old.claim(model.DARQOS_TYPE, model.StringValue("darq.entity"))
    kb.put(old)
    implementation.start()
    renamed = kb.get("old")
    assert [s.get_value().value for s in renamed.get_statements(model.DARQOS_TYPE)] == [model.THING_TYPE]
    assert implementation.kind_of(renamed) == model.THING_TYPE
    revision = renamed.revision
    implementation.start()
    assert kb.get("old").revision == revision
