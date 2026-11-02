# darqos
# Copyright (C) 2026 David Arnold

# Tests for queries, services/kb/query.py: their checks, and their
# answers, from a KnowledgeBase in memory and from one in a Store, which
# must be the same, on a made-up graph of people, organisations and
# places; and the store's upgrade from schema version 1.

import importlib
import json
import pathlib
import sqlite3
import sys

import pytest

import darq.kb as model
from darq.kb import ANY, EntityValue, Query, Rank, Reference, Repeat, StringValue, TimeValue


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
query = load("query")


# People, organisations and places.  Queensland (Q36074) has Brisbane,
# which has an address; New South Wales, Sydney, which has another.
# Acme's headquarters are at the first, and Globex's at the second.
def graph(k):
    k.add_property("instance of", model.Datatype.Item, "P31")
    k.add_property("subclass of", model.Datatype.Item, "P279")
    k.add_property("employer", model.Datatype.Item, "P108")
    k.add_property("headquarters location", model.Datatype.Item, "P159")
    k.add_property("located in", model.Datatype.Item, "P131")
    k.add_property("residence", model.Datatype.Item, "P551")
    k.add_property("start time", model.Datatype.Time, "P580")
    k.add_property("end time", model.Datatype.Time, "P582")
    k.add_property("stated in", model.Datatype.Item, "P248")
    k.add_property("phone number", model.Datatype.String, "P1329")
    k.add_property("series ordinal", model.Datatype.String, "P1545")

    k.add_item("human", "Q5")
    k.add_item("employee", "employee").claim("P279", EntityValue("Q5"))
    k.add_item("address book", "book")
    k.add_item("other book", "other")

    k.add_item("Queensland", "Q36074")
    k.add_item("New South Wales", "Q3224")
    k.add_item("Brisbane", "Q34932").claim("P131", EntityValue("Q36074"))
    k.add_item("Sydney", "Q3130").claim("P131", EntityValue("Q3224"))
    k.add_item("1 Queen St", "home").claim("P131", EntityValue("Q34932"))
    k.add_item("2 George St", "work").claim("P131", EntityValue("Q3130"))
    k.add_item("Acme", "acme").claim("P159", EntityValue("home"))
    k.add_item("Globex", "globex").claim("P159", EntityValue("work"))
    k.add_item("Zed", "zed")

    alice = k.add_item("Alice", "alice")
    alice.claim("P31", EntityValue("Q5"))
    alice.claim("P108", EntityValue("acme"))
    alice.claim("P551", EntityValue("home")).add_qualifier("P582", TimeValue.parse("2020"))
    alice.claim("P551", EntityValue("work")).add_qualifier("P580", TimeValue.parse("2020"))
    alice.claim("P1329", StringValue("+61 7 3000 1234")) \
        .add_reference(Reference().add("P248", EntityValue("book")))

    bob = k.add_item("Bob", "bob")
    bob.claim("P31", EntityValue("employee"))
    bob.claim("P108", EntityValue("globex"))
    bob.claim("P551", EntityValue("home"))
    bob.claim("P1329", StringValue("+61 7 3000 5678")) \
        .add_reference(Reference().add("P248", EntityValue("other")))
    bob.claim("P1329", StringValue("work")).add_qualifier("P1545", StringValue("Work"))

    dave = k.add_item("Dave", "dave")
    dave.claim("P31", EntityValue("Q5"))
    dave.claim("P108", EntityValue("acme"), Rank.Deprecated)

    erin = k.add_item("Erin", "erin")
    erin.claim("P31", EntityValue("Q5"))
    erin.claim("P108", EntityValue("acme"))
    k.destroy("erin")

    gina = k.add_item("Gina", "gina")
    gina.claim("P31", EntityValue("Q5"))
    gina.claim("P108", EntityValue("zed"))
    k.destroy("zed")


@pytest.fixture
def memory():
    k = kb.KnowledgeBase()
    graph(k)
    return k


@pytest.fixture
def stored(tmp_path):
    s = store.Store(str(tmp_path / "kb.sqlite"))
    k = kb.KnowledgeBase(s)
    with k.transaction():
        graph(k)
    yield k
    s.close()


@pytest.fixture
def both(memory, stored):
    """Answer a query from both, checking they agree."""
    def answer(q: Query, after=(), limit=None):
        expected = memory.query(q, after, limit)
        assert stored.query(q, after, limit) == expected
        return expected[1]
    return answer


def test_one_pattern(both):
    q = Query()
    p = q.variable()
    q.match(p, "P108", "acme")
    assert both(q) == [("alice",)]


