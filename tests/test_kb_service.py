# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Knowledge Base service, services/kb/main.py: its
# handler directly, on a temporary database, with the protocol's structs
# converted by darq.kb; and then as a service, through a real p-kernel
# (see test_pkernel.py for the fixtures), with darq.kb.KB as its client.

import importlib.util
import pathlib
import sys

import pytest

import darq.kb as model
from darq.protocols import kb as wire
from darq.protocols.kb import (
    DestroyRequest,
    FindByLabelRequest,
    FindByValueRequest,
    FindLinksRequest,
    GetEntitiesRequest,
    InstancesRequest,
    Match,
    MergeRequest,
    PutEntitiesRequest,
    Role,
    Status,
    UnwatchRequest,
    WatchRequest,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "kb"


def load_service():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(SERVICE_PATH))
    try:
        spec = importlib.util.spec_from_file_location("kb_service", SERVICE_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SERVICE_PATH))
    return module


module = load_service()


class Darq:
    """The p-calls the service makes for watches, faked."""

    def __init__(self):
        self.sent: list[tuple[int, str, int]] = []
        self.watched: list[int] = []
        self.closed: list[int] = []
        self.dead: set[int] = set()

    def send(self, port, message):
        if port in self.dead:
            raise module.darq.DarqError(f"{port} is dead")
        self.sent.append((port, message.entity_id, message.revision))

    def notify_on_death(self, handle, port):
        self.watched.append(handle)

    def close_port(self, port):
        self.closed.append(port)


@pytest.fixture
def fake(monkeypatch):
    fake = Darq()
    for name in ("send", "notify_on_death", "close_port"):
        monkeypatch.setattr(module.darq, name, getattr(fake, name))
    return fake


@pytest.fixture
def service(tmp_path):
    service = module.KBService(str(tmp_path / "kb.sqlite"))
    service.port = 1
    yield service
    service.close()


def put(service, *entities, status=Status.OK):
    response = service.put_entities(PutEntitiesRequest(entities=[model.entity_to_wire(e) for e in entities]), 0)
    assert response.status == status, response.failed_id
    for entity, written in zip(entities, response.entities):
        entity.revision = written.revision
    return response


def get(service, *ids, follow=True, languages=()):
    response = service.get_entities(GetEntitiesRequest(ids=list(ids), follow=follow, languages=list(languages)), 0)
    assert response.status == Status.OK
    return [model.entity_from_wire(e) for e in response.entities], \
        {label.entity_id: (label.language, label.text) for label in response.labels}


def item(entity_id, label=None, language="en"):
    entity = model.Item(entity_id)
    if label is not None:
        entity.set_label(label, language)
    return entity


def prop(entity_id, label, datatype):
    entity = model.Property(datatype, entity_id)
    entity.set_label(label)
    return entity


def vocabulary(service):
    put(service,
        prop("P31", "instance of", model.Datatype.Item),
        prop("P279", "subclass of", model.Datatype.Item),
        prop("P108", "employer", model.Datatype.Item),
        prop("P580", "start time", model.Datatype.Time),
        prop("P1329", "phone number", model.Datatype.String),
        prop("P2048", "height", model.Datatype.Quantity),
        prop("note", "note", model.Datatype.Text),
        item("Q5", "human"),
        item("Q43229", "organization"),
        item("Q11573", "metre"))


################################################################
# Structs
################################################################

@pytest.mark.parametrize("value", [
    model.EntityValue("Q5"),
    model.StringValue("+61 7 3000 1234"),
    model.TextValue("hello"),
    model.QuantityValue("1.650", "Q11573", "1.6", "1.7"),
    model.QuantityValue(3),
    model.TimeValue.parse("1974"),
    model.TimeValue.parse("-0044-03-15"),
    model.TimeValue(2026, 10, 30, 0, 0, 0, 5, model.TimePrecision.Nanoseconds,
                    before=2, after=1, calendar="julian"),
])
def test_values_round_trip(value):
    snak = model.Snak("P1", value)
    statement = model.Statement(snak, [snak], [model.Reference([snak])], model.Rank.Deprecated)
    entity = model.Item("Q1")
    entity.add_statement(statement)
    entity.set_label("one")
    entity.add_alias("uno", "es")

    message = PutEntitiesRequest(entities=[model.entity_to_wire(entity)])
    data, handles = message.encode()
    decoded = PutEntitiesRequest()
    decoded.decode(data, handles)
    assert model.document(model.entity_from_wire(decoded.entities[0])) == model.document(entity)


def test_time_sends_only_its_precision():
    v = model.value_to_wire(model.TimeValue(1974, 5, 11, 13, precision=model.TimePrecision.Months))
    assert (v.year, v.month, v.day, v.hour) == (1974, 5, 0, 0)
    back = model.value_from_wire(v)
    assert isinstance(back, model.TimeValue)
    assert (back.month, back.day, back.hour) == (5, None, None)


def test_property_needs_a_datatype():
    with pytest.raises(ValueError):
        model.entity_from_wire(wire.Entity("P1", wire.Kind.PROPERTY, wire.Datatype.NONE))


