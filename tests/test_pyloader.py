# darqos
# Copyright (C) 2026 David Arnold

# Tests for pyloader's p-call path, and the application's lifecycle.
#
# As in a real process, the application runs on the main thread (the
# test's), while pyloader's I/O thread runs its event loop.  A fake
# p-kernel thread answers requests over a socket pair standing in for
# the TCP session.

import _thread
import importlib.machinery
import importlib.util
import os
import pathlib
import socket
import sys
import textwrap
import threading
import time
import typing

import pytest

import darq


PYLOADER_PATH = pathlib.Path(__file__).parent.parent / "kernel" / "pyloader"
TIMEOUT = 5.0

# The dpid of the process under test.
DPID = 3


def load_pyloader():
    """Import kernel/pyloader as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("pyloader", str(PYLOADER_PATH))
    spec = importlib.util.spec_from_loader("pyloader", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakePKernel:
    """Answer p-call requests on one end of a socket pair."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.received: list[darq.Message] = []

        # Result for the RUN_RESPONSE to a RUN_REQUEST, or None to not answer.
        self.run_result: int | None = 0

        # Actions to take, by message type, instead of answering.  Each is
        # called with the received message.
        self.actions: dict[int, typing.Callable[[typing.Any], None]] = {}
        self.thread = threading.Thread(target=self.run, daemon=True)

    def send(self, message: darq.Message):
        self.sock.sendall(message.encode())

    def run(self):
        buf = darq.Buffer()
        while True:
            while (buf.length() < 8 or
                   buf.length() < darq.Message.decode_length(buf.peek(8))):
                try:
                    data = self.sock.recv(65536)
                except OSError:
                    data = b''
                if not data:
                    # The process has closed its side: close ours too.
                    try:
                        self.sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    return
                buf.append(data)

            length = darq.Message.decode_length(buf.peek(8))
            message_buf = buf.peek(length)
            buf.consume(length)
            self.handle(darq.Message.decode_type(message_buf), message_buf)

    def wait_for(self, count: int):
        """Wait until this many messages have been received."""
        deadline = time.monotonic() + TIMEOUT
        while len(self.received) < count:
            assert time.monotonic() < deadline, "messages not received"
            time.sleep(0.01)

    def handle(self, message_type, message_buf):
        message = self.decode(message_type, message_buf)
        self.received.append(message)

        action = self.actions.get(message_type)
        if action is not None:
            action(message)
            return

        if message_type == darq.MessageType.OPEN_PORT_RQST:
            port = message.requested_port or 16385
            self.send(darq.OpenPortResponse(message.request_id, 0, port))

        elif message_type == darq.MessageType.CLOSE_PORT_RQST:
            self.send(darq.ClosePortResponse(message.request_id, 0, message.port))

        elif message_type == darq.MessageType.SPAWN_PROCESS_RQST:
            self.send(darq.SpawnProcessResponse(message.request_id, 0, 7, None))

        elif message_type == darq.MessageType.KILL_PROCESS_RQST:
            self.send(darq.KillProcessResponse(message.request_id, 0))

        elif message_type == darq.MessageType.RUN_REQUEST:
            if self.run_result is not None:
                self.send(darq.RunResponse(self.run_result))

    @staticmethod
    def decode(message_type, message_buf) -> typing.Any:
        if message_type == darq.MessageType.OPEN_PORT_RQST:
            message = darq.OpenPortRequest()
        elif message_type == darq.MessageType.CLOSE_PORT_RQST:
            message = darq.ClosePortRequest()
        elif message_type == darq.MessageType.SPAWN_PROCESS_RQST:
            message = darq.SpawnProcessRequest(0, "")
        elif message_type == darq.MessageType.KILL_PROCESS_RQST:
            message = darq.KillProcessRequest()
        elif message_type == darq.MessageType.SEND_MESSAGE:
            message = darq.SendMessage()
        elif message_type == darq.MessageType.RUN_REQUEST:
            message = darq.RunRequest()
        elif message_type == darq.MessageType.EXITED:
            message = darq.Exited()
        else:
            raise AssertionError(f"unexpected message type {message_type}")
        message.decode(message_buf)
        return message