def test_join(both):
    q = Query()
    p, o, a = q.variables(3)
    q.match(p, "P108", o).match(o, "P159", a)
    assert both(q) == [("alice", "acme", "home"), ("bob", "globex", "work")]
    assert both(q.select(a, p)) == [("home", "alice"), ("work", "bob")]


def test_repeat(both):
    # Employed by an organisation in Queensland.
    q = Query()
    p, o, a = q.variables(3)
    q.match(p, "P108", o).match(o, "P159", a).match(a, "P131", "Q36074", repeat=Repeat.OneOrMore)
    assert both(q.select(p)) == [("alice",)]

    # What's in Queensland, perhaps indirectly, and Queensland itself.
    q = Query()
    x = q.variable()
    q.match(x, "P131", "Q36074", repeat=Repeat.OneOrMore)
    assert both(q) == [("Q34932",), ("home",)]
    q = Query()
    x = q.variable()
    q.match(x, "P131", "Q36074", repeat=Repeat.ZeroOrMore)
    assert both(q) == [("Q34932",), ("Q36074",), ("home",)]

    # What an address is in, forwards.
    q = Query()
    x = q.variable()
    q.match("home", "P131", x, repeat=Repeat.OneOrMore)
    assert both(q) == [("Q34932",), ("Q36074",)]

    # Both ends constant.
    q = Query()
    x = q.variable()
    q.match(x, "P159", "home").match("home", "P131", "Q36074", repeat=Repeat.OneOrMore)
    assert both(q) == [("acme",)]
    q = Query()
    x = q.variable()
    q.match(x, "P159", "home").match("home", "P131", "Q3224", repeat=Repeat.OneOrMore)
    assert both(q) == []


def test_subclasses(both):
    q = Query()
    p, c = q.variables(2)
    q.match(p, "P31", c).match(c, "P279", "Q5", repeat=Repeat.ZeroOrMore).select(p)
    assert both(q) == [("alice",), ("bob",), ("dave",), ("gina",)]


def test_statements_and_negation(both, stored):
    # Who lives at an address now: with no end time.
    q = Query()
    p, a, s = q.variables(3)
    q.match(p, "P551", a, statement=s).exclude(p, "P582", ANY, role="qualifier", statement=s).select(p, a)
    assert both(q) == [("alice", "work"), ("bob", "home")]

    # And with one.
    q = Query()
    p, a, s = q.variables(3)
    q.match(p, "P551", a, statement=s).match(p, "P582", ANY, role="qualifier", statement=s).select(p, a)
    assert both(q) == [("alice", "home")]

    # A statement's identifier: each KB gives its own.
    alice = stored.get("alice")
    assert stored.query(q.select(s))[1] == [(alice.get_statements("P551")[0].id,)]


def test_negation_with_its_own_variable(both):
    # People employed by no one: Dave's employer is deprecated, and
    # Gina's destroyed.
    q = Query()
    p, o = q.variables(2)
    q.match(p, "P31", "Q5").exclude(p, "P108", o)
    assert both(q.select(p)) == [("dave",), ("gina",)]


def test_references(both):
    q = Query()
    e, v, s = q.variables(3)
    q.match(e, "P1329", v, statement=s).match(e, "P248", "book", role="reference", statement=s)
    assert both(q.select(e, v)) == [("alice", "+61 7 3000 1234")]


def test_strings(both):
    # Normalised: a phone number's digits, case ignored.
    q = Query()
    e = q.variable()
    q.match(e, "P1329", StringValue("+61730001234"))
    assert both(q) == [("alice",)]

    # A qualifier's string, matched without case, and returned as
    # written.
    q = Query()
    e, v = q.variables(2)
    q.match(e, "P1545", StringValue("work"), role="qualifier").match(e, "P1545", v, role="qualifier")
    assert both(q.select(e, v)) == [("bob", "Work")]

    # Joined on a string: values two statements share.
    q = Query()
    e, v = q.variables(2)
    q.match(e, "P1329", v).match(e, "P1545", v, role="qualifier")
    assert both(q.select(e, v)) == [("bob", "work")]


def test_ranks(both):
    q = Query()
    p = q.variable()
    q.match(p, "P108", "acme")
    assert both(q) == [("alice",)]
    q.deprecated = True
    assert both(q) == [("alice",), ("dave",)]


def test_destroyed_entities_are_left_out(both):
    # Erin is destroyed, and so is Zed, which Gina links to.
    q = Query()
    p, o = q.variables(2)
    q.match(p, "P108", o)
    assert both(q) == [("alice", "acme"), ("bob", "globex")]
    q = Query()
    p = q.variable()
    q.match(p, "P108", "zed")
    assert both(q) == [("gina",)]
    q = Query()
    p = q.variable()
    q.match("erin", "P31", p)
    assert both(q) == []


