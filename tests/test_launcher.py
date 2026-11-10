# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.ui.launcher: creating and using items off the UI's
# thread, with faked services, on a real QtEventLoop and threads.

import threading
import time
import typing

import pytest

from darq.errors import LaunchError
from darq.loop import QtEventLoop
from darq.ui.launcher import Launcher

from ui_fakes import DOC, TEXT, FakeServices, inline


class FakeHost:
    # It doesn't hold a launcher: the tests do.
    launcher: typing.Any = None

    def __init__(self, services: typing.Any):
        self.services = services
        self.statuses: list[str] = []
        self.results: list[tuple] = []
        self.thread: threading.Thread | None = None

    def show_status(self, message: str):
        self.statuses.append(message)

    def launched(self, context, item_id, error):
        self.thread = threading.current_thread()
        self.results.append((context, item_id, error))


class BlockingLaunch:
    """A launch whose use_item waits until it's released."""

    def __init__(self, launch):
        self.launch = launch
        self.release = threading.Event()
        self.started = threading.Event()

    def create_item(self, type_id):
        return self.launch.create_item(type_id)

    def use_item(self, item_id, item=None):
        self.started.set()
        assert self.release.wait(10)
        return self.launch.use_item(item_id, item)


@pytest.fixture
def loop(qt_app):
    created = QtEventLoop()
    yield created
    created.close()


def run_until(condition, what: str):
    from PyQt6.QtWidgets import QApplication
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        QApplication.processEvents()
        time.sleep(0.01)


def test_create_doesnt_block(loop):
    from PyQt6.QtCore import QTimer
    services: typing.Any = FakeServices()
    blocking = BlockingLaunch(services.launch)
    services.launch = blocking
    host = FakeHost(services)
    done = []
    Launcher(host, "test", loop.post).create(TEXT, "UTF-8 Text", context="ctx", done=lambda: done.append(True))
    assert host.statuses == ["Creating a UTF-8 Text ..."]

    # While the lens is starting, the UI's loop runs.
    assert blocking.started.wait(10)
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    run_until(lambda: ticks, "the loop to run")
    assert host.results == [] and done == []

    blocking.release.set()
    run_until(lambda: host.results, "the launch")
    assert host.results == [("ctx", DOC, None)]
    assert host.thread is threading.main_thread()
    assert done == [True]
    assert services.history.added == [(DOC, "created", "test")]
    assert services.launch.launch.used == [(DOC, 42)]


def test_use(loop):
    services = FakeServices()
    host = FakeHost(services)
    Launcher(host, "test", loop.post).use(DOC, context=7)
    run_until(lambda: host.results, "the launch")
    assert host.statuses == [f"Starting a lens for {DOC} ..."]
    assert host.results == [(7, DOC, None)]
    assert services.launch.used == [(DOC, None)]


@pytest.mark.parametrize("fail_in, error", [
    ("create_item", f"Can't create a UTF-8 Text: Can't activate {TEXT}"),
    ("use_item", "No lens"),
    ("crash", "Launch failed: RuntimeError('oops')"),
])
def test_failures(fail_in, error):
    services = FakeServices()
    if fail_in == "create_item":
        services.launch.fail = True
    elif fail_in == "use_item":
        def use_item(item_id, item=None, lens_for=""):
            raise LaunchError("No lens")
        services.launch.use_item = use_item
    else:
        def create_item(type_id):
            raise RuntimeError("oops")
        services.launch.create_item = create_item
    host = FakeHost(services)
    done = []
    Launcher(host, "test", inline, inline).create(TEXT, "UTF-8 Text", done=lambda: done.append(True))

    [(_, _, reported)] = host.results
    assert reported == error and done == [True]
    assert services.history.added == ([] if fail_in != "use_item" else [(DOC, "created", "test")])


@pytest.mark.parametrize("name, created, error", [
    ("notes.txt", [(TEXT, b"Hello")], None),
    # The first type to take it.
    ("photo.dat", [("public.jpeg", b"Hello")], None),
    # What no type takes is kept as data.
    ("archive.zip", [("public.data", b"Hello")], None),
    ("README", [("public.data", b"Hello")], None),
])
def test_create_from_file(tmp_path, name, created, error):
    path = tmp_path / name
    path.write_bytes(b"Hello")
    services = FakeServices()
    host = FakeHost(services)
    done = []
    Launcher(host, "test", inline, inline).create_from_file(str(path), context=3, done=lambda: done.append(True))

    assert host.statuses == [f"Importing {name} ..."]
    assert services.launch.created == created
    assert done == [True]
    if error is None:
        assert host.results == [(3, DOC, None)]
        assert services.history.added == [(DOC, "imported", "test")]
        assert services.launch.used == [(DOC, 43)]
    else:
        assert host.results == [(3, None, error)]
        assert services.history.added == []


def test_data_thats_refused_by_its_type_is_kept_as_data(tmp_path, monkeypatch):
    services = FakeServices()
    host = FakeHost(services)
    monkeypatch.setattr(services.launch, "refuses", {"public.png", "public.jpeg"})
    path = tmp_path / "photo.dat"
    path.write_bytes(b"junk")
    Launcher(host, "test", inline, inline).create_from_file(str(path))
    assert services.launch.created == [("public.data", b"junk")] and services.launch.names == ["photo.dat"]


def test_create_from_file_refused_and_missing(tmp_path, monkeypatch):
    services = FakeServices()
    host = FakeHost(services)
    monkeypatch.setattr(services.launch, "refuses", {"public.png", "public.jpeg", "public.data"})
    path = tmp_path / "photo.dat"
    path.write_bytes(b"junk")
    Launcher(host, "test", inline, inline).create_from_file(str(path))
    assert host.results[-1][2] == "Can't import photo.dat: it isn't a public.png or public.jpeg or public.data"

    Launcher(host, "test", inline, inline).create_from_file(str(tmp_path / "gone.txt"))
    assert host.results[-1][2] == "Can't read gone.txt: No such file or directory"


def test_closed_loop_and_failing_host():
    services = FakeServices()
    host = FakeHost(services)

    # The UI's gone: the result is only logged.
    def closed(function):
        raise RuntimeError("event loop is closed")
    Launcher(host, "test", closed, inline).use(DOC)
    assert host.results == []

    # Nothing's raised into the UI's loop.
    def launched(context, item_id, error):
        raise ValueError("bug")
    host.launched = launched
    Launcher(host, "test", inline, inline).use(DOC)
