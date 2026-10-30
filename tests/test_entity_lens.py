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
import darq.kb as model
from darq.protocols import kb as wire
from darq.protocols.kb import (
    Changed,
    GetEntityResponse,
    GetLinksResponse,
    Linked,
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
render = lens.render

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


def page_for(entity, links=None, linked_from=(), more=0, note=None):
    page = render.Page(["en-AU", "en"], links or {})
    return page, page.render(entity, list(linked_from), more, note)


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
    page, html = page_for(ada, links, [render.Link("darq.person:bob", "Bob")], more=3, note="merged")

    assert "mathematician &lt;b&gt;" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "Also known as: Augusta" in html
    assert 'Acme &amp; Co</a> ★' in html
    assert '<s><a href="#2">old</a></s>' in html
    assert "start time" not in html and "P580: 1843" in html
    assert "source: P248: " in html and "Address book</a>" in html
    assert "1.65 ± 0.05 " in html and "metre</a>" in html
    assert "g&#x27;day" in html
    assert "employer" in html and 'href' not in html.split("employer")[0][-10:]
    assert "Linked from" in html and "and 3 more" in html and "(Person)" in html
    assert "merged" in html
    assert ACME in page.targets and "darq.person:bob" in page.targets

    # The property's name is on its first statement only.
    assert html.count(">employer<") == 1


def test_render_empty():
    _, html = page_for(model.Item("x"))
    assert "No statements." in html and "Linked from" not in html


################################################################
# Window and navigation, offscreen
################################################################

class World:
    """Items, as an EntityItem implementation would have them."""

    def __init__(self):
        self.entities: dict[str, model.Entity] = {}
        self.links: dict[str, list[Linked]] = {}
        self.ports: dict[int, str] = {}
        self.next_port = 500
        self.closed_items: list[str] = []
        self.watched: list[tuple[str, int]] = []
        self.failing: set[str] = set()

    def add(self, item_id, label, *refs):
        _, entity_id = darq.split_item_id(item_id)
        e = model.Item(entity_id)
        e.set_label(label)
        for ref in refs:
            e.claim("P108", model.EntityValue(darq.split_item_id(ref)[1]))
        self.entities[item_id] = e
        return e

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
        linked = []
        for s in entity.statements:
            value = s.get_value()
            assert isinstance(value, model.EntityValue)
            target = value.get_id()
            item_id = next(i for i, e in self.world.entities.items() if e.id == target)
            linked.append(Linked(target, item_id, "en", self.world.entities[item_id].get_label() or ""))
        linked.append(Linked("P108", "", "en", "employer"))
        return GetEntityResponse(entity=model.entity_to_wire(entity), linked=linked, status=wire.Status.OK)

    def get_links(self, property_id, role, languages, max, after):
        return GetLinksResponse(linked=self.world.links.get(self.item_id, []), status=wire.Status.OK)

    def watch_entity(self, listener):
        self.world.watched.append((self.item_id, listener))
        return WatchEntityResponse(status=wire.Status.OK)

    def close(self):
        self.world.closed_items.append(self.item_id)


class FakeOpener:
    def __init__(self, world):
        self.world = world
        self.used: list[str] = []

    def open(self, item_id):
        return self.world.open(item_id)

    def use(self, item_id):
        self.used.append(item_id)


class FakeLoop:
    def __init__(self):
        self.stopped = False

    def post(self, function):
        function()

    def stop(self):
        self.stopped = True


class FakeHistory:
    def __init__(self):
        self.events = []

    def add_event(self, item_id, event, source):
        self.events.append((item_id, event, source))


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
    history = FakeHistory()
    loop = FakeLoop()
    entity_lens = lens.EntityLens(loop, history, FakeOpener(world))
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
    assert entity_lens.history.events == [(ADA, "opened", "entity-lens")]
    assert window.targets == [ACME]

    other = world.open(ACME)
    assert entity_lens.use(UseRequest(item_id=ACME, item=other), 0).status == Status.BUSY


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
    assert "Linked from" in window.browser.toHtml()
    assert window.targets == [ADA, "darq.person:bob"]

    window.follow("darq.person:bob")
    assert window.back == [(ADA, 0), (ACME, 0)]
    window.go_back()
    assert window.title.text() == "Acme"
    window.go_back()
    assert window.title.text() == "Ada" and not window.back_button.isEnabled()
    window.go_back()
    assert window.title.text() == "Ada"

    opened = [e[0] for e in entity_lens.history.events]
    assert opened == [ADA, ACME, "darq.person:bob", ACME, ADA]


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
            history = History.Client(darq.connect("darq.history"), timeout=10)
            events = [(e.event, e.source) for e in history.get_events(item_ids=[person_id]).events]
            if events != [("opened", "entity-lens")]:
                return f"events are {events!r}"
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
