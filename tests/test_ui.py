# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Terminal's parts, darq.ui: New's list of types, the History
# view, the Search view, and their helpers, offscreen, with faked services
# (see ui_fakes.py).  The Terminal's panels, which hold them, are tested
# in test_terminal.py.

import datetime
import typing

import pytest

from darq.ui import history as ui_history
from darq.ui import search as ui_search
from darq.ui import wait_for_display
from darq.ui.launcher import Launcher
from darq.ui.types import TypeList
from darq.protocols.history import Event

from ui_fakes import DOC, TEXT, FakeServices, inline


class Host:
    """What holds a part: services, a launcher that runs at once, and a
    status line."""

    def __init__(self, types=None):
        self.services = FakeServices(types)
        self.launcher = Launcher(typing.cast(typing.Any, self), "terminal", inline, inline)
        self.status = ""

    def show_status(self, message: str):
        self.status = message

    def launched(self, context: object, item_id: str | None, error: str | None):
        self.status = error or f"Using {item_id}"


@pytest.fixture
def made(qt_app):
    """Make a part, with a host, closing it after."""
    parts = []

    def make(part, types=None):
        host = Host(types)
        widget = part(typing.cast(typing.Any, host))
        parts.append(widget)
        return widget, host

    yield make
    for widget in parts:
        widget.close()


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


