# darqos
# Copyright (C) 2026 David Arnold

# Tests for the entity lens, lenses/entity: its rendering, without Qt;
# its window and navigation, offscreen, against fake items; its Lens
# handler; and then the whole system, through a real p-kernel (see
# test_pkernel.py for the fixtures).

import importlib.util
import pathlib
import sys

import pytest

import darq
import darq.items
import darq.kb as model
import darq.message
import darq.url
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    FindEntitiesResponse,
    GetEntityResponse,
    GetLinkSummaryResponse,
    GetLinksResponse,
    Kind,
    LinkGroup,
    Linked,
    PutEntityResponse,
    Role,
    SuggestPropertiesResponse,
    WatchEntityResponse,
)
from darq.protocols.lens import Status, UseRequest

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "entity"


def load():
    sys.path.insert(0, str(LENS_PATH))
    try:
        spec = importlib.util.spec_from_file_location("entity_lens", LENS_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LENS_PATH))
    return module


lens = load()
grouped = lens.grouped
render = lens.render
edit = lens.edit
times = edit.times

ADA = "darq.person:ada"
ACME = "darq.organisation:acme"


################################################################
# Rendering
################################################################

def test_languages():
    assert render.languages("en_AU.UTF-8") == ["en-AU", "en"]
    assert render.languages("fr_FR") == ["fr-FR", "fr", "en"]
    assert render.languages("en") == ["en"]


@pytest.mark.parametrize("time, text", [
    (model.TimeValue.parse("1974"), "1974"),
    (model.TimeValue.parse("1974-03"), "March 1974"),
    (model.TimeValue.parse("1815-12-10"), "10 December 1815"),
    (model.TimeValue.parse("-0044-03-15"), "15 March 44 BCE"),
    (model.TimeValue.parse("2026-10-30T09:05:07"), "30 October 2026, 09:05:07"),
    (model.TimeValue(1974, precision=model.TimePrecision.TenYears), "1970s"),
    (model.TimeValue(1901, precision=model.TimePrecision.HundredYears), "20th century"),
    (model.TimeValue(1974, precision=model.TimePrecision.Years, before=1, after=1), "1974 ± 1 years"),
    (model.TimeValue(1974, precision=model.TimePrecision.Years, before=2), "1974 (−2, +0 years)"),
    (model.TimeValue(1582, 10, 4, calendar="julian"), "4 October 1582 (julian)"),
])
def test_format_time(time, text):
    assert render.format_time(time) == text


def page_for(entity, links=None, groups=(), inline=None, note=None):
    page = render.Page(["en-AU", "en"], links or {})
    return page, page.render(entity, grouped.sections(groups), inline, note)


def test_render():
    ada = model.Item("ada")
    ada.set_label("Ada")
    ada.set_description("mathematician <b>")
    ada.add_alias("Augusta")
    ada.claim("P108", model.EntityValue("acme"), model.Rank.Preferred) \
        .add_qualifier("P580", model.TimeValue.parse("1843")) \
        .add_reference(model.Reference().add("P248", model.EntityValue("book")))
    ada.claim("P108", model.EntityValue("old"), model.Rank.Deprecated)
    ada.claim("P1329", model.StringValue("<script>"))
    ada.claim("P2048", model.QuantityValue("1.65", "Q11573", "1.60", "1.70"))
    note = model.TextValue("hello")
    note.set_text("g'day", "en-AU")
    ada.claim("note", note)
    links = {
        "acme": render.Link(ACME, "Acme & Co"),
        "old": render.Link("darq.organisation:old", ""),
        "book": render.Link("darq.entity:book", "Address book"),
        "P108": render.Link("", "employer"),
        "Q11573": render.Link("darq.entity:Q11573", "metre"),
    }
    group = LinkGroup("P108", Role.MAIN, "darq.person", 1, "employer", "employs")
    bob = Linked("bob", "darq.person:bob", "en", "Bob")
    page, html = page_for(ada, links, [group], {("P108", Role.MAIN, "darq.person"): [bob]}, note="merged")

    assert "mathematician &lt;b&gt;" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "Also known as: Augusta" in html
    assert 'Acme &amp; Co</a> ★' in html
    assert '<s class="deprecated"><a href="#2">old</a></s>' in html
    assert "start time" not in html and "P580: 1843" in html
    assert "source: P248: " in html and "Address book</a>" in html
    assert "1.65 ± 0.05 " in html and "metre</a>" in html
    assert "g&#x27;day" in html
    assert "employer" in html and 'href' not in html.split("employer")[0][-10:]
    assert "<h3>Employs</h3>" in html and "(Person)" in html
    assert "merged" in html
    assert ACME in page.targets and grouped.Row("darq.person:bob") in page.targets

    # The property's name is on its first statement only.
    assert html.count(">employer<") == 1


def test_old_item_identifiers_are_links():
    url = model.Item("url")
    url.claim(darq.url.OLD_SNAPSHOT, model.StringValue("com.adobe.pdf:abc"))
    page, html = page_for(url)
    assert grouped.Row("com.adobe.pdf:abc") in page.targets
    assert "com.adobe.pdf:abc</a>" in html


