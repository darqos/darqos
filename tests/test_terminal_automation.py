# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Terminal protocol, darq.terminal (services/terminal/
# automation.py): what a script can ask of the Terminal, answered by a
# Terminal with a fake compositor and services, as in test_terminal.py.

import json
import os
import pathlib
import typing

import pytest

import darq
import darq.items
from darq.protocols import terminal as protocol
from darq.protocols.compositor import (
    CloseWindow, FocusWindow, OutputChanged, OutputRemoved, RaiseWindow, SetGeometry, WindowAdded, WindowFocused,
    WindowGeometry, WindowRemoved, WindowRetitled,
)
from darq.protocols.terminal import Rect, Status
from darq.version import Version

from darq._runtime.compositor import COMPOSITOR

from ui_fakes import DOC, inline
from test_terminal import automation, host, security, terminal, unlock  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


@pytest.fixture(autouse=True)
def version(monkeypatch):
    """The system's version, which the p-kernel would say."""
    monkeypatch.setattr(darq, "get_version", lambda: Version("2.9", 2, 9))


class Later:
    """A deferred reply, kept to look at."""

    def __init__(self):
        self.replies: list = []

    def reply(self, response):
        if self.replies:
            raise RuntimeError("answered already")
        self.replies.append(response)


@pytest.fixture
def service(terminal, monkeypatch):  # noqa: F811
    """What answers the protocol, unlocked, its launches run inline, its
    deferred replies kept, and what it tells watchers kept too."""
    unlock(terminal)
    answering = terminal.service
    answering.run = answering.post = inline
    answering.deferred = []

    def defer(timeout=None):
        answering.deferred.append(Later())
        return answering.deferred[-1]

    monkeypatch.setattr(answering, "defer", defer)
    told: list = []
    monkeypatch.setattr(darq, "send", lambda port, message: told.append((port, message)))
    answering.told = told
    return answering


def request(cls, **fields) -> typing.Any:
    return cls(**fields)


def window(terminal, number=3, title="Notes", app_id="darq-test"):  # noqa: F811
    terminal.handle(WindowAdded(window=number, title=title, app_id=app_id))
    terminal.handle(WindowGeometry(window=number, x=10, y=20, width=300, height=200))


################################################################
# The session
################################################################

def test_while_locked_only_state_lock_and_watch(terminal):  # noqa: F811
    answering = terminal.service
    assert answering.state(protocol.StateRequest(), 0) == protocol.StateResponse(locked=True, version="2.9")
    for method, cls in [("list_windows", protocol.ListWindowsRequest), ("focus", protocol.FocusRequest),
                        ("open_item", protocol.OpenItemRequest), ("run_tool", protocol.RunToolRequest),
                        ("notify", protocol.NotifyRequest), ("create_item", protocol.CreateItemRequest)]:
        assert getattr(answering, method)(cls(), 0).status == Status.LOCKED, method
    assert answering.lock(protocol.LockRequest(), 0).status == Status.OK


def test_lock_and_notify(service, terminal):  # noqa: F811
    assert service.notify(protocol.NotifyRequest(text="Backed up", source="nightly"), 0).status == Status.OK
    assert terminal.notice.message.text() == "Backed up"
    assert service.lock(protocol.LockRequest(), 0).status == Status.OK
    assert terminal.locked and service.state(protocol.StateRequest(), 0).locked


################################################################
# Windows
################################################################

def test_windows_and_outputs(service, terminal):  # noqa: F811
    window(terminal, 3, "Notes")
    window(terminal, 4, "Paper")
    terminal.handle(WindowFocused(window=4))
    terminal.handle(WindowFocused(window=3))
    terminal.handle(WindowRetitled(window=4, title="Paper, p. 2"))
    terminal.handle(OutputChanged(name="eDP-1", x=0, y=0, width=1920, height=1080))
    terminal.handle(OutputChanged(name="HDMI-1", x=1920, y=0, width=2560, height=1440))
    terminal.handle(OutputRemoved(name="HDMI-1"))

    listed = service.list_windows(protocol.ListWindowsRequest(), 0)
    assert listed.status == Status.OK
    assert [(w.window, w.title, w.focused) for w in listed.windows] == [(3, "Notes", True), (4, "Paper, p. 2", False)]
    assert listed.windows[0].geometry == Rect(x=10, y=20, width=300, height=200)
    outputs = service.list_outputs(protocol.ListOutputsRequest(), 0).outputs
    assert [(o.name, o.geometry.width) for o in outputs] == [("eDP-1", 1920)]

    terminal.handle(WindowRemoved(window=4))
    assert [w.window for w in service.list_windows(protocol.ListWindowsRequest(), 0).windows] == [3]


