# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Terminal's parts, darq.ui: New's list of types, the History
# view, the Search view, and their helpers, offscreen, with faked services
# (see ui_fakes.py).  The Terminal's panels, which hold them, are tested
# in test_terminal.py.

import datetime
import types as python_types
import typing

import pytest

import darq
import darq.items
import darq.kb as model
from darq.ui import history as ui_history
from darq.ui import names
from darq.ui import search as ui_search
from darq.ui import wait_for_display
from darq.ui.launcher import Launcher
from darq.ui.types import TypeList
from darq.protocols.history import Event

from fake_kb import FakeKB
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
    # Its type records it, as the launcher's context says.
    assert services.history.added == []
    assert [(c.event, c.source, c.note) for c in services.launch.contexts] == [("", "terminal", "")]
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
    assert services.history.added == []
    assert [(c.event, c.source, c.note) for c in services.launch.contexts] == [("imported", "terminal", str(path))]
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
    return [Event(time=start + (n - i) * 1_000_000, item_id=DOC, event="edited", source="text-lens", count=i + 1,
                  note=f"note {i}" if i else "")
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
    # With no KB, no name: the identifier's specifier.
    assert [view.table.item(0, c).text() for c in range(1, 7)] == [
        "Text File", "0123456789abcdef0123456789abcdef", "edited", "text-lens", "1", ""]
    # Its note, as for where a reader had got to.
    assert view.table.item(1, 6).text() == "note 1" and view.table.item(1, 6).toolTip() == "note 1"
    assert view.table.item(0, 1).toolTip() == TEXT and view.table.item(0, 2).toolTip() == DOC
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
    assert [view.table.item(0, c).text() for c in range(3)] == [
        "0123456789abcdef0123456789abcdef", "UTF-8 Text", "Meet at the coffee shop"]
    assert view.table.item(0, 0).toolTip() == DOC
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


################################################################
# Items' names (see doc/item.rst)
################################################################

def named(store, item_id: str, label: str = "", created=None) -> model.Item:
    entity = darq.items.made(darq.split_item_id(item_id)[0], darq.split_item_id(item_id)[1], label=label,
                             created=created)
    store.put(entity)
    return entity


def test_an_items_name_is_its_label():
    store = FakeKB()
    entity = named(store, DOC, "Shopping list")
    assert names.display_name(entity) == "Shopping list"


def test_an_unnamed_items_name_is_its_kind_and_when_it_was_made():
    when = datetime.datetime(2026, 11, 8, 12, tzinfo=datetime.timezone.utc)
    entity = darq.items.made("public.jpeg", "a" * 32, created=when)
    made = when.astimezone()
    assert names.display_name(entity) == f"JPEG Image, {made.day} {made:%b %Y}"
    assert names.display_name(model.Item("x"), "public.png") == "PNG Image"


def test_items_are_named_a_batch_at_a_time():
    store = FakeKB()
    named(store, DOC, "Shopping list")
    other = f"{TEXT}:fedcba9876543210fedcba9876543210"
    named(store, other)
    found = names.item_names(typing.cast(typing.Any, store), [DOC, other, "public.png:" + "b" * 32, "nonsense"])
    assert found[DOC] == "Shopping list" and found[other].startswith("Text File, ")
    assert set(found) == {DOC, other}


def test_a_merged_entitys_name_is_what_it_was_merged_intos():
    store = FakeKB()
    person = model.Item("Q313254")
    person.set_label("Peter Carey")
    old = model.Item("old")
    old.redirect = "Q313254"
    store.entities.update({"Q313254": person, "old": old})
    assert names.item_names(typing.cast(typing.Any, store), ["darq.person:old"]) == {"darq.person:old": "Peter Carey"}


def test_without_the_kb_theres_no_name():
    assert names.item_names(None, [DOC]) == {}


def test_history_and_search_show_names(made):
    store = FakeKB()
    named(store, DOC, "Shopping list")
    view, host = history_view(made)
    host.services.kb_client = store
    host.services.history.events = events(1)
    view.set_period(0)
    assert view.table.item(0, 2).text() == "Shopping list"

    found, host = made(ui_search.SearchView)
    host.services.kb_client = store
    host.services.index.items = {DOC: "eggs"}
    found.field.setText("eggs")
    found.search()
    assert found.table.item(0, 0).text() == "Shopping list"


################################################################
# What every lens answers alike (darq.ui.lens)
################################################################

def test_a_lens_says_what_its_showing():
    from darq.protocols.lens import ShownRequest
    from darq.ui.lens import LensHandler
    handler = LensHandler()
    assert (handler.shown(ShownRequest(), 0).item_id, handler.shown(ShownRequest(), 0).entity) == ("", False)
    handler.shown_item = DOC
    assert handler.shown(ShownRequest(), 0).item_id == DOC


def test_a_lens_is_found_by_its_windows(qt_app, monkeypatch):
    """Its port's registered under its windows' Wayland app_id."""
    from PyQt6.QtGui import QGuiApplication
    from darq.ui import lens as ui_lens
    registered = []
    monkeypatch.setattr(darq, "getpid", lambda: 12)
    monkeypatch.setattr(darq, "register_name", lambda name, port: registered.append((name, port)))
    before = QGuiApplication.desktopFileName()
    try:
        assert ui_lens.announce(40) == "lens.12"
        assert registered == [("lens.12", 40)] and QGuiApplication.desktopFileName() == "lens.12"
    finally:
        QGuiApplication.setDesktopFileName(before)


def test_a_lens_thats_not_registered_still_shows_its_item(qt_app, monkeypatch):
    from PyQt6.QtGui import QGuiApplication
    from darq.ui import lens as ui_lens

    def taken(name, port):
        raise darq.NameTakenError(name)

    monkeypatch.setattr(darq, "getpid", lambda: 12)
    monkeypatch.setattr(darq, "register_name", taken)
    before = QGuiApplication.desktopFileName()
    try:
        assert ui_lens.announce(40) == "lens.12"
    finally:
        QGuiApplication.setDesktopFileName(before)


################################################################
# Viewing (darq.ui.lens)
################################################################

class ViewedItem:
    def __init__(self):
        self.views: list[tuple[str, str]] = []
        self.dead = False

    def viewed(self, source="", note=""):
        if self.dead:
            raise darq.DeadPortError("gone")
        self.views.append((source, note))


@pytest.fixture
def viewing(qt_app):
    from PyQt6.QtWidgets import QWidget
    from darq.ui.lens import Viewing
    window = QWidget()
    clock = python_types.SimpleNamespace(now=100.0)
    shown = Viewing(window, "a-lens", clock=lambda: clock.now)
    yield shown, clock, window
    shown.stop()


def test_viewing_says_so_when_shown(viewing):
    shown, _, _ = viewing
    item = ViewedItem()
    shown.show(item, "page 1 of 9")
    assert item.views == [("a-lens", "page 1 of 9")]
    assert shown.focused.isActive()


def test_moving_on_says_where_at_most_every_gap(viewing):
    from darq.ui.lens import VIEWED_GAP
    shown, clock, _ = viewing
    item = ViewedItem()
    shown.show(item, "page 1 of 9")

    # Soon after: it's said once the gap's passed, with the latest place.
    clock.now += 5
    shown.move("page 2 of 9")
    shown.move("page 3 of 9")
    assert item.views == [("a-lens", "page 1 of 9")]
    assert shown.pending.isActive() and shown.pending.interval() == (VIEWED_GAP - 5) * 1000
    shown.pending.timeout.emit()
    assert item.views[-1] == ("a-lens", "page 3 of 9")

    # Long after: it's said now.
    clock.now += VIEWED_GAP
    shown.move("page 4 of 9")
    assert item.views[-1] == ("a-lens", "page 4 of 9") and not shown.pending.isActive()

    # Where it already is says nothing.
    shown.move("page 4 of 9")
    assert len(item.views) == 3


def test_while_focused_its_said_again(viewing, monkeypatch):
    shown, _, window = viewing
    item = ViewedItem()
    shown.show(item)
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    shown.tick()
    assert len(item.views) == 1
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    shown.tick()
    assert len(item.views) == 2


def test_once_stopped_nothings_said_and_a_gone_item_is_only_logged(viewing, caplog):
    shown, _, _ = viewing
    item = ViewedItem()
    shown.show(item)
    shown.stop()
    shown.tick()
    shown.move("page 2")
    assert len(item.views) == 1 and not shown.focused.isActive()

    item.dead = True
    shown.show(item)
    assert "Can't say it's viewed" in caplog.text


################################################################
# Dialogs (darq.ui.dialogs)
################################################################

def test_choosing_from_a_list(qt_app):
    from darq.ui import dialogs
    dialog = dialogs.ChooseDialog(None, "Wikidata", "Which?", ["Viv Albertine — British musician", "Someone else"])
    assert dialog.chosen() == 0 and dialog.list.count() == 2
    dialog.list.setCurrentRow(1)
    assert dialog.chosen() == 1
    assert dialog.styleSheet() == dialogs.STYLE
    assert dialogs.ChooseDialog(None, "T", "Q", []).chosen() is None


def test_a_line_starts_with_its_text(qt_app):
    from darq.ui import dialogs
    dialog = dialogs.TextDialog(None, "Merge", "Into?", "Bloomsbury")
    assert dialog.line.text() == "Bloomsbury" and dialog.line.selectedText() == "Bloomsbury"


@pytest.mark.parametrize("key, accepted", [("Key_Y", True), ("Key_N", False)])
def test_confirming_by_key(qt_app, key, accepted):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QDialog
    from darq.ui import dialogs
    dialog = dialogs.ConfirmDialog(None, "Merge", "Merge them?")
    assert dialog.no.isDefault()
    dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, getattr(Qt.Key, key), Qt.KeyboardModifier.NoModifier))
    assert dialog.result() == (QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected)
