# darqos
# Copyright (C) 2026 David Arnold

# Tests for the vCard import, tools/vcard-import: reading cards as
# drafts; importing them, with a KB service and KB type implementation
# in this process (see test_kb_type.py), and History faked: finding,
# creating, sharing, re-importing and cleaning up; its window,
# offscreen; the development Terminal's Tools menu; and then the whole
# system, through a real p-kernel, with address books made up for them.

import importlib
import importlib.util
import json
import logging
import pathlib
import sys

import pytest

import darq
import darq.kb as model
from darq.protocols.type import CreateRequest, DestroyRequest

from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


TOOL_PATH = pathlib.Path(__file__).parent.parent / "tools" / "vcard-import"
DEVTERM_PATH = pathlib.Path(__file__).parent.parent / "tools" / "devterm"

def load():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(TOOL_PATH))
    try:
        modules = [importlib.import_module(name) for name in ("cards", "importer", "vcard", "vocabulary")]
        spec = importlib.util.spec_from_file_location("vcard_import", TOOL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
    finally:
        sys.path.remove(str(TOOL_PATH))
    return (*modules, tool)


cards, importer, vcard, vocabulary, tool = load()


CARD = """BEGIN:VCARD
VERSION:3.0
UID:{uid}
FN:{given} {family}
N:{family};{given};;;
ORG:{org};Sales
TEL;TYPE=WORK:+61 7 3000 1234
ADR;TYPE=HOME:;;{street};{locality};QLD;4000;Australia
X-ABRELATEDNAMES;TYPE=pref:Sam
END:VCARD
"""

GROUP = """BEGIN:VCARD
VERSION:3.0
UID:g1
FN:Friends
X-ADDRESSBOOKSERVER-KIND:group
X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:u1
END:VCARD
"""


def book(*people, group=False, org="Acme Widgets", street="1 Example St", locality="Brisbane"):
    text = "".join(CARD.format(uid=uid, given=g, family=f, org=org, street=street, locality=locality)
                   for uid, g, f in people)
    return list(vcard.parse(text + (GROUP if group else "")))


class Implementation:
    """An Implementation client that calls the KB type implementation's
    handler, in this process."""

    timeout = 1.0

    def __init__(self, handler):
        self.handler = handler
        self.fail_types: set[str] = set()

    def create(self, type_id):
        if type_id in self.fail_types:
            raise darq.DarqError(f"can't create a {type_id}")
        return self.handler.create(CreateRequest(type_id=type_id), 0)

    def destroy(self, item_id):
        return self.handler.destroy(DestroyRequest(item_id=item_id), 0)


class History:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []

    def add_event(self, item_id, event, source):
        self.events.append((item_id, event, source))


@pytest.fixture
def target(kb, implementation, monkeypatch):  # noqa: F811
    """What an import needs: the KB, the implementation, and History."""
    adapter = Implementation(implementation)

    class ItemClient:
        def __init__(self, port, timeout=None):
            self.port = port

        def close(self):
            implementation.entity_ports[self.port].close(None, 0)

    monkeypatch.setattr(importer, "EntityItem", type("EntityItem", (), {"Client": ItemClient}))
    return kb, adapter, History()


def run(target, people, name="Address book"):
    client, adapter, history = target
    wd = vocabulary.Vocabulary()
    reader = cards.Importer(wd)
    reader.read(people)
    items = model.KB(client.client, Writer(adapter.handler))
    return importer.Import(client, items, adapter, history, wd, name).run(reader.drafts)


def find(client, label, class_id=""):
    return list(client.find_by_label(label, class_id=class_id))


################################################################
# Reading
################################################################

def test_drafts():
    wd = vocabulary.Vocabulary()
    reader = cards.Importer(wd)
    reader.read(book(("u1", "Ann", "Example"), ("u2", "Ben", "Example"), group=True))
    categories = sorted(d.category for d in reader.drafts.values())
    assert categories == ["address", "card", "card", "card", "department", "locality", "name", "name",
                          "name", "organisation", "single stub", "single stub"]

    family = next(d for d in reader.drafts.values() if d.category == "name" and d.label == "Example")
    assert family.cards == {"u1", "u2"} and family.cls == wd.family_name_class
    locality = next(d for d in reader.drafts.values() if d.category == "locality")
    assert (locality.region, locality.country) == ("Q36074", "Q408")
    address = next(d for d in reader.drafts.values() if d.category == "address")
    assert address.parent == locality.entity.id and address.postcode == "4000"
    department = next(d for d in reader.drafts.values() if d.category == "department")
    assert reader.drafts[department.parent].label == "Acme Widgets"


def test_names_are_in_order():
    wd = vocabulary.Vocabulary()
    reader = cards.Importer(wd)
    reader.read(vcard.parse("BEGIN:VCARD\nVERSION:3.0\nUID:u1\nFN:Ann Marie Jo Smith-Jones\n"
                            "N:Smith,Jones;Ann;Marie,Jo;;\nEND:VCARD\n"))
    labels = {d.entity.id: d.label for d in reader.drafts.values()}
    [person] = [d.entity for d in reader.drafts.values() if d.category == "card"]

    def names(property_id):
        return [(labels[s.get_value().get_id()], [q.value.value for q in s.qualifiers
                                                  if q.property_id == wd.series_ordinal])
                for s in person.get_statements(property_id)]

    assert names(wd.given_name) == [("Ann", ["1"]), ("Marie", ["2"]), ("Jo", ["3"])]
    assert names(wd.family_name) == [("Smith", ["1"]), ("Jones", ["2"])]
    assert wd.series_ordinal in wd.qualifiers


def sources(entity) -> dict[str, list[str]]:
    """An entity's string values, with the sources each is stated in."""
    result = {}
    for s in entity.statements:
        value = s.get_value()
        assert isinstance(value, model.StringValue)
        cited = [r.snaks[0].value for r in s.references]
        result[value.value] = sorted(v.get_id() for v in cited if isinstance(v, model.EntityValue))
    return result


def test_restate():
    def statement(value, *sources):
        s = model.Statement(model.Snak("P1", model.StringValue(value)))
        for source in sources:
            s.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue(source)))
        return s

    e = model.Item("x")
    for s in (statement("other's", "other"), statement("mine"), statement("old", "book")):
        e.add_statement(s)
    new = [statement("other's"), statement("mine"), statement("new")]
    assert model.restate(e, "book", new)
    values = sources(e)
    # Another source's statement gains a reference; the operator's isn't
    # repeated; the old one goes.
    assert values == {"other's": ["book", "other"], "mine": [], "new": ["book"]}
    assert not model.restate(e, "book", new)

    # The source no longer says it: its reference goes, but not the other's.
    assert model.restate(e, "book", [])
    values = sources(e)
    assert values == {"other's": ["other"], "mine": []}