def test_a_lenss_window_says_what_its_showing(service, terminal, monkeypatch):  # noqa: F811
    window(terminal, 3, "Notes", app_id="lens.70")
    window(terminal, 4, "Gone", app_id="lens.71")

    def lens_shows(name, timeout=None):
        if name == "lens.71":
            raise darq.errors.DarqError("lens.71 isn't there")
        return DOC, False

    monkeypatch.setattr(automation, "lens_shows", lens_shows)
    assert service.list_windows(protocol.ListWindowsRequest(), 0) is service.deferred[0]
    [listed] = service.deferred[0].replies
    assert [(w.window, w.item_id) for w in listed.windows] == [(3, DOC), (4, "")]


def test_focus_move_and_close(service, terminal):  # noqa: F811
    window(terminal, 3)
    terminal.compositor.received()
    assert service.focus(protocol.FocusRequest(window=3), 0).status == Status.OK
    geometry = Rect(x=-5, y=6, width=0, height=480)
    assert service.set_geometry(protocol.SetGeometryRequest(window=3, geometry=geometry), 0).status == Status.OK
    assert service.close(protocol.CloseRequest(window=3), 0).status == Status.OK
    assert terminal.compositor.received() == [RaiseWindow(window=3), FocusWindow(window=3),
                                              SetGeometry(window=3, x=-5, y=6, width=0, height=480),
                                              CloseWindow(window=3)]
    for method, cls in [("focus", protocol.FocusRequest), ("close", protocol.CloseRequest),
                        ("set_geometry", protocol.SetGeometryRequest), ("open_entity", protocol.OpenEntityRequest)]:
        assert getattr(service, method)(cls(window=9), 0).status == Status.NOT_FOUND, method
    assert terminal.compositor.received() == []


def test_watchers_are_told(service, terminal, monkeypatch):  # noqa: F811
    assert service.watch(protocol.WatchRequest(listener=50), 0).status == Status.OK
    assert service.watch(protocol.WatchRequest(listener=51), 0).status == Status.OK
    window(terminal, 3, "Notes")
    told = [(port, type(m).__name__) for port, m in service.told]
    assert told == [(50, "WindowAdded"), (51, "WindowAdded"), (50, "WindowChanged"), (51, "WindowChanged")]
    assert service.told[-1][1].window.geometry == Rect(x=10, y=20, width=300, height=200)

    # One that can't be told is forgotten.
    closed: list[int] = []

    def send(port, message):
        if port == 51:
            raise darq.errors.DarqError("gone")
        service.told.append((port, message))

    monkeypatch.setattr(darq, "send", send)
    monkeypatch.setattr(darq, "close_port", closed.append)
    service.told.clear()
    terminal.lock()
    assert [(port, type(m).__name__) for port, m in service.told] == [(50, "Locked")]
    assert terminal.watchers == {50} and closed == [51]


################################################################
# Items
################################################################

def test_opening_an_item_answers_with_its_window(service, terminal):  # noqa: F811
    deferred = service.open_item(protocol.OpenItemRequest(item_id=DOC, source="tidy"), 0)
    assert deferred is service.deferred[0] and deferred.replies == []
    assert terminal.fake_services.launch.used == [(DOC, None)]

    # Another window isn't it; the lens's, named by its DPID, is.
    window(terminal, 5, "Other", app_id="lens.69")
    assert deferred.replies == []
    window(terminal, 6, "Doc", app_id="lens.70")
    assert deferred.replies == [protocol.OpenItemResponse(window=6, status=Status.OK)]


def test_a_window_shown_before_the_launch_finishes(service, terminal):  # noqa: F811
    window(terminal, 6, "Doc", app_id="lens.70")
    service.open_item(protocol.OpenItemRequest(item_id=DOC), 0)
    assert service.deferred[0].replies == [protocol.OpenItemResponse(window=6, status=Status.OK)]


