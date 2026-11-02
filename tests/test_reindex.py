# darqos
# Copyright (C) 2026 David Arnold

# Tests for the reindex, tools/reindex/main.py: with faked History, Type
# service and implementations; the Index service starting it with a new
# database; and then the whole thing, through a real p-kernel (see
# test_pkernel.py for the fixtures).

import importlib.util
import logging
import pathlib

import pytest

import darq
import darq.kb as model
from darq.protocols.history import Event, GetEventsResponse
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.type import ActivateResponse, CreateRequest, IndexEntryRequest, IndexEntryResponse, Status

from test_index import module as index_module
from test_kb_type import entity, implementation, index, kb, kb_service, ports  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


ROOT = pathlib.Path(__file__).parent.parent
REINDEX_PATH = ROOT / "tools" / "reindex"


def load():
    spec = importlib.util.spec_from_file_location("reindex_tool", REINDEX_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load()

TEXT = "public.utf8-plain-text"
DOC = f"{TEXT}:0123456789abcdef0123456789abcdef"
GONE = f"{TEXT}:fedcba9876543210fedcba9876543210"
PICTURE = "public.png:00000000000000000000000000000001"
THING = "test.thing:1"


class FakeHistory:
    """Events, newest first, filtered by event, and paged as History
    pages them."""

    def __init__(self, events: list[tuple[str, str]]):
        # (item, event), oldest first.
        self.events = [Event(time, item_id, event, "test", 1) for time, (item_id, event) in enumerate(events, 1)]
        self.queries = []

    def get_events(self, start, end, events, max):
        self.queries.append(end)
        found = [e for e in reversed(self.events) if e.event in events and (end == 0 or e.time < end)]
        return GetEventsResponse(events=found[:max], status=HistoryStatus.OK)


class FakeImplementation:
    def __init__(self, entries: dict[str, IndexEntryResponse]):
        self.entries = entries

    def index_entry(self, item_id):
        if item_id not in self.entries:
            raise darq.DarqError("not answering")
        return self.entries[item_id]


class FakeTypes:
    """Activates the types it has implementations for: by port."""

    def __init__(self, implemented: dict[str, int]):
        self.ports = implemented
        self.activated = []

    def activate(self, type_id):
        self.activated.append(type_id)
        if type_id not in self.ports:
            return ActivateResponse(status=Status.NOT_FOUND)
        return ActivateResponse(implementation=self.ports[type_id], status=Status.OK)


class FakePublisher:
    def __init__(self):
        self.sent = []

    def publish(self, item_id, names=(), body=""):
        self.sent.append(("publish", item_id, list(names), body))

    def withdraw(self, item_id):
        self.sent.append(("withdraw", item_id))


def test_item_ids(monkeypatch):
    monkeypatch.setattr(module, "PAGE", 2)
    history = FakeHistory([(DOC, "created"), (GONE, "created"), (DOC, "opened"), (PICTURE, "imported"),
                           (DOC, "edited"), (GONE, "destroyed"), (DOC, "created")])
    assert module.item_ids(history) == [DOC, PICTURE, GONE]
    # Paged by the oldest time of each page.
    assert history.queries == [0, 4, 1]


def test_reindex(caplog):
    history = FakeHistory([(DOC, "created"), (GONE, "created"), (PICTURE, "imported"), (THING, "created")])
    texts = FakeImplementation({
        DOC: IndexEntryResponse(names=[], body="coffee shop", status=Status.OK),
        GONE: IndexEntryResponse(status=Status.NOT_FOUND),
    })
    pictures = FakeImplementation({PICTURE: IndexEntryResponse(status=Status.UNSUPPORTED)})
    types = FakeTypes({TEXT: 1, "public.png": 2})
    publisher = FakePublisher()

    with caplog.at_level(logging.WARNING):
        counts = module.reindex(history, types, publisher, connect={1: texts, 2: pictures}.__getitem__)
    assert counts == module.Counts(published=1, withdrawn=1, skipped=2)
    assert sorted(publisher.sent) == [("publish", DOC, [], "coffee shop"), ("withdraw", GONE)]

    # Each type's activated once; one that can't be is logged, but not
    # one whose items aren't indexed.
    assert sorted(types.activated) == sorted([TEXT, "public.png", "test.thing"])
    assert "Can't activate test.thing: NOT_FOUND" in caplog.text
    assert PICTURE not in caplog.text


def test_unanswered_entries_are_skipped(caplog):
    history = FakeHistory([(DOC, "created"), (GONE, "created")])
    texts = FakeImplementation({DOC: IndexEntryResponse(body="kept", status=Status.OK)})
    publisher = FakePublisher()
    with caplog.at_level(logging.WARNING):
        counts = module.reindex(history, FakeTypes({TEXT: 1}), publisher, connect=lambda port: texts)
    assert counts == module.Counts(published=1, withdrawn=0, skipped=1)
    assert f"Can't get the entry for {GONE}" in caplog.text


################################################################
# The KB's entities
################################################################

def test_kb_item_ids(kb, implementation):  # noqa: F811
    person = implementation.create(CreateRequest(type_id="darq.person"), 0).item_id
    implementation.create(CreateRequest(type_id="darq.entity"), 0)
    kb.put(entity("Q7259", "Ada", "Q5"), entity("acme", "Acme", "Q43229"), entity("Q1", "Somewhere", "Q319608"),
           entity("Q2", "Nothing much"), model.Property(model.Datatype.String, "P999"))
    gone = implementation.create(CreateRequest(type_id="darq.group"), 0).item_id
    kb.destroy(darq.split_item_id(gone)[1])

    assert sorted(module.kb_item_ids(kb)) == sorted(
        [person, "darq.person:Q7259", "darq.organisation:acme", "darq.place:Q1"])


def test_reindex_with_kb(kb, implementation):  # noqa: F811
    class Entities:
        def index_entry(self, item_id):
            return implementation.index_entry(IndexEntryRequest(item_id=item_id), 0)

    kb.put(entity("Q7259", "Ada", "Q5"))
    history = FakeHistory([(DOC, "created")])
    texts = FakeImplementation({DOC: IndexEntryResponse(body="coffee", status=Status.OK)})
    publisher = FakePublisher()
    counts = module.reindex(history, FakeTypes({TEXT: 1, "darq.person": 2}), publisher, kb,
                            connect={1: texts, 2: Entities()}.__getitem__)
    assert counts == module.Counts(published=2, withdrawn=0, skipped=0)
    assert ("publish", "darq.person:Q7259", ["Ada"], "") in publisher.sent


################################################################
# The Index starts it
################################################################

class Started(Exception):
    pass


@pytest.mark.parametrize("exists", [False, True])
def test_index_reindexes_a_new_database(tmp_path, monkeypatch, exists):
    if exists:
        index_module.IndexService(str(tmp_path / index_module.DATABASE)).close()
    threads = []

    class Server:
        def add(self, port, handler):
            handler.close()

        def run(self):
            raise Started

    class Thread:
        def __init__(self, target, name, daemon):
            threads.append(target)

        def start(self):
            pass

    monkeypatch.setenv("DARQ_RUN", str(tmp_path))
    monkeypatch.setattr(darq, "open_port", lambda: 5)
    monkeypatch.setattr(darq, "register_name", lambda name, port: None)
    monkeypatch.setattr(darq, "Server", Server)
    monkeypatch.setattr(index_module.threading, "Thread", Thread)
    with pytest.raises(Started):
        index_module.darq_main()
    assert threads == ([] if exists else [index_module.reindex])


def test_index_runs_and_collects_the_reindex(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("DARQ_ROOT", str(tmp_path))
    monkeypatch.setattr(darq, "spawn_process", lambda path: calls.append(("spawn", path)) or 9)
    monkeypatch.setattr(darq, "wait", lambda dpid: calls.append(("wait", dpid)))
    monkeypatch.setattr(darq, "release_reply_port", lambda: calls.append(("release",)))
    index_module.reindex()
    assert calls == [("spawn", str(tmp_path / "tools" / "reindex")), ("wait", 9), ("release",)]


################################################################
# End to end
################################################################

def test_reindex_tool(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # A client that creates a text item, as New does, loses its entry,
    # and reindexes, as the Tools menu does.
    client_path = write_app(tmp_path / "client", f"""
        import time
        import darq
        import darq.history
        import darq.index
        from darq.protocols.history import History
        from darq.protocols.text import Text
        from darq.protocols.type import Implementation, Status, Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="public.utf8-plain-text").implementation:
                if time.monotonic() > deadline:
                    return "public.utf8-plain-text never registered"
                time.sleep(0.05)
            activated = types.activate(type_id="public.utf8-plain-text")
            implementation = Implementation.Client(activated.implementation, timeout=10)
            created = implementation.create(type_id="public.utf8-plain-text")
            History.Client(darq.connect("darq.history"), timeout=10).add_event(
                item_id=created.item_id, event=darq.history.CREATED, source="test")
            item = Text.Client(created.item, timeout=10)
            item.replace(start=0, end=0, text="Reindexed words")
            item.close()

            def found():
                return [r.item_id for r in darq.index.search("reindexed")] == [created.item_id]

            deadline = time.monotonic() + 10
            while not found():
                if time.monotonic() > deadline:
                    return "never published"
                time.sleep(0.05)
            darq.index.Publisher().withdraw(created.item_id)
            while found():
                if time.monotonic() > deadline:
                    return "never withdrawn"
                time.sleep(0.05)

            dpid = darq.spawn_process({str(REINDEX_PATH)!r})
            status = darq.wait(dpid)
            if status.status != 0:
                return f"reindex: {{status!r}}"
            while not found():
                if time.monotonic() > deadline:
                    return "never reindexed"
                time.sleep(0.05)
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