def test_restate_with_qualifiers():
    def statement(value, *qualifiers, source=None, rank=None):
        s = model.Statement(model.Snak("P1", model.StringValue(value)), rank=rank)
        for pid, v in qualifiers:
            s.add_qualifier(pid, model.StringValue(v))
        if source is not None:
            s.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue(source)))
        return s

    def text(s):
        value = s.get_value()
        assert isinstance(value, model.StringValue)
        return value.value

    own = frozenset({"label"})
    e = model.Item("x")
    dated = statement("home", ("label", "home"), ("end", "2023"), source="book")
    mine = statement("new home", ("start", "2024"))
    for s in (dated, mine, statement("deprecated", source="book", rank=model.Rank.Deprecated),
              statement("plain", source="book"), statement("relabelled", ("label", "a"), ("end", "2020"),
                                                          source="book")):
        e.add_statement(s)

    # Qualifiers added by hand aren't compared, but the source's are.
    restated = model.restate(e, "book", [statement("home", ("label", "home")), statement("new home"),
                                         statement("relabelled", ("label", "b"))], own)
    assert restated and restated.matched == [dated, mine]
    assert model.is_sourced(dated, "book") and not mine.references
    [added] = restated.added
    assert model.encode_statement(added, with_id=False)["qualifiers"] == [
        {"property": "label", "value": {"type": "string", "value": "b"}}]

    # Those it no longer makes go, unless they've qualifiers of others, or
    # deprecated rank: those are kept, as the operator's.
    values = {text(s) for s in restated.dropped}
    assert values == {"deprecated", "plain", "relabelled"}
    assert {text(s) for s in restated.kept} == {"deprecated", "relabelled"}
    assert {text(s) for s in e.statements} == {"home", "new home", "deprecated", "relabelled"}
    assert not any(s.references for s in restated.kept)

    # Its preferred rank raises a matched statement's, but its normal
    # rank doesn't lower one.
    wanted = [statement("home", ("label", "home"), rank=model.Rank.Preferred),
              statement("relabelled", ("label", "b"))]
    restated = model.restate(e, "book", wanted, own)
    assert restated and dated.rank == model.Rank.Preferred
    assert not model.restate(e, "book", [statement("home", ("label", "home")), wanted[1]], own)
    assert dated.rank == model.Rank.Preferred

    # Without qualifiers, all are compared, and none kept.
    restated = model.restate(e, "book", [statement("home", ("label", "home"))])
    assert restated.dropped and restated.kept == []


