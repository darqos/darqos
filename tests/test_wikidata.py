# darqos
# Copyright (C) 2026 David Arnold

# Tests for Wikidata (see doc/wikidata.rst): what Wikidata says, in the
# shapes it says it in; making an entity Wikidata's, and giving it its
# facts, with a KB service and KB type implementation in this process (see
# test_kb_type.py); languages; the entity lens's W; the matching tool; and
# that a request says who's asking.

import datetime
import importlib.util
import json
import pathlib
import sys
import types as python_types
import typing
import urllib.parse

import pytest

import darq.book
import darq.kb as model
import darq.wikidata as wd

from test_http_service import Handler, Web, web  # noqa: F401
from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_vcard_import import Implementation


TODAY = datetime.date(2026, 11, 8)


def value(statement: typing.Any) -> typing.Any:
    return statement.get_value()
AGENT = "DarqOS/2.2 (David Arnold; d+wikidata@pobox.com)"


def time(text: str, precision: int, calendar: str = "http://www.wikidata.org/entity/Q1985727") -> dict:
    return {"time": text, "timezone": 0, "before": 0, "after": 0, "precision": precision, "calendarmodel": calendar}


def claim(property_id: str, value: typing.Any, kind: str, rank: str = "normal") -> dict:
    data = {"value": {"entity-type": "item", "id": value}, "type": "wikibase-entityid"} if kind == "item" else \
        {"value": value, "type": kind}
    return {"mainsnak": {"snaktype": "value", "property": property_id, "datavalue": data}, "type": "statement",
            "rank": rank}


# As Wikidata answered, cut down.
CAREY = {"entities": {"Q313254": {
    "id": "Q313254",
    "labels": {"en": {"language": "en", "value": "Peter Carey"}},
    "descriptions": {"en": {"language": "en", "value": "Australian novelist (born 1943)"}},
    "aliases": {"en": [{"language": "en", "value": "Peter Philip Carey"}]},
    "claims": {
        "P31": [claim("P31", "Q5", "item")],
        "P569": [claim("P569", time("+1943-05-07T00:00:00Z", 11), "time")],
        "P19": [claim("P19", "Q2878215", "item")],
        "P27": [claim("P27", "Q408", "item")],
        "P106": [claim("P106", "Q36180", "item"), claim("P106", "Q6625963", "item", "deprecated")],
        "P1412": [claim("P1412", "Q1860", "item")],
    },
    "sitelinks": {"enwiki": {"site": "enwiki", "title": "Peter Carey (novelist)", "badges": [],
                             "url": "https://en.wikipedia.org/wiki/Peter_Carey_(novelist)"}},
}}}

PLACES = {"entities": {
    "Q2878215": {"id": "Q2878215", "labels": {"en": {"value": "Bacchus Marsh"}},
                 "descriptions": {"en": {"value": "town in Victoria, Australia"}},
                 "claims": {"P31": [claim("P31", "Q3957", "item")]}},
    "Q408": {"id": "Q408", "labels": {"en": {"value": "Australia"}}, "descriptions": {"en": {"value": "country"}},
             "claims": {}},
    "Q36180": {"id": "Q36180", "labels": {"en": {"value": "writer"}}, "claims": {}},
}}

SEARCH = {"searchinfo": {"search": "Peter Carey"}, "search": [
    {"id": "Q313254", "title": "Q313254", "label": "Peter Carey", "description": "Australian novelist (born 1943)",
     "match": {"type": "label", "language": "en", "text": "Peter Carey"}},
    {"id": "Q112498425", "title": "Q112498425", "label": "Peter Carey"},
    {"id": "Q7173178", "label": "Peter Carey", "description": "British historian"},
], "success": 1}

LANGUAGES = {"head": {"vars": ["language", "code", "label"]}, "results": {"bindings": [
    {"language": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1860"},
     "code": {"type": "literal", "value": "en"}, "label": {"xml:lang": "en", "type": "literal", "value": "English"}},
    {"language": {"type": "uri", "value": "http://www.wikidata.org/entity/Q397"},
     "code": {"type": "literal", "value": "la"}, "label": {"xml:lang": "en", "type": "literal", "value": "Latin"}},
]}}


