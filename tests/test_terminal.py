# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Terminal, services/terminal: its hotkeys, panels, window
# list and locking, offscreen, with faked services and a socket pair
# for the compositor's end of the policy channel; and then started at
# boot through a real p-kernel, with the compositor headless.

import importlib.util
import json
import os
import pathlib
import socket
import sys
import time
import types as python_types
import typing

import pytest

from darq.compositor import COMPOSITOR, PolicyChannel
from darq.errors import DeadPortError
from darq.protocols import compositor as protocol
from darq.protocols.security import (
    CheckPasswordResponse,
    IsPasswordSetResponse,
    SetPasswordResponse,
    Status as SecurityStatus,
)
from darq.protocols.compositor import (
    BindHotkey,
    CloseWindow,
    FocusWindow,
    HotkeyPressed,
    Lock,
    Modifier,
    RaiseWindow,
    Ready,
    SystemReleased,
    Unlock,
    WindowAdded,
    WindowFocused,
    WindowRemoved,
    WindowRetitled,
)

from darq.protocols.http import SnapshotResponse
from darq.protocols.http import Status as HttpStatus
from darq.ui.search import is_url

from ui_fakes import DOC, PDF, TEXT, FakeServices, inline
from test_pkernel import live_pkernel, pkernel, run_kernel_until  # noqa: F401


TERMINAL_PATH = pathlib.Path(__file__).parent.parent / "services" / "terminal"


def load():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(TERMINAL_PATH))
    try:
        spec = importlib.util.spec_from_file_location("terminal_main", TERMINAL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TERMINAL_PATH))
    return module


main = load()
panels = sys.modules["panels"]


class Compositor:
    """The compositor's end of the policy channel."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.sock.setblocking(False)

    def received(self) -> list:
        """The Policy messages the Terminal has sent, since last asked."""
        messages = []
        while True:
            try:
                data = self.sock.recv(4096)
            except BlockingIOError:
                return messages
            message = protocol.Policy.REQUESTS[int.from_bytes(data[6:8], "little")]()
            message.decode(data)
            messages.append(message)


class FakeSecurity:
    """The Security service: the password, if it's set."""

    def __init__(self, password: str | None = "swordfish"):
        self.password = password
        self.down = False

    def check_up(self):
        if self.down:
            raise DeadPortError("darq.security is down")

    def is_password_set(self):
        self.check_up()
        return IsPasswordSetResponse(set=self.password is not None)

    def set_password(self, old_password: str, new_password: str):
        self.check_up()
        if self.password is not None:
            return SetPasswordResponse(status=SecurityStatus.ALREADY_SET)
        self.password = new_password
        return SetPasswordResponse(status=SecurityStatus.OK)

    def check_password(self, password: str):
        self.check_up()
        if self.password is None:
            return CheckPasswordResponse(status=SecurityStatus.NOT_SET)
        return CheckPasswordResponse(status=SecurityStatus.OK if password == self.password
                                     else SecurityStatus.WRONG_PASSWORD)


class FakeApp:
    """Stands in for the QApplication, where the Terminal would exit it."""

    def __init__(self, app):
        self.app = app
        self.exited: list[int] = []

    def exit(self, status: int):
        self.exited.append(status)

    def __getattr__(self, name):
        return getattr(self.app, name)


@pytest.fixture
def security():
    return FakeSecurity()


class FakeHost:
    """Stands in for the host's name and addresses (darq.host)."""

    def __init__(self):
        self.found = ["192.0.2.1"]
        self.reads = 0

    def addresses(self):
        self.reads += 1
        return list(self.found)


@pytest.fixture
def host(monkeypatch):
    import darq.host
    fake = FakeHost()
    monkeypatch.setattr(darq.host, "name", lambda: "testbox")
    monkeypatch.setattr(darq.host, "addresses", fake.addresses)
    return fake


