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
from darq import pcall as pk


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
        self.received: list[pk.Message] = []

        # Next handle for an opened port, or a looked-up name.
        self.next_handle = 16385

        # Result to give each SendMessageRequest.  Sends are answered
        # before any action for them is taken.
        self.send_result = pk.SendResult.OK
        self.send_delay = 0.0

        # Response to a WaitRequest: (result, dpid, reason, status, error).
        self.wait_response = (pk.WaitResult.NO_SUCH_CHILD, 0, 0, 0, None)

        # Registered names, and the result to give a registration.
        self.names: dict[str, int] = {}
        self.register_result = pk.NameResult.OK

        # Result for the RUN_RESPONSE to a RUN_REQUEST, or None to not answer.
        self.run_result: int | None = 0
        self.bootstrap: int = 0

        # Actions to take, by message type, instead of answering.  Each is
        # called with the received message.
        self.actions: dict[int, typing.Callable[[typing.Any], None]] = {}
        self.thread = threading.Thread(target=self.run, daemon=True)

    def send(self, message: pk.Message):
        self.sock.sendall(message.encode())

    def run(self):
        buf = pk.Buffer()
        while True:
            while (buf.length() < 8 or
                   buf.length() < pk.Message.decode_length(buf.peek(8))):
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

            length = pk.Message.decode_length(buf.peek(8))
            message_buf = buf.peek(length)
            buf.consume(length)
            self.handle(pk.Message.decode_type(message_buf), message_buf)

    def wait_for(self, count: int):
        """Wait until this many messages have been received."""
        deadline = time.monotonic() + TIMEOUT
        while len(self.received) < count:
            assert time.monotonic() < deadline, "messages not received"
            time.sleep(0.01)

    def handle(self, message_type, message_buf):
        message = self.decode(message_type, message_buf)
        self.received.append(message)

        if message_type == pk.MessageType.SEND_MESSAGE_RQST:
            response = pk.SendMessageResponse(message.request_id, self.send_result)
            if self.send_delay:
                threading.Timer(self.send_delay, self.send, args=(response,)).start()
            else:
                self.send(response)

        action = self.actions.get(message_type)
        if action is not None:
            action(message)
            return

        if message_type == pk.MessageType.OPEN_PORT_RQST:
            self.send(pk.OpenPortResponse(message.request_id, 0, self.next_handle))
            self.next_handle += 1

        elif message_type == pk.MessageType.CLOSE_PORT_RQST:
            self.send(pk.ClosePortResponse(message.request_id, 0, message.port))

        elif message_type == pk.MessageType.SPAWN_PROCESS_RQST:
            self.send(pk.SpawnProcessResponse(message.request_id, 0, 7, None))

        elif message_type == pk.MessageType.KILL_PROCESS_RQST:
            self.send(pk.KillProcessResponse(message.request_id, 0))

        elif message_type == pk.MessageType.RUN_REQUEST:
            if self.run_result is not None:
                self.send(pk.RunResponse(self.run_result, self.bootstrap))

        elif message_type == pk.MessageType.REGISTER_NAME_RQST:
            if self.register_result == pk.NameResult.OK:
                self.names[message.get_name()] = message.port
            self.send(pk.RegisterNameResponse(message.request_id, self.register_result))

        elif message_type == pk.MessageType.WAIT_RQST:
            self.send(pk.WaitResponse(message.request_id, *self.wait_response))

        elif message_type == pk.MessageType.LOOKUP_NAME_RQST:
            if message.get_name() in self.names:
                self.send(pk.LookupNameResponse(message.request_id, pk.NameResult.OK, self.next_handle))
                self.next_handle += 1
            else:
                self.send(pk.LookupNameResponse(message.request_id, pk.NameResult.NOT_FOUND, 0))

    @staticmethod
    def decode(message_type, message_buf) -> typing.Any:
        if message_type == pk.MessageType.OPEN_PORT_RQST:
            message = pk.OpenPortRequest()
        elif message_type == pk.MessageType.CLOSE_PORT_RQST:
            message = pk.ClosePortRequest()
        elif message_type == pk.MessageType.SPAWN_PROCESS_RQST:
            message = pk.SpawnProcessRequest(0, "")
        elif message_type == pk.MessageType.KILL_PROCESS_RQST:
            message = pk.KillProcessRequest()
        elif message_type == pk.MessageType.SEND_MESSAGE_RQST:
            message = pk.SendMessageRequest()
        elif message_type == pk.MessageType.RUN_REQUEST:
            message = pk.RunRequest()
        elif message_type == pk.MessageType.REGISTER_NAME_RQST:
            message = pk.RegisterNameRequest()
        elif message_type == pk.MessageType.LOOKUP_NAME_RQST:
            message = pk.LookupNameRequest()
        elif message_type == pk.MessageType.WAIT_RQST:
            message = pk.WaitRequest()
        elif message_type == pk.MessageType.EXITED:
            message = pk.Exited()
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

    monkeypatch.setitem(darq.system.__dict__, "_darq_process_state", process)

    kernel = FakePKernel(kernel_sock)
    kernel.thread.start()
    process.kernel = kernel

    yield process
    process.io_loop.close()

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


