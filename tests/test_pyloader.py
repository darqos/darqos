# darqos
# Copyright (C) 2026 David Arnold

# Round-trip tests for pyloader's p-call path.
#
# The application thread makes p-calls through darq.syscall, the main
# thread runs pyloader's event loop, and a fake p-kernel thread answers
# requests over a socket pair standing in for the TCP session.

import importlib.machinery
import importlib.util
import pathlib
import socket
import sys
import textwrap
import threading
import time

import pytest

import darq


PYLOADER_PATH = pathlib.Path(__file__).parent.parent / "kernel" / "pyloader"
TIMEOUT = 5.0


def load_pyloader():
    """Import kernel/pyloader as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("pyloader", str(PYLOADER_PATH))
    spec = importlib.util.spec_from_loader("pyloader", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakePKernel:
    """Answer p-call requests on one end of a socket pair."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.received: list[darq.Message] = []

        # Message types to receive without answering.
        self.ignore: set[int] = set()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        buf = darq.Buffer()
        while True:
            while (buf.length() < 8 or
                   buf.length() < darq.Message.decode_length(buf.peek(8))):
                data = self.sock.recv(65536)
                if not data:
                    return
                buf.append(data)

            length = darq.Message.decode_length(buf.peek(8))
            message_buf = buf.peek(length)
            buf.consume(length)
            self.handle(darq.Message.decode_type(message_buf), message_buf)

    def handle(self, message_type, message_buf):
        if message_type in self.ignore:
            self.received.append(message_type)

        elif message_type == darq.MessageType.OPEN_PORT_RQST:
            request = darq.OpenPortRequest()
            request.decode(message_buf)
            self.received.append(request)
            port = request.requested_port or 16385
            self.sock.sendall(darq.OpenPortResponse(request.request_id, 0, port).encode())

        elif message_type == darq.MessageType.CLOSE_PORT_RQST:
            request = darq.ClosePortRequest()
            request.decode(message_buf)
            self.received.append(request)
            response = darq.ClosePortResponse()
            response.request_id = request.request_id
            response.port = request.port
            self.sock.sendall(response.encode())

        elif message_type == darq.MessageType.SPAWN_PROCESS_RQST:
            request = darq.SpawnProcessRequest(0, "")
            request.decode(message_buf)
            self.received.append(request)
            self.sock.sendall(darq.SpawnProcessResponse(request.request_id, 0, 7, None).encode())

        elif message_type == darq.MessageType.KILL_PROCESS_RQST:
            request = darq.KillProcessRequest()
            request.decode(message_buf)
            self.received.append(request)
            self.sock.sendall(darq.KillProcessResponse(request.request_id, 0).encode())

        elif message_type == darq.MessageType.SEND_MESSAGE:
            request = darq.SendMessage()
            request.decode(message_buf)
            self.received.append(request)


@pytest.fixture
def pproc(monkeypatch):
    """A PythonPProcess connected to a fake p-kernel."""
    pyloader = load_pyloader()

    # Don't rename the pytest process.
    monkeypatch.setattr(pyloader.setproctitle, "setproctitle", lambda title: None)

    kernel_sock, process_sock = socket.socketpair()
    process = pyloader.PythonPProcess(0, 3, 2, "/unused")
    process._pkernel_sock.close()
    process._pkernel_sock = process_sock
    process.loop.add_socket(process_sock, process)

    monkeypatch.setitem(darq.syscall.__dict__, "_darq_process_state", process)

    kernel = FakePKernel(kernel_sock)
    kernel.thread.start()
    process.kernel = kernel

    yield process

    # Stop the fake p-kernel before closing its socket.
    process_sock.shutdown(socket.SHUT_RDWR)
    kernel.thread.join(TIMEOUT)
    process_sock.close()
    kernel_sock.close()