class Wikidata:
    """Answers as Wikidata would, and remembers what was asked."""

    def __init__(self):
        self.asked: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: list[tuple[str, str]]) -> bytes:
        self.asked.append((url, dict(headers)))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if url.startswith(wd.QUERY):
            return json.dumps(LANGUAGES).encode()
        if query.get("action") == ["wbsearchentities"]:
            return json.dumps(SEARCH).encode()
        ids = query["ids"][0].split("|")
        if ids == ["Q313254"]:
            return json.dumps(CAREY).encode()
        return json.dumps({"entities": {i: PLACES["entities"].get(i, {"id": i, "missing": ""}) for i in ids}}).encode()


################################################################
# What Wikidata says
################################################################

def test_search():
    found = wd.parse_search(json.dumps(SEARCH).encode())
    assert [(c.id, c.description) for c in found] == [("Q313254", "Australian novelist (born 1943)"),
                                                      ("Q112498425", ""), ("Q7173178", "British historian")]
    assert found[0].describe() == "Peter Carey — Australian novelist (born 1943)"
    assert found[1].describe() == "Peter Carey"


def test_facts_of_a_person():
    got = wd.parse_facts(json.dumps(CAREY).encode(), "Q313254", wd.PERSON, TODAY)
    assert (got.label, got.description, got.aliases) == ("Peter Carey", "Australian novelist (born 1943)",
                                                         ["Peter Philip Carey"])
    by = {s.get_property_id(): value(s) for s in got.statements}
    born = by["P569"]
    assert (born.year, born.month, born.day, born.precision) == (1943, 5, 7, model.TimePrecision.Days)
    assert by["P19"].entity_id == "Q2878215" and by["P27"].entity_id == "Q408"
    assert by[wd.DESCRIBED_AT].value == "https://en.wikipedia.org/wiki/Peter_Carey_(novelist)"
    assert "P1412" not in by
    assert set(got.refers_to()) == {"Q2878215", "Q408", "Q36180"}


def test_deprecated_values_arent_taken():
    got = wd.parse_facts(json.dumps(CAREY).encode(), "Q313254", wd.PERSON, TODAY)
    assert [value(s).entity_id for s in got.statements if s.get_property_id() == "P106"] == ["Q36180"]


def test_preferred_values_are_taken_alone():
    statements = [claim("P27", "Q408", "item"), claim("P27", "Q145", "item", "preferred")]
    assert [s["mainsnak"]["datavalue"]["value"]["id"] for s in wd.best(statements)] == ["Q145"]


def test_facts_are_cited():
    got = wd.parse_facts(json.dumps(CAREY).encode(), "Q313254", wd.PERSON, TODAY)
    [reference] = got.statements[0].references
    snaks: dict[str, typing.Any] = {s.property_id: s.value for s in reference.snaks}
    assert snaks[model.STATED_IN].entity_id == wd.WIKIDATA and snaks[wd.RETRIEVED].year == 2026


@pytest.mark.parametrize("value, wanted", [
    (time("+1943-05-07T00:00:00Z", 11), (1943, 5, 7, "gregorian")),
    (time("+1869-00-00T00:00:00Z", 9), (1869, None, None, "gregorian")),
    (time("+1600-03-00T00:00:00Z", 10, "http://www.wikidata.org/entity/Q1985786"), (1600, 3, None, "julian")),
    (time("-0044-03-15T00:00:00Z", 11), (-44, 3, 15, "gregorian")),
])
def test_times(value, wanted):
    made = wd.time_of(value)
    assert made is not None and (made.year, made.month, made.day, made.calendar) == wanted


def test_a_time_that_cant_be_read():
    assert wd.time_of({"time": "nonsense", "precision": 11}) is None


def test_an_organisations_facts_are_its_own():
    assert wd.properties_for(["Q5"]) == wd.PERSON and wd.properties_for(["Q43229"]) == wd.ORGANISATION


def test_a_missing_entity():
    with pytest.raises(wd.WikidataError):
        wd.parse_facts(json.dumps({"entities": {"Q1": {"id": "Q1", "missing": ""}}}).encode(), "Q1", [], TODAY)


def test_a_wikipedia_url_from_elsewhere_isnt_taken():
    answer = json.loads(json.dumps(CAREY))
    answer["entities"]["Q313254"]["sitelinks"]["enwiki"]["url"] = "https://example.com/x"
    got = wd.parse_facts(json.dumps(answer).encode(), "Q313254", [], TODAY)
    assert not [s for s in got.statements if s.get_property_id() == wd.DESCRIBED_AT]