def run_app(process, app) -> typing.Any:
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
    return [m for m in process.kernel.received if isinstance(m, pk.Exited)]


def pcalls(process) -> list:
    """The messages received by the p-kernel, other than the session's own."""
    return [m for m in process.kernel.received if not isinstance(m, (pk.RunRequest, pk.Exited))]


def test_app_runs_on_main_thread(pproc):
    assert run_app(pproc, lambda: threading.current_thread() is threading.main_thread())


def test_open_port(pproc):
    assert run_app(pproc, lambda: darq.open_port()) == 16385
    assert list(pproc.ports) == [16385]
    assert pproc._pending == {}


def test_close_port(pproc):
    def app():
        port = darq.open_port()
        opened = port in pproc.ports
        darq.close_port(port)
        return port, opened

    port, opened = run_app(pproc, app)
    assert opened
    assert port not in pproc.ports
    assert pproc._pending == {}


def test_release_reply_port(pproc):
    def app():
        # A thread's reply port is opened by its first call, and closed
        # once: then, the next call opens another.
        darq.release_reply_port()
        port = darq.system._reply_port()
        darq.release_reply_port()
        darq.release_reply_port()
        return port, darq.system._reply_port()

    port, again = run_app(pproc, app)
    assert again != port
    closed = [m.port for m in pcalls(pproc) if isinstance(m, pk.ClosePortRequest)]
    assert closed == [port]


def test_spawn_process(pproc):
    assert run_app(pproc, lambda: darq.spawn_process("/darq/bin/x")) == 7
    assert pcalls(pproc)[-1].get_exec_path() == "/darq/bin/x"
    assert pproc._pending == {}


def test_request_ids_assigned_by_io_thread(pproc):
    def app():
        darq.open_port()
        darq.spawn_process("/darq/bin/x")
        darq.close_port(16385)

    run_app(pproc, app)
    assert [m.request_id for m in pcalls(pproc)] == [1, 2, 3]


def test_send_message_waits_for_response(pproc):
    def app():
        port = darq.open_port()
        darq.send_message(port, 16386, b"hello")

    run_app(pproc, app)
    [sent] = [m for m in pproc.kernel.received if isinstance(m, pk.SendMessageRequest)]
    assert (sent.source, sent.destination, sent.payload) == (16385, 16386, b"hello")
    assert sent.request_id == 2
    assert pproc._pending == {}


def test_send_message_blocks_until_answered(pproc):
    pproc.kernel.send_delay = 0.1

    def app():
        port = darq.open_port()
        start = time.monotonic()
        darq.send_message(port, 16386, b"hello")
        return time.monotonic() - start

    assert run_app(pproc, app) >= 0.09