def run_app(process, app):
    """Run app() on an application thread while the main thread runs the loop."""
    result = {}

    def target():
        try:
            result["value"] = app()
        except BaseException as e:
            result["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    deadline = time.monotonic() + TIMEOUT
    while thread.is_alive():
        assert time.monotonic() < deadline, "p-call did not complete"
        process.loop.next()

    if "error" in result:
        raise result["error"]
    return result.get("value")


def test_open_port(pproc):
    assert run_app(pproc, lambda: darq.open_port(0)) == 16385
    assert list(pproc.ports) == [16385]
    assert pproc._pending == {}


def test_close_port(pproc):
    run_app(pproc, lambda: darq.open_port(42))
    assert 42 in pproc.ports

    run_app(pproc, lambda: darq.close_port(42))
    assert 42 not in pproc.ports
    assert pproc._pending == {}


def test_spawn_process(pproc):
    assert run_app(pproc, lambda: darq.spawn_process("/darq/bin/x")) == 7
    assert pproc.kernel.received[-1].get_exec_path() == "/darq/bin/x"
    assert pproc._pending == {}


def test_request_ids_assigned_by_main_thread(pproc):
    def app():
        darq.open_port(0)
        darq.spawn_process("/darq/bin/x")
        darq.close_port(16385)

    run_app(pproc, app)
    assert [m.request_id for m in pproc.kernel.received] == [1, 2, 3]


def test_send_message_not_tracked(pproc):
    def app():
        port = darq.open_port(0)
        darq.send_message(port, 16386, b"hello")
        # Synchronise: the spawn reply follows the send in queue order.
        darq.spawn_process("/darq/bin/x")

    run_app(pproc, app)
    sent = [m for m in pproc.kernel.received if isinstance(m, darq.SendMessage)]
    assert len(sent) == 1
    assert (sent[0].source, sent[0].destination, sent[0].payload) == (16385, 16386, b"hello")
    assert pproc._pending == {}


def test_unknown_response_ignored(pproc):
    response = darq.OpenPortResponse(99, 0, 16385)
    assert pproc.complete_syscall(response) is None


def test_kill_process(pproc):
    run_app(pproc, lambda: darq.kill_process(9))
    assert pproc.kernel.received[-1].dpid == 9
    assert pproc._pending == {}


################################################################
# Killing the application thread
################################################################

@pytest.fixture
def start_app(pproc, tmp_path, monkeypatch):
    """Start darq_main() from the given source on the application thread."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "main", raising=False)

    def start(source: str):
        (tmp_path / "main.py").write_text(textwrap.dedent(source))
        pproc._exec_path = str(tmp_path)
        pproc.start_app()
        return pproc._app_thread

    yield start
    monkeypatch.delitem(sys.modules, "main", raising=False)


def test_kill_app_running(pproc, start_app):
    thread = start_app("""
        def darq_main():
            while True:
                pass
        """)
    assert thread.is_alive()
    assert pproc.kill_app()
    assert not thread.is_alive()


def test_kill_app_blocked_in_pcall(pproc, start_app):
    pproc.kernel.ignore.add(darq.MessageType.SPAWN_PROCESS_RQST)
    thread = start_app("""
        import darq
        def darq_main():
            darq.spawn_process("/hang")
        """)

    # Run the loop until the spawn request is waiting for its response.
    deadline = time.monotonic() + TIMEOUT
    while not pproc._pending:
        assert time.monotonic() < deadline, "spawn request not sent"
        pproc.loop.next()

    assert pproc.kill_app()
    assert not thread.is_alive()
    assert pproc._pending == {}


def test_kill_app_not_started(pproc):
    assert pproc.kill_app()


def test_kill_process_request(pproc, start_app):
    thread = start_app("""
        import time
        def darq_main():
            while True:
                time.sleep(0.01)
        """)
    pproc.loop.active = True
    pproc.dispatch(darq.MessageType.KILL_PROCESS_RQST,
                   darq.KillProcessRequest(0, pproc.getpid()).encode())
    assert not thread.is_alive()
    assert not pproc.loop.active


def test_kill_process_request_other_dpid(pproc, start_app):
    thread = start_app("""
        import time
        def darq_main():
            while True:
                time.sleep(0.01)
        """)
    pproc.dispatch(darq.MessageType.KILL_PROCESS_RQST,
                   darq.KillProcessRequest(0, pproc.getpid() + 1).encode())
    assert thread.is_alive()
    assert pproc.kill_app()


def test_getpid_and_getppid_are_local(pproc):
    assert run_app(pproc, lambda: (darq.getpid(), darq.getppid())) == (3, 2)
    assert pproc.kernel.received == []