def test_languages():
    found = wd.parse_languages(json.dumps(LANGUAGES).encode())
    assert [(language.id, language.code, language.label) for language in found] == [("Q1860", "en", "English"),
                                                                                     ("Q397", "la", "Latin")]


def test_answers_that_cant_be_read():
    with pytest.raises(wd.WikidataError):
        wd.parse_search(b"<html>")


@pytest.mark.parametrize("entity_id, wanted", [("Q313254", True), ("Q1", True), ("P31", False), ("Q", False),
                                               ("1e8a1b19-2b71", False), ("Qx1", False)])
def test_is_wikidata(entity_id, wanted):
    assert wd.is_wikidata(entity_id) == wanted


def test_who_asks(tmp_path):
    (tmp_path / wd.CONTACT_FILE).write_text("David Arnold; d+wikidata@pobox.com\n")
    assert wd.user_agent("2.2", wd.contact(str(tmp_path))) == AGENT


@pytest.mark.parametrize("text", [None, "", "\n"])
def test_no_one_to_ask_as(tmp_path, text):
    if text is not None:
        (tmp_path / wd.CONTACT_FILE).write_text(text)
    with pytest.raises(wd.WikidataError, match="who's asking"):
        wd.contact(str(tmp_path))


def test_every_request_says_who_asks():
    fetch = Wikidata()
    wd.search("Peter Carey", fetch, AGENT)
    wd.facts("Q313254", fetch, AGENT, TODAY)
    wd.languages(fetch, AGENT)
    assert {headers["User-Agent"] for _, headers in fetch.asked} == {AGENT}


def test_a_requests_user_agent_is_sent(web, tmp_path):  # noqa: F811
    """darq.http's own doesn't say who's asking: these do."""
    from test_http_service import fetcher
    fetcher.fetch("GET", web.base + "/hello.txt", [("User-Agent", AGENT)], b"", 1000, "DarqOS/2.2", tmp_path / "b")
    assert web.requests[-1][2]["User-Agent"] == AGENT


################################################################
# Making entities Wikidata's
################################################################

@pytest.fixture
def linker(kb, implementation):  # noqa: F811
    adapter = Implementation(implementation)
    items = model.KB(kb.client, Writer(adapter.handler))
    kb.ensure(*wd.vocabulary(), *darq.book.vocabulary())
    return wd.Linker(kb, items, Wikidata(), AGENT)


def person(linker, label="Peter Carey") -> str:
    made = model.Item()
    made.set_label(label)
    made.claim(model.INSTANCE_OF, model.EntityValue("Q5"))
    linker.items.create(darq.book.PERSON_TYPE, made)
    return made.id


def test_linking_merges_into_wikidatas(linker):
    local = person(linker)
    linker.link(local, "Q313254")
    carey = linker.kb.get("Q313254")
    assert carey.get_label() == "Peter Carey" and carey.descriptions["en"] == "Australian novelist (born 1943)"
    assert linker.kb.get(local, follow=False).redirect == "Q313254"
    assert linker.kb.get(local).id == "Q313254"


def test_a_linked_person_is_still_a_person(linker):
    local = person(linker)
    linker.link(local, "Q313254")
    assert model.recorded_type(linker.kb.get("Q313254")) == darq.book.PERSON_TYPE


def test_linking_brings_facts(linker):
    local = person(linker)
    linker.link(local, "Q313254")
    carey = linker.kb.get("Q313254")
    born = carey.get_statements("P569")[0]
    assert value(born).year == 1943
    assert any(a.value.entity_id == wd.WIKIDATA for r in born.references for a in r.snaks
               if a.property_id == model.STATED_IN)
    assert [value(s).value for s in carey.get_statements(wd.DESCRIBED_AT)] == [
        "https://en.wikipedia.org/wiki/Peter_Carey_(novelist)"]


def test_places_are_made_minimally(linker):
    linker.link(person(linker), "Q313254")
    town = linker.kb.get("Q2878215")
    assert (town.get_label(), town.descriptions["en"]) == ("Bacchus Marsh", "town in Victoria, Australia")
    assert town.get_statements(wd.PERSON[0]) == []


