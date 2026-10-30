# darqos
# Copyright (C) 2026 David Arnold

# Tests for the knowledge base's SQLite store, services/kb/store.py,
# with its KnowledgeBase, services/kb/model.py, and the data model,
# darq/model.py: documents, the index
# tables, revisions and conflicts, redirects, and repeatable imports,
# with an address book made up for them.

import importlib
import os
import pathlib
import sqlite3
import stat
import sys

import pytest

import darq.kb as model


KB_PATH = pathlib.Path(__file__).parent.parent / "services" / "kb"


def load(name: str):
    # Its modules import each other by name, as they do run from there.
    sys.path.insert(0, str(KB_PATH))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(KB_PATH))


kb = load("kb")
store = load("store")
vcard = load("vcard")
wikidata = load("wikidata")
addressbook = load("addressbook")


# Stores opened by a test, closed after it.
stores = []


@pytest.fixture(autouse=True)
def close_stores():
    yield
    while stores:
        stores.pop().close()


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "model.sqlite")


def open_store(path):
    s = store.Store(path)
    stores.append(s)
    return s


def open_kb(path):
    return kb.KnowledgeBase(open_store(path))


def vocabulary(k):
    """A few properties and classes."""
    k.add_property("instance of", model.Datatype.Item, "P31")
    k.add_property("subclass of", model.Datatype.Item, "P279")
    k.add_property("employer", model.Datatype.Item, "P108")
    k.add_property("start time", model.Datatype.Time, "P580")
    k.add_property("stated in", model.Datatype.Item, "P248")
    k.add_property("phone number", model.Datatype.String, "P1329")
    k.add_property("height", model.Datatype.Quantity, "P2048")
    k.add_property("note", model.Datatype.Text, "note")
    k.add_item("human", "Q5")
    k.add_item("organization", "Q43229")
    k.add_item("business", "Q4830453").claim(
        "P279", model.EntityValue("Q43229"))
    k.add_item("metre", "Q11573")