@pytest.fixture
def pproc(monkeypatch):
    """A PythonPProcess connected to a fake p-kernel."""
    pyloader = load_pyloader()

    # Don't rename the pytest process.
    monkeypatch.setattr(pyloader.setproctitle, "setproctitle", lambda title: None)

    # Loading an application adds to the module search path.
    monkeypatch.setattr(sys, "path", list(sys.path))

    kernel_sock, process_sock = socket.socketpair()
    process = pyloader.PythonPProcess(0, DPID, 2, "/unused")
    process._pkernel_sock.close()
    process._pkernel_sock = process_sock
    process.io_loop.add_socket(process_sock, process)

    monkeypatch.setitem(darq.syscall.__dict__, "_darq_process_state", process)

    kernel = FakePKernel(kernel_sock)
    kernel.thread.start()
    process.kernel = kernel

    yield process

    # Stop the fake p-kernel before closing its socket (unless pyloader
    # has already closed it).
    if process_sock.fileno() != -1:
        process_sock.shutdown(socket.SHUT_RDWR)
    try:
        kernel_sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    kernel.thread.join(TIMEOUT)
    process_sock.close()
    kernel_sock.close()


def run_process(process):
    """Run the process, as pyloader's main does, until it's finished.

    The application runs on this thread.  If it takes too long, it's
    interrupted, and the I/O thread stopped."""
    timed_out = []

    def watchdog():
        timed_out.append(True)
        process.io_loop.post(process.io_loop.stop)
        _thread.interrupt_main()

    timer = threading.Timer(TIMEOUT, watchdog)
    timer.start()
    try:
        process.send_run_request()
        process.run()
    finally:
        timer.cancel()
    assert not timed_out, "process did not finish"
    assert not process._io_thread.is_alive()


def run_app(process, app):
    """Run app() as the application's darq_main, and return its result."""
    result = {}

    def darq_main():
        result["value"] = app()

    process.load_app = lambda: darq_main
    run_process(process)

    [exited] = exited_messages(process)
    assert exited.status == 0, exited.get_error()
    return result.get("value")


def exited_messages(process) -> list:
    return [m for m in process.kernel.received if isinstance(m, darq.Exited)]


def pcalls(process) -> list:
    """The messages received by the p-kernel, other than the session's own."""
    return [m for m in process.kernel.received if not isinstance(m, (darq.RunRequest, darq.Exited))]


def test_app_runs_on_main_thread(pproc):
    assert run_app(pproc, lambda: threading.current_thread() is threading.main_thread())


def test_open_port(pproc):
    assert run_app(pproc, lambda: darq.open_port(0)) == 16385
    assert list(pproc.ports) == [16385]
    assert pproc._pending == {}


def test_close_port(pproc):
    def app():
        darq.open_port(42)
        opened = 42 in pproc.ports
        darq.close_port(42)
        return opened

    assert run_app(pproc, app)
    assert 42 not in pproc.ports
    assert pproc._pending == {}


def test_spawn_process(pproc):
    assert run_app(pproc, lambda: darq.spawn_process("/darq/bin/x")) == 7
    assert pcalls(pproc)[-1].get_exec_path() == "/darq/bin/x"
    assert pproc._pending == {}


def test_request_ids_assigned_by_io_thread(pproc):
    def app():
        darq.open_port(0)
        darq.spawn_process("/darq/bin/x")
        darq.close_port(16385)

    run_app(pproc, app)
    assert [m.request_id for m in pcalls(pproc)] == [1, 2, 3]


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
    assert pcalls(pproc)[-1].dpid == 9
    assert pproc._pending == {}


def test_getpid_and_getppid_are_local(pproc):
    assert run_app(pproc, lambda: (darq.getpid(), darq.getppid())) == (DPID, 2)
    assert pcalls(pproc) == []


################################################################
# Callbacks and delivered messages, on the application's loop
################################################################

@pytest.mark.parametrize("pcall, args", [
    (lambda loop, cb: darq.open_port_cb(0, loop, cb), (16385, 0)),
    (lambda loop, cb: darq.close_port_cb(42, loop, cb), (42, 0)),
    (lambda loop, cb: darq.spawn_process_cb("/darq/bin/x", loop, cb), (7, 0)),
    (lambda loop, cb: darq.kill_process_cb(9, loop, cb), (9, 0)),
])
def test_callback_runs_on_app_loop(pproc, pcall, args):
    def app():
        loop = darq.SelectEventLoop()
        darq.init(loop)
        calls = []

        def callback(value, error):
            calls.append((value, error, threading.current_thread() is threading.main_thread()))
            loop.stop()

        pcall(loop, callback)
        loop.run()
        return calls

    assert run_app(pproc, app) == [(*args, True)]
    assert pproc._pending == {}