def test_a_file_that_isnt_an_item_isnt_a_link():
    message = model.Item("m")
    message.claim(darq.message.OLD_ATTACHMENT, model.StringValue(""))
    assert page_for(message)[0].targets == []


def test_render_empty():
    _, html = page_for(model.Item("x"))
    assert "No statements." in html and "<h3>" not in html


def test_render_names_in_order():
    ada = model.Item("ada")
    for name, ordinal in [("Byron", "2"), ("Augusta", "1")]:
        ada.claim("P735", model.StringValue(name)).add_qualifier(model.SERIES_ORDINAL, model.StringValue(ordinal))
    _, html = page_for(ada)
    assert html.index("Augusta") < html.index("Byron")


def test_render_hides_deprecated():
    ada = model.Item("ada")
    ada.claim("P1329", model.StringValue("old"), model.Rank.Deprecated)
    ada.claim("P1329", model.StringValue("new"))
    html = render.Page(["en"], {}).render(ada, [], deprecated=False)
    assert "old" not in html and "new" in html and "1 deprecated statement hidden" in html
    ada.statements.pop()
    html = render.Page(["en"], {}).render(ada, [], deprecated=False)
    assert "No statements." not in html and "1 deprecated statement hidden" in html


################################################################
# Times, as typed
################################################################

P = model.TimePrecision


@pytest.mark.parametrize("text, year, month, day, precision", [
    ("2024", 2024, None, None, P.Years),
    ("June 2024", 2024, 6, None, P.Months),
    ("jun. 2024", 2024, 6, None, P.Months),
    ("6/2024", 2024, 6, None, P.Months),
    ("2024-06", 2024, 6, None, P.Months),
    ("30 June 2024", 2024, 6, 30, P.Days),
    ("30th Jun 2024", 2024, 6, 30, P.Days),
    ("June 30, 2024", 2024, 6, 30, P.Days),
    ("30/6/2024", 2024, 6, 30, P.Days),
    ("2024-06-30", 2024, 6, 30, P.Days),
    ("  1970s ", 1970, None, None, P.TenYears),
    ("15 March 44 BCE", -44, 3, 15, P.Days),
    ("-0044-03-15", -44, 3, 15, P.Days),
])
def test_parse_times(text, year, month, day, precision):
    t = times.parse(text)
    assert (t.year, t.month, t.day, t.precision, t.before, t.after) == (year, month, day, precision, 0, 0)


def test_approximate_times():
    t = times.parse("about 1974")
    assert (t.year, t.precision, t.before, t.after) == (1974, P.Years, 1, 1)
    assert times.parse("c. March 1974").before == 1 and times.parse("~1974").after == 1
    assert times.text(t) == "about 1974"


@pytest.mark.parametrize("text, why", [
    ("", "no time"), ("31 June 2024", "no day 31"), ("13/2024", "no month 13"), ("Smarch 2024", "isn't a month"),
    ("1975s", "isn't a decade"), ("next week", "can't read"), ("0 BCE", "from 1"),
])
def test_bad_times(text, why):
    with pytest.raises(ValueError, match=why):
        times.parse(text)


@pytest.mark.parametrize("time", [
    model.TimeValue.parse("1974"), model.TimeValue.parse("1974-03"), model.TimeValue.parse("1815-12-10"),
    model.TimeValue.parse("-0044-03-15"), model.TimeValue(1970, precision=P.TenYears),
    model.TimeValue(1974, precision=P.Years, before=1, after=1),
])
def test_times_read_back(time):
    again = times.parse(times.text(time))
    assert model.encode_value(again) == model.encode_value(time)


def test_rank_found():
    found = [Linked("a", "", "en", "Brisbane City"), Linked("b", "", "en", "Bris"), Linked("c", "", "en", "Old Bris")]
    assert [link.entity_id for link in edit.rank(found, "bris")] == ["b", "a", "c"]


################################################################
# Window and navigation, offscreen
################################################################

