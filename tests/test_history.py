# darqos
# Copyright (C) 2026 David Arnold

# Tests for the History service, services/history/main.py: its handler
# directly, on a temporary database with a fake clock, and then as a
# service, through a real p-kernel (see test_pkernel.py for the
# fixtures).

import importlib.util
import logging
import pathlib

import pytest

from darq.protocols.history import AddEvent, GetEventsRequest, Status

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "history"


def load_service():
    spec = importlib.util.spec_from_file_location("history_service", SERVICE_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_service()

TEXT = "public.utf8-plain-text"
DOC = f"{TEXT}:0123456789abcdef0123456789abcdef"
OTHER = f"{TEXT}:fedcba9876543210fedcba9876543210"
URL = "public.url:https://example.com/"

MINUTE = 60 * 1_000_000


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
    return str(tmp_path / "history.sqlite")


@pytest.fixture
def service(database, clock):
    service = module.HistoryService(database, clock)
    yield service
    service.db.close()


def add(service, item_id=DOC, event="edited", source="lens"):
    assert service.add_event(AddEvent(item_id=item_id, event=event, source=source), 0) is None


def get(service, **kwargs) -> list[tuple]:
    response = service.get_events(GetEventsRequest(**kwargs), 0)
    assert response.status == Status.OK
    return [(e.time, e.item_id, e.event, e.source, e.count) for e in response.events]


################################################################
# AddEvent
################################################################

def test_add_and_get(service, clock):
    add(service, event="created")
    clock.now += 1
    add(service, OTHER, "opened", "indexer")
    assert get(service) == [
        (clock.now, OTHER, "opened", "indexer", 1),
        (clock.now - 1, DOC, "created", "lens", 1),
    ]


def test_names_and_ids_are_normalized(service, clock):
    add(service, "PUBLIC.UTF8-Plain-Text:0123456789abcdef0123456789abcdef", "Edited", "ＬＥＮＳ")
    assert get(service) == [(clock.now, DOC, "edited", "lens", 1)]


@pytest.mark.parametrize("item_id, event, source", [
    ("no colon", "edited", "lens"),
    (DOC, "", "lens"),
    (DOC, "edited", ""),
    (DOC, "two words", "lens"),
    (DOC, "edited", "le\tns"),
    (DOC, "nul\x00", "lens"),
    (DOC, "e" * 32, "lens"),
])
def test_invalid_events_are_dropped(service, caplog, item_id, event, source):
    with caplog.at_level(logging.WARNING):
        add(service, item_id, event, source)
    assert get(service) == []
    assert "Dropped event" in caplog.text


def test_times_are_unique(service, clock):
    # The clock doesn't move, then goes back.
    add(service, event="created")
    add(service, event="opened")
    clock.now -= MINUTE
    add(service, event="edited")
    times = [e[0] for e in get(service)]
    assert times == [clock.now + MINUTE + 2, clock.now + MINUTE + 1, clock.now + MINUTE]


def test_repeats_are_counted(service, clock):
    add(service)
    first = clock.now
    for _ in range(3):
        clock.now += MINUTE
        add(service)
    assert get(service) == [(first, DOC, "edited", "lens", 4)]


def test_throttle_is_from_the_first(service, clock):
    # Ten minutes after it was first recorded, it's recorded again, even
    # though it's been repeated since.
    add(service)
    first = clock.now
    clock.now += 9 * MINUTE
    add(service)
    clock.now += MINUTE
    add(service)
    assert get(service) == [(first + 10 * MINUTE, DOC, "edited", "lens", 1), (first, DOC, "edited", "lens", 2)]


def test_every_event_is_throttled_alike(service, clock):
    for event in ("created", "opened", "destroyed", "printed"):
        add(service, event=event)
        clock.now += 1
        add(service, event=event)
        clock.now += 1
    assert [(e[2], e[4]) for e in get(service)] == [("printed", 2), ("destroyed", 2), ("opened", 2), ("created", 2)]


def test_throttle_is_per_item_event_and_source(service, clock):
    add(service)
    add(service, OTHER)
    add(service, event="opened")
    add(service, source="indexer")
    add(service)
    assert [e[1:] for e in get(service)] == [
        (DOC, "edited", "indexer", 1),
        (DOC, "opened", "lens", 1),
        (OTHER, "edited", "lens", 1),
        (DOC, "edited", "lens", 2),
    ]


def test_events_persist(database, clock):
    service = module.HistoryService(database, clock)
    add(service, event="created")
    service.db.close()

    service = module.HistoryService(database, clock)
    assert get(service) == [(clock.now, DOC, "created", "lens", 1)]
    service.db.close()


################################################################
# GetEvents
################################################################

@pytest.fixture
def events(service, clock):
    """Five events, a minute apart: return their times."""
    times = []
    for item_id, event, source in [
        (DOC, "created", "lens"),
        (OTHER, "created", "lens"),
        (DOC, "edited", "lens"),
        (URL, "opened", "lens"),
        (OTHER, "opened", "indexer"),
    ]:
        add(service, item_id, event, source)
        times.append(clock.now)
        clock.now += MINUTE
    return times


def test_time_bounds_are_half_open(service, events):
    assert [e[0] for e in get(service, start=events[1], end=events[3])] == [events[2], events[1]]
    assert get(service, start=events[4] + 1) == []
    assert get(service, start=events[2], end=events[2]) == []


def test_end_zero_is_now(service, events):
    assert len(get(service, start=events[0], end=0)) == 5


@pytest.mark.parametrize("filters, expected", [
    ({"type_ids": ["public.url"]}, [3]),
    ({"type_ids": ["Public.UTF8-Plain-Text"]}, [4, 2, 1, 0]),
    ({"type_ids": [TEXT, "public.url"]}, [4, 3, 2, 1, 0]),
    ({"item_ids": [DOC]}, [2, 0]),
    ({"item_ids": [DOC, URL]}, [3, 2, 0]),
    ({"events": ["CREATED"]}, [1, 0]),
    ({"sources": ["indexer"]}, [4]),
    ({"item_ids": [OTHER], "events": ["opened"], "sources": ["lens"]}, []),
    ({"type_ids": [TEXT], "events": ["opened", "created"]}, [4, 1, 0]),
])
def test_filters(service, events, filters, expected):
    assert [e[0] for e in get(service, **filters)] == [events[n] for n in expected]


def test_type_ids_match_exactly(service, events):
    # Not by conformance, yet.
    assert get(service, type_ids=["public.text"]) == []


@pytest.mark.parametrize("filters", [
    {"type_ids": ["not a type"]},
    {"item_ids": ["no colon"]},
    {"events": ["two words"]},
    {"sources": [""]},
])
def test_invalid_filters(service, events, filters):
    response = service.get_events(GetEventsRequest(**filters), 0)
    assert (response.status, response.events) == (Status.INVALID_ARGUMENT, [])


def test_paging(service, events):
    page = get(service, max=2)
    assert [e[0] for e in page] == [events[4], events[3]]
    page = get(service, end=page[-1][0], max=2)
    assert [e[0] for e in page] == [events[2], events[1]]
    page = get(service, end=page[-1][0], max=2)
    assert [e[0] for e in page] == [events[0]]


def test_limit(service, clock, monkeypatch):
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    for n in range(5):
        add(service, event=f"e{n}")
    assert len(get(service)) == 3
    assert len(get(service, max=2)) == 2
    assert len(get(service, max=10)) == 3


################################################################
# Service
################################################################

def test_history_service(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    client_path = write_app(tmp_path / "client", """
        import darq
        from darq.protocols.history import History, Status

        DOC = "public.utf8-plain-text:0123456789abcdef0123456789abcdef"

        def darq_main():
            history = History.Client(darq.connect("darq.history"), timeout=10)
            history.add_event(item_id=DOC, event="created", source="lens")
            history.add_event(item_id=DOC, event="Edited", source="lens")
            history.add_event(item_id=DOC, event="edited", source="lens")
            history.add_event(item_id=DOC, event="not valid", source="lens")

            # Messages to a port arrive in order, so these are recorded
            # by the time it's answered.
            response = history.get_events()
            if response.status != Status.OK:
                return f"get_events: {response!r}"
            events = [(e.item_id, e.event, e.source, e.count) for e in response.events]
            if events != [(DOC, "edited", "lens", 2), (DOC, "created", "lens", 1)]:
                return f"events are {events!r}"
            if response.events[0].time <= response.events[1].time:
                return "events aren't newest first"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (pathlib.Path(kernel.run_dir) / "history.sqlite").exists()