@pytest.fixture
def terminal(qt_app, security, host):
    """A started Terminal, locked, and the compositor's end of its
    channel, with the hotkey bindings read."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    services: typing.Any = FakeServices()
    services.security = security
    started = main.Terminal(qt_app, PolicyChannel(ours), services)
    started.launcher.run = started.launcher.post = inline
    started.update_result = lambda: None
    started.start()
    compositor = Compositor(theirs)
    started.bindings = compositor.received()
    started.compositor = compositor
    started.fake_services = services
    yield started

    for panel in started.panels:
        panel.close()
    started.loop.close()
    for screen in list(started.screens.windows):
        started.screens.remove(screen)
    started.notifier.setEnabled(False)
    ours.close()
    theirs.close()


def key(code, text: str = ""):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier, text)


def hotkey(terminal, keysym: int, modifiers: int = 0):
    terminal.handle(HotkeyPressed(keysym=keysym, modifiers=modifiers))


def enter(terminal, password: str, confirm: str | None = None):
    """Type on the lock screen, as Enter is pressed after each field."""
    lock = terminal.screens.locks()[0]
    lock.password.setText(password)
    lock.password.returnPressed.emit()
    if confirm is not None:
        lock.confirm.setText(confirm)
        lock.confirm.returnPressed.emit()


def unlock(terminal):
    enter(terminal, "swordfish")
    assert terminal.compositor.received() == [Unlock()]


def visible(terminal) -> list:
    return [panel for panel in terminal.panels if panel.isVisible()]


################################################################
# Starting
################################################################

def test_binds_hotkeys(terminal):
    assert terminal.bindings == [BindHotkey(keysym=k, modifiers=m) for k, m in main.HOTKEYS]
    assert (main.XK_Tab, Modifier.SHIFT) in main.HOTKEYS


def test_starts_locked(terminal):
    from PyQt6.QtCore import Qt
    [(background, lock)] = terminal.screens.windows.values()
    assert background.windowTitle() == "darq:background" and background.isVisible()
    assert lock.windowTitle() == "darq:lock" and lock.isVisible()

    # It asks for the password.
    assert lock.heading.text() == "Locked"
    assert lock.password.isVisibleTo(lock) and not lock.confirm.isVisibleTo(lock)

    # Hotkeys do nothing while it's locked (bar Sys-b), and only the
    # password unlocks.
    hotkey(terminal, main.XK_n)
    hotkey(terminal, main.XK_l)
    assert visible(terminal) == []
    lock.keyPressEvent(key(Qt.Key.Key_Return))
    enter(terminal, "guess")
    assert lock.message.text() == "Wrong password" and lock.password.text() == ""
    assert terminal.locked and terminal.compositor.received() == []

    unlock(terminal)
    assert not terminal.locked and not lock.isVisible()
    assert background.isVisible()


@pytest.mark.parametrize("security", [FakeSecurity(None)])
def test_first_password(terminal, security):
    lock = terminal.screens.locks()[0]
    assert lock.heading.text() == "Choose a password"
    assert lock.confirm.isVisibleTo(lock)

    # It's typed twice: they must match, and not be empty.
    enter(terminal, "", "")
    assert lock.message.text() == "The password can't be empty"
    enter(terminal, "swordfish", "swordfsh")
    assert lock.message.text() == "Those don't match: try again"
    assert (lock.password.text(), lock.confirm.text()) == ("", "")
    assert security.password is None and terminal.locked

    enter(terminal, "swordfish", "swordfish")
    assert security.password == "swordfish"
    assert terminal.compositor.received() == [Unlock()]

    # Next time, it's asked for.
    hotkey(terminal, main.XK_l)
    terminal.compositor.received()
    assert lock.heading.text() == "Locked" and not lock.confirm.isVisibleTo(lock)
    unlock(terminal)


@pytest.mark.parametrize("security", [FakeSecurity(None)])
def test_password_set_meanwhile(terminal, security):
    lock = terminal.screens.locks()[0]
    security.password = "swordfish"
    enter(terminal, "new", "new")
    assert lock.message.text() == "There's a password already: enter it"
    assert terminal.screens.mode == "enter"
    unlock(terminal)


def test_security_unavailable(terminal, security):
    from PyQt6.QtCore import Qt
    lock = terminal.screens.locks()[0]

    # A check that can't be made doesn't unlock.
    security.down = True
    enter(terminal, "swordfish")
    assert lock.message.text().startswith("Can't check the password")
    assert terminal.locked

    # Nor does the screen, if it can't ask whether there's a password:
    # Enter asks again.
    terminal.ask_password_set()
    assert terminal.screens.mode == "unavailable"
    assert not lock.password.isVisibleTo(lock)
    security.down = False
    lock.keyPressEvent(key(Qt.Key.Key_Return))
    assert terminal.screens.mode == "enter"
    unlock(terminal)


def test_busy_while_checking(terminal):
    terminal.launcher.run = deferred = Deferred()
    lock = terminal.screens.locks()[0]
    enter(terminal, "swordfish")
    assert lock.message.text() == "Checking ..." and not lock.password.isEnabled()
    enter(terminal, "again")
    assert len(deferred.jobs) == 1
    deferred.finish()
    assert terminal.compositor.received() == [Unlock()]


def test_power_from_lock_screen(terminal):
    from PyQt6.QtCore import Qt
    lock = terminal.screens.locks()[0]
    hotkey(terminal, main.XK_b)
    assert lock.prompting and visible(terminal) == []

    # The keys come to the prompt, not the password field.
    assert not lock.password.isEnabled()
    assert lock.message.text().replace("\u00a0", " ") == "s: Shut down    b: Reboot    any other key: Cancel"
    # All of it shows, below the fields, even wrapped.
    message = lock.message
    assert message.heightForWidth(message.width()) <= message.height()
    assert message.height() >= 2 * message.fontMetrics().lineSpacing()
    # And if it wraps, it's between the choices.
    assert all(" " not in choice for choice in lock.message.text().split("    "))

    # A modifier isn't an answer; another key cancels; s shuts down.
    lock.keyPressEvent(key(Qt.Key.Key_Shift))
    assert lock.prompting
    lock.keyPressEvent(key(Qt.Key.Key_Q, "q"))
    assert not lock.prompting and terminal.fake_services.calls == []
    assert lock.password.isEnabled() and lock.message.text() == ""
    unlock(terminal)

    terminal.lock()
    terminal.compositor.received()
    hotkey(terminal, main.XK_b)
    lock.keyPressEvent(key(Qt.Key.Key_S, "s"))
    assert terminal.fake_services.calls == ["shutdown"]


def test_wait_ready(qt_app):
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    channel = PolicyChannel(ours)
    theirs.send(Ready().encode()[0])
    assert main.wait_ready(channel, 1)
    assert not main.wait_ready(channel, 0.1)
    theirs.close()
    assert not main.wait_ready(channel, 1)
    ours.close()


def test_no_compositor(tmp_path):
    assert main.darq_main(tmp_path / "missing") == 1


def test_channel_closed_exits(terminal):
    app = FakeApp(terminal.app)
    terminal.app = app
    terminal.compositor.sock.close()
    terminal.read_channel()
    assert app.exited == [1]


################################################################
# Panels
################################################################

def test_one_panel_at_a_time(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    assert visible(terminal) == [terminal.new]
    assert terminal.new.windowTitle() == "darq:overlay"
    hotkey(terminal, main.XK_h)
    assert visible(terminal) == [terminal.history]

    # The History panel searches the last hour, offering the types.
    assert terminal.fake_services.history.queries[-1]["end"] == 0
    assert terminal.history.view.types.count() == 2

    terminal.history.keyPressEvent(key(Qt.Key.Key_Escape))
    assert visible(terminal) == []


def test_history_offers_every_type_before_new_is_opened(terminal):
    """Its types are asked for when it opens, not borrowed from New's list,
    which is loaded when that's opened, and leaves out a book's levels."""
    unlock(terminal)
    terminal.fake_services.types.types["darq.book.item"] = ("Copy", True)
    hotkey(terminal, main.XK_h)
    types = terminal.history.view.types
    assert [types.item(n).text() for n in range(types.count())] == ["darq.book.item", "public.png", TEXT]


def test_new_creates_and_uses(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    assert types.filter is not None

    # The type with a lens is selected; filtering leaves none.
    assert types.selected().type_id == TEXT
    types.filter.setText("png")
    assert types.selected() is None
    types.filter.setText("utf")
    types.filter.returnPressed.emit()

    services = terminal.fake_services
    assert services.launch.created == [TEXT]
    # Its type records it, as the launcher's context says.
    assert services.history.added == []
    assert [(c.event, c.source, c.note) for c in services.launch.contexts] == [("", "terminal", "")]
    assert services.launch.used == [(DOC, 42)]
    assert visible(terminal) == []


def test_new_arrow_keys(terminal):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    unlock(terminal)
    terminal.fake_services.types.types = {TEXT: ("UTF-8 Text", True), "public.png": ("PNG", True),
                                          "public.jpeg": ("JPEG", False)}
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    assert types.filter is not None

    # In the filter, Down and Up move through the usable types, stopping
    # at the ends.
    assert types.selected().name == "PNG"
    for code, name in ((Qt.Key.Key_Down, "UTF-8 Text"), (Qt.Key.Key_Down, "UTF-8 Text"),
                       (Qt.Key.Key_Up, "PNG"), (Qt.Key.Key_Up, "PNG")):
        QApplication.sendEvent(types.filter, key(code))
        assert types.selected().name == name


def test_new_failure_stays(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    terminal.fake_services.launch.fail = True
    terminal.new.types.create_item()
    assert visible(terminal) == [terminal.new]
    assert terminal.new.status.text() == f"Can't create a UTF-8 Text: Can't activate {TEXT}"


class Deferred:
    """Runs launches only when told: as if they were still starting."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def finish(self):
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