def test_database_is_private(path):
    open_store(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_newer_schema_is_refused(path):
    db = sqlite3.connect(path)
    db.execute(f"pragma user_version = {store.VERSION + 1}")
    db.close()
    with pytest.raises(ValueError, match="newer"):
        store.Store(path)


def test_entity_round_trip(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        person = k.add_item("Ada", "Q7259", "mathematician")
        person.set_label("Ada", "fr")
        person.add_alias("Augusta Ada King")
        person.claim("P31", model.EntityValue("Q5"), model.Rank.Preferred)
        person.claim("P2048", model.QuantityValue("1.650", "Q11573", "1.6", "1.7"))
        person.claim("P108", model.EntityValue("Q43229")) \
            .add_qualifier("P580", model.TimeValue(
                -44, 3, 15, 12, 30, 5, 120, model.TimePrecision.Nanoseconds,
                before=2, after=1, calendar="julian")) \
            .add_reference(model.Reference().add("P248", model.EntityValue("Q5")))
        person.claim("P1329", model.StringValue("+61 7 3000 1234"))
        note = model.TextValue("hello")
        note.set_text("bonjour", "fr")
        person.claim("note", note)

    expected = model.document(person)
    again = open_kb(path)
    loaded = again.get("Q7259")
    assert loaded is not person
    assert model.document(loaded) == expected
    assert loaded.revision == 1
    assert str(loaded.get_statements("P2048")[0].get_value()) == \
        "1.650 [1.6, 1.7] Q11573"
    time = loaded.get_statements("P108")[0].qualifiers[0].value
    assert (time.year, time.nanosecond, time.calendar) == (-44, 120, "julian")
    assert again.get_property("P580").get_datatype() == model.Datatype.Time
    assert [s.get_id() for s in loaded.statements] == \
        [s.get_id() for s in person.statements]


def test_lookups(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        acme = k.add_item("Acme Widgets", "acme")
        acme.add_alias("Acme Pty Ltd")
        acme.claim("P31", model.EntityValue("Q4830453"))
        acme.claim("P1329", model.StringValue("+61 (7) 3000-1234"))
        ada = k.add_item("Ada", "ada")
        ada.claim("P108", model.EntityValue("acme")) \
            .add_reference(model.Reference().add("P248", model.EntityValue("Q5")))

    k = open_kb(path)
    assert [e.id for e in k.find_items("Acme Widgets")] == ["acme"]
    assert k.find_items("acme widgets") == []
    assert [e.id for e in k.search("acm pty")] == ["acme"]
    assert [e.id for e in k.search("Wid")] == ["acme"]
    assert [e.id for e in k.find_by_value("P1329", "+61 7 3000 1234")] == \
        ["acme"]
    assert [e.id for e in k.find_links("acme")] == ["ada"]
    assert [e.id for e in k.find_links("acme", "P31")] == []
    assert [e.id for e in k.find_links("Q5", "P248", "reference")] == ["ada"]
    assert [e.id for e in k.instances("Q43229")] == ["acme"]


def test_lookups_see_uncommitted_changes(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        item = k.add_item("old name", "x")
        assert k.find_items("old name") == [item]
        item.set_label("new name")
        assert k.find_items("old name") == []
        assert k.find_items("new name") == [item]


def test_in_memory_lookups_match():
    k = kb.KnowledgeBase()
    vocabulary(k)
    acme = k.add_item("Acme Widgets", "acme")
    acme.add_alias("Acme Pty Ltd")
    acme.claim("P31", model.EntityValue("Q4830453"))
    acme.claim("P1329", model.StringValue("+61 (7) 3000-1234"))
    assert k.search("acm pty") == [acme]
    assert k.find_by_value("P1329", "+61730001234") == [acme]
    assert k.instances("Q43229") == [acme]


def test_revisions_are_kept(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        item = k.add_item("one", "x")
    with k.transaction():
        item.set_label("two")
        item.set_label("three")
    with k.transaction():
        item.set_label("three")

    assert item.revision == 2
    db = sqlite3.connect(path)
    rows = db.execute("select revision, document from revisions "
                      "where id = 'x' order by revision").fetchall()
    db.close()
    assert [r for r, _ in rows] == [1, 2]
    assert '"one"' in rows[0][1] and '"three"' in rows[1][1]


def test_conflict(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        k.add_item("one", "x")

    first, second = open_kb(path), open_kb(path)
    a, b = first.get("x"), second.get("x")
    with first.transaction():
        a.set_label("first")
    with pytest.raises(model.ConflictError):
        with second.transaction():
            b.set_label("second")
    assert second.get("x").get_label() == "first"

    with pytest.raises(model.ConflictError):
        with second.transaction():
            second.register(model.Item("y"))
            first.add_item("other", "y")
            first.commit()


def test_rollback(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        item = k.add_item("kept", "x")

    with pytest.raises(RuntimeError):
        with k.transaction():
            item.set_label("lost")
            k.add_item("new", "y")
            assert k.find_items("lost") == [item]
            raise RuntimeError()
    assert k.get("x").get_label() == "kept"
    assert k.get("y") is None
    assert open_kb(path).get("y") is None


def test_merge_and_destroy(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        k.add_item("Robert Smith", "old").claim("P31", model.EntityValue("Q5"))
        k.add_item("Bob Smith", "new")
        k.add_item("gone", "gone")
        k.add_item("friend", "friend").claim("P108", model.EntityValue("old"))
    with k.transaction():
        k.merge("old", "new")
        k.destroy("gone")

    k = open_kb(path)
    new = k.get("old")
    assert new is not None and new.id == "new"
    assert k.get("old", follow=False).redirect == "new"
    assert new.get_aliases() == ["Robert Smith"]
    assert [e.id for e in k.instances("Q5")] == ["new"]
    assert k.find_items("Robert Smith") == []
    assert k.find_items("gone") == []
    assert k.get("gone").destroyed
    assert "gone" not in [e.id for e in k.entities()]
    assert [e.id for e in k.find_links("old")] == ["friend"]


CARD = """BEGIN:VCARD
VERSION:3.0
UID:{uid}
FN:{name}
N:{family};{given};;;
ORG:Acme Widgets;Sales
TEL;TYPE=WORK:+61 7 3000 1234
ADR;TYPE=HOME:;;1 Example St;Brisbane;QLD;4000;Australia
X-ABRELATEDNAMES;TYPE=pref:Sam
END:VCARD
"""


def book(*people):
    return list(vcard.parse("".join(CARD.format(uid=uid, name=f"{g} {f}",
                                           given=g, family=f)
                               for uid, g, f in people)))


def import_book(path, cards):
    s = open_store(path)
    k = kb.KnowledgeBase(s)
    with k.transaction():
        importer = addressbook.Importer(k, wikidata.load(k))
        importer.read(cards)
    return k, s


def person(k, uid):
    return k.get(model.key_id(f"vcard:{uid}"))


def test_reimport(path):
    cards = book(("u1", "Ann", "Example"), ("u2", "Ben", "Example"))
    k, s = import_book(path, cards)
    assert s.writes > 0
    statements = {e.id: model.document(e) for e in k.entities()}

    k, s = import_book(path, cards)
    assert s.writes == 0
    assert {e.id: model.document(e) for e in k.entities()} == statements

    # A statement made by hand is kept by a re-import.
    with k.transaction():
        person(k, "u1").claim("P1329", model.StringValue("+61 400 000 000"))
    k, s = import_book(path, cards)
    assert s.writes == 0
    assert len(k.find_by_value("P1329", "+61400000000")) == 1

    # A card that's gone loses its statements, and a person with none
    # left is destroyed: the one with a statement made by hand isn't.
    k, s = import_book(path, book(("u3", "Cat", "Example")))
    ann, ben = person(k, "u1"), person(k, "u2")
    assert [st.get_property_id() for st in ann.statements] == ["P1329"]
    assert not ann.destroyed
    assert ben.destroyed and not ben.statements
    assert person(k, "u3").get_label() == "Cat Example"
    assert len(k.find_items("Acme Widgets")) == 1

    # Every check passes, references included.
    for e in k.entities():
        for st in e.statements:
            k.check(st)


def test_vocabulary_reload(path):
    k = open_kb(path)
    with k.transaction():
        wikidata.load(k)
    k = open_kb(path)
    with k.transaction():
        wd = wikidata.load(k)
    assert k.store.writes == 0
    assert k.get(wd.birthday).get_label() == "birthday"
    australia = k.get("Q408")
    assert [s.get_property_id() for s in australia.statements] == ["P31"]
    assert australia.statements[0].references[0].snaks[0].value.get_id() == \
        "Q2013"