def test_creating_records_the_callers_source(service, terminal):  # noqa: F811
    service.create_item(protocol.CreateItemRequest(type_id="public.text", source="tidy"), 0)
    window(terminal, 6, "Doc", app_id="lens.70")
    [reply] = service.deferred[0].replies
    assert (reply.item_id, reply.window, reply.status) == (DOC, 6, Status.OK)
    assert terminal.fake_services.launch.contexts[-1].source == "tidy"


def test_a_failed_launch_says_why(service, terminal):  # noqa: F811
    terminal.fake_services.launch.fail = True
    service.open_item(protocol.OpenItemRequest(item_id=DOC), 0)
    [reply] = service.deferred[0].replies
    assert reply.status == Status.FAILED and DOC in reply.message


def test_opening_a_windows_entity(service, terminal, monkeypatch):  # noqa: F811
    window(terminal, 3, "Notes", app_id="lens.70")
    window(terminal, 4, "Plain", app_id="darq-test")
    assert service.open_entity(protocol.OpenEntityRequest(window=4), 0).status == Status.NOT_FOUND

    monkeypatch.setattr(automation, "lens_shows", lambda name, timeout=None: (DOC, False))
    service.open_entity(protocol.OpenEntityRequest(window=3), 0)
    assert terminal.fake_services.launch.used[-1] == (DOC, None, darq.items.BASE_TYPE)


################################################################
# Tools
################################################################

def test_tools(service, terminal, tmp_path):  # noqa: F811
    (tmp_path / "tools" / "reindex").mkdir(parents=True)
    (tmp_path / "tools" / "reindex" / "tool.json").write_text(json.dumps({"name": "Reindex"}))
    terminal.root = tmp_path
    tools = terminal.tools.tools
    tools.spawn = lambda path: 9
    finishing: list = []
    terminal.launcher.background = lambda job, done: finishing.append((job, done))
    tools.wait = lambda dpid: darq.ChildStatus(dpid, darq.ExitReason.EXITED, 0, None)
    service.watch(protocol.WatchRequest(listener=50), 0)

    assert [(t.name, t.running) for t in service.list_tools(protocol.ListToolsRequest(), 0).tools] == [
        ("Reindex", False)]
    assert service.run_tool(protocol.RunToolRequest(name="Nothing"), 0).status == Status.NOT_FOUND
    assert service.run_tool(protocol.RunToolRequest(name="Reindex", source="nightly"), 0).status == Status.OK
    assert service.run_tool(protocol.RunToolRequest(name="Reindex"), 0).status == Status.RUNNING
    assert service.list_tools(protocol.ListToolsRequest(), 0).tools[0].running

    [(job, done)] = finishing
    done(job(), None)
    assert service.told[-1] == (50, protocol.ToolFinished(name="Reindex", outcome="Reindex finished"))


################################################################
# On the wire, and the client
################################################################

def test_windows_and_events_round_trip():
    windows = [protocol.Window(window=3, title="Notes", app_id="lens.70",
                               geometry=Rect(x=-5, y=6, width=300, height=200), focused=True, item_id=DOC),
               protocol.Window(window=4, title="", app_id="", geometry=Rect(), focused=False)]
    for message in (protocol.ListWindowsResponse(windows=windows, status=Status.OK),
                    protocol.WindowChanged(window=windows[0]),
                    protocol.OutputChanged(output=protocol.Output(name="eDP-1", geometry=Rect(x=0, y=0, width=1920,
                                                                                               height=1080))),
                    protocol.OpenEntityResponse(item_id=DOC, entity_window=7, status=Status.FAILED, message="why")):
        data, handles = message.encode()
        decoded = type(message)()
        decoded.decode(data, handles)
        assert decoded == message


class FakeClient:
    """The Terminal, as the client sees it."""

    def __init__(self, **responses):
        self.responses = responses
        self.asked: list = []

    def __getattr__(self, name):
        def call(**fields):
            self.asked.append((name, fields))
            return self.responses[name]
        return call


