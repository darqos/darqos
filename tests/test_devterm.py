# darqos
# Copyright (C) 2026 David Arnold

# Tests for the development Terminal, tools/devterm/main.py: its window,
# offscreen, with faked services; and then started at boot through a
# real p-kernel (see test_pkernel.py for the fixtures).

import importlib.util
import pathlib
import time

import pytest

from darq.errors import LaunchError
from darq.ui import history as ui_history
from darq.protocols.history import Event, GetEventsResponse
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.type import (
    Description,
    DescribeResponse,
    FindLensesResponse,
    ListTypesResponse,
    Status,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until  # noqa: F401


DEVTERM_PATH = pathlib.Path(__file__).parent.parent / "tools" / "devterm"
TEXT = "public.utf8-plain-text"
DOC = f"{TEXT}:0123456789abcdef0123456789abcdef"


def load():
    spec = importlib.util.spec_from_file_location("devterm", DEVTERM_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


devterm = load()


class FakeTypes:
    def __init__(self, types=None):
        # Type identifier to (name, has a lens).
        self.types = {TEXT: ("UTF-8 Text", True), "public.png": ("PNG", False)} if types is None else types

    def list_types(self, implemented):
        assert implemented
        return ListTypesResponse(type_ids=sorted(self.types))

    def describe(self, type_id):
        name, _ = self.types[type_id]
        return DescribeResponse(description=Description(name=name, description=f"About {name}"),
                                status=Status.OK)

    def find_lenses(self, type_id, context):
        assert context == "terminal"
        return FindLensesResponse(lenses=["/lens"] if self.types[type_id][1] else [], status=Status.OK)


class FakeHistory:
    def __init__(self):
        self.added = []
        self.events: list[Event] = []
        self.queries = []

    def add_event(self, item_id, event, source):
        self.added.append((item_id, event, source))

    def get_events(self, **query):
        self.queries.append(query)
        if any(len(name) > 31 for name in query["events"]):
            return GetEventsResponse(status=HistoryStatus.INVALID_ARGUMENT)
        end = query["end"] or 2**62
        events = [e for e in self.events if query["start"] <= e.time < end]
        return GetEventsResponse(events=events[:query["max"]], status=HistoryStatus.OK)


class FakeLaunch:
    def __init__(self):
        self.created = []
        self.used = []
        self.fail = False

    def create_item(self, type_id):
        if self.fail:
            raise LaunchError(f"Can't activate {type_id}")
        self.created.append(type_id)
        return DOC, 42

    def use_item(self, item_id, item=None):
        if self.fail:
            raise LaunchError(f"No lens for {item_id}")
        self.used.append((item_id, item))
        return 7


class FakeServices:
    def __init__(self, types=None):
        self.types = FakeTypes(types)
        self.history = FakeHistory()
        self.launch = FakeLaunch()
        self.calls = []

    def shutdown(self):
        self.calls.append("shutdown")

    def reboot(self):
        self.calls.append("reboot")


def inline(function):
    """Run a launch, or post its result, at once: see darq.ui.launcher."""
    function()


@pytest.fixture
def terminal(qt_app):
    created = []

    def make(types=None):
        services = FakeServices(types)
        window = devterm.DevTerminal(services)
        window.launcher.run = window.launcher.post = inline
        window.show()
        created.append(window)
        return window, services

    yield make
    for window in created:
        window.close()
        window.loop.close()


################################################################
# Helpers
################################################################

@pytest.mark.parametrize("text, names", [
    ("", []),
    ("edited", ["edited"]),
    ("created, opened", ["created", "opened"]),
    (" a,b  c ,, ", ["a", "b", "c"]),
])
def test_parse_names(text, names):
    assert ui_history.parse_names(text) == names


def test_times(qt_app):
    from PyQt6.QtCore import QDateTime
    when = QDateTime.fromMSecsSinceEpoch(1_800_000_000_123)
    assert ui_history.to_micros(when) == 1_800_000_000_123_000
    assert ui_history.format_time(1_800_000_000_123_000) == when.toString("yyyy-MM-dd HH:mm:ss")


################################################################
# New
################################################################

def test_types_listed(terminal):
    from PyQt6.QtCore import Qt
    window, _ = terminal()
    table = window.factory.table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["PNG", "UTF-8 Text"]
    assert [table.item(row, 1).text() for row in range(table.rowCount())] == ["public.png", TEXT]

    # A type with no lens is greyed out.
    assert not table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert table.item(1, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert window.status.text() == "2 implemented types"

    # And they're offered as History filters.
    history = window.history.types
    assert [history.item(n).text() for n in range(history.count())] == ["public.png", TEXT]


def test_create(terminal):
    window, services = terminal()
    window.factory.table.selectRow(1)
    window.factory.create_button.click()
    assert services.launch.created == [TEXT]
    assert services.history.added == [(DOC, "created", "devterm")]
    assert services.launch.used == [(DOC, 42)]
    assert window.status.text() == f"Using {DOC}"


def test_create_needs_a_usable_type(terminal):
    window, services = terminal()
    window.factory.create_item()
    assert window.status.text() == "Select a type with a lens"

    # The greyed-out type can't be selected.
    window.factory.table.selectRow(0)
    window.factory.create_item()
    assert services.launch.created == []


def test_create_failure(terminal):
    window, services = terminal()
    services.launch.fail = True
    window.factory.table.selectRow(1)
    window.factory.create_item()
    assert window.status.text() == f"Can't create a UTF-8 Text: Can't activate {TEXT}"
    assert services.history.added == []


def test_types_retried_until_synced(terminal):
    window, services = terminal(types={})
    assert window.factory.table.rowCount() == 0
    assert window.retry.isActive()

    services.types.types = {TEXT: ("UTF-8 Text", True)}
    window.load_types()
    assert window.factory.table.rowCount() == 1
    assert not window.retry.isActive()


################################################################
# History
################################################################

def events(n: int, start: int = 1_800_000_000_000_000) -> list[Event]:
    """Newest first, a second apart."""
    return [Event(time=start + (n - i) * 1_000_000, item_id=DOC, event="edited", source="text-lens", count=i + 1)
            for i in range(n)]


def test_search(terminal):
    window, services = terminal()
    services.history.events = events(3)
    tab = window.history
    tab.set_period(0)
    query = services.history.queries[-1]
    assert (query["start"], query["end"], query["max"]) == (0, 0, ui_history.PAGE)
    assert tab.table.rowCount() == 3
    assert [tab.table.item(0, c).text() for c in range(1, 6)] == [
        TEXT, "0123456789abcdef0123456789abcdef", "edited", "text-lens", "1"]
    assert not tab.more.isEnabled()
    assert window.status.text() == "3 events"


def test_filters(terminal):
    window, services = terminal()
    tab = window.history
    tab.types.item(1).setSelected(True)
    tab.events.setText("created, opened")
    tab.sources.setText("text-lens")
    tab.until_now.setChecked(False)
    tab.search()
    query = services.history.queries[-1]
    assert query["type_ids"] == [TEXT]
    assert query["events"] == ["created", "opened"]
    assert query["sources"] == ["text-lens"]
    assert query["end"] == ui_history.to_micros(tab.end.dateTime()) != 0
    assert query["start"] == ui_history.to_micros(tab.start.dateTime())


def test_invalid_filter(terminal):
    window, services = terminal()
    window.history.events.setText("ok, " + "x" * 32)
    window.history.search()
    assert window.status.text().startswith("Invalid filter")


def test_paging(terminal, monkeypatch):
    monkeypatch.setattr(ui_history, "PAGE", 2)
    window, services = terminal()
    services.history.events = events(5)
    tab = window.history
    tab.set_period(0)
    assert tab.table.rowCount() == 2 and tab.more.isEnabled()

    tab.more.click()
    assert services.history.queries[-1]["end"] == services.history.events[1].time
    tab.more.click()
    assert tab.table.rowCount() == 5 and not tab.more.isEnabled()
    assert [tab.table.item(r, 5).text() for r in range(5)] == ["1", "2", "3", "4", "5"]


def test_open_from_history(terminal):
    window, services = terminal()
    services.history.events = events(1)
    window.history.set_period(0)
    window.history.use(0)
    assert services.launch.used == [(DOC, None)]

    services.launch.fail = True
    window.history.use(0)
    assert window.status.text() == f"No lens for {DOC}"


################################################################
# System
################################################################

def test_system_menu(terminal):
    window, services = terminal()
    [system] = [a.menu() for a in window.menuBar().actions() if a.text() == "System"]
    actions = {a.text(): a for a in system.actions()}
    actions["Shutdown"].trigger()
    actions["Reboot"].trigger()
    assert services.calls == ["shutdown", "reboot"]
    actions["Quit"].trigger()
    assert not window.isVisible()


def test_devterm_at_boot(live_pkernel, monkeypatch):  # noqa: F811
    # Started last at boot, it connects to the services, and keeps
    # running, until the system is shut down.
    kernel = live_pkernel
    monkeypatch.setenv("DARQ_DEV_TERMINAL", "1")
    kernel.do_boot()
    [process] = [p for p in kernel._process_table.values() if p._image_path == str(DEVTERM_PATH)]
    running = kernel.module.KernelProcessStatus.RUNNING
    run_kernel_until(kernel, lambda: process.get_status() == running, "the devterm to run")

    deadline = time.monotonic() + 2
    run_kernel_until(kernel, lambda: time.monotonic() > deadline, "a while")
    assert process.get_status() == running

    kernel.begin_shutdown(reboot=False)
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: process.get_status() == zombie, "the devterm to exit")


################################################################
# Waiting for the Terminal's display
################################################################

def test_wait_for_display(tmp_path, monkeypatch):
    from darq.ui import wait_for_display
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    # Only the compositor's display is waited for.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert wait_for_display(0)
    monkeypatch.setenv("WAYLAND_DISPLAY", "darq-0")
    assert not wait_for_display(0.2)
    (tmp_path / "darq-0").touch()
    assert wait_for_display(0)