@pytest.mark.parametrize("result, error", [
    (pk.SendResult.BAD_HANDLE, darq.NonExistentPortError),
    (pk.SendResult.NOT_RECEIVE_RIGHT, darq.PortRightError),
    (pk.SendResult.DEAD_PORT, darq.DeadPortError),
    (99, darq.DarqError),
])
def test_send_message_errors(pproc, result, error):
    pproc.kernel.send_result = result

    def app():
        with pytest.raises(error):
            darq.send_message(darq.open_port(), 16386, b"refused")
        return True

    assert run_app(pproc, app)


def test_unknown_response_ignored(pproc):
    response = pk.OpenPortResponse(99, 0, 16385)
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
    (lambda loop, cb: darq.open_port_cb(loop, cb), (16385, 0)),
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
        loop.close()
        return calls

    assert run_app(pproc, app) == [(*args, True)]
    assert pproc._pending == {}


def test_delivered_message_runs_on_app_loop(pproc):
    # The p-kernel delivers each sent message back to its destination.
    deliver_sent_messages(pproc)

    def app():
        loop = darq.SelectEventLoop()
        delivered = []

        class Listener(darq.EventListener):
            def on_message(self, source, destination, message, handles=()):
                delivered.append((source, destination, message, handles,
                                  threading.current_thread() is threading.main_thread()))
                loop.stop()

        darq.init_cb(loop, Listener())
        port = darq.open_port()
        darq.send_message(port, port, b"hello", ports=[port, 77])
        loop.run()
        loop.close()
        return delivered

    # Transferred handles are passed to the listener too.
    assert run_app(pproc, app) == [(16385, 16385, b"hello", (16385, 77), True)]


def test_port_listener_runs_on_app_loop(pproc):
    # A port with its own listener gets its messages on the loop, even
    # with the callback model, while the process' listener gets the rest.
    deliver_sent_messages(pproc)

    def app():
        loop = darq.SelectEventLoop()
        delivered = []

        class Listener(darq.EventListener):
            def on_message(self, source, destination, message, handles=()):
                delivered.append(("process", destination, message))
                loop.stop()

        def port_listener(port, source, payload, handles):
            delivered.append(("port", port, payload, threading.current_thread() is threading.main_thread()))

        darq.init_cb(loop, Listener())
        listened, other = darq.open_port(), darq.open_port()
        darq.set_port_listener(listened, port_listener, loop)
        darq.send_message(listened, listened, b"mine")
        darq.send_message(listened, other, b"theirs")
        loop.run()
        loop.close()
        return listened, other, delivered

    listened, other, delivered = run_app(pproc, app)
    assert delivered == [("port", listened, b"mine", True), ("process", other, b"theirs")]


def test_port_listener_needs_an_open_port(pproc):
    def app():
        loop = darq.SelectEventLoop()
        try:
            darq.set_port_listener(4242, lambda *message: None, loop)
        except darq.NonExistentPortError:
            return "raised"
        finally:
            loop.close()

    assert run_app(pproc, app) == "raised"


################################################################
# Receiving messages
################################################################

def deliver_sent_messages(process):
    """Have the p-kernel deliver each sent message to its destination.

    Transferred handles are delivered as they were sent."""
    def deliver(request):
        message = pk.DeliverMessage(request.source, request.destination)
        message.set_payload(request.payload)
        message.set_handles(request.handles)
        process.kernel.send(message)

    process.kernel.actions[pk.MessageType.SEND_MESSAGE_RQST] = deliver


def test_receive_message(pproc):
    deliver_sent_messages(pproc)

    def app():
        port = darq.open_port()
        darq.send_message(port, port, b"hello")
        return darq.receive_message(port)

    assert run_app(pproc, app) == (16385, b"hello", ())