class World:
    """Items, as an EntityItem implementation would have them."""

    def __init__(self):
        self.entities: dict[str, model.Entity] = {}
        self.links: dict[str, list[Linked]] = {}
        self.link_reads = 0
        self.inverse = ""
        self.failing_links: set[str] = set()
        self.ports: dict[int, str] = {}
        self.next_port = 500
        self.closed_items: list[str] = []
        self.watched: list[tuple[str, int]] = []
        self.failing: set[str] = set()
        self.puts: list[model.Entity] = []

        # The contexts writes were described with, for History, and the
        # items told they're viewed, with by whom.
        self.contexts: list = []
        self.viewed: list[tuple[str, str]] = []

        # Properties, by identifier: their labels and datatypes.
        self.properties: dict[str, tuple[str, model.Datatype]] = {
            "P108": ("employer", model.Datatype.Item),
            "P1329": ("phone number", model.Datatype.String),
            "P551": ("residence", model.Datatype.Item),
            "P248": ("stated in", model.Datatype.Item),
            darq.items.HAS_TAG: ("has tag", model.Datatype.Item),
            model.START_TIME: ("start time", model.Datatype.Time),
            model.END_TIME: ("end time", model.Datatype.Time),
            model.REASON_FOR_DEPRECATED_RANK: ("reason for deprecated rank", model.Datatype.Item),
        }

    def add(self, item_id, label, *refs):
        _, entity_id = darq.split_item_id(item_id)
        e = model.Item(entity_id)
        e.set_label(label)
        for ref in refs:
            e.claim("P108", model.EntityValue(darq.split_item_id(ref)[1]))
        e.revision = 1
        self.entities[item_id] = e
        return e

    def change(self, item_id, change):
        """Change an item's entity elsewhere."""
        e = self.entities[item_id]
        change(e)
        e.revision += 1

    def linked(self, entity_id) -> Linked:
        item_id = next((i for i, e in self.entities.items() if e.id == entity_id), None)
        if item_id is not None:
            return Linked(entity_id, item_id, "en", self.entities[item_id].get_label() or "", "",
                          wire.Datatype.NONE)
        label, datatype = self.properties.get(entity_id, ("", model.Datatype.String))
        prop = model.Property(datatype, entity_id)
        return Linked(entity_id, "", "en", label, "", model.datatype_to_wire(prop))

    def open(self, item_id):
        if item_id not in self.entities:
            raise darq.LaunchError(f"Can't open {item_id}: NOT_FOUND")
        self.next_port += 1
        self.ports[self.next_port] = item_id
        return self.next_port


class FakeItem:
    """Stands in for an EntityItem.Client."""

    def __init__(self, world: World, port: int):
        self.world = world
        self.item_id = world.ports[port]

    def get_entity(self, languages):
        if self.item_id in self.world.failing:
            raise darq.DeadPortError("gone")
        entity = self.world.entities[self.item_id]
        linked = [self.world.linked(i) for i in sorted(model.referenced(entity))]
        return GetEntityResponse(entity=model.entity_to_wire(entity), linked=linked, status=wire.Status.OK)

    def viewed(self, source="", note=""):
        self.world.viewed.append((self.item_id, source))

    def put_entity(self, entity, context=None):
        self.world.contexts.append(context)
        current = self.world.entities[self.item_id]
        if entity.revision != current.revision:
            return PutEntityResponse(status=wire.Status.CONFLICT)
        written = model.entity_from_wire(entity)
        written.revision = current.revision + 1
        self.world.entities[self.item_id] = written
        self.world.puts.append(written)
        return PutEntityResponse(revision=written.revision, status=wire.Status.OK)

    def find_entities(self, text, languages, kind, class_id, max, after):
        words = text.lower().split()

        def matches(label):
            return all(any(w.startswith(word) for w in label.lower().split()) for word in words)

        if kind == Kind.PROPERTY:
            ids = [p for p, (label, _) in self.world.properties.items() if matches(label)]
        else:
            # Only tags are found as instances of a class.
            ids = [e.id for i, e in self.world.entities.items() if matches(e.get_label() or "")
                   and (class_id != model.TAG_CLASS or i.startswith("darq.tag:"))]
        return FindEntitiesResponse(found=[self.world.linked(i) for i in sorted(ids)], status=wire.Status.OK)

    def suggest_properties(self, languages, max):
        entity = self.world.entities[self.item_id]
        ids = [model.START_TIME, model.END_TIME]
        ids += [p for p in dict.fromkeys(s.get_property_id() for s in entity.statements) if p not in ids]
        return SuggestPropertiesResponse(found=[self.world.linked(i) for i in ids], status=wire.Status.OK)

    def get_link_summary(self, languages):
        if self.item_id in self.world.failing_links:
            return GetLinkSummaryResponse(status=wire.Status.NOT_FOUND)
        types: dict[str, int] = {}
        for link in self.world.links.get(self.item_id, []):
            type_id = darq.split_item_id(link.item_id)[0]
            types[type_id] = types.get(type_id, 0) + 1
        return GetLinkSummaryResponse(groups=[LinkGroup("P108", Role.MAIN, t, n, "employer", self.world.inverse)
                                              for t, n in types.items()], status=wire.Status.OK)

    def get_links(self, property_id, role, languages, max, after, reverse=False, type_id=""):
        # The links as listed are newest first.
        assert reverse
        self.world.link_reads += 1
        links = [link for link in self.world.links.get(self.item_id, [])
                 if darq.split_item_id(link.item_id)[0] == type_id]
        ids = [link.entity_id for link in links]
        start = ids.index(after) + 1 if after else 0
        return GetLinksResponse(linked=links[start:start + max], status=wire.Status.OK)

    def watch_entity(self, listener):
        self.world.watched.append((self.item_id, listener))
        return WatchEntityResponse(status=wire.Status.OK)

    def close(self):
        self.world.closed_items.append(self.item_id)


class FakeOpener:
    def __init__(self, world):
        self.world = world
        self.used: list[str] = []
        self.own_lenses: set[str] = set()

    def open(self, item_id):
        return self.world.open(item_id)

    def use(self, item_id):
        self.used.append(item_id)

    def own_lens(self, item_id):
        return darq.split_item_id(item_id)[0] in self.own_lenses