def test_one_create_at_a_time(terminal):
    unlock(terminal)
    terminal.launcher.run = deferred = Deferred()
    hotkey(terminal, main.XK_n)
    types = terminal.new.types
    types.create_item()
    assert not types.create_button.isEnabled()

    # A second Enter, even after reopening, doesn't make a second item.
    types.create_item()
    assert terminal.new.status.text() == "Still starting a UTF-8 Text ..."
    hotkey(terminal, main.XK_n)
    types.create_item()
    assert len(deferred.jobs) == 1

    deferred.finish()
    assert types.creating is None and types.create_button.isEnabled()
    assert terminal.fake_services.launch.created == [TEXT]
    assert visible(terminal) == []


def test_failure_after_panel_closed(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    terminal.launcher.run = deferred = Deferred()
    terminal.fake_services.launch.fail = True
    hotkey(terminal, main.XK_n)
    terminal.new.types.create_item()
    terminal.new.keyPressEvent(key(Qt.Key.Key_Escape))
    deferred.finish()

    # It's shown in a notice, which any key closes.
    assert visible(terminal) == [terminal.notice]
    assert terminal.notice.message.text() == f"Can't create a UTF-8 Text: Can't activate {TEXT}"
    terminal.notice.keyPressEvent(key(Qt.Key.Key_Shift))
    assert terminal.notice.isVisible()
    terminal.notice.keyPressEvent(key(Qt.Key.Key_Return))
    assert visible(terminal) == []


def test_history_use(terminal):
    unlock(terminal)
    services = terminal.fake_services
    services.history.events = [main_event()]
    hotkey(terminal, main.XK_h)
    terminal.history.view.use(0)
    assert services.launch.used == [(DOC, None)]
    assert visible(terminal) == []

    # A failure, while it's open, is shown in it.
    hotkey(terminal, main.XK_h)
    services.launch.fail = True
    terminal.history.view.use(0)
    assert visible(terminal) == [terminal.history]
    assert terminal.history.status.text() == f"No lens for {DOC}"


def main_event():
    from darq.protocols.history import Event
    return Event(time=int(time.time() * 1_000_000) - 1_000_000, item_id=DOC, event="edited",
                 source="text-lens", count=1)


def test_closes_when_deactivated(terminal):
    from PyQt6.QtCore import QEvent
    unlock(terminal)
    for panel, shown in ((terminal.new, False), (terminal.switcher, True)):
        panel.open()
        panel.was_active = True
        panel.changeEvent(QEvent(QEvent.Type.ActivationChange))
        assert panel.isVisible() == shown
        panel.hide()


def test_power_panel(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, main.XK_b)
    assert visible(terminal) == [terminal.power_panel]
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_Control))
    assert visible(terminal) == [terminal.power_panel]
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_X, "x"))
    assert visible(terminal) == [] and terminal.fake_services.calls == []

    hotkey(terminal, main.XK_b)
    terminal.power_panel.keyPressEvent(key(Qt.Key.Key_B, "b"))
    assert terminal.fake_services.calls == ["reboot"]