def test_bootstrap_port(pproc):
    pproc.kernel.bootstrap = 9
    assert run_app(pproc, darq.get_bootstrap_port) == 9


def test_no_bootstrap_port(pproc):
    assert run_app(pproc, darq.get_bootstrap_port) is None


def test_receive_any(pproc):
    deliver_sent_messages(pproc)

    def app():
        a, b = darq.open_port(), darq.open_port()
        darq.send_message(a, b, b"to b")
        darq.send_message(b, a, b"to a")
        ports = darq.PortSet([a, b])
        return [ports.receive(), ports.receive(), ports.receive(blocking=False)]

    assert run_app(pproc, app) == [(16386, 16385, b"to b", ()), (16385, 16386, b"to a", ()), None]


def test_receive_any_unknown_port(pproc):
    def app():
        port = darq.open_port()
        try:
            darq.receive_any([port, 12345], blocking=False)
        except darq.NonExistentPortError as e:
            return str(e)

    assert run_app(pproc, app) == "12345"


def test_spawn_failure_raises(pproc):
    def refuse(message):
        pproc.kernel.send(pk.SpawnProcessResponse(message.request_id, pk.SpawnResult.BAD_BOOTSTRAP, 0,
                                                  "bootstrap handle 3 isn't open"))
    pproc.kernel.actions[pk.MessageType.SPAWN_PROCESS_RQST] = refuse

    def app():
        try:
            darq.spawn_process("/x", bootstrap=3)
        except darq.SpawnError as e:
            return e.result, e.description

    assert run_app(pproc, app) == (pk.SpawnResult.BAD_BOOTSTRAP, "bootstrap handle 3 isn't open")


def test_spawn_sends_bootstrap(pproc):
    sent = []

    def record(message):
        sent.append(message.bootstrap)
        pproc.kernel.send(pk.SpawnProcessResponse(message.request_id, 0, 7, None))
    pproc.kernel.actions[pk.MessageType.SPAWN_PROCESS_RQST] = record

    assert run_app(pproc, lambda: [darq.spawn_process("/x", bootstrap=4), darq.spawn_process("/x")]) == [7, 7]
    assert sent == [4, 0]


def test_transferred_handles_sent_and_received(pproc):
    deliver_sent_messages(pproc)

    def app():
        port = darq.open_port()
        darq.send_message(port, port, b"hello", ports=[port, 77])
        return darq.receive_message(port)

    assert run_app(pproc, app) == (16385, b"hello", (16385, 77))
    [sent] = [m for m in pproc.kernel.received if isinstance(m, pk.SendMessageRequest)]
    assert sent.handles == (16385, 77)


def test_too_many_handles_refused_locally(pproc):
    def app():
        port = darq.open_port()
        try:
            darq.send_message(port, port, b"hello", ports=[port] * (pk.MAX_HANDLES + 1))
        except darq.TooManyHandlesError:
            return "refused"

    assert run_app(pproc, app) == "refused"
    assert not [m for m in pproc.kernel.received if isinstance(m, pk.SendMessageRequest)]


def test_receive_timeout(pproc):
    def app():
        port = darq.open_port()
        start = time.monotonic()
        result = darq.receive_message(port, timeout=0.1)
        return result, time.monotonic() - start

    result, elapsed = run_app(pproc, app)
    assert result is None
    assert 0.09 <= elapsed < 2


def test_receive_messages_in_order(pproc):
    deliver_sent_messages(pproc)

    def app():
        port = darq.open_port()
        for payload in (b"one", b"two", b"three"):
            darq.send_message(port, port, payload)
        payloads = []
        for _ in range(3):
            message = darq.receive_message(port)
            assert message is not None
            payloads.append(message[1])
        return payloads

    assert run_app(pproc, app) == [b"one", b"two", b"three"]