################################################################
# Entities
################################################################

def test_put_and_get(service):
    vocabulary(service)
    ada = item("Q7259", "Ada")
    ada.claim("P31", model.EntityValue("Q5"))
    ada.claim("P2048", model.QuantityValue("1.65", "Q11573"))
    ada.claim("P108", model.EntityValue("Q43229")) \
        .add_qualifier("P580", model.TimeValue.parse("1843"))
    response = put(service, ada)
    assert [e.revision for e in response.entities] == [1]

    [got], labels = get(service, "Q7259", "missing", languages=["fr", "en"])
    assert model.document(got) == model.document(ada)
    assert labels == {"P31": ("en", "instance of"), "Q5": ("en", "human"), "P2048": ("en", "height"),
                      "Q11573": ("en", "metre"), "P108": ("en", "employer"),
                      "Q43229": ("en", "organization"), "P580": ("en", "start time")}

    ada.set_label("Ada Lovelace")
    put(service, ada)
    assert ada.revision == 2
    assert get(service, "Q7259")[0][0].get_label() == "Ada Lovelace"


def test_new_entity_is_given_an_id(service):
    response = service.put_entities(PutEntitiesRequest(entities=[wire.Entity(labels=[wire.Text("en", "x")])]), 0)
    assert response.status == Status.OK
    assert len(response.entities[0].id) == 36


def test_conflicts(service):
    vocabulary(service)
    put(service, item("Q1", "one"))
    response = put(service, item("Q1", "again"), status=Status.CONFLICT)
    assert response.failed_id == "Q1"

    [first], _ = get(service, "Q1")
    [second], _ = get(service, "Q1")
    first.set_label("first")
    put(service, first)
    second.set_label("second")
    assert put(service, second, status=Status.CONFLICT).failed_id == "Q1"
    assert get(service, "Q1")[0][0].get_label() == "first"

    missing = item("Q2", "two")
    missing.revision = 3
    assert put(service, missing, status=Status.NOT_FOUND).failed_id == "Q2"


def test_put_is_all_or_none(service):
    vocabulary(service)
    good = item("Q1", "good")
    bad = item("Q2", "bad")
    bad.claim("P31", model.StringValue("not an item"))
    assert put(service, good, bad, status=Status.INVALID_STATEMENT).failed_id == "Q2"
    assert get(service, "Q1", "Q2")[0] == []

    # Entities in one batch can refer to each other.
    a, b = item("a", "a"), item("b", "b")
    a.claim("P108", model.EntityValue("b"))
    put(service, a, b)

    unknown = item("Q3")
    unknown.claim("P9999", model.StringValue("x"))
    put(service, unknown, status=Status.INVALID_STATEMENT)


def test_put_keeps_redirect_and_destroyed(service, fake):
    vocabulary(service)
    put(service, item("Q1", "one"))
    service.destroy(DestroyRequest(entity_id="Q1"), 0)
    [one], _ = get(service, "Q1")
    assert one.destroyed
    one.destroyed = False
    put(service, one)
    assert get(service, "Q1")[0][0].destroyed


def test_merge_and_destroy(service, fake):
    vocabulary(service)
    old, new, friend = item("old", "Robert"), item("new", "Bob"), item("friend", "friend")
    old.claim("P31", model.EntityValue("Q5"))
    friend.claim("P108", model.EntityValue("old"))
    put(service, old, new, friend)

    assert service.merge(MergeRequest(old_id="old", new_id="new"), 0).status == Status.OK
    assert service.merge(MergeRequest(old_id="old", new_id="new"), 0).status == Status.INVALID_ARGUMENT
    assert service.merge(MergeRequest(old_id="nothing", new_id="new"), 0).status == Status.NOT_FOUND

    [merged], _ = get(service, "old")
    assert (merged.id, merged.get_aliases()) == ("new", ["Robert"])
    [redirect], _ = get(service, "old", follow=False)
    assert redirect.redirect == "new"

    assert service.destroy(DestroyRequest(entity_id="friend"), 0).status == Status.OK
    assert service.destroy(DestroyRequest(entity_id="nothing"), 0).status == Status.NOT_FOUND
    ids = service.find_by_label(FindByLabelRequest(text="friend", language="en"), 0).ids
    assert ids == []


################################################################
# Lookups
################################################################

@pytest.fixture
def people(service):
    vocabulary(service)
    business = item("Q4830453", "business")
    business.claim("P279", model.EntityValue("Q43229"))
    acme = item("acme", "Acme Widgets")
    acme.add_alias("Acme Pty Ltd")
    acme.claim("P31", model.EntityValue("Q4830453"))
    acme.claim("P1329", model.StringValue("+61 (7) 3000-1234"))
    people = []
    for n in range(5):
        person = item(f"person{n}", f"Person {n}")
        person.claim("P31", model.EntityValue("Q5"))
        person.claim("P108", model.EntityValue("acme"))
        people.append(person)
    put(service, business, acme, *people)
    return service