################################################################
# Importing
################################################################

def test_first_import(target):
    client, _, history = target
    result = run(target, book(("u1", "Ann", "Example"), ("u2", "Ben", "Example"), group=True))
    assert (result.created, result.found, result.failed) == (12, 0, set())

    # Created events for items of a kind, not names.
    kinds = sorted(item_id.partition(":")[0] for item_id, event, source in history.events)
    assert kinds == ["darq.group", "darq.organisation", "darq.organisational-unit", "darq.person",
                     "darq.person", "darq.person", "darq.person", "darq.place", "darq.place"]
    assert {(e, s) for _, e, s in history.events} == {("created", "vcard-import")}

    # Items' statements, with their references to the address book.
    [ann] = find(client, "Ann Example")
    person = client.get(ann)
    assert [s.get_value().value for s in person.get_statements(model.DARQOS_TYPE)] == ["darq.person"]
    source = model.key_id("address book:Address book")
    employer = person.get_statements("P108")[0]
    assert model.is_sourced(employer, source)
    assert client.get(employer.get_value().get_id()).get_label() == "Acme Widgets"
    # Create's statement, stated in DarqOS, is the import's too.
    [instance_of] = person.get_statements(model.INSTANCE_OF)
    assert model.is_sourced(instance_of, model.DARQOS) and model.is_sourced(instance_of, source)


def test_items_written_through_their_implementation(target, monkeypatch):
    # The drafts' entities go through EntityWriter, and Create; only the
    # vocabulary is written to the KB service directly.
    client, adapter, _ = target
    through, direct = [], []
    inside = []

    def wrap(cls, name, record):
        original = getattr(cls, name)

        def wrapper(self, *args, **kwargs):
            inside.append(True)
            try:
                result = original(self, *args, **kwargs)
            finally:
                inside.pop()
            record(args)
            return result
        monkeypatch.setattr(cls, name, wrapper)

    wrap(Writer, "write_entities", lambda args: through.extend(e.id for e in args[0]))
    wrap(Implementation, "create", lambda args: None)
    wrap(Implementation, "destroy", lambda args: None)
    put = client.client.put_entities

    def put_entities(entities):
        if not inside:
            direct.extend(e.id for e in entities)
        return put(entities)
    monkeypatch.setattr(client.client, "put_entities", put_entities)
    run(target, book(("u1", "Ann", "Example")))

    [ann] = find(client, "Ann Example")
    assert ann in through and ann not in direct
    assert model.key_id("address book:Address book") in direct
    assert not set(through) & set(direct)


def test_imported_items_are_published(target, index):  # noqa: F811
    client, _, _ = target
    run(target, book(("u1", "Ann", "Example")))
    [ann] = find(client, "Ann Example")
    published = {item_id: names for (what, item_id, _), names in zip(index.sent, index.names) if what == "publish"}
    assert "Ann Example" in published[f"darq.person:{ann}"]
    assert any(i.startswith("darq.organisation:") and "Acme Widgets" in n for i, n in published.items())

    # Its vocabulary isn't.
    assert not any(i.startswith("darq.entity:") for i in published)


def test_reimport_changes_nothing(target, kb_service):  # noqa: F811
    _, _, history = target
    people = book(("u1", "Ann", "Example"), ("u2", "Ben", "Example"), group=True)
    run(target, people)
    events, writes = len(history.events), kb_service.store.writes
    result = run(target, people)
    assert (result.created, result.updated, result.matches) == (0, 0, [])
    assert result.unchanged == 12
    assert (len(history.events), kb_service.store.writes) == (events, writes)