def test_non_blocking_receive_without_message(pproc):
    def app():
        port = darq.open_port()
        start = time.monotonic()
        result = darq.receive_message(port, blocking=False)
        return result, time.monotonic() - start

    result, elapsed = run_app(pproc, app)
    assert result is None
    assert elapsed < 0.1


def test_non_blocking_receive_of_delivered_message(pproc):
    deliver_sent_messages(pproc)

    def app():
        port = darq.open_port()
        darq.send_message(port, port, b"hello")
        # Synchronise: the delivery precedes the spawn reply.
        darq.spawn_process("/darq/bin/x")
        return darq.receive_message(port, blocking=False)

    assert run_app(pproc, app) == (16385, b"hello", ())


def test_blocking_receive_waits_for_delivery(pproc):
    # The message is delivered some time after the port is opened.
    def open_then_deliver_later(request):
        pproc.kernel.send(pk.OpenPortResponse(request.request_id, 0, 16385))
        message = pk.DeliverMessage(16386, 16385)
        message.set_payload(b"later")
        threading.Timer(0.1, pproc.kernel.send, args=(message,)).start()

    pproc.kernel.actions[pk.MessageType.OPEN_PORT_RQST] = open_then_deliver_later

    def app():
        port = darq.open_port()
        start = time.monotonic()
        return darq.receive_message(port), time.monotonic() - start

    result, elapsed = run_app(pproc, app)
    assert result == (16386, b"later", ())
    assert elapsed >= 0.05


def test_receive_on_port_not_open(pproc):
    def app():
        with pytest.raises(darq.NonExistentPortError):
            darq.receive_message(16385, blocking=False)
        return True

    assert run_app(pproc, app)


def test_receive_after_close(pproc):
    def app():
        port = darq.open_port()
        darq.close_port(port)
        with pytest.raises(darq.NonExistentPortError):
            darq.receive_message(port)
        return True

    assert run_app(pproc, app)


def test_message_for_unknown_port_dropped(pproc):
    def deliver_elsewhere(request):
        message = pk.DeliverMessage(request.source, 16399)
        message.set_payload(request.payload)
        pproc.kernel.send(message)

    pproc.kernel.actions[pk.MessageType.SEND_MESSAGE_RQST] = deliver_elsewhere

    def app():
        port = darq.open_port()
        darq.send_message(port, port, b"lost")
        darq.spawn_process("/darq/bin/x")
        return darq.receive_message(port, blocking=False)

    assert run_app(pproc, app) is None


def test_kill_app_blocked_in_receive(pproc, install_app):
    install_app("""
        import darq
        def darq_main():
            port = darq.open_port()
            darq.send_message(port, 16386, b"started")
            darq.receive_message(port)
        """)
    kill_on(pproc, pk.MessageType.SEND_MESSAGE_RQST)
    run_process(pproc)

    assert pproc._app_killed
    assert exited_messages(pproc) == []


################################################################
# Name registry
################################################################

def test_register_and_lookup_name(pproc):
    def app():
        port = darq.open_port()
        darq.register_name("test.service", port)
        return darq.lookup_name("test.service")

    assert run_app(pproc, app) == 16386
    [request] = [m for m in pproc.kernel.received if isinstance(m, pk.RegisterNameRequest)]
    assert (request.get_name(), request.port) == ("test.service", 16385)


@pytest.mark.parametrize("blocking", [True, False])
def test_lookup_name_sends_blocking_flag(pproc, blocking):
    pproc.kernel.names["test.service"] = 1
    run_app(pproc, lambda: darq.lookup_name("test.service", blocking=blocking))
    [request] = [m for m in pproc.kernel.received if isinstance(m, pk.LookupNameRequest)]
    assert request.blocking == blocking


def test_lookup_name_not_found(pproc):
    assert run_app(pproc, lambda: darq.lookup_name("test.missing", blocking=False)) is None


