# darqos
# Copyright (C) 2026 David Arnold

# Round-trip tests for pyloader's p-call path.
#
# The application thread makes p-calls through darq.syscall, the main
# thread runs pyloader's event loop, and a fake p-kernel thread answers
# requests over a socket pair standing in for the TCP session.

import importlib.machinery
import importlib.util
import os
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

        elif message_type == darq.MessageType.RUN_REQUEST:
            message = darq.RunRequest()
            message.decode(message_buf)
            self.received.append(message)

        elif message_type == darq.MessageType.EXITED:
            message = darq.Exited()
            message.decode(message_buf)
            self.received.append(message)


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

    # Tests poll conditions the loop can't see (such as a thread ending):
    # don't let an idle iteration block for long.
    process.loop.MAX_TIMEOUT = 0.05

    monkeypatch.setitem(darq.syscall.__dict__, "_darq_process_state", process)

    kernel = FakePKernel(kernel_sock)
    kernel.thread.start()
    process.kernel = kernel

    yield process

    # Stop the fake p-kernel before closing its socket (unless pyloader
    # has already closed it).
    if process_sock.fileno() != -1:
        process_sock.shutdown(socket.SHUT_RDWR)
    kernel_sock.shutdown(socket.SHUT_RDWR)
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
def install_app(pproc, tmp_path, monkeypatch):
    """Make the given source the application's main.py, without starting it."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "main", raising=False)

    def install(source: str):
        (tmp_path / "main.py").write_text(textwrap.dedent(source))
        pproc._exec_path = str(tmp_path)

    yield install
    monkeypatch.delitem(sys.modules, "main", raising=False)


@pytest.fixture
def start_app(pproc, install_app):
    """Start darq_main() from the given source on the application thread."""

    def start(source: str):
        install_app(source)
        pproc.start_app()
        return pproc._app_thread

    return start


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



################################################################
# p-kernel disconnection
################################################################

def run_loop(process):
    """Run the event loop until it's stopped."""
    process.loop.active = True
    deadline = time.monotonic() + TIMEOUT
    while process.loop.active:
        assert time.monotonic() < deadline, "event loop did not stop"
        process.loop.next()


@pytest.mark.parametrize("blocked_in_pcall", [False, True])
def test_p_kernel_disconnect(pproc, start_app, blocked_in_pcall):
    if blocked_in_pcall:
        pproc.kernel.ignore.add(darq.MessageType.SPAWN_PROCESS_RQST)
        thread = start_app("""
            import darq
            def darq_main():
                darq.spawn_process("/hang")
            """)
        deadline = time.monotonic() + TIMEOUT
        while not pproc._pending:
            assert time.monotonic() < deadline, "spawn request not sent"
            pproc.loop.next()
    else:
        thread = start_app("""
            import time
            def darq_main():
                while True:
                    time.sleep(0.01)
            """)

    # The p-kernel drops the connection.
    pproc.kernel.sock.shutdown(socket.SHUT_RDWR)
    run_loop(pproc)

    assert pproc.exit_status == 1
    assert pproc._pkernel_sock is None
    assert not thread.is_alive()
    pproc.disconnect()


################################################################
# Application exit
################################################################

def exited_messages(process) -> list:
    return [m for m in process.kernel.received if isinstance(m, darq.Exited)]


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
def test_app_exit_reported(pproc, start_app, body, status, error):
    thread = start_app(f"""
        def darq_main():
            {body}
        """)
    run_loop(pproc)

    [exited] = exited_messages(pproc)
    assert (exited.status, exited.get_error()) == (status, error)
    assert not thread.is_alive()

    # The session is closed, and the host exit status reflects the app's.
    assert pproc._pkernel_sock is None
    assert pproc.exit_status == (status if 0 <= status <= 255 else 1)


def test_app_exception_reported(pproc, start_app):
    start_app("""
        def darq_main():
            raise ValueError("boom")
        """)
    run_loop(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 1
    assert exited.get_error().startswith("Traceback (most recent call last):")
    assert exited.get_error().rstrip().endswith("ValueError: boom")
    assert pproc.exit_status == 1


def test_app_exit_follows_its_last_pcalls(pproc, start_app):
    # A one-way p-call queued just before the app returns must reach the
    # p-kernel before the Exited message.
    start_app("""
        import darq
        def darq_main():
            darq.send_message(16385, 16386, b"last words")
        """)
    run_loop(pproc)

    kinds = [type(m) for m in pproc.kernel.received]
    assert kinds == [darq.SendMessage, darq.Exited]


def test_killed_app_does_not_report_exit(pproc, start_app):
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

    # Give a stray exit report the chance to be processed.
    pproc.loop.next()
    assert exited_messages(pproc) == []
    assert pproc._app_result is None


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

LONG_RUNNING_APP = """
    import time
    def darq_main():
        while True:
            time.sleep(0.01)
    """


def test_run_request(pproc):
    pproc.send_run_request()
    pproc.kernel.wait_for(1)
    [request] = pproc.kernel.received
    assert (request.dpid, request.host_pid) == (3, os.getpid())


def test_app_not_started_before_run_response(pproc, install_app):
    install_app(LONG_RUNNING_APP)
    pproc.send_run_request()
    pproc.loop.next()
    assert pproc._app_thread is None


def test_run_response_starts_app(pproc, install_app):
    install_app(LONG_RUNNING_APP)
    pproc.dispatch(darq.MessageType.RUN_RESPONSE, darq.RunResponse(0).encode())

    thread = pproc._app_thread
    assert thread is not None and thread.is_alive()

    # A duplicate response doesn't start a second application.
    pproc.dispatch(darq.MessageType.RUN_RESPONSE, darq.RunResponse(0).encode())
    assert pproc._app_thread is thread
    assert pproc.kill_app()


def test_refused_run_response(pproc, install_app):
    install_app(LONG_RUNNING_APP)
    pproc.loop.active = True
    pproc.dispatch(darq.MessageType.RUN_RESPONSE, darq.RunResponse(1).encode())

    assert pproc._app_thread is None
    assert pproc.exit_status == 1
    assert not pproc.loop.active


def test_app_that_fails_to_load(pproc, install_app):
    install_app("""
        raise ImportError("broken app")
        """)
    pproc.dispatch(darq.MessageType.RUN_RESPONSE, darq.RunResponse(0).encode())
    run_loop(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 1
    assert exited.get_error().rstrip().endswith("ImportError: broken app")
    assert pproc.exit_status == 1