def test_delivered_message_runs_on_app_loop(pproc):
    # The p-kernel delivers each sent message back to its destination.
    def deliver(request):
        message = darq.DeliverMessage(request.source, request.destination)
        message.set_payload(request.payload)
        pproc.kernel.send(message)

    pproc.kernel.actions[darq.MessageType.SEND_MESSAGE] = deliver

    def app():
        loop = darq.SelectEventLoop()
        delivered = []

        class Listener(darq.EventListener):
            def on_message(self, source, destination, message):
                delivered.append((source, destination, message,
                                  threading.current_thread() is threading.main_thread()))
                loop.stop()

        darq.init_cb(loop, Listener())
        port = darq.open_port(0)
        darq.send_message(port, port, b"hello")
        loop.run()
        return delivered

    assert run_app(pproc, app) == [(16385, 16385, b"hello", True)]


################################################################
# Killing the application
################################################################

@pytest.fixture
def install_app(pproc, tmp_path, monkeypatch):
    """Make the given source the application's main.py."""
    monkeypatch.delitem(sys.modules, "main", raising=False)

    def install(source: str):
        (tmp_path / "main.py").write_text(textwrap.dedent(source))
        pproc._exec_path = str(tmp_path)

    yield install
    monkeypatch.delitem(sys.modules, "main", raising=False)


def kill_on(process, message_type: int, dpid: int = DPID):
    """Have the p-kernel answer this message type with a kill request."""
    process.kernel.actions[message_type] = \
        lambda request: process.kernel.send(darq.KillProcessRequest(0, dpid))


@pytest.mark.parametrize("body", [
    "while True: pass",
    "while True: time.sleep(0.01)",
])
def test_kill_running_app(pproc, install_app, body):
    install_app(f"""
        import time
        import darq
        def darq_main():
            darq.send_message(16385, 16386, b"started")
            {body}
        """)
    kill_on(pproc, darq.MessageType.SEND_MESSAGE)
    run_process(pproc)

    # A killed application doesn't report its exit.
    assert pproc._app_killed
    assert exited_messages(pproc) == []
    assert pproc._app_result is None
    assert pproc._pending == {}
    assert pproc.exit_status == 0


def test_kill_app_blocked_in_pcall(pproc, install_app):
    install_app("""
        import darq
        def darq_main():
            darq.spawn_process("/hang")
        """)
    kill_on(pproc, darq.MessageType.SPAWN_PROCESS_RQST)
    run_process(pproc)

    assert pproc._app_killed
    assert exited_messages(pproc) == []
    assert pproc._pending == {}


def test_kill_before_start(pproc):
    loaded = []
    pproc.load_app = lambda: loaded.append(True)
    kill_on(pproc, darq.MessageType.RUN_REQUEST)

    run_process(pproc)
    assert loaded == []
    assert exited_messages(pproc) == []
    assert pproc.exit_status == 0


def test_kill_app_not_started(pproc):
    assert pproc.kill_app()
    assert pproc._app_start.is_set()


def test_kill_process_request_other_dpid(pproc):
    kill_on(pproc, darq.MessageType.SEND_MESSAGE, DPID + 1)

    def app():
        darq.send_message(16385, 16386, b"started")
        # The kill request is handled before this p-call's response.
        return darq.spawn_process("/darq/bin/x")

    assert run_app(pproc, app) == 7
    assert not pproc._app_killed


################################################################
# p-kernel disconnection
################################################################

@pytest.mark.parametrize("blocked_in_pcall", [False, True])
def test_p_kernel_disconnect(pproc, install_app, blocked_in_pcall):
    install_app(f"""
        import time
        import darq
        def darq_main():
            darq.send_message(16385, 16386, b"started")
            if {blocked_in_pcall}:
                darq.spawn_process("/hang")
            while True:
                time.sleep(0.01)
        """)

    # The p-kernel drops the connection.
    def disconnect(request):
        pproc.kernel.sock.shutdown(socket.SHUT_RDWR)

    if blocked_in_pcall:
        pproc.kernel.actions[darq.MessageType.SPAWN_PROCESS_RQST] = disconnect
    else:
        pproc.kernel.actions[darq.MessageType.SEND_MESSAGE] = disconnect

    run_process(pproc)

    assert pproc.exit_status == 1
    assert pproc._pkernel_sock is None
    assert pproc._app_killed