@pytest.mark.parametrize("result, error", [
    (pk.NameResult.BAD_HANDLE, darq.NonExistentPortError),
    (pk.NameResult.INVALID_NAME, darq.InvalidNameError),
    (pk.NameResult.NAME_TAKEN, darq.NameTakenError),
    (pk.NameResult.RESERVED_NAME, darq.ReservedNameError),
    (pk.NameResult.PORT_ALREADY_NAMED, darq.PortAlreadyNamedError),
    (99, darq.DarqError),
])
def test_register_name_errors(pproc, result, error):
    pproc.kernel.register_result = result

    def app():
        with pytest.raises(error):
            darq.register_name("test.service", darq.open_port())
        return True

    assert run_app(pproc, app)


def test_name_callbacks_run_on_app_loop(pproc):
    def app():
        loop = darq.SelectEventLoop()
        darq.init(loop)
        calls = []

        def registered(port, error):
            calls.append(("registered", port, error))
            darq.lookup_name_cb("test.service", False, loop, looked_up)

        def looked_up(handle, error):
            calls.append(("looked up", handle, error, threading.current_thread() is threading.main_thread()))
            loop.stop()

        darq.register_name_cb("test.service", darq.open_port(), loop, registered)
        loop.run()
        loop.close()
        return calls

    assert run_app(pproc, app) == [("registered", 16385, 0), ("looked up", 16386, 0, True)]


################################################################
# Exit and wait
################################################################