def test_find_by_label(people):
    service = people

    def find(text, match=Match.EXACT, class_id=""):
        response = service.find_by_label(FindByLabelRequest(text=text, language="en", match=match,
                                                            class_id=class_id), 0)
        assert response.status == Status.OK
        return response.ids

    assert find("Acme Widgets") == ["acme"]
    assert find("acme widgets") == []
    assert find("acm pty", Match.WORDS) == ["acme"]
    assert find("person", Match.WORDS) == [f"person{n}" for n in range(5)]
    assert find("person", Match.WORDS, "Q43229") == []
    assert find("acme", Match.WORDS, "Q43229") == ["acme"]


def test_find_by_value_links_and_instances(people):
    service = people
    response = service.find_by_value(FindByValueRequest(property_id="P1329", value="+61 7 3000 1234"), 0)
    assert response.ids == ["acme"]

    response = service.find_links(FindLinksRequest(target_id="acme", property_id="P108", role=Role.MAIN), 0)
    assert response.ids == [f"person{n}" for n in range(5)]
    assert service.find_links(FindLinksRequest(target_id="acme", role=Role.QUALIFIER), 0).ids == []

    assert service.instances(InstancesRequest(class_id="Q43229"), 0).ids == ["acme"]


def test_paging(people, monkeypatch):
    service = people
    first = service.instances(InstancesRequest(class_id="Q5", max=2), 0).ids
    assert first == ["person0", "person1"]
    second = service.instances(InstancesRequest(class_id="Q5", max=2, after=first[-1]), 0).ids
    assert second == ["person2", "person3"]

    monkeypatch.setattr(module, "MAX_IDS", 3)
    assert len(service.instances(InstancesRequest(class_id="Q5"), 0).ids) == 3
    assert len(service.instances(InstancesRequest(class_id="Q5", max=10), 0).ids) == 3


################################################################
# Notifications
################################################################

def test_watch(service, fake):
    vocabulary(service)
    one = service.watch(WatchRequest(ids=["Q1"], listener=10), 0)
    everything = service.watch(WatchRequest(ids=[], listener=11), 0)
    assert (one.status, everything.status) == (Status.OK, Status.OK)
    assert fake.watched == [10, 11]

    put(service, item("Q1", "one"), item("Q2", "two"))
    assert sorted(fake.sent) == [(10, "Q1", 1), (11, "Q1", 1), (11, "Q2", 1)]

    # A failed change isn't reported.
    fake.sent.clear()
    put(service, item("Q1", "again"), status=Status.CONFLICT)
    assert fake.sent == []

    assert service.unwatch(UnwatchRequest(watch_id=one.watch_id), 0).status == Status.OK
    assert service.unwatch(UnwatchRequest(watch_id=one.watch_id), 0).status == Status.NOT_FOUND
    assert fake.closed == [10]
    assert service.watch(WatchRequest(ids=[]), 0).status == Status.INVALID_ARGUMENT


def test_dead_listeners_are_dropped(service, fake):
    vocabulary(service)
    service.watch(WatchRequest(ids=[], listener=10), 0)
    service.watch(WatchRequest(ids=[], listener=11), 0)
    service.port_destroyed(10)
    fake.dead.add(11)
    put(service, item("Q1", "one"))
    assert service.watches == {}
    assert sorted(fake.closed) == [10, 11]


################################################################
# Service
################################################################

def test_kb_service(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    client_path = write_app(tmp_path / "client", """
        import darq
        import darq.kb as model
        from darq.protocols.kb import Changed

        def darq_main():
            kb = model.KB.connect(timeout=10)
            listener = darq.open_port()
            kb.watch([], listener)

            instance_of = model.Property(model.Datatype.Item, "P31")
            instance_of.set_label("instance of")
            human = model.Item("Q5")
            human.set_label("human")
            ada = model.Item()
            ada.set_label("Ada Lovelace")
            ada.claim("P31", model.EntityValue("Q5"))
            kb.put(instance_of, human, ada)

            ada.add_alias("Ada")
            kb.put(ada)
            if ada.revision != 2:
                return f"revision is {ada.revision}"
            try:
                stale = kb.get(ada.id)
                stale.revision = 1
                kb.put(stale)
                return "a stale put succeeded"
            except model.ConflictError:
                pass

            entities, labels = kb.get_entities([ada.id], languages=["en"])
            if model.document(entities[0]) != model.document(ada):
                return f"got {model.document(entities[0])}"
            if labels != {"P31": "instance of", "Q5": "human"}:
                return f"labels are {labels!r}"
            if list(kb.find_by_label("ada", words=True)) != [ada.id]:
                return "not found by label"
            if list(kb.instances("Q5")) != [ada.id]:
                return "not found as a human"

            # Four changes: three entities, then Ada again.
            changed = []
            for _ in range(4):
                source, payload, handles = darq.receive_message(listener, timeout=10)
                message = Changed()
                message.decode(payload, handles)
                changed.append((message.entity_id, message.revision))
            if changed[-1] != (ada.id, 2):
                return f"changes were {changed!r}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (pathlib.Path(kernel.run_dir) / "kb.sqlite").exists()