def test_sharing_between_address_books(target, caplog):
    client, _, _ = target
    run(target, book(("u1", "Ann", "Example")))
    caplog.set_level(logging.INFO, logger="vcard-import")
    result = run(target, book(("w1", "Cat", "Example")), name="Work")
    assert result.created == 3  # Cat, her given name, and her Sam

    matched = sorted(m.what for m in result.matches)
    assert matched == ["address", "department", "locality", "name", "organisation"]
    assert all(m.card == "w1" and m.item_id and not m.others for m in result.matches)
    assert "Matched locality 'Brisbane, Q36074, Q408' for card w1 to darq.place:" in caplog.text
    assert len(find(client, "Brisbane")) == 1 and len(find(client, "Acme Widgets")) == 1


def test_ambiguous_and_missing_fields(target):
    client, _, _ = target

    def locality(entity_id, *claims):
        e = model.Item(entity_id)
        e.set_label("Brisbane")
        e.claim(model.INSTANCE_OF, model.EntityValue("Q486972"))
        for pid, value in claims:
            e.claim(pid, model.EntityValue(value))
        return e

    run(target, [])  # The vocabulary.
    client.put(locality("b1", ("P131", "Q36074"), ("P17", "Q408")),
           locality("b2", ("P131", "Q36074"), ("P17", "Q408")),
           locality("b3", ("P17", "Q408")))
    result = run(target, book(("u1", "Ann", "Example")))
    [match] = [m for m in result.matches if m.what == "locality"]
    assert match.item_id == "darq.place:b1" and match.others == ["darq.place:b2"]

    # A locality with no region doesn't match one with a region.
    people = list(vcard.parse(CARD.format(uid="u9", given="Dee", family="Example", org="Acme Widgets",
                                          street="9 Other St", locality="Brisbane").replace(";QLD;", ";;")))
    result = run(target, people)
    [match] = [m for m in result.matches if m.what == "locality"]
    assert match.item_id == "darq.place:b3"


def test_hand_changes_survive(target):
    client, _, _ = target
    people = book(("u1", "Ann", "Example"))
    run(target, people)
    [ann] = find(client, "Ann Example")
    person = client.get(ann)
    person.claim("P1329", model.StringValue("+61 400 000 000"))
    client.put(person)
    [brisbane] = find(client, "Brisbane")
    place = client.get(brisbane)
    place.set_label("Brisbane City")
    client.put(place)

    # The renamed locality is found by where the last import led.
    result = run(target, people)
    assert result.created == 0 and result.matches == []
    assert list(client.find_by_value("P1329", "+61400000000")) == [ann]
    assert find(client, "Brisbane") == [] and find(client, "Brisbane City") == [brisbane]


def residence(client, entity_id):
    [s] = client.get(entity_id).get_statements("P551")
    return s


def test_dated_addresses(target):
    client, _, _ = target
    run(target, book(("u1", "Ann", "Example"), ("u2", "Ben", "Example")))
    [ann] = find(client, "Ann Example")
    [ben] = find(client, "Ben Example")
    [brisbane] = find(client, "Brisbane")
    person = client.get(ann)
    [s] = person.get_statements("P551")
    old = s.get_value().get_id()
    s.add_qualifier("P580", model.TimeValue(2019, 3))
    client.put(person)

    # A date added by hand is kept: the statement's still the address book's.
    result = run(target, book(("u1", "Ann", "Example"), ("u2", "Ben", "Example")))
    assert (result.updated, result.reviews) == (0, [])
    s = residence(client, ann)
    assert [q.property_id for q in s.qualifiers] == [vocabulary.Vocabulary().contact_type, "P580"]
    person = client.get(ann)
    person.get_statements("P551")[0].add_qualifier("P582", model.TimeValue(2024, 6, 30))
    client.put(person)

    # Ann moves, to another locality, as Ben does after her.  Her dated
    # address is kept, as hers (the operator's), not rewritten.
    people = [*book(("u1", "Ann", "Example"), street="2 New St", locality="Toowong"),
              *book(("u2", "Ben", "Example"))]
    result = run(target, people)
    statements = client.get(ann).get_statements("P551")
    [kept] = [s for s in statements if s.get_value().get_id() == old]
    [new] = [s for s in statements if s.get_value().get_id() != old]
    assert not kept.references and len(kept.qualifiers) == 3
    assert model.is_sourced(new, model.key_id("address book:Address book"))
    assert residence(client, ben).get_value().get_id() == old
    [change] = result.address_changes
    assert change.item_id == f"darq.person:{ann}" and change.card == "u1"
    assert change.what == "residence" and change.detail == f"darq.place:{old} -> darq.place:{new.get_value().get_id()}"
    assert result.destroyed == 0 and result.kept == 0

    # Once Ben's gone too, the old address and Brisbane are kept, linked
    # by Ann's statement, as hers, rather than destroyed.
    result = run(target, book(("u1", "Ann", "Example"), street="2 New St", locality="Toowong"))
    assert result.kept == 2
    address = client.get(old)
    assert not address.destroyed and address.get_statements("P6375")
    assert not any(s.references for s in address.get_statements("P6375"))
    assert not client.get(brisbane).destroyed