class FakeLoop:
    def __init__(self):
        self.stopped = False

    def post(self, function):
        function()

    def stop(self):
        self.stopped = True


@pytest.fixture
def world():
    world = World()
    world.add(ACME, "Acme")
    world.add(ADA, "Ada", ACME)
    world.add("darq.person:bob", "Bob", ACME)
    world.links[ACME] = [Linked("ada", ADA, "en", "Ada"), Linked("bob", "darq.person:bob", "en", "Bob")]
    return world


@pytest.fixture
def entity_lens(qt_app, world, monkeypatch):
    ports = iter(range(900, 1000))
    closed = []
    monkeypatch.setattr(darq, "open_port", lambda: next(ports))
    monkeypatch.setattr(darq, "close_port", closed.append)
    loop = FakeLoop()
    entity_lens = lens.EntityLens(loop, FakeOpener(world))
    entity_lens.client = lambda port: FakeItem(world, port)
    server = darq.Server()
    server.add(10, entity_lens)
    entity_lens.closed_ports = closed
    yield entity_lens
    if entity_lens.window is not None:
        entity_lens.window.on_closed = None
        entity_lens.window.close()


def use(entity_lens, world, item_id=ADA):
    return entity_lens.use(UseRequest(item_id=item_id, item=world.open(item_id)), 0)


def test_use_shows_the_item(entity_lens, world):
    assert use(entity_lens, world).status == Status.OK
    window = entity_lens.window
    assert window.title.text() == "Ada" and window.kind.text() == "Person"
    assert window.item_label.text() == ADA
    assert not window.back_button.isEnabled()
    assert world.watched == [(ADA, 900)]
    # It's told it's viewed: its type records it.
    assert world.viewed == [(ADA, "entity-lens")]
    assert window.targets == [ACME]

    other = world.open(ACME)
    assert entity_lens.use(UseRequest(item_id=ACME, item=other), 0).status == Status.BUSY


def test_use_opens_the_entity_by_its_identifier(entity_lens, world, monkeypatch):
    """The port it's given is its type's, which, for a content item, isn't
    an entity's: so it's closed, and the entity opened by OpenEntity."""
    opened = []
    monkeypatch.setattr(entity_lens.opener, "open", lambda item_id: opened.append(item_id) or world.open(item_id))
    given = world.open(ADA)
    assert entity_lens.use(UseRequest(item_id=ADA, item=given), 0).status == Status.OK
    assert opened == [ADA] and given in entity_lens.closed_ports


def test_it_says_its_showing_an_items_page(entity_lens, world):
    """For the Terminal's Sys-i: what it shows now, as it follows links."""
    from darq.protocols.lens import ShownRequest
    assert entity_lens.shown(ShownRequest(), 0).item_id == ""
    use(entity_lens, world)
    shown = entity_lens.shown(ShownRequest(), 0)
    assert (shown.item_id, shown.page) == (ADA, True)
    entity_lens.window.follow(ACME)
    assert entity_lens.shown(ShownRequest(), 0).item_id == ACME


def test_use_fails_if_unreadable(entity_lens, world):
    world.failing.add(ADA)
    assert use(entity_lens, world).status == Status.FAILED
    assert entity_lens.window is None
    assert world.closed_items == [ADA]


def test_follow_and_back(entity_lens, world):
    use(entity_lens, world)
    window = entity_lens.window
    window.follow(ACME)
    assert window.title.text() == "Acme" and window.kind.text() == "Organisation"
    assert window.back_button.isEnabled()
    assert world.closed_items == [ADA]
    assert 900 in entity_lens.closed_ports
    assert "With this as employer" in window.browser.toHtml()
    assert window.targets == [grouped.Row(ADA), grouped.Row("darq.person:bob")]

    window.follow("darq.person:bob")
    assert window.back == [lens.Place(ADA, 0), lens.Place(ACME, 0)]
    window.go_back()
    assert window.title.text() == "Acme"
    window.go_back()
    assert window.title.text() == "Ada" and not window.back_button.isEnabled()
    window.go_back()
    assert window.title.text() == "Ada"

    viewed = [item_id for item_id, _ in world.viewed]
    assert viewed == [ADA, ACME, "darq.person:bob", ACME, ADA]


def messages(world, n):
    """Make n messages link to Acme, newest first."""
    links = []
    for number in range(n, 0, -1):
        item_id = f"darq.message:m{number:04d}"
        world.add(item_id, "")
        links.append(Linked(f"m{number:04d}", item_id, "", "", "", wire.Datatype.NONE, f"Ada: message {number}"))
    world.links[ACME] = links


KEY = ("P108", Role.MAIN, "darq.message")


def test_small_groups_are_shown_in_place(entity_lens, world):
    messages(world, grouped.INLINE_MAX)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    assert "Ada: message 3" in window.browser.toHtml()
    assert grouped.Row("darq.message:m0003") in window.targets