@pytest.mark.parametrize("keysym, name", [(main.XK_e, "Events")])
def test_tbd(terminal, keysym, name):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    hotkey(terminal, keysym)
    assert visible(terminal) == [terminal.tbd]
    assert terminal.tbd.heading.text() == name
    terminal.tbd.keyPressEvent(key(Qt.Key.Key_Shift))
    assert terminal.tbd.isVisible()
    terminal.tbd.keyPressEvent(key(Qt.Key.Key_Space, " "))
    assert visible(terminal) == []


class FakeLensClient:
    """A lens's Lens port, saying what it's showing."""

    shown_as: tuple[str, bool] = ("", False)

    def __init__(self, handle, timeout=None):
        self.handle = handle

    def shown(self):
        return python_types.SimpleNamespace(item_id=self.shown_as[0], entity=self.shown_as[1])


def focused_lens(terminal, monkeypatch, app_id="lens.12", shown=(DOC, False), found=True):
    """A focused window, of a lens named app_id, showing an item."""
    looked_up: list[str] = []

    def lookup_name(name, blocking=True):
        looked_up.append(name)
        return 77 if found else None

    monkeypatch.setattr(main.darq, "lookup_name", lookup_name)
    monkeypatch.setattr(main.darq, "close_port", lambda handle: None)
    monkeypatch.setattr(FakeLensClient, "shown_as", shown)
    monkeypatch.setattr(main, "Lens", python_types.SimpleNamespace(Client=FakeLensClient))
    terminal.handle(WindowAdded(window=3, title="A note", app_id=app_id))
    terminal.handle(WindowFocused(window=3))
    return looked_up