def test_exit_on_main_thread(pproc, install_app, tmp_path):
    cleaned = tmp_path / "cleaned"
    install_app(f"""
        import darq
        def darq_main():
            try:
                darq.exit(5)
            finally:
                open({str(cleaned)!r}, "w").close()
            return "not reached"
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert (exited.status, exited.get_error()) == (5, None)
    assert pproc.exit_status == 5
    assert cleaned.exists()


# darq.exit() ends the thread calling it with SystemExit, which threading
# ignores, but pytest reports.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("blocked_in", ["sleep", "receive"])
def test_exit_from_another_thread(pproc, install_app, blocked_in):
    block = "time.sleep(0.01)" if blocked_in == "sleep" else "darq.receive_message(port)"
    install_app(f"""
        import threading
        import time
        import darq
        def darq_main():
            port = darq.open_port()
            threading.Timer(0.05, darq.exit, args=(300,)).start()
            while True:
                {block}
        """)
    run_process(pproc)

    # Let the exiting thread finish within this test, so its SystemExit is
    # reported (and ignored) here.
    for thread in threading.enumerate():
        if isinstance(thread, threading.Timer):
            thread.join(TIMEOUT)

    [exited] = exited_messages(pproc)
    assert exited.status == 300

    # The host exit status is limited to 0 to 255.
    assert pproc.exit_status == 1


@pytest.mark.parametrize("status", [2 ** 31, -2 ** 31 - 1, "1", 1.0])
def test_exit_status_checked(pproc, status):
    def app():
        with pytest.raises(ValueError):
            darq.exit(status)
        return True

    assert run_app(pproc, app)


def test_wait_collects_child(pproc):
    pproc.kernel.wait_response = (pk.WaitResult.OK, 7, pk.ExitReason.EXITED, 3, "failed")
    result = run_app(pproc, lambda: darq.wait(7, blocking=False))
    assert result == darq.ChildStatus(7, pk.ExitReason.EXITED, 3, "failed")
    assert isinstance(result.reason, pk.ExitReason)

    [request] = [m for m in pproc.kernel.received if isinstance(m, pk.WaitRequest)]
    assert (request.dpid, request.blocking) == (7, False)


def test_wait_not_finished(pproc):
    pproc.kernel.wait_response = (pk.WaitResult.NOT_FINISHED, 0, 0, 0, None)
    assert run_app(pproc, lambda: darq.wait(blocking=False)) is None


def test_wait_no_such_child(pproc):
    def app():
        with pytest.raises(darq.NoSuchChildError):
            darq.wait()
        return True

    assert run_app(pproc, app)


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
        lambda request: process.kernel.send(pk.KillProcessRequest(0, dpid))


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
    kill_on(pproc, pk.MessageType.SEND_MESSAGE_RQST)
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
    kill_on(pproc, pk.MessageType.SPAWN_PROCESS_RQST)
    run_process(pproc)

    assert pproc._app_killed
    assert exited_messages(pproc) == []
    assert pproc._pending == {}


def test_kill_before_start(pproc):
    loaded = []
    pproc.load_app = lambda: loaded.append(True)
    kill_on(pproc, pk.MessageType.RUN_REQUEST)

    run_process(pproc)
    assert loaded == []
    assert exited_messages(pproc) == []
    assert pproc.exit_status == 0


def test_kill_app_not_started(pproc):
    assert pproc.kill_app()
    assert pproc._app_start.is_set()


def test_kill_process_request_other_dpid(pproc):
    kill_on(pproc, pk.MessageType.SEND_MESSAGE_RQST, DPID + 1)

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
        pproc.kernel.actions[pk.MessageType.SPAWN_PROCESS_RQST] = disconnect
    else:
        pproc.kernel.actions[pk.MessageType.SEND_MESSAGE_RQST] = disconnect

    run_process(pproc)

    assert pproc.exit_status == 1
    assert pproc._pkernel_sock is None
    assert pproc._app_killed


def test_p_kernel_disconnect_before_start(pproc):
    loaded = []
    pproc.load_app = lambda: loaded.append(True)
    pproc.kernel.actions[pk.MessageType.RUN_REQUEST] = \
        lambda request: pproc.kernel.sock.shutdown(socket.SHUT_RDWR)

    run_process(pproc)
    assert loaded == []
    assert pproc.exit_status == 1


@pytest.mark.parametrize("length", [0, 7])
def test_invalid_message_length_disconnects(pproc, install_app, length):
    install_app("""
        import time
        import darq
        def darq_main():
            darq.send_message(16385, 16386, b"started")
            while True:
                time.sleep(0.01)
        """)

    # A header whose length can't cover itself: the stream can't be
    # split into messages after it.
    def send_bad_header(request):
        message = pk.Message(pk.MessageType.DELIVER_MESSAGE)
        message.set_length(length)
        pproc.kernel.sock.sendall(message.encode())

    pproc.kernel.actions[pk.MessageType.SEND_MESSAGE_RQST] = send_bad_header
    run_process(pproc)

    assert pproc.exit_status == 1
    assert pproc._pkernel_sock is None
    assert pproc._app_killed


def test_message_split_across_reads(pproc):
    # A header arriving in pieces is held until it's complete.
    def answer_in_pieces(request):
        buf = pk.RunResponse(0).encode()
        for piece in (buf[:3], buf[3:7], buf[7:]):
            pproc.kernel.sock.sendall(piece)
            time.sleep(0.05)

    pproc.kernel.actions[pk.MessageType.RUN_REQUEST] = answer_in_pieces
    assert run_app(pproc, lambda: True)


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
    assert kinds == [pk.RunRequest, pk.SendMessageRequest, pk.Exited]


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
        pproc.kernel.send(pk.RunResponse(0))

    pproc.kernel.actions[pk.MessageType.RUN_REQUEST] = answer
    run_process(pproc)
    assert loaded == [False, True]


def test_duplicate_run_response(pproc):
    loads = []

    def load_app():
        loads.append(True)
        return lambda: None

    pproc.load_app = load_app

    def answer_twice(request):
        pproc.kernel.send(pk.RunResponse(0))
        pproc.kernel.send(pk.RunResponse(0))

    pproc.kernel.actions[pk.MessageType.RUN_REQUEST] = answer_twice
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
        PORT = darq.open_port()
        def darq_main():
            return PORT
        """)
    run_process(pproc)

    [exited] = exited_messages(pproc)
    assert exited.status == 16385