def test_lens_edits_survive(target):
    client, _, _ = target
    people = book(("u1", "Ann", "Example"))
    run(target, people)
    [ann] = find(client, "Ann Example")

    # As the entity lens makes them: a residence dated, a work phone
    # changed, and a related person removed.
    related = vocabulary.Vocabulary().related_person
    person = client.get(ann)
    home = person.get_statements("P551")[0].id
    phone = next(s.id for s in person.get_statements("P1329") if "3000 1234" in s.get_value().value)
    [relation] = person.get_statements(related)
    start = model.Snak(model.START_TIME, model.TimeValue(2019, 3, precision=model.TimePrecision.Months))
    edits = (model.change_qualifier(person, home, None, start, model.ENTITY_EDITOR)
             + model.change_value(person, phone, model.StringValue("+61 7 3000 9999"), model.ENTITY_EDITOR)
             + model.remove_statement(person, relation.id))
    edited = model.apply_edits(person, edits)
    client.put(edited)
    before = model.document(client.get(ann))

    result = run(target, people)
    assert result.updated == 0 and model.document(client.get(ann)) == before
    person = client.get(ann)
    assert [q.property_id for q in residence(client, ann).qualifiers][-1] == model.START_TIME
    numbers = {s.get_value().value: s for s in person.get_statements("P1329")}
    assert numbers["+61 7 3000 1234"].rank == model.Rank.Deprecated
    assert model.is_operators(numbers["+61 7 3000 9999"])
    [relation] = person.get_statements(related)
    assert relation.rank == model.Rank.Deprecated and relation.references


def test_moving_from_a_shared_address(target):
    client, _, _ = target
    run(target, book(("u1", "Ann", "Example"), ("u2", "Ben", "Example")))
    [ann] = find(client, "Ann Example")
    [ben] = find(client, "Ben Example")
    shared = residence(client, ben).get_value().get_id()

    # Ann's address changes: it's another address, not the shared one rewritten.
    result = run(target, [*book(("u1", "Ann", "Example"), street="2 New St"), *book(("u2", "Ben", "Example"))])
    assert residence(client, ben).get_value().get_id() == shared
    assert residence(client, ann).get_value().get_id() != shared
    [street] = client.get(shared).get_statements("P6375")
    assert "1 Example St" in str(model.encode_value(street.get_value()))
    assert len(result.address_changes) == 1


def test_several_values_are_paired_by_label(target):
    text = CARD.format(uid="u1", given="Ann", family="Example", org="Acme Widgets", street="1 Example St",
                       locality="Brisbane")
    text = text.replace("X-ABRELATEDNAMES;TYPE=pref:Sam\n",
                        "item1.X-ABRELATEDNAMES:Sam\nitem1.X-ABLABEL:child\n"
                        "item2.X-ABRELATEDNAMES:Max\nitem2.X-ABLABEL:child\n")
    text = text.replace("N:Example;Ann;;;", "N:Example;Ann;Beth;;")
    people = list(vcard.parse(text))
    first = run(target, people)
    result = run(target, people)
    assert (result.created, result.matches, result.updated) == (0, [], 0)
    assert first.created == 10


def test_cards_gone(target):
    client, _, _ = target
    run(target, book(("u1", "Ann", "Example"), ("u2", "Ben", "Example")))
    [ben] = find(client, "Ben Example")
    result = run(target, book(("u1", "Ann", "Example")))
    assert result.destroyed >= 2  # Ben, his Sam
    assert client.get(ben).destroyed
    assert find(client, "Ann Example")


def test_failed_cards_are_logged(target, caplog):
    client, adapter, _ = target
    adapter.fail_types.add("darq.group")
    result = run(target, book(("u1", "Ann", "Example"), group=True))
    assert result.failed == {"g1"}
    assert "Card g1 failed: card: creating a darq.group" in caplog.text
    assert find(client, "Ann Example") and not find(client, "Friends")