def test_sys_i_opens_the_focused_items_page(terminal, monkeypatch):
    unlock(terminal)
    looked_up = focused_lens(terminal, monkeypatch)
    hotkey(terminal, main.XK_i)
    assert looked_up == ["lens.12"]
    assert terminal.fake_services.launch.used == [(DOC, None, "public.item")]


@pytest.mark.parametrize("options, said", [
    ({"app_id": "org.example.app"}, "this isn't one"),
    ({"found": False}, "Can't ask the window"),
    ({"shown": ("", False)}, "isn't showing an item"),
    ({"shown": (DOC, True)}, f"This is {DOC}'s entity"),
])
def test_sys_i_says_why_it_cant(terminal, monkeypatch, options, said):
    unlock(terminal)
    focused_lens(terminal, monkeypatch, **options)
    hotkey(terminal, main.XK_i)
    assert visible(terminal) == [terminal.notice] and said in terminal.notice.message.text()
    assert terminal.fake_services.launch.used == []


def test_a_closed_windows_app_id_is_forgotten(terminal):
    terminal.handle(WindowAdded(window=3, title="A note", app_id="lens.12"))
    terminal.handle(WindowRemoved(window=3))
    assert 3 not in terminal.app_ids


def test_search_panel(terminal):
    from PyQt6.QtCore import Qt
    unlock(terminal)
    services = terminal.fake_services
    services.index.items = {DOC: "coffee shop"}
    hotkey(terminal, main.XK_space)
    assert visible(terminal) == [terminal.search]
    assert terminal.search.clock.isActive()
    assert terminal.search.time.text() and terminal.search.date.text()

    view = terminal.search.view
    view.field.setText("coffee")
    view.search()
    assert terminal.search.status.text() == "1 found"
    view.use(0)
    assert services.launch.used == [(DOC, None)]
    assert visible(terminal) == [] and not terminal.search.clock.isActive()

    # Opened again, it starts afresh; a failure is shown in it.
    hotkey(terminal, main.XK_space)
    assert view.field.text() == "" and view.shown == []
    view.field.setText("coffee")
    view.search()
    services.launch.fail = True
    view.use(0)
    assert visible(terminal) == [terminal.search]
    assert terminal.search.status.text() == f"No lens for {DOC}"
    terminal.search.keyPressEvent(key(Qt.Key.Key_Escape))
    assert visible(terminal) == []


def fetch_panel(terminal):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    unlock(terminal)
    hotkey(terminal, main.XK_space)
    view = terminal.search.view

    def fetch(url):
        view.field.setText(url)
        view.field.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
                                           Qt.KeyboardModifier.NoModifier, ""))

    return view, fetch, terminal.fake_services


URL = "https://example.org/paper.pdf"


def test_search_fetches_a_url_and_shows_it(terminal):
    view, fetch, services = fetch_panel(terminal)
    fetch(URL)
    assert services.fake_http.fetched == [URL]
    assert services.launch.used == [(PDF, None)]
    assert visible(terminal) == []


def test_search_says_a_url_will_be_fetched(terminal):
    view, _, services = fetch_panel(terminal)
    services.index.items = {DOC: "https example org"}
    view.field.setText(URL)
    view.search()
    assert view.shown == []
    assert terminal.search.status.text() == "Enter fetches this"