def test_paging(both):
    q = Query()
    p, c = q.variables(2)
    q.match(p, "P31", c)
    rows = both(q)
    assert rows == [("alice", "Q5"), ("bob", "employee"), ("dave", "Q5"), ("gina", "Q5")]
    assert both(q, limit=2) == rows[:2]
    assert both(q, after=rows[1], limit=1) == rows[2:3]
    assert both(q, after=rows[-1]) == []


def test_any_value(both):
    q = Query()
    p = q.variable()
    q.match(p, "P580", ANY, role="qualifier")
    assert both(q) == [("alice",)]


@pytest.mark.parametrize("build, why", [
    (lambda q, a, b: q.exclude(a, "P31", "Q5"), "needs a pattern"),
    (lambda q, a, b: q.match(a, "P999", b), "no such property"),
    (lambda q, a, b: q.match(a, "P580", b, role="qualifier"), "can't be a variable"),
    (lambda q, a, b: q.match(a, "P31", b).match(b, "P1329", a), "both"),
    (lambda q, a, b: q.match(a, "P31", "Q5").select(b), "isn't in a pattern"),
    (lambda q, a, b: q.match(a, "P131", b, repeat=Repeat.OneOrMore), "constant at one end"),
    (lambda q, a, b: q.match(a, "P1329", StringValue("x"), repeat=Repeat.OneOrMore), "can be repeated"),
    (lambda q, a, b: q.match(a, "P31", "Q5").exclude(a, "P108", b).exclude(b, "P159", ANY),
     "two negated patterns"),
])
def test_invalid(memory, build, why):
    q = Query()
    a, b = q.variables(2)
    build(q, a, b)
    with pytest.raises(query.QueryError, match=why):
        memory.query(q)


def test_too_many_patterns(memory):
    q = Query()
    p = q.variable()
    for _ in range(query.MAX_PATTERNS + 1):
        q.match(p, "P31", "Q5")
    with pytest.raises(query.QueryError, match="patterns"):
        memory.query(q)


def test_after_must_fit(memory):
    q = Query()
    p = q.variable()
    q.match(p, "P31", "Q5")
    with pytest.raises(query.QueryError, match="after"):
        memory.query(q, after=("a", "b"))


def test_too_costly(memory, stored):
    q = Query()
    a, b, c, d = q.variables(4)
    q.match(a, "P31", ANY).match(b, "P31", ANY).match(c, "P31", ANY).match(d, "P31", ANY)
    with pytest.raises(query.TooCostly):
        stored.query(q, time_limit=0)
    with pytest.raises(query.TooCostly):
        memory.query(q, time_limit=0)


def test_wire_round_trip():
    q = Query(deprecated=True)
    p, a, s = q.variables(3)
    q.match(p, "P551", a, statement=s).exclude(p, "P582", ANY, "qualifier", s) \
        .match(a, "P131", "Q36074", repeat=Repeat.ZeroOrMore) \
        .match(p, "P1329", StringValue("x")).select(p, a)
    patterns, select = model.query_to_wire(q)
    again = model.query_from_wire(patterns, select, True)
    assert repr(again.patterns) == repr(q.patterns)
    assert again.selected == q.selected
    assert again.deprecated
    assert again.count == 3


def test_upgrade_from_version_1(tmp_path):
    path = str(tmp_path / "kb.sqlite")
    s = store.Store(path)
    k = kb.KnowledgeBase(s)
    with k.transaction():
        graph(k)
    s.close()

    # Make it as version 1 had it: links and strings without ranks or
    # roles, and no snaks.
    db = sqlite3.connect(path)
    db.executescript("""
        drop table links; drop table strings; drop table snaks;
        create table links (entity_id text not null, statement_id text not null,
            property_id text not null, target_id text not null, role text not null);
        create table strings (entity_id text not null, statement_id text not null,
            property_id text not null, value text not null);
        pragma user_version = 1;
    """)
    documents = db.execute("select count(*) from entities").fetchone()[0]
    db.close()

    s = store.Store(path)
    try:
        assert s.db.execute("pragma user_version").fetchone()[0] == store.VERSION
        assert s.db.execute("select count(distinct entity_id) from snaks").fetchone()[0] > 0
        assert s.db.execute("select count(*) from entities").fetchone()[0] == documents
        k = kb.KnowledgeBase(s)
        q = Query()
        p = q.variable()
        q.match(p, "P108", "acme")
        assert k.query(q)[1] == [("alice",)]
        q.deprecated = True
        assert k.query(q)[1] == [("alice",), ("dave",)]
        assert [e.id for e in k.find_by_value("P1329", "work")] == ["bob"]
        assert json.loads(s.db.execute("select document from entities where id = 'bob'").fetchone()[0])
    finally:
        s.close()