################################################################
# Window and Tools menu, offscreen
################################################################

class Loop:
    def post(self, function):
        function()


def test_window(qt_app, monkeypatch, tmp_path):
    window = tool.ImportWindow(Loop())
    window.begin()
    assert "Choose a vCard file" in window.status.text()

    path = tmp_path / "book.vcf"
    path.write_text("BEGIN:VCARD\nEND:VCARD\n")
    window.path.setText(str(path))
    result = importer.Result()
    result.created = 3
    result.reviews += [
        importer.Review(importer.MATCH, "u1", "darq.place:b1", "locality", "Brisbane", ["darq.place:b2"]),
        importer.Review(importer.ADDRESS_CHANGED, "u1", "darq.person:a", "residence", "darq.place:p1 -> darq.place:p2")]
    monkeypatch.setattr(tool, "run_import", lambda p, n, progress: (["1 cards"], result))
    monkeypatch.setattr(tool, "start_thread", lambda job: job())
    monkeypatch.setattr(darq, "release_reply_port", lambda: None)
    window.begin()
    text = window.summary.toPlainText()
    assert window.status.text() == "Imported." and "3 created" in text
    assert "locality 'Brisbane', for card u1: darq.place:b1 (also darq.place:b2)" in text
    assert "darq.person:a residence, for card u1: darq.place:p1 -> darq.place:p2" in text
    assert window.start.isEnabled()

    monkeypatch.setattr(tool, "run_import", lambda p, n, progress: (_ for _ in ()).throw(OSError("unreadable")))
    window.begin()
    assert window.status.text() == "Import failed: unreadable"
    window.close()


def test_tools_menu(tmp_path):
    sys.path.insert(0, str(DEVTERM_PATH))
    try:
        spec = importlib.util.spec_from_file_location("devterm_main", DEVTERM_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        devterm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(devterm)
    finally:
        sys.path.remove(str(DEVTERM_PATH))

    for name, manifest in [("b", {"name": "Beta"}), ("a", {"name": "Alpha"}), ("c", None)]:
        (tmp_path / "tools" / name).mkdir(parents=True)
        if manifest is not None:
            (tmp_path / "tools" / name / "tool.json").write_text(json.dumps(manifest))
    (tmp_path / "tools" / "bad").mkdir()
    (tmp_path / "tools" / "bad" / "tool.json").write_text("not json")
    assert devterm.find_tools(tmp_path) == [("Alpha", str(tmp_path / "tools" / "a")),
                                            ("Beta", str(tmp_path / "tools" / "b"))]
    names = [name for name, _ in devterm.find_tools(TOOL_PATH.parent.parent)]
    assert "Import vCards" in names


################################################################
# The system
################################################################

def test_vcard_import(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel
    path = tmp_path / "book.vcf"
    path.write_text("".join(CARD.format(uid=uid, given=g, family="Example", org="Acme Widgets",
                                        street="1 Example St", locality="Brisbane")
                            for uid, g in [("u1", "Ann"), ("u2", "Ben")]))

    client_path = write_app(tmp_path / "client", f"""
        import sys
        import time
        import darq
        from darq.protocols.history import History
        from darq.protocols.type import Status, Type

        import importlib.util
        sys.path.insert(0, {str(TOOL_PATH)!r})
        spec = importlib.util.spec_from_file_location("vcard_import", {str(TOOL_PATH / "main.py")!r})
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while types.describe(type_id="darq.person").status != Status.OK:
                if time.monotonic() > deadline:
                    return "darq.person never registered"
                time.sleep(0.05)

            read, result = tool.run_import({str(path)!r}, "Test", lambda step, done, total: None)
            if result.created != 11 or result.failed:
                return f"first: {{result.lines()}}"
            read, result = tool.run_import({str(path)!r}, "Test", lambda step, done, total: None)
            if result.created != 0 or result.updated != 0:
                return f"again: {{result.lines()}}"

            history = History.Client(darq.connect("darq.history"), timeout=10)
            events = history.get_events(sources=["vcard-import"]).events
            kinds = sorted(e.item_id.partition(":")[0] for e in events)
            if kinds != ["darq.organisation", "darq.organisational-unit", "darq.person", "darq.person",
                         "darq.person", "darq.person", "darq.place", "darq.place"]:
                return f"events: {{kinds}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
