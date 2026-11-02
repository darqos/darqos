# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Index service, services/index/main.py: its handler
# directly, on a temporary database with a fake clock; darq.index, its
# client; and then the service, through a real p-kernel (see
# test_pkernel.py for the fixtures).

import importlib.util
import logging
import os
import pathlib
import stat

import pytest

import darq
import darq.index
from darq.protocols.index import Field, FieldKind, Publish, SearchRequest, Status, Withdraw

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "index"


def load_service():
    spec = importlib.util.spec_from_file_location("index_service", SERVICE_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_service()

TEXT = "public.utf8-plain-text"
DOC = f"{TEXT}:0123456789abcdef0123456789abcdef"
OTHER = f"{TEXT}:fedcba9876543210fedcba9876543210"
PERSON = "darq.person:Q7259"


class Clock:
    """A clock that only moves when told."""

    def __init__(self, now: int = 1_800_000_000_000_000):
        self.now = now

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def database(tmp_path):
    return str(tmp_path / "index.sqlite")


@pytest.fixture
def service(database, clock):
    service = module.IndexService(database, clock)
    yield service
    service.close()


def publish(service, item_id=DOC, body="", names=()):
    fields = [Field(FieldKind.NAMES, n) for n in names]
    if body:
        fields.append(Field(FieldKind.BODY, body))
    assert service.publish(Publish(item_id=item_id, fields=fields), 0) is None


def search(service, text, **kwargs) -> list[str]:
    response = service.search(SearchRequest(text=text, **kwargs), 0)
    assert response.status == Status.OK
    return [r.item_id for r in response.results]


################################################################
# Publishing and searching
################################################################

def test_publish_and_search(service, clock):
    publish(service, DOC, "Meet at the coffee shop on Tuesday")
    response = service.search(SearchRequest(text="coffee"), 0)
    assert response.status == Status.OK
    [result] = response.results
    assert (result.item_id, result.updated) == (DOC, clock.now)
    assert "coffee shop" in result.snippet


@pytest.mark.parametrize("text", ["cof sho", "COFFEE", "Café", "tues coffee"])
def test_words_are_prefixes_ignoring_case_and_accents(service, text):
    publish(service, DOC, "Meet at the cafe, the coffee shop, on Tuesday")
    assert search(service, text) == [DOC]


def test_every_word_must_match(service):
    publish(service, DOC, "coffee shop")
    publish(service, OTHER, "coffee beans")
    assert search(service, "coffee shop") == [DOC]
    assert sorted(search(service, "coffee")) == [DOC, OTHER]
    assert search(service, "tea") == []


@pytest.mark.parametrize("text", ["", "  ", "...", '"', "*"])
def test_no_words_finds_nothing(service, text):
    publish(service, DOC, "anything")
    assert search(service, text) == []


@pytest.mark.parametrize("text", ['coffee"', "coffee*", "coffee AND", "(coffee", "body:coffee", "-coffee"])
def test_query_syntax_is_just_words(service, text):
    publish(service, DOC, "coffee and body")
    assert search(service, text) == [DOC]


def test_names_rank_above_body(service, clock):
    publish(service, DOC, "Ada wrote about the analytical engine, Ada said")
    clock.now += 1
    publish(service, PERSON, names=["Ada Lovelace", "Augusta Ada King"])
    assert search(service, "ada") == [PERSON, DOC]


def test_recency_breaks_ties(service, clock):
    publish(service, DOC, "coffee")
    clock.now += 1
    publish(service, OTHER, "coffee")
    assert search(service, "coffee") == [OTHER, DOC]


def test_publish_replaces(service, clock):
    publish(service, DOC, "first draft")
    clock.now += 1
    publish(service, DOC, "second version")
    assert search(service, "first") == []
    response = service.search(SearchRequest(text="second"), 0)
    assert [(r.item_id, r.updated) for r in response.results] == [(DOC, clock.now)]


def test_withdraw(service):
    publish(service, DOC, "coffee")
    publish(service, OTHER, "coffee")
    assert service.withdraw(Withdraw(item_id=DOC), 0) is None
    assert search(service, "coffee") == [OTHER]

    # Withdrawing what isn't indexed does nothing.
    service.withdraw(Withdraw(item_id=DOC), 0)
    assert search(service, "coffee") == [OTHER]


def test_identifiers_are_canonical(service):
    publish(service, "PUBLIC.UTF8-Plain-Text:0123456789abcdef0123456789abcdef", "coffee")
    assert search(service, "coffee") == [DOC]
    service.withdraw(Withdraw(item_id=DOC.upper().replace("0123456789ABCDEF", "0123456789abcdef")), 0)
    assert search(service, "coffee") == []


@pytest.mark.parametrize("item_id", ["no colon", "", f"{TEXT}:"])
def test_invalid_items_are_dropped(service, caplog, item_id):
    with caplog.at_level(logging.WARNING):
        publish(service, item_id, "coffee")
        service.withdraw(Withdraw(item_id=item_id), 0)
    assert search(service, "coffee") == []
    assert "Dropped entry" in caplog.text and "Dropped withdrawal" in caplog.text


def test_type_filter(service):
    publish(service, DOC, "ada")
    publish(service, PERSON, names=["Ada"])
    assert search(service, "ada", type_ids=["darq.person"]) == [PERSON]
    assert sorted(search(service, "ada", type_ids=["DARQ.Person", TEXT])) == sorted([PERSON, DOC])
    assert search(service, "ada", type_ids=["darq.place"]) == []
    response = service.search(SearchRequest(text="ada", type_ids=["no good:"]), 0)
    assert (response.status, response.results) == (Status.INVALID_ARGUMENT, [])


def test_max_and_offset(service, clock, monkeypatch):
    items = [f"{TEXT}:{n:032x}" for n in range(5)]
    for item_id in items:
        publish(service, item_id, "coffee")
        clock.now += 1
    newest = list(reversed(items))
    assert search(service, "coffee") == newest
    assert search(service, "coffee", max=2) == newest[:2]
    assert search(service, "coffee", max=2, offset=2) == newest[2:4]
    assert search(service, "coffee", offset=4) == newest[4:]

    monkeypatch.setattr(module, "MAX_RESULTS", 3)
    assert search(service, "coffee") == newest[:3]
    assert search(service, "coffee", max=10) == newest[:3]


def test_text_is_capped(service, monkeypatch):
    monkeypatch.setattr(module, "MAX_TEXT", 20)
    publish(service, DOC, "early words ééé later words", names=["Ada"])
    assert search(service, "ada") == [DOC]
    assert search(service, "early") == [DOC]
    assert search(service, "later") == []


def test_truncate():
    assert module.truncate("abc", 3) == "abc"
    assert module.truncate("abcd", 3) == "abc"
    # A character isn't split.
    assert module.truncate("aé", 2) == "a"
    assert module.truncate("aé", 3) == "aé"


################################################################
# The database
################################################################

def test_entries_persist(database, clock):
    service = module.IndexService(database, clock)
    publish(service, DOC, "coffee")
    service.close()

    service = module.IndexService(database, clock)
    try:
        assert search(service, "coffee") == [DOC]
    finally:
        service.close()


def test_only_its_owner_can_read_it(service, database):
    assert stat.S_IMODE(os.stat(database).st_mode) == 0o600


################################################################
# Clients
################################################################

class FakeIndex:
    """Stands in for Index.Client, recording what's sent."""

    sent: list[tuple] = []
    fail = False

    def __init__(self, handle, timeout=None):
        self.handle = handle

    def publish(self, item_id, fields):
        if FakeIndex.fail:
            raise darq.DarqError("gone")
        FakeIndex.sent.append(("publish", self.handle, item_id, [(f.kind, f.text) for f in fields]))

    def withdraw(self, item_id):
        if FakeIndex.fail:
            raise darq.DarqError("gone")
        FakeIndex.sent.append(("withdraw", self.handle, item_id))


@pytest.fixture
def fake_index(monkeypatch):
    lookups = []
    handles = iter(range(7, 100))

    def lookup_name(name, blocking=True):
        assert (name, blocking) == ("darq.index", False)
        lookups.append(name)
        return next(handles) if registered else None

    registered = True
    monkeypatch.setattr(darq.index, "lookup_name", lookup_name)
    monkeypatch.setattr(darq.index.Index, "Client", FakeIndex)
    monkeypatch.setattr(FakeIndex, "sent", [])
    monkeypatch.setattr(FakeIndex, "fail", False)
    return lookups


def test_publisher(fake_index):
    publisher = darq.index.Publisher()
    publisher.publish(PERSON, names=["Ada", "", "Augusta"], body="mathematician")
    publisher.publish(DOC, body="")
    publisher.withdraw(DOC)
    assert FakeIndex.sent == [
        ("publish", 7, PERSON, [(FieldKind.NAMES, "Ada"), (FieldKind.NAMES, "Augusta"),
                                (FieldKind.BODY, "mathematician")]),
        ("publish", 7, DOC, []),
        ("withdraw", 7, DOC),
    ]
    # Looked up once.
    assert fake_index == ["darq.index"]


def test_publisher_without_the_index(monkeypatch):
    monkeypatch.setattr(darq.index, "lookup_name", lambda name, blocking=True: None)
    publisher = darq.index.Publisher()
    publisher.publish(DOC, body="coffee")
    publisher.withdraw(DOC)
    assert publisher.client is None


def test_publisher_failure_is_logged_and_looked_up_again(fake_index, caplog):
    publisher = darq.index.Publisher()
    FakeIndex.fail = True
    with caplog.at_level(logging.WARNING):
        publisher.publish(DOC, body="coffee")
    assert f"Can't publish {DOC}" in caplog.text
    assert publisher.client is None

    FakeIndex.fail = False
    publisher.publish(DOC, body="coffee")
    assert FakeIndex.sent == [("publish", 8, DOC, [(FieldKind.BODY, "coffee")])]


def test_search_without_the_index(monkeypatch):
    monkeypatch.setattr(darq.index, "lookup_name", lambda name, blocking=True: None)
    with pytest.raises(darq.DarqError):
        darq.index.search("coffee")


################################################################
# Service
################################################################

def test_index_service(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    client_path = write_app(tmp_path / "client", """
        import darq
        import darq.index

        DOC = "public.utf8-plain-text:0123456789abcdef0123456789abcdef"
        OTHER = "public.utf8-plain-text:fedcba9876543210fedcba9876543210"

        def darq_main():
            darq.connect("darq.index")
            publisher = darq.index.Publisher()
            publisher.publish(DOC, body="Meet at the coffee shop")
            publisher.publish(OTHER, body="Buy coffee beans")
            publisher.withdraw(OTHER)

            # Messages to a port arrive in order, so these are indexed
            # by the time it's answered.
            found = [(r.item_id, r.snippet) for r in darq.index.search("cof")]
            if found != [(DOC, "Meet at the coffee shop")]:
                return f"found {found!r}"
            try:
                darq.index.search("coffee", type_ids=["bad:"])
            except ValueError:
                return 0
            return "a bad type identifier was searched for"
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (pathlib.Path(kernel.run_dir) / "index.sqlite").exists()