def test_large_groups_are_counts(entity_lens, world):
    messages(world, grouped.PAGE_ROWS * 2 + 5)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    assert "205 Messages" in window.browser.toPlainText()
    assert "Ada: message" not in window.browser.toPlainText()
    assert window.targets == [grouped.OpenGroup(KEY)]

    # Reading a page doesn't read a large group's entities.
    assert world.link_reads == 0


def test_group_is_shown_in_the_window(entity_lens, world):
    messages(world, 105)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    viewed = len(world.viewed)
    window.choose(window.targets[0])

    text = window.browser.toPlainText()
    assert "1 to 100, newest first" in text
    assert "Ada: message 105" in text and "Ada: message 6" in text and "Ada: message 5\n" not in text
    assert window.title.text() == "Acme" and window.back_button.isEnabled()

    # It's not an item: it isn't viewed, and the item stays open.
    assert len(world.viewed) == viewed
    assert world.closed_items == []


def test_group_pages(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    assert grouped.Turn(-1) not in window.targets

    window.choose(grouped.Turn(1))
    text = window.browser.toPlainText()
    assert "101 to 200, newest first" in text and "Ada: message 105" in text
    assert grouped.Turn(-1) in window.targets and grouped.Turn(1) in window.targets

    window.choose(grouped.Turn(1))
    assert "201 to 205, newest first" in window.browser.toPlainText()
    assert grouped.Turn(1) not in window.targets

    # Past the end does nothing.
    window.turn(1)
    assert "201 to 205, newest first" in window.browser.toPlainText()

    window.choose(grouped.Turn(-1))
    assert "101 to 200, newest first" in window.browser.toPlainText()


def test_back_leaves_a_group_however_many_pages_in(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    window.turn(1)
    window.turn(1)
    window.go_back()
    assert window.view is None
    assert "205 Messages" in window.browser.toPlainText()
    assert not window.back_button.isEnabled()
    assert world.closed_items == []


def test_back_returns_to_the_place_in_a_group(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    window.turn(1)
    window.choose(grouped.Row("darq.message:m0090"))
    assert window.view is None
    assert window.item_label.text() == "darq.message:m0090"

    window.go_back()
    assert window.item_label.text() == ACME
    assert "101 to 200, newest first" in window.browser.toPlainText()
    window.go_back()
    assert window.view is None and "205 Messages" in window.browser.toPlainText()


def test_row_opens_in_its_own_lens(entity_lens, world):
    messages(world, 205)
    entity_lens.opener.own_lenses.add("darq.message")
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    window.choose(grouped.Row("darq.message:m0205"))
    assert entity_lens.opener.used == ["darq.message:m0205"]

    # This window stays where it is.
    assert window.item_label.text() == ACME and window.view is not None


def test_following_failure_keeps_the_group(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    world.failing.add("darq.message:m0205")
    window.choose(grouped.Row("darq.message:m0205"))
    assert window.item_label.text() == ACME and window.view is not None


def test_group_that_cant_be_read_stays_on_the_page(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    world.failing_links.add(ACME)
    window.open_group(KEY)
    assert window.view is None and window.back == []
    assert "Can't read" in window.status.text()


def test_group_gone_leaves_the_page(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    world.links[ACME] = []
    window.refresh()
    assert window.view is None


def test_groups_arent_edited(entity_lens, world):
    messages(world, 205)
    use(entity_lens, world, ACME)
    window = entity_lens.window
    window.open_group(KEY)
    assert not window.edit_button.isEnabled()
    window.start_editing()
    assert not window.editing
    window.go_back()
    assert window.edit_button.isEnabled()


def test_follow_in_a_new_window(entity_lens, world):
    use(entity_lens, world)
    window = entity_lens.window
    window.follow(ACME, new_window=True)
    assert entity_lens.opener.used == [ACME]
    assert window.title.text() == "Ada" and window.back == []


def test_follow_failure_stays(entity_lens, world):
    use(entity_lens, world)
    window = entity_lens.window
    window.follow("darq.person:nobody")
    assert window.title.text() == "Ada" and window.back == []
    assert "Can't open darq.person:nobody" in window.status.text()

    world.failing.add(ACME)
    window.follow(ACME)
    assert window.title.text() == "Ada" and window.back == []
    assert ACME in world.closed_items


def test_anchor_and_context_targets(entity_lens, world):
    use(entity_lens, world)
    window = entity_lens.window
    assert window.target("#0") == ACME
    assert window.target("#7") is None and window.target("http://x") is None
    from PyQt6.QtCore import QUrl
    window.on_anchor(QUrl("#0"))
    assert window.title.text() == "Acme"


def test_changes_refresh(entity_lens, world):
    use(entity_lens, world)
    window = entity_lens.window
    world.entities[ADA].set_label("Ada Lovelace")
    listener = entity_lens.server._handlers[900]
    listener.changed(Changed(entity_id="ada", revision=2), 0)
    assert window.title.text() == "Ada Lovelace"


def test_close(entity_lens, world):
    use(entity_lens, world)
    entity_lens.window.close()
    assert entity_lens.loop.stopped
    assert world.closed_items == [ADA]


def test_merged_note(entity_lens, world):
    world.entities["darq.person:old"] = world.entities[ADA]
    use(entity_lens, world, "darq.person:old")
    assert "old was merged into this." in entity_lens.window.browser.toPlainText()


################################################################
# Editing, offscreen
################################################################

BOOK = "book"


@pytest.fixture
def editing(entity_lens, world, qt_app):
    """Ada, with a residence and a phone number from an address book,
    shown in edit mode."""
    world.add("darq.place:home", "1 Example St")
    world.add("darq.place:new", "2 New St")
    world.add(f"darq.entity:{BOOK}", "Address book")
    ada = world.entities[ADA]
    for property_id, value in [("P551", model.EntityValue("home")), ("P1329", model.StringValue("+61 7 3000 1234"))]:
        ada.claim(property_id, value).add_reference(model.Reference().add("P248", model.EntityValue(BOOK)))
    use(entity_lens, world)
    window = entity_lens.window
    window.start_editing()
    settle(qt_app)
    return window


def settle(qt_app):
    """Let the form build itself again."""
    for _ in range(3):
        qt_app.processEvents()


def statement(world, property_id, n=0):
    return world.entities[ADA].get_statements(property_id)[n]


def test_edit_mode(editing, entity_lens, world, qt_app):
    window = editing
    assert window.editing and window.stack.currentWidget() is window.form_area
    assert window.language == "en" and window.edit_button.text() == "Done"
    assert {"labels:en", "descriptions:en", "aliases:en"} <= set(window.form.editors)
    assert f"value:{statement(world, 'P551').id}" in window.form.editors

    window.finish_editing()
    assert not window.editing and window.stack.currentWidget() is window.browser
    assert world.viewed == [(ADA, "entity-lens")]


def test_each_change_is_saved(editing, entity_lens, world, qt_app):
    window = editing
    label = window.form.editors["labels:en"]
    label.setText("Ada Lovelace")
    label.finish()
    settle(qt_app)
    assert world.entities[ADA].get_label() == "Ada Lovelace" and window.title.text() == "Ada Lovelace"
    assert window.status.text() == "Saved"

    aliases = window.form.editors["aliases:en"]
    aliases.setText("Augusta,  Countess of Lovelace ,")
    aliases.finish()
    settle(qt_app)
    assert world.entities[ADA].aliases == {"en": ["Augusta", "Countess of Lovelace"]}

    # Each save's recorded by its type, as this lens says: History counts
    # them together.
    assert world.viewed == [(ADA, "entity-lens")]
    assert {(c.source, c.quiet) for c in world.contexts} == {("entity-lens", False)}

    # Undo and redo.
    window.undo()
    settle(qt_app)
    assert world.entities[ADA].aliases == {}
    window.redo()
    assert world.entities[ADA].aliases == {"en": ["Augusta", "Countess of Lovelace"]}
    window.undo()
    window.undo()
    assert world.entities[ADA].get_label() == "Ada" and not window.undo_stack


def test_dating_an_address(editing, world, qt_app):
    window = editing
    sid = statement(world, "P551").id
    window.form.add_dates(sid)
    settle(qt_app)
    start = window.form.editors[f"new-qualifier:{sid}:0"]
    start.line.setText("Smarch 2019")
    start.typed("Smarch 2019")
    start.finish()
    assert "isn't a month" in start.hint.text() and not world.puts

    start.line.setText("March 2019")
    start.typed("March 2019")
    assert start.hint.text() == "= March 2019"
    start.finish()
    settle(qt_app)

    # The address book's statement is annotated, and stays its own.
    s = statement(world, "P551")
    assert s.id == sid and s.references and [q.property_id for q in s.qualifiers] == [model.START_TIME]
    assert window.form.new_qualifiers[sid] == [model.END_TIME]
    end = window.form.editors[f"new-qualifier:{sid}:0"]
    end.line.setText("30 June 2024")
    end.finish()
    settle(qt_app)
    assert [q.property_id for q in statement(world, "P551").qualifiers] == [model.START_TIME, model.END_TIME]


def test_changing_a_sources_statement(editing, world, qt_app):
    window = editing
    sid = statement(world, "P1329").id
    phone = window.form.editors[f"value:{sid}"]
    phone.line.setText("+61 7 3000 9999")
    phone.finish()
    settle(qt_app)
    old, new = world.entities[ADA].get_statements("P1329")
    assert old.id == sid and old.rank == model.Rank.Deprecated and old.references
    assert new.get_value().value == "+61 7 3000 9999" and model.is_sourced(new, model.ENTITY_EDITOR)
    assert window.status.text() == "Stated in Address book: kept, deprecated, and replaced with yours"

    # Removed, it's deprecated; undone, it's back.
    window.form.remove(statement(world, "P551").id)
    settle(qt_app)
    assert statement(world, "P551").rank == model.Rank.Deprecated
    assert "hidden" in window.status.text()
    window.undo()
    assert statement(world, "P551").rank == model.Rank.Normal


def test_choosing_an_item(editing, world, qt_app):
    window = editing
    sid = statement(world, "P551").id
    residence = window.form.editors[f"value:{sid}"]
    assert residence.line.text() == "1 Example St"
    residence.picker.line.setText("new")
    residence.picker.search()
    assert [link.entity_id for link in residence.picker.found] == ["new"]
    assert residence.picker.accept()
    settle(qt_app)

    # It's the address book's: replaced, by the operator's.
    old, new = world.entities[ADA].get_statements("P551")
    assert old.rank == model.Rank.Deprecated and new.get_value().get_id() == "new"

    # Nothing chosen, it's put back.
    residence = window.form.editors[f"value:{new.id}"]
    residence.picker.line.setText("nothing like it")
    residence.picker.search()
    assert not residence.picker.accept() and residence.picker.hint.text() == "Choose one from the list"
    residence.left()
    assert residence.line.text() == "2 New St"


def test_adding_a_statement(editing, world, qt_app):
    window = editing
    window.form.pick_statement()
    settle(qt_app)
    picker = window.form.start_picker
    assert picker is not None
    assert model.START_TIME not in [link.entity_id for link in picker.found]
    picker.line.setText("phone")
    picker.search()
    assert picker.accept()
    settle(qt_app)
    assert window.form.new_statements == ["P1329"]
    new = window.form.editors["new-statement:0"]
    new.line.setText("+61 400 000 000")
    new.finish()
    settle(qt_app)
    phones = world.entities[ADA].get_statements("P1329")
    assert [s.get_value().value for s in phones] == ["+61 7 3000 1234", "+61 400 000 000"]
    assert model.is_sourced(phones[1], model.ENTITY_EDITOR) and window.form.new_statements == []


def test_changes_elsewhere_are_rebased(editing, world, qt_app):
    window = editing
    world.change(ADA, lambda e: e.set_description("mathematician"))
    label = window.form.editors["labels:en"]
    label.setText("Ada Lovelace")
    label.finish()
    settle(qt_app)
    ada = world.entities[ADA]
    assert (ada.get_label(), ada.get_description()) == ("Ada Lovelace", "mathematician")
    assert window.form.conflict is None


def conflict(window):
    """Return the conflict the form shows."""
    assert window.form.conflict is not None
    return window.form.conflict


def test_conflicts_are_shown(editing, world, qt_app):
    window = editing
    world.change(ADA, lambda e: e.set_label("Augusta"))
    label = window.form.editors["labels:en"]
    label.setText("Ada Lovelace")
    label.finish()
    settle(qt_app)
    assert world.entities[ADA].get_label() == "Augusta"
    message, target, keep_mine = conflict(window)
    assert target == "labels:en" and "The label has been changed elsewhere" in message

    keep_mine()
    settle(qt_app)
    assert world.entities[ADA].get_label() == "Ada Lovelace" and window.form.conflict is None

    # A statement removed elsewhere can only be given up on.
    sid = statement(world, "P1329").id
    phone = window.form.editors[f"value:{sid}"]
    world.change(ADA, lambda e: e.remove_statement(e.get_statements("P1329")[0]))
    phone.line.setText("+61 7 3000 9999")
    phone.finish()
    settle(qt_app)
    message, target, keep_mine = conflict(window)
    assert target == f"statement:{sid}" and keep_mine is not None
    keep_mine()
    settle(qt_app)
    message, target, keep_mine = conflict(window)
    assert "removed elsewhere" in message and keep_mine is None
    window.form.take_theirs()
    assert window.form.conflict is None


def test_changes_elsewhere_wait_for_a_field(editing, entity_lens, world, qt_app):
    window = editing
    description = window.form.editors["descriptions:en"]
    description.setText("mathem")
    world.change(ADA, lambda e: e.set_label("Augusta"))
    listener = entity_lens.server._handlers[900]
    listener.changed(Changed(entity_id="ada", revision=world.entities[ADA].revision), 0)
    assert "Changed elsewhere" in window.status.text()
    assert window.form.editors["descriptions:en"] is description

    # The label's as it is now, so the description's saved over it.
    description.setText("mathematician")
    description.finish()
    settle(qt_app)
    ada = world.entities[ADA]
    assert (ada.get_label(), ada.get_description()) == ("Augusta", "mathematician")
    assert window.form.editors["labels:en"].text() == "Augusta"


def test_keys(editing, qt_app):
    from PyQt6.QtGui import QKeyEvent
    window = editing

    def press(key, mods=lens.Modifier.NoModifier):
        window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, mods))

    press(lens.Key.Key_Escape)
    assert not window.editing
    press(lens.Key.Key_D)
    assert window.show_deprecated
    press(lens.Key.Key_E)
    assert window.editing


################################################################
# The system
################################################################

def test_entity_lens(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # Plays the Terminal: creates a person working for an organisation,
    # and has the lens use the person.
    client_path = write_app(tmp_path / "client", """
        import time
        import darq
        import darq.kb as model
        import darq.launch
        from darq.protocols.history import History
        from darq.protocols.lens import Lens
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.find_lenses(type_id="darq.person", context="terminal").lenses:
                if time.monotonic() > deadline:
                    return "no entity lens registered"
                time.sleep(0.05)
            path = darq.launch.find_lens("darq.person")
            if not path.endswith("lenses/entity"):
                return f"lens is {path}"

            org_id, org_port = darq.launch.create_item("darq.organisation")
            darq.close_port(org_port)
            person_id, person_port = darq.launch.create_item("darq.person")

            kb = model.KB.connect(timeout=10)
            employer = model.Property(model.Datatype.Item, "P108")
            employer.set_label("employer")
            kb.ensure(employer)
            person = kb.get(darq.split_item_id(person_id)[1])
            person.set_label("Ada")
            person.claim("P108", model.EntityValue(darq.split_item_id(org_id)[1]))
            kb.put(person)

            lens = Lens.Client(darq.launch.use_item(person_id, person_port), timeout=10)
            # Its type records it viewed, as it comes, one way.
            history = History.Client(darq.connect("darq.history"), timeout=10)
            deadline = time.monotonic() + 10
            while True:
                events = [(e.event, e.source) for e in history.get_events(item_ids=[person_id]).events]
                if ("viewed", "entity-lens") in events:
                    break
                if time.monotonic() > deadline:
                    return f"events are {events!r}"
                time.sleep(0.05)
            lens.close()
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)

    [lens_process] = [p for p in kernel._process_table.values() if p._image_path == str(LENS_PATH)]
    run_kernel_until(kernel, lambda: lens_process.get_status() == zombie, "the lens to exit")
    assert (lens_process.get_exit_status(), lens_process.get_exit_error()) == (0, None)


def add_tag_statement(window, qt_app):
    """Start a has tag statement, and return its value's editor."""
    window.form.pick_statement()
    settle(qt_app)
    picker = window.form.start_picker
    picker.line.setText("has tag")
    picker.search()
    assert picker.accept()
    settle(qt_app)
    return window.form.editors["new-statement:0"]


def test_a_tag_is_chosen_from_the_tags(editing, world, qt_app):
    world.add("darq.tag:fiction", "Fiction")
    tag = add_tag_statement(editing, qt_app)
    tag.picker.line.setText("fic")
    tag.picker.search()
    assert [(link.entity_id, link.label) for link in tag.picker.found] == [("fiction", "Fiction"),
                                                                           ("", 'New tag "fic"')]
    # Ada's found by "a", but she's not a tag.
    tag.picker.line.setText("fiction")
    tag.picker.search()
    assert [link.entity_id for link in tag.picker.found] == ["fiction"]
    assert tag.picker.accept()
    settle(qt_app)
    [made] = world.entities[ADA].get_statements(darq.items.HAS_TAG)
    assert made.get_value().get_id() == "fiction"


def test_a_new_tag_is_made(editing, entity_lens, world, qt_app):
    made = []

    def make(name, language):
        made.append((name, language))
        world.add("darq.tag:reading", name)
        return Linked("reading", "darq.tag:reading", language, name)

    entity_lens.tag_maker = make
    tag = add_tag_statement(editing, qt_app)
    tag.picker.line.setText("  To   read ")
    tag.picker.search()
    assert [link.label for link in tag.picker.found] == ['New tag "To read"']
    assert tag.picker.accept()
    settle(qt_app)
    assert made == [("To read", editing.language)]
    [tagged] = world.entities[ADA].get_statements(darq.items.HAS_TAG)
    assert tagged.get_value().get_id() == "reading"


def test_a_tag_that_cant_be_made(editing, entity_lens, world, qt_app):
    def fail(name, language):
        raise darq.DarqError("no KB")

    entity_lens.tag_maker = fail
    tag = add_tag_statement(editing, qt_app)
    tag.picker.line.setText("later")
    tag.picker.search()
    assert not tag.picker.accept()
    assert tag.picker.hint.text() == "Can't make it"
    assert "Can't make a tag 'later': no KB" in editing.status.text()
    assert world.entities[ADA].get_statements(darq.items.HAS_TAG) == []


def test_making_a_tag_names_it(world, monkeypatch):
    """A tag's created by the KB type implementation, and named through
    its port, which is closed."""
    world.add("darq.tag:t1", "")
    created, closed = [], []
    monkeypatch.setattr(lens.darq.launch, "create_item",
                        lambda type_id, context: created.append((type_id, context.source)) or ("darq.tag:t1", 77))
    monkeypatch.setattr(lens.darq, "close_port", closed.append)
    monkeypatch.setattr(lens.EntityItem, "Client", lambda port, timeout=None: FakeItem(world, port))
    world.ports[77] = "darq.tag:t1"
    made = lens.make_tag(" To  read ", "en")
    assert created == [(darq.items.TAG_TYPE, "entity-lens")] and closed == [77]
    assert world.contexts[-1].quiet
    assert (made.entity_id, made.item_id, made.label) == ("t1", "darq.tag:t1", "To read")
    assert world.entities["darq.tag:t1"].get_label() == "To read"