def test_the_client(monkeypatch):
    import darq.terminal as client
    fake = FakeClient(open_item=protocol.OpenItemResponse(window=6, status=Status.OK),
                      focus=protocol.FocusResponse(status=Status.NOT_FOUND),
                      create_item=protocol.CreateItemResponse(status=Status.FAILED, message="No lens"),
                      set_geometry=protocol.SetGeometryResponse(status=Status.OK))
    monkeypatch.setattr(client, "_client", lambda timeout=client.TIMEOUT: fake)
    monkeypatch.setattr(client, "source", "tidy")
    assert client.open_item(DOC) == 6
    assert fake.asked[-1] == ("open_item", {"item_id": DOC, "lens_for": "", "source": "tidy"})
    client.set_geometry(6, 0, 0)
    assert fake.asked[-1] == ("set_geometry", {"window": 6, "geometry": Rect(x=0, y=0, width=0, height=0)})
    with pytest.raises(client.TerminalError, match="focus 9: NOT_FOUND") as e:
        client.focus(9)
    assert e.value.status == Status.NOT_FOUND
    with pytest.raises(client.TerminalError, match="create a public.text: FAILED: No lens"):
        client.create_item("public.text")
    fake.responses["run_item"] = protocol.RunItemResponse(status=Status.RUNNING)
    with pytest.raises(client.TerminalError, match="RUNNING"):
        client.run_item(DOC)
    assert fake.asked[-1] == ("run_item", {"item_id": DOC, "source": "tidy"})



################################################################
# At boot, over DIPC
################################################################

def test_a_script_asks_the_terminal(live_pkernel, monkeypatch, tmp_path):  # noqa: F811
    """The Terminal, at boot, serves darq.terminal: a script asks it, with
    darq.terminal, what state it's in, and for the outputs, which the
    compositor's told it of, and the windows, which it won't say while
    it's locked."""
    if not COMPOSITOR.exists():
        pytest.skip("the compositor isn't built: see doc/terminal.rst")
    kernel = live_pkernel
    runtime = pathlib.Path(f"/tmp/darq-test-{os.getpid()}")
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("DARQ_TERMINAL", "1")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("WLR_BACKENDS", "headless")
    monkeypatch.setenv("WLR_RENDERER", "pixman")
    results = tmp_path / "results.json"
    script = write_app(tmp_path / "script", f"""
        import json
        import os
        import darq.terminal as terminal

        def darq_main():
            terminal.source = "test"
            state = terminal.state()
            outputs = [(o.name, o.geometry.width, o.geometry.height) for o in terminal.outputs()]
            try:
                terminal.windows()
                windows = "listed"
            except terminal.TerminalError as e:
                windows = e.status.name
            with open({str(results)!r} + ".tmp", "w") as f:
                json.dump({{"locked": state.locked, "outputs": outputs, "windows": windows}}, f)
            os.rename({str(results)!r} + ".tmp", {str(results)!r})
        """)
    try:
        kernel.do_boot()
        terminal_process = kernel._terminal
        assert terminal_process is not None
        running = kernel.module.KernelProcessStatus.RUNNING
        run_kernel_until(kernel, lambda: terminal_process.get_status() == running and (runtime / "darq-0").exists(),
                         "the Terminal and its compositor to run")
        kernel.do_spawn_process(None, script)
        run_kernel_until(kernel, results.exists, "the script to ask the Terminal")
        report = json.loads(results.read_text())
        assert report == {"locked": True, "outputs": [["HEADLESS-1", 1280, 720]], "windows": "LOCKED"}

        kernel.begin_shutdown(reboot=False)
        run_kernel_until(kernel, lambda: kernel._stopped, "the shutdown")
        assert terminal_process.get_child().wait(10) == 0
    finally:
        for path in runtime.iterdir():
            path.unlink()
        runtime.rmdir()



def test_running_a_package(service, terminal):  # noqa: F811
    program = "darq.python:" + "7" * 32
    terminal.launcher.background = lambda job, done: None
    assert service.run_item(protocol.RunItemRequest(item_id=program, source="repl"), 0).status == Status.OK
    assert service.run_item(protocol.RunItemRequest(item_id=program), 0).status == Status.RUNNING
    refused = service.run_item(protocol.RunItemRequest(item_id=DOC), 0)
    assert (refused.status, refused.message) == (Status.FAILED, "Only a Python package can be run")
    terminal.lock()
    assert service.run_item(protocol.RunItemRequest(item_id=program), 0).status == Status.LOCKED
