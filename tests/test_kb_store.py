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


def fts_rows(s, word):
    """The entities whose full-text rows match a word, deleted or not."""
    return sorted(r[0] for r in s.db.execute(
        "select entity_id from labels_fts where labels_fts match ?", (f'"{word}"*',)))


def fts_intact(s):
    """Whether the full-text index agrees with the labels."""
    s.db.execute("insert into labels_fts (labels_fts, rank) values ('integrity-check', 1)")
    return True


def test_search_follows_changes(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        k.add_item("Robert Smith", "old")
        k.add_item("Bob Smith", "new")
        k.add_item("Widget Corp", "widgets").add_alias("Gadgets")
        k.add_item("Ephemeral", "gone")
    with k.transaction():
        k.get("widgets").set_label("Sprocket Corp")
    with k.transaction():
        k.merge("old", "new")
        k.destroy("gone")

    k = open_kb(path)
    assert [e.id for e in k.search("sprock")] == ["widgets"]
    assert k.search("widget") == []
    assert [e.id for e in k.search("gadg")] == ["widgets"]
    assert [e.id for e in k.search("robert")] == ["new"]
    assert k.search("ephem") == []
    s = k.store
    # The old label's full-text row went with it.  A merged entity keeps
    # its rows, as its labels row does, and search leaves it out.
    assert fts_rows(s, "widget") == []
    assert fts_rows(s, "smith") == ["new", "new", "old"]
    assert fts_intact(s)


def test_writes_find_full_text_rows_by_index(path):
    # An entity's full-text rows are deleted, with its labels, through an
    # index, not by scanning labels_fts: as it was, each write scanned
    # the whole table, and slowed as the knowledge base grew.
    s = open_store(path)
    plan = s.db.execute("explain query plan delete from labels where entity_id = ?", ("x",)).fetchall()
    assert any("labels_by_entity" in row[-1] for row in plan), plan
    assert not any("labels_fts" in row[-1] for row in plan), plan


def test_upgrade_from_version_2(path):
    k = open_kb(path)
    with k.transaction():
        vocabulary(k)
        k.add_item("Acme Pty Ltd", "acme").add_alias("Widgets Inc")
        k.add_item("Ada Lovelace", "ada")
    before = (k.search("acm"), k.search("wid"), k.search("lovel"))
    before = [[e.id for e in found] for found in before]
    k.store.close()
    stores.clear()

    # Make it as version 2 had it: labels without an id, and the
    # full-text rows kept apart, by entity_id.
    db = sqlite3.connect(path)
    db.executescript("""
        drop table labels_fts;
        create table old_labels as select entity_id, language, role, text, folded from labels;
        drop table labels;
        alter table old_labels rename to labels;
        create index labels_by_text on labels (language, text);
        create index labels_by_entity on labels (entity_id);
        create virtual table labels_fts using fts5 (entity_id unindexed, language unindexed, text,
            tokenize = 'unicode61 remove_diacritics 2');
        insert into labels_fts select entity_id, language, text from labels;
        pragma user_version = 2;
    """)
    labels = db.execute("select count(*) from labels").fetchone()[0]
    db.close()

    k = open_kb(path)
    s = k.store
    assert s.db.execute("pragma user_version").fetchone()[0] == store.VERSION == 4
    assert s.db.execute("select count(*) from labels").fetchone()[0] == labels
    after = [[e.id for e in k.search(text)] for text in ("acm", "wid", "lovel")]
    assert after == before == [["acme"], ["acme"], ["ada"]]
    assert fts_intact(s)

    # And it's kept in step from then on.
    with k.transaction():
        k.get("ada").set_label("Augusta Ada King")
    assert k.search("lovel") == []
    assert [e.id for e in k.search("augus")] == ["ada"]
    assert fts_intact(s)


def walk(lookup, size):
    """Every batch of a lookup, of at most size, joined."""
    found, after = [], ""
    while True:
        batch = lookup(after, size)
        assert len(batch) <= size
        if not batch:
            return found
        found += batch
        after = batch[-1]


def classes_and_links(k):
    """Instances of a class and two of its subclasses, some of two, and
    links to a hub by two properties, some merged or destroyed."""
    vocabulary(k)
    k.add_property("knows", model.Datatype.Item, "knows")
    k.add_item("company", "company").claim("P279", model.EntityValue("Q4830453"))
    k.add_item("hub", "hub")
    for n in range(30):
        e = k.add_item(f"Thing {n}", f"e{n:02d}")
        cls = ("Q43229", "Q4830453", "company")[n % 3]
        e.claim("P31", model.EntityValue(cls))
        if n % 4 == 0:
            # An instance of two of the classes, twice over.
            e.claim("P31", model.EntityValue("company"))
            e.claim("P31", model.EntityValue("Q43229"))
        e.claim("P108" if n % 2 else "knows", model.EntityValue("hub"))
        if n % 5 == 0:
            e.claim("knows", model.EntityValue("hub"))


def test_batches_match_whole_lookups(path):
    k = open_kb(path)
    with k.transaction():
        classes_and_links(k)
    with k.transaction():
        k.destroy("e07")
        k.merge("e11", "e12")

    memory = kb.KnowledgeBase()
    classes_and_links(memory)
    memory.destroy("e07")
    memory.merge("e11", "e12")

    instances = [e.id for e in k.instances("Q43229")]
    assert len(instances) == len(set(instances)) == 28
    for size in (1, 2, 7, 100):
        assert walk(lambda after, n: k.instance_ids("Q43229", after, n), size) == instances
        assert walk(lambda after, n: memory.instance_ids("Q43229", after, n), size) == instances
        for prop in ("P108", "knows", None):
            links = [e.id for e in k.find_links("hub", prop)]
            assert walk(lambda after, n: k.link_ids("hub", prop, "main", after, n), size) == links
            assert walk(lambda after, n: memory.link_ids("hub", prop, "main", after, n), size) == links
    assert "e07" not in instances and "e11" not in instances
    assert k.instances_among(["e00", "e01", "e07", "nothing"], "Q4830453") == ["e00", "e01"]
    assert memory.instances_among(["e00", "e01", "e07", "nothing"], "Q4830453") == ["e00", "e01"]


def test_batches_read_in_order(path):
    # A batch of links by a property, or of a class's instances, is read
    # in order from an index ending in entity_id, and stops at its size,
    # rather than sorting every match first: no temporary b-tree.  (Both
    # link indexes serve links by a property: the planner may use either.)
    s = open_store(path)
    live = store.LIVE
    links = ("select l.entity_id from links l join entities e on e.id = l.entity_id "
             "where l.target_id = ? and l.property_id = ? and l.role = ? and l.type = ? and l.entity_id > ? "
             f"and {live} group by l.entity_id order by l.entity_id limit ?")
    instances = ("select l.entity_id from links l join entities e on e.id = l.entity_id "
                 "where l.property_id = ? and l.target_id = ? and l.role = 'main' and l.entity_id > ? "
                 f"and {live} group by l.entity_id order by l.entity_id limit ?")
    for sql, args in ((links, ("a", "b", "c", 1, "", 10)), (instances, ("a", "b", "", 10))):
        plan = [row[-1] for row in s.db.execute(f"explain query plan {sql}", args)]
        assert any("links_by_" in step and "entity_id>?" in step for step in plan), plan
        assert not any("TEMP B-TREE" in step for step in plan), plan


def analysed(s):
    """The tables the query planner has statistics for."""
    return {row[0] for row in s.db.execute("select tbl from sqlite_stat1")}


def many_links(k, n):
    """A hub, linked to by most of n entities, and a few people with an
    employer of their own, as a query's two patterns would find them."""
    vocabulary(k)
    k.add_item("hub", "hub")
    k.add_item("org", "org")
    for i in range(n):
        k.add_item(f"Thing {i}", f"e{i}").claim("P108", model.EntityValue("hub" if i % 50 else "org"))


def test_statistics_are_gathered_on_opening(path):
    k = open_kb(path)
    with k.transaction():
        many_links(k, 1000)
    assert "links" not in analysed(k.store)
    k.store.close()
    stores.clear()

    s = open_store(path)
    assert {"links", "labels"} <= analysed(s)


def test_statistics_are_refreshed_as_it_grows(path, monkeypatch):
    # A knowledge base that grows after opening isn't left with what it
    # knew when it was small.
    monkeypatch.setattr(store, "OPTIMIZE_WRITES", 100)
    s = open_store(path)
    k = kb.KnowledgeBase(s)
    assert "links" not in analysed(s)
    with k.transaction():
        vocabulary(k)
    for i in range(300):
        with k.transaction():
            k.add_item(f"Thing {i}", f"e{i}").claim("P31", model.EntityValue("Q5"))
    assert "links" in analysed(s)
    assert s.writes - s.optimized < 100


def links_world(k):
    """Five entities linking to a hub, two of them messages."""
    k.add_property("link", model.Datatype.Item, "link")
    k.add_property("DarqOS type", model.Datatype.String, model.DARQOS_TYPE)
    k.add_item("hub", "hub")
    for name in ("a", "b", "c", "d", "e"):
        item = k.add_item(name, name)
        item.claim("link", model.EntityValue("hub"))
        if name in ("b", "d"):
            item.claim(model.DARQOS_TYPE, model.StringValue("darq.message"))


@pytest.fixture(params=[True, False], ids=["stored", "memory"])
def links_kb(request, tmp_path):
    # The store's SQL and the in-memory scan agree.
    return kb.KnowledgeBase(open_store(str(tmp_path / "links.sqlite"))) if request.param else kb.KnowledgeBase()


def test_link_ids_reverse_and_type(links_kb):
    k = links_kb
    with k.transaction():
        links_world(k)

    assert k.link_ids("hub", "link") == ["a", "b", "c", "d", "e"]
    assert k.link_ids("hub", "link", reverse=True) == ["e", "d", "c", "b", "a"]

    # Backwards in batches: after is the last one returned.
    assert k.link_ids("hub", "link", limit=2, reverse=True) == ["e", "d"]
    assert k.link_ids("hub", "link", after="d", limit=2, reverse=True) == ["c", "b"]
    assert k.link_ids("hub", "link", after="b", limit=2, reverse=True) == ["a"]
    assert k.link_ids("hub", "link", after="a", limit=2, reverse=True) == []

    assert k.link_ids("hub", "link", type_id="darq.message") == ["b", "d"]
    assert k.link_ids("hub", None, type_id="darq.message") == ["b", "d"]
    assert k.link_ids("hub", "link", type_id="darq.entity") == ["a", "c", "e"]
    assert k.link_ids("hub", "link", type_id="darq.person") == []
    assert k.link_ids("hub", "link", limit=1, type_id="darq.message") == ["b"]
    assert k.link_ids("hub", "link", reverse=True, limit=1, type_id="darq.message") == ["d"]

    # Types are merged in order.
    assert k.link_ids("hub", "link", limit=3) == ["a", "b", "c"]
    assert k.link_ids("hub", "link", after="b", limit=2) == ["c", "d"]


def test_link_counts(links_kb):
    k = links_kb
    with k.transaction():
        links_world(k)
    assert k.link_counts("hub") == [("link", "main", "darq.entity", 3), ("link", "main", "darq.message", 2)]
    assert k.link_counts("a") == []


def test_link_counts_are_of_entities_not_links(links_kb):
    # Every statement's reference to a source is one link: it's one entity.
    k = links_kb
    with k.transaction():
        k.add_property("link", model.Datatype.Item, "link")
        k.add_property("stated in", model.Datatype.Item, model.STATED_IN)
        k.add_item("source", "source")
        k.add_item("hub", "hub")
        k.add_item("other", "other")
        item = k.add_item("a", "a")
        for target in ("hub", "other"):
            item.claim("link", model.EntityValue(target)).add_reference(
                model.Reference().add(model.STATED_IN, model.EntityValue("source")))
    assert k.link_counts("source") == [(model.STATED_IN, "reference", "darq.entity", 1)]


def test_link_counts_follow_changes(links_kb):
    k = links_kb
    with k.transaction():
        links_world(k)

    def counts():
        return {t: n for _, _, t, n in k.link_counts("hub")}

    # Rewriting it changes nothing.
    with k.transaction():
        k.get("a").set_label("Another")
    assert counts() == {"darq.entity": 3, "darq.message": 2}

    # A statement removed.
    with k.transaction():
        a = k.get("a")
        a.remove_statement(a.get_statements("link")[0])
    assert counts() == {"darq.entity": 2, "darq.message": 2}

    # Destroyed.
    with k.transaction():
        k.destroy("b")
    assert counts() == {"darq.entity": 2, "darq.message": 1}
    assert k.link_ids("hub", "link", type_id="darq.message") == ["d"]

    # Merged: its statements are the other's now, with its type.
    with k.transaction():
        k.merge("c", "d")
    assert counts() == {"darq.entity": 1, "darq.message": 1}
    assert k.link_ids("hub", "link") == ["d", "e"]

    # Its type changed.
    with k.transaction():
        k.get("e").claim(model.DARQOS_TYPE, model.StringValue("darq.person"))
    assert counts() == {"darq.message": 1, "darq.person": 1}


def test_link_counts_roll_back(tmp_path):
    k = kb.KnowledgeBase(open_store(str(tmp_path / "links.sqlite")))
    with k.transaction():
        links_world(k)
    with pytest.raises(RuntimeError):
        with k.transaction():
            k.get("a").claim(model.DARQOS_TYPE, model.StringValue("darq.place"))
            raise RuntimeError
    assert {t: n for _, _, t, n in k.link_counts("hub")} == {"darq.entity": 3, "darq.message": 2}

    # A type that went with the transaction can be used again.
    with k.transaction():
        k.get("a").claim(model.DARQOS_TYPE, model.StringValue("darq.place"))
    assert k.link_ids("hub", "link", type_id="darq.place") == ["a"]


def test_batch_from_the_end_uses_the_index(tmp_path):
    # It costs its own size, without sorting them all.
    k = kb.KnowledgeBase(open_store(str(tmp_path / "links.sqlite")))
    with k.transaction():
        links_world(k)
    plan = k.store.db.execute(
        "explain query plan select l.entity_id from links l join entities e on e.id = l.entity_id "
        "where l.target_id = 'hub' and l.property_id = 'link' and l.role = 'main' and l.type = 1 "
        "and e.destroyed = 0 and e.redirect is null group by l.entity_id order by l.entity_id desc limit 2"
    ).fetchall()
    assert not any("TEMP B-TREE" in row[-1] for row in plan)