def test_p_kernel_disconnect_before_start(pproc):
    loaded = []
    pproc.load_app = lambda: loaded.append(True)
    pproc.kernel.actions[darq.MessageType.RUN_REQUEST] = \
        lambda request: pproc.kernel.sock.shutdown(socket.SHUT_RDWR)

    run_process(pproc)
    assert loaded == []
    assert pproc.exit_status == 1


################################################################
# Application exit
################################################################

@pytest.mark.parametrize("body, status, error", [
    ("return", 0, None),
    ("return 0", 0, None),
    ("return 3", 3, None),
    ("return -1", -1, None),
    ("return 'done'", 1, "darq_main returned 'done'"),
    ("return 2 ** 40", 1, "darq_main returned 1099511627776"),
    ("import sys; sys.exit()", 0, None),
    ("import sys; sys.exit(5)", 5, None),
    ("import sys; sys.exit('failed')", 1, "sys.exit() with 'failed'"),
])
def test_app_exit_reported(pproc, install_app, body, status, error):
    install_app(f"""
        def darq_main():
            {body}
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert (exited.status, exited.get_error()) == (status, error)

    # The session is closed, and the host exit status reflects the app's.
    assert pproc._pkernel_sock is None
    assert pproc.exit_status == (status if 0 <= status <= 255 else 1)


def test_app_exception_reported(pproc, install_app):
    install_app("""
        def darq_main():
            raise ValueError("boom")
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 1
    assert exited.get_error().startswith("Traceback (most recent call last):")
    assert exited.get_error().rstrip().endswith("ValueError: boom")
    assert pproc.exit_status == 1


def test_app_exit_follows_its_last_pcalls(pproc, install_app):
    # A one-way p-call queued just before the app returns must reach the
    # p-kernel before the Exited message.
    install_app("""
        import darq
        def darq_main():
            darq.send_message(16385, 16386, b"last words")
        """)
    run_process(pproc)

    kinds = [type(m) for m in pproc.kernel.received]
    assert kinds == [darq.RunRequest, darq.SendMessage, darq.Exited]


@pytest.mark.parametrize("value, result", [
    (None, (0, None)),
    (0, (0, None)),
    (True, (1, None)),
    (2 ** 31 - 1, (2 ** 31 - 1, None)),
    (-2 ** 31, (-2 ** 31, None)),
    (2 ** 31, (1, "returned 2147483648")),
    (1.5, (1, "returned 1.5")),
])
def test_status_from_result(pproc, value, result):
    assert pproc.status_from_result(value, "returned") == result


################################################################
# Starting: RUN_REQUEST and RUN_RESPONSE
################################################################

def test_run_request(pproc):
    pproc.kernel.run_result = None
    pproc.send_run_request()
    pproc.kernel.wait_for(1)
    [request] = pproc.kernel.received
    assert (request.dpid, request.host_pid) == (DPID, os.getpid())


def test_app_not_started_before_run_response(pproc):
    loaded = []
    pproc.load_app = lambda: (lambda: loaded.append(True))

    # When the p-kernel receives the request, the app hasn't been loaded.
    def answer(request):
        loaded.append(False)
        pproc.kernel.send(darq.RunResponse(0))

    pproc.kernel.actions[darq.MessageType.RUN_REQUEST] = answer
    run_process(pproc)
    assert loaded == [False, True]


def test_duplicate_run_response(pproc):
    loads = []

    def load_app():
        loads.append(True)
        return lambda: None

    pproc.load_app = load_app

    def answer_twice(request):
        pproc.kernel.send(darq.RunResponse(0))
        pproc.kernel.send(darq.RunResponse(0))

    pproc.kernel.actions[darq.MessageType.RUN_REQUEST] = answer_twice
    run_process(pproc)
    assert loads == [True]
    assert len(exited_messages(pproc)) == 1


def test_refused_run_response(pproc):
    loaded = []
    pproc.load_app = lambda: loaded.append(True)
    pproc.kernel.run_result = 1

    run_process(pproc)
    assert loaded == []
    assert exited_messages(pproc) == []
    assert pproc.exit_status == 1


def test_app_that_fails_to_load(pproc, install_app):
    install_app("""
        raise ImportError("broken app")
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 1
    assert exited.get_error().rstrip().endswith("ImportError: broken app")
    assert pproc.exit_status == 1


def test_app_can_make_pcalls_while_loading(pproc, install_app):
    install_app("""
        import darq
        PORT = darq.open_port(0)
        def darq_main():
            return PORT
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 16385