def test_search_still_searches_for_words(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.index.items = {DOC: "coffee shop"}
    view.field.setText("coffee")
    view.search()
    fetch("coffee")
    assert services.fake_http.fetched == []
    assert services.launch.used == [(DOC, None)]


def test_search_shows_what_went_wrong(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http.response = SnapshotResponse(status=HttpStatus.NO_TYPE)
    fetch(URL)
    assert visible(terminal) == [terminal.search]
    assert terminal.search.status.text() == f"No type can hold {URL}"
    assert services.launch.used == []
    assert not view.fetching


def test_search_when_the_server_says_no(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http.response = SnapshotResponse(http_status=404, status=HttpStatus.OK)
    fetch(URL)
    assert terminal.search.status.text() == f"{URL}: the server said 404"
    assert services.launch.used == []


def test_search_when_the_server_says_no_shows_what_was_kept(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http.response = SnapshotResponse(http_status=404, item=PDF, status=HttpStatus.OK)
    fetch(URL)
    assert services.launch.used == [(PDF, None)]
    assert "the last copy kept" in terminal.search.status.text()


def test_search_when_nothing_changed_shows_what_was_kept(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http.response = SnapshotResponse(http_status=304, item=PDF, status=HttpStatus.OK)
    fetch(URL)
    assert services.launch.used == [(PDF, None)]
    assert visible(terminal) == []


def test_search_without_the_http_service(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http = None
    fetch(URL)
    assert terminal.search.status.text() == "The HTTP service isn't running"


def test_search_when_the_fetch_times_out(terminal):
    view, fetch, services = fetch_panel(terminal)
    services.fake_http.error = TimeoutError("no reply")
    fetch(URL)
    assert terminal.search.status.text().startswith(f"Can't fetch {URL}")


def test_search_fetches_one_at_a_time(terminal):
    view, fetch, services = fetch_panel(terminal)
    terminal.launcher.run = deferred = Deferred()
    fetch(URL)
    fetch(URL)
    assert terminal.search.status.text() == "Still fetching ..."
    assert len(deferred.jobs) == 1


@pytest.mark.parametrize("text, expected", [
    (URL, True), ("http://example.org", True), ("HTTPS://EXAMPLE.ORG/", True), ("  https://example.org  ", True),
    ("coffee shop", False), ("https", False), ("https://", False), ("see https://example.org", False),
    ("ftp://example.org", False), ("example.org", False), ("", False)])
def test_is_url(text, expected):
    assert is_url(text) == expected


def test_clock_text():
    import datetime
    assert panels.clock_text(datetime.datetime(2026, 11, 2, 9, 5)) == ("09:05", "Monday 2 November 2026")


def installed(root: pathlib.Path, *tools: tuple[str, str, str]) -> pathlib.Path:
    """Install tools, by directory, name and description, under a root."""
    for directory, name, description in tools:
        (root / "tools" / directory).mkdir(parents=True)
        (root / "tools" / directory / "tool.json").write_text(json.dumps({"name": name, "description": description}))
    return root


def tools_at(terminal, root, finishes=None):
    """Point the Tools panel at a root, with tools that spawn as dpid 7,
    and finish as given, as darq.wait reports."""
    spawned = []
    tools = terminal.tools.tools
    tools.root = root

    def spawn(path):
        spawned.append(path)
        if finishes is None:
            raise main.darq.SpawnError(1, "no such program")
        return 7

    tools.spawn = spawn
    tools.wait = lambda dpid: finishes
    return spawned


def test_the_tools_panel_lists_and_runs_tools(terminal, tmp_path):
    root = installed(tmp_path, ("vcard-import", "Import vCards", "Import an address book"),
                     ("reindex", "Reindex", "Publish every item's entry again"))
    spawned = tools_at(terminal, root, main.darq.ChildStatus(7, main.darq.ExitReason.EXITED, 0, None))

    # Not while it's locked.
    hotkey(terminal, main.XK_t)
    assert visible(terminal) == []

    unlock(terminal)
    hotkey(terminal, main.XK_t)
    assert visible(terminal) == [terminal.tools]
    table = terminal.tools.tools.table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["Import vCards", "Reindex"]

    terminal.tools.tools.filter.setText("index")
    terminal.tools.tools.filter.returnPressed.emit()
    assert spawned == [str(root / "tools" / "reindex")]
    assert terminal.tools.status.text() == "Reindex finished"


def test_a_tool_that_fails_says_why(terminal, tmp_path):
    root = installed(tmp_path, ("slack-import", "Import Slack", ""))
    tools_at(terminal, root, main.darq.ChildStatus(
        7, main.darq.ExitReason.EXITED, 1, "Traceback (most recent call last):\n  ...\nValueError: no export\n"))
    unlock(terminal)
    hotkey(terminal, main.XK_t)
    terminal.tools.tools.run_selected()
    assert terminal.tools.status.text() == "Import Slack failed: ValueError: no export"


def test_a_tool_that_finishes_once_the_panels_closed_says_so_in_a_notice(terminal, tmp_path):
    root = installed(tmp_path, ("reindex", "Reindex", ""))
    tools_at(terminal, root, main.darq.ChildStatus(7, main.darq.ExitReason.KILLED, -1, None))
    unlock(terminal)
    hotkey(terminal, main.XK_t)
    terminal.tools.hide()
    terminal.tools.tools.run_selected()
    assert visible(terminal) == [terminal.notice]
    assert terminal.notice.message.text() == "Reindex was stopped"


def test_a_tool_that_cant_be_started_says_so(terminal, tmp_path):
    root = installed(tmp_path, ("reindex", "Reindex", ""))
    tools_at(terminal, root, None)
    unlock(terminal)
    hotkey(terminal, main.XK_t)
    terminal.tools.tools.run_selected()
    message = terminal.tools.status.text()
    assert message.startswith("Can't run Reindex:") and "no such program" in message


def test_lock(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_n)
    hotkey(terminal, main.XK_l)
    assert terminal.compositor.received() == [Lock()]
    assert visible(terminal) == []
    assert all(lock.isVisible() for lock in terminal.screens.locks())

    # Sys-l does nothing while it's locked.
    hotkey(terminal, main.XK_l)
    assert terminal.compositor.received() == []


################################################################
# Windows
################################################################

def windows(terminal, *titles: str):
    """Add windows 1, 2, ..., focusing each in turn: the last is the
    most recent."""
    for n, title in enumerate(titles, 1):
        terminal.handle(WindowAdded(window=n, title=title, app_id=""))
        terminal.handle(WindowFocused(window=n))


def switcher_titles(terminal) -> list[str]:
    widget = terminal.switcher.list
    return [widget.item(n).text() for n in range(widget.count())]


def test_switcher(terminal):
    unlock(terminal)
    windows(terminal, "a", "b", "c")
    assert terminal.recent == [3, 2, 1]

    # Most recent first, starting at the previous window, and moving on
    # with each Tab, round to the start.
    hotkey(terminal, main.XK_Tab)
    assert visible(terminal) == [terminal.switcher]
    assert switcher_titles(terminal) == ["c", "b", "a"]
    assert terminal.switcher.list.currentRow() == 1
    hotkey(terminal, main.XK_Tab)
    hotkey(terminal, main.XK_Tab)
    assert terminal.switcher.list.currentRow() == 0
    hotkey(terminal, main.XK_Tab, Modifier.SHIFT)
    assert terminal.switcher.list.currentRow() == 2

    # The panel's focus isn't a window's.
    terminal.handle(WindowFocused(window=0))
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]
    assert visible(terminal) == []
    terminal.handle(WindowFocused(window=1))
    assert terminal.recent == [1, 3, 2]


def test_switcher_tap_and_back(terminal):
    unlock(terminal)
    windows(terminal, "a", "b")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]

    # Starting backwards is at the least recent.
    windows(terminal, "a", "b", "c")
    hotkey(terminal, main.XK_Tab, Modifier.SHIFT)
    assert terminal.switcher.list.currentRow() == 2

    # System's release without switching is nothing.
    terminal.handle(SystemReleased())
    terminal.compositor.received()
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == []


def test_switcher_with_none_or_one(terminal):
    unlock(terminal)
    hotkey(terminal, main.XK_Tab)
    assert visible(terminal) == []
    windows(terminal, "a")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(SystemReleased())
    assert terminal.compositor.received() == [RaiseWindow(window=1), FocusWindow(window=1)]


def test_windows_change_while_switching(terminal):
    unlock(terminal)
    windows(terminal, "a", "b", "c")
    hotkey(terminal, main.XK_Tab)
    terminal.handle(WindowRetitled(window=2, title="B"))
    assert switcher_titles(terminal) == ["c", "B", "a"]

    # The chosen one goes: the next is chosen; and one before it goes,
    # keeping it chosen.
    terminal.handle(WindowRemoved(window=2))
    assert switcher_titles(terminal) == ["c", "a"]
    assert terminal.switcher.list.currentRow() == 1
    terminal.handle(WindowRemoved(window=3))
    assert switcher_titles(terminal) == ["a"]
    assert terminal.switcher.list.currentRow() == 0
    terminal.handle(WindowRemoved(window=1))
    assert terminal.switching is None and visible(terminal) == []
    assert terminal.windows == {} and terminal.recent == []


def test_close_window(terminal):
    unlock(terminal)
    windows(terminal, "a", "b")
    hotkey(terminal, main.XK_x)
    assert terminal.compositor.received() == [CloseWindow(window=2)]

    # Not one of the Terminal's own.
    terminal.handle(WindowFocused(window=0))
    hotkey(terminal, main.XK_x)
    assert terminal.compositor.received() == []


################################################################
# At boot
################################################################

def test_terminal_at_boot(live_pkernel, monkeypatch):  # noqa: F811
    # Started at boot, it starts the compositor (headless), and keeps
    # running, until the system's shut down: then, the compositor exits
    # too, removing its socket.
    if not COMPOSITOR.exists():
        pytest.skip("the compositor isn't built: see doc/terminal.rst")
    kernel = live_pkernel
    runtime = pathlib.Path(f"/tmp/darq-test-{os.getpid()}")
    runtime.mkdir(mode=0o700)
    socket_path = runtime / "darq-0"
    monkeypatch.setenv("DARQ_TERMINAL", "1")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("WLR_BACKENDS", "headless")
    monkeypatch.setenv("WLR_RENDERER", "pixman")
    try:
        kernel.do_boot()
        terminal = kernel._terminal
        assert terminal is not None
        running = kernel.module.KernelProcessStatus.RUNNING
        run_kernel_until(kernel, lambda: terminal.get_status() == running and socket_path.exists(),
                         "the Terminal and its compositor to run")

        deadline = time.monotonic() + 2
        run_kernel_until(kernel, lambda: time.monotonic() > deadline, "a while")
        assert terminal.get_status() == running and not kernel._stopping

        kernel.begin_shutdown(reboot=False)
        run_kernel_until(kernel, lambda: kernel._stopped, "the shutdown")
        assert not kernel.reboot_requested()
        assert terminal.get_child().wait(10) == 0
        deadline = time.monotonic() + 5
        while socket_path.exists():
            assert time.monotonic() < deadline, "the compositor didn't exit"
            time.sleep(0.05)
    finally:
        for path in runtime.iterdir():
            path.unlink()
        runtime.rmdir()



################################################################
# The lock screen's address
################################################################

def test_the_lock_screen_shows_the_machines_address(terminal, host):
    lock = terminal.screens.locks()[0]
    assert lock.address.text() == "testbox  ·  192.0.2.1"
    assert terminal.screens.address_timer.isActive()
    assert terminal.screens.address_timer.interval() == 60_000


def test_its_read_again_while_locked_and_not_while_unlocked(terminal, host):
    lock = terminal.screens.locks()[0]
    host.found = ["192.0.2.7"]
    terminal.screens.address_timer.timeout.emit()
    assert lock.address.text() == "testbox  ·  192.0.2.7"

    unlock(terminal)
    assert not terminal.screens.address_timer.isActive()
    reads = host.reads
    host.found = []
    terminal.screens.lock()
    assert host.reads == reads + 1 and terminal.screens.address_timer.isActive()
    assert lock.address.text() == "testbox  ·  No network"



################################################################
# After an update
################################################################

@pytest.mark.parametrize("result, said", [
    ({"outcome": "ok", "message": "updated from 2.8 to 2.9"}, "DarqOS updated from 2.8 to 2.9"),
    ({"outcome": "failed", "message": "its signature isn't the release key's", "log": "/var/log/darqos/u.log"},
     "Update failed: its signature isn't the release key's.  See /var/log/darqos/u.log"),
])
def test_how_an_update_went_is_said_after_unlocking(terminal, result, said):
    results = [result]
    terminal.update_result = lambda: results.pop() if results else None
    unlock(terminal)
    assert terminal.notice.isVisible() and terminal.notice.message.text() == said
    # Once.
    terminal.notice.hide()
    terminal.lock()
    terminal.compositor.received()
    unlock(terminal)
    assert not terminal.notice.isVisible()