def test_wait_for_display(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    # Only the compositor's display is waited for.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert wait_for_display(0)
    monkeypatch.setenv("WAYLAND_DISPLAY", "darq-0")
    assert not wait_for_display(0.2)
    (tmp_path / "darq-0").touch()
    assert wait_for_display(0)


################################################################
# New's types
################################################################

def types_list(made):
    widget, host = made(TypeList)
    widget.refresh()
    return widget, host


def test_types_listed(made):
    from PyQt6.QtCore import Qt
    widget, _ = types_list(made)
    table = widget.table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["PNG", "UTF-8 Text"]
    assert [table.item(row, 1).text() for row in range(table.rowCount())] == ["public.png", TEXT]

    # A type with no lens is greyed out.
    assert not table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert table.item(1, 0).flags() & Qt.ItemFlag.ItemIsEnabled


def test_create(made):
    widget, host = types_list(made)
    widget.table.selectRow(1)
    widget.create_button.click()
    services = host.services
    assert services.launch.created == [TEXT]
    assert services.history.added == [(DOC, "created", "terminal")]
    assert services.launch.used == [(DOC, 42)]
    assert host.status == f"Using {DOC}"


def test_import_file(made, tmp_path, monkeypatch):
    widget, host = types_list(made)
    path = tmp_path / "notes.txt"
    path.write_bytes(b"Hello")
    monkeypatch.setattr(widget, "choose_file", lambda: str(path))
    widget.import_button.click()
    services = host.services
    assert services.launch.created == [(TEXT, b"Hello")]
    assert services.launch.names == ["notes.txt"]
    assert services.history.added == [(DOC, "imported", "terminal")]
    assert services.launch.used == [(DOC, 43)]
    assert host.status == f"Using {DOC}"
    assert widget.import_button.isEnabled()

    # Cancelled: nothing's imported.
    monkeypatch.setattr(widget, "choose_file", lambda: "")
    widget.import_button.click()
    assert len(services.launch.created) == 1


def test_create_needs_a_usable_type(made):
    widget, host = types_list(made)
    widget.create_item()
    assert host.status == "Select a type with a lens"

    # The greyed-out type can't be selected.
    widget.table.selectRow(0)
    widget.create_item()
    assert host.services.launch.created == []


def test_create_failure(made):
    widget, host = types_list(made)
    host.services.launch.fail = True
    widget.table.selectRow(1)
    widget.create_item()
    assert host.status == f"Can't create a UTF-8 Text: Can't activate {TEXT}"
    assert host.services.history.added == []


################################################################
# History
################################################################

def events(n: int, start: int = 1_800_000_000_000_000) -> list[Event]:
    """Newest first, a second apart."""
    return [Event(time=start + (n - i) * 1_000_000, item_id=DOC, event="edited", source="text-lens", count=i + 1)
            for i in range(n)]


def history_view(made):
    view, host = made(ui_history.HistoryView)
    view.set_types(["public.png", TEXT])
    return view, host


def test_history_search(made):
    view, host = history_view(made)
    host.services.history.events = events(3)
    view.set_period(0)
    query = host.services.history.queries[-1]
    assert (query["start"], query["end"], query["max"]) == (0, 0, ui_history.PAGE)
    assert view.table.rowCount() == 3
    assert [view.table.item(0, c).text() for c in range(1, 6)] == [
        TEXT, "0123456789abcdef0123456789abcdef", "edited", "text-lens", "1"]
    assert not view.more.isEnabled()
    assert host.status == "3 events"


def test_filters(made):
    view, host = history_view(made)
    view.types.item(1).setSelected(True)
    view.events.setText("created, opened")
    view.sources.setText("text-lens")
    view.until_now.setChecked(False)
    view.search()
    query = host.services.history.queries[-1]
    assert query["type_ids"] == [TEXT]
    assert query["events"] == ["created", "opened"]
    assert query["sources"] == ["text-lens"]
    assert query["end"] == ui_history.to_micros(view.end.dateTime()) != 0
    assert query["start"] == ui_history.to_micros(view.start.dateTime())


def test_invalid_filter(made):
    view, host = history_view(made)
    view.events.setText("ok, " + "x" * 32)
    view.search()
    assert host.status.startswith("Invalid filter")


def test_paging(made, monkeypatch):
    monkeypatch.setattr(ui_history, "PAGE", 2)
    view, host = history_view(made)
    host.services.history.events = events(5)
    view.set_period(0)
    assert view.table.rowCount() == 2 and view.more.isEnabled()

    view.more.click()
    assert host.services.history.queries[-1]["end"] == host.services.history.events[1].time
    view.more.click()
    assert view.table.rowCount() == 5 and not view.more.isEnabled()
    assert [view.table.item(r, 5).text() for r in range(5)] == ["1", "2", "3", "4", "5"]


def test_open_from_history(made):
    view, host = history_view(made)
    host.services.history.events = events(1)
    view.set_period(0)
    view.use(0)
    assert host.services.launch.used == [(DOC, None)]

    host.services.launch.fail = True
    view.use(0)
    assert host.status == f"No lens for {DOC}"


################################################################
# Search
################################################################

OTHER = f"{TEXT}:fedcba9876543210fedcba9876543210"


def test_format_updated():
    now = datetime.datetime(2027, 1, 15, 18, 30).astimezone()
    today = int(now.replace(hour=9, minute=5).timestamp() * 1_000_000)
    assert ui_search.format_updated(today, now) == "09:05"
    earlier = int(datetime.datetime(2026, 12, 25, 9, 5).astimezone().timestamp() * 1_000_000)
    assert ui_search.format_updated(earlier, now) == "2026-12-25"


def test_search_as_typed(made):
    view, host = made(ui_search.SearchView)
    services = host.services
    services.index.items = {DOC: "Meet at the coffee\nshop", OTHER: "Buy coffee beans"}
    view.field.setText("coffee")
    assert view.timer.isActive() and services.index.searches == []
    view.search()
    assert services.index.searches == ["coffee"]
    assert [r.item_id for r in view.shown] == [DOC, OTHER]
    assert [view.table.item(0, c).text() for c in range(3)][:2] == ["UTF-8 Text", "Meet at the coffee shop"]
    assert view.table.item(0, 1).toolTip() == DOC
    assert view.table.currentRow() == 0
    assert host.status == "2 found"

    view.field.setText("coffee shop")
    view.search()
    assert [r.item_id for r in view.shown] == [DOC]

    view.field.setText("tea")
    view.search()
    assert view.shown == [] and host.status == "Nothing found"


def test_empty_search_finds_nothing(made):
    view, host = made(ui_search.SearchView)
    host.services.index.items = {DOC: "coffee"}
    view.field.setText("   ")
    view.search()
    assert view.shown == [] and host.services.index.searches == []


def test_search_keys(made):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent

    view, host = made(ui_search.SearchView)

    def press(code):
        view.field.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier))

    host.services.index.items = {DOC: "coffee", OTHER: "coffee"}
    view.field.setText("coffee")
    view.search()
    press(Qt.Key.Key_Down)
    assert view.table.currentRow() == 1
    press(Qt.Key.Key_Down)
    assert view.table.currentRow() == 1
    press(Qt.Key.Key_Return)
    assert host.services.launch.used == [(OTHER, None)]
    press(Qt.Key.Key_Up)
    press(Qt.Key.Key_Up)
    assert view.table.currentRow() == 0


def test_search_without_the_index(made):
    view, host = made(ui_search.SearchView)
    host.services.index.fail = True
    view.field.setText("coffee")
    view.search()
    assert host.status == "Can't search: the Index isn't running"


def test_type_names_are_looked_up_once(made, monkeypatch):
    view, host = made(ui_search.SearchView)
    described = []
    describe = host.services.types.describe
    monkeypatch.setattr(host.services.types, "describe", lambda type_id: described.append(type_id) or describe(type_id))
    assert view.type_name(TEXT) == "UTF-8 Text"
    assert view.type_name(TEXT) == "UTF-8 Text"
    assert described == [TEXT]