def test_what_a_book_said_of_them_stays(linker):
    local = person(linker)
    work = model.Item()
    work.set_label("Bliss")
    work.claim(darq.book.AUTHOR, model.EntityValue(local))
    linker.items.create(darq.book.WORK_TYPE, work)
    linker.link(local, "Q313254")

    # What linked to the old one links to Wikidata's: the old one redirects.
    assert value(linker.kb.get(work.id).get_statements(darq.book.AUTHOR)[0]).entity_id == "Q313254"
    assert linker.kb.get(local).id == "Q313254"

    # And Wikidata's entity is what the book links to, to see from his page.
    assert list(linker.kb.find_links("Q313254", darq.book.AUTHOR)) == [work.id]
    assert [(p, t) for p, _, t, _ in linker.kb.link_counts("Q313254")] == [(darq.book.AUTHOR, darq.book.WORK_TYPE)]


def test_refreshing_changes_nothing_that_hasnt(linker):
    linker.link(person(linker), "Q313254")
    before = linker.kb.get("Q313254")
    linker.refresh("Q313254")
    assert len(linker.kb.get("Q313254").statements) == len(before.statements)


def test_a_hand_edit_survives_a_refresh(linker):
    linker.link(person(linker), "Q313254")
    carey = linker.kb.get("Q313254")
    [born] = carey.get_statements("P569")
    changed = model.apply_edits(carey, model.change_value(carey, born.id, model.TimeValue(1944),
                                                          model.ENTITY_EDITOR))
    linker.items.put(changed)
    linker.refresh("Q313254")
    current = [value(s).year for s in linker.kb.get("Q313254").get_statements("P569")
               if s.rank != model.Rank.Deprecated]
    assert current == [1944]


def test_bringing_languages(linker):
    english = model.Item()
    english.set_label("English")
    linker.items.create(model.THING_TYPE, english)
    assert linker.bring_languages() == 1
    q1860 = linker.kb.get("Q1860")
    assert q1860.get_label() == "English" and "en" in q1860.get_aliases()
    assert q1860.get_statements(wd.ISO_639_1)[0].get_value().value == "en"
    assert linker.kb.get(english.id).id == "Q1860"
    assert linker.kb.get("Q397").get_label() == "Latin"


def test_bringing_languages_again_changes_nothing(linker):
    linker.bring_languages()
    before = linker.kb.get("Q1860")
    assert linker.bring_languages() == 0
    assert linker.kb.get("Q1860").revision == before.revision


def test_a_language_is_found_by_its_code(linker):
    from darq.ui import book_store
    linker.bring_languages()
    store = book_store.ItemStore(["en"])
    store.kb = linker.kb
    assert store.find("en", darq.book.LANGUAGE_KIND) == "Q1860"
    assert store.find("english", darq.book.LANGUAGE_KIND) == "Q1860"
    assert store.names_like("Lat", darq.book.LANGUAGE_KIND) == ["Latin"]


def test_a_language_before_wikidatas_is_one_made_here(linker):
    from darq.ui import book_store
    made = model.Item()
    made.set_label("Klingon")
    linker.items.create(model.THING_TYPE, made)
    store = book_store.ItemStore(["en"])
    store.kb = linker.kb
    assert store.find("Klingon", darq.book.LANGUAGE_KIND) == made.id


################################################################
# The entity lens's W, and the page
################################################################

ENTITY_LENS = pathlib.Path(__file__).parent.parent / "lenses" / "entity"


def load(path: pathlib.Path, name: str):
    sys.path.insert(0, str(path))
    try:
        spec = importlib.util.spec_from_file_location(name, path / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path))
    return module


entity_lens = load(ENTITY_LENS, "entity_lens_for_wikidata")


def test_a_matched_page_says_where_it_is_on_wikidata():
    page = entity_lens.render.Page(["en"], {})
    carey = model.Item("Q313254")
    carey.set_label("Peter Carey")
    assert "https://www.wikidata.org/wiki/Q313254" in page.render(carey)
    local = model.Item()
    assert "wikidata.org" not in page.render(local)


class FakeWindow:
    lens: typing.Any
    wikidata = entity_lens.EntityWindow.wikidata
    found_on_wikidata = entity_lens.EntityWindow.found_on_wikidata
    wikidata_done = entity_lens.EntityWindow.wikidata_done

    def __init__(self, entity: model.Entity):
        self.shown = python_types.SimpleNamespace(entity=entity)
        self.chain = ["en"]
        self.said: list[str] = []
        self.refreshed = 0

    def show_status(self, message: str):
        self.said.append(message)

    def refresh(self):
        self.refreshed += 1


def lens_for(window, fetch, chooses=0):
    """A window, with an entity lens doing what's asked at once."""
    calls = []

    class Linker:
        def __init__(self):
            self.fetch = fetch
            self.agent = AGENT

        def link(self, local, target):
            calls.append(("link", local, target))

        def refresh(self, target):
            calls.append(("refresh", target))

    used = python_types.SimpleNamespace(make_linker=Linker, run=lambda job: job(), choose=lambda q, c: chooses,
                                        loop=python_types.SimpleNamespace(post=lambda job: job()))
    used.in_background = lambda job, done: entity_lens.EntityLens.in_background(typing.cast(typing.Any, used), job,
                                                                                  done)
    window.lens = used
    return calls


def test_w_finds_and_links():
    carey = model.Item()
    carey.set_label("Peter Carey")
    window = FakeWindow(carey)
    calls = lens_for(window, Wikidata())
    window.wikidata()
    assert calls == [("link", carey.id, "Q313254")] and window.refreshed == 1


def test_none_of_these_links_nothing():
    carey = model.Item()
    carey.set_label("Peter Carey")
    window = FakeWindow(carey)
    calls = lens_for(window, Wikidata(), chooses=3)
    window.wikidata()
    assert calls == []


def test_w_refreshes_one_thats_matched():
    window = FakeWindow(model.Item("Q313254"))
    calls = lens_for(window, Wikidata())
    window.wikidata()
    assert calls == [("refresh", "Q313254")]


def test_no_contact_says_so():
    carey = model.Item()
    carey.set_label("Peter Carey")
    window = FakeWindow(carey)

    def fail():
        raise wd.WikidataError("Wikidata needs to know who's asking")

    lens_for(window, Wikidata())
    window.lens.make_linker = fail
    window.wikidata()
    assert "who's asking" in window.said[-1]


################################################################
# The matching tool
################################################################

TOOL = pathlib.Path(__file__).parent.parent / "tools" / "wikidata-match"
tool = load(TOOL, "wikidata_match")


def test_whos_unmatched(linker):
    local = person(linker)
    org = model.Item()
    org.set_label("Picador")
    org.claim(model.INSTANCE_OF, model.EntityValue("Q43229"))
    linker.items.create("darq.organisation", org)
    assert [u.label for u in tool.unmatched(linker.kb)] == ["Peter Carey", "Picador"]
    linker.link(local, "Q313254")
    assert [u.label for u in tool.unmatched(linker.kb)] == ["Picador"]


def test_the_tool_links_and_goes_on(qt_app, linker):
    person(linker)
    person(linker, "Kate Grenville")
    window = tool.MatchWindow(python_types.SimpleNamespace(post=lambda job: job()), lambda: linker,
                              lambda job: job())
    window.load()
    assert window.people.count() == 2 and window.query.text() == "Kate Grenville"
    window.skip()
    assert window.query.text() == "Peter Carey" and window.found.count() == 3
    window.link()
    assert window.people.count() == 0 and "is Q313254" in window.status.text()
    assert window.found.count() == 0
    assert linker.kb.get("Q313254").get_label() == "Peter Carey"
    window.close()



def test_a_name_only_in_mul_is_taken():
    """Wikidata keeps many people's names in mul, its code for every
    language, rather than in English."""
    answer = {"entities": {"Q1": {"id": "Q1", "labels": {"mul": {"language": "mul", "value": "Robin Calder"}},
                                  "claims": {}}}}
    got = wd.parse_facts(json.dumps(answer).encode(), "Q1", [], TODAY)
    assert (got.label, got.label_language) == ("Robin Calder", "mul")
    answer["entities"]["Q1"]["labels"]["en"] = {"language": "en", "value": "Robin A. Calder"}
    got = wd.parse_facts(json.dumps(answer).encode(), "Q1", [], TODAY)
    assert (got.label, got.label_language) == ("Robin A. Calder", "en")
    assert "languages=en%7Cmul" in wd.entities_url(["Q1"], "labels")
