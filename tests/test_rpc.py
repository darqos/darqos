# darqos
# Copyright (C) 2026 David Arnold

# Tests for requests and responses over ports, in darq/system.py, using
# a module generated from a DIDL protocol.  The p-calls are faked here:
# test_pkernel.py runs a service and client through a real p-kernel.

import collections
import gc
import importlib.machinery
import importlib.util
import logging
import pathlib
import textwrap
import time

import pytest

from darq import dipc, system
from darq.errors import DeadPortError, NonExistentPortError, RemoteError


BIN = pathlib.Path(__file__).parent.parent / "bin"

SOURCE = """\
    library test_kv;

    protocol KV {
        1: Put(opaque key, opaque value) -> ();
        2: Get(opaque key) -> (opaque value, uint32 found);
        3: Forget(opaque key);
        4: Fail() -> ();
        5: Missing() -> ();
        6: Share(port p) -> (port echoed);
    }
    """


def load_script(name: str):
    """Import a script from bin/ as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / name))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def generate_module(source: str, path: pathlib.Path):
    """Compile DIDL source into a Python module at path, and import it."""
    didlc = load_script("didlc")
    didlpygen = load_script("didlpygen")
    ir = didlc.compile_ir(didlc.Parser(didlc.lex(textwrap.dedent(source))).parse())
    path.write_text(didlpygen.Generator(ir, "test.dir").generate())
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kv(tmp_path):
    return generate_module(SOURCE, tmp_path / "test_kv.py")


@pytest.fixture
def handler(kv):
    class Handler(kv.KV.Handler):
        def __init__(self):
            self.store = {}

        def put(self, request, source):
            self.store[request.key] = request.value
            return kv.PutResponse()

        def get(self, request, source):
            if request.key not in self.store:
                return kv.GetResponse(found=0)
            return kv.GetResponse(value=self.store[request.key], found=1)

        def forget(self, request, source):
            self.store.pop(request.key, None)

        def fail(self, request, source):
            raise ValueError("as requested")

        def share(self, request, source):
            return kv.ShareResponse(echoed=request.p)

    return Handler()


def encode(message, txid=None):
    if txid is not None:
        message.txid = txid
    return message.encode()


def error_of(reply) -> tuple[int, int, dipc.ErrorCode]:
    assert isinstance(reply, dipc.ErrorReply)
    return reply.txid, reply.METHOD, dipc.ErrorCode(reply.code)


################################################################
# Handler
################################################################

def test_dispatch_returns_response_with_txid(kv, handler):
    reply = handler.dispatch(encode(kv.PutRequest(key=b"k", value=b"v"), 7)[0], 99)
    assert reply == kv.PutResponse(7)
    assert handler.store == {b"k": b"v"}


def test_dispatch_decodes_ports(kv, handler):
    buf, handles = encode(kv.ShareRequest(p=1234), 3)
    assert handler.dispatch(buf, 99, tuple(handles)) == kv.ShareResponse(3, echoed=1234)


def test_dispatch_one_way_has_no_reply(kv, handler):
    handler.store[b"k"] = b"v"
    assert handler.dispatch(encode(kv.Forget(key=b"k"))[0], 99) is None
    assert handler.store == {}


def test_unknown_method(kv, handler):
    buf = bytearray(encode(kv.FailRequest(), 5)[0])
    buf[6:8] = (77).to_bytes(2, "little")
    assert error_of(handler.dispatch(bytes(buf), 99)) == (5, 77, dipc.ErrorCode.UNKNOWN_METHOD)


def test_bad_message(kv, handler):
    buf = encode(kv.GetRequest(key=b"key"), 5)[0]
    assert error_of(handler.dispatch(buf[:12], 99)) == (5, 2, dipc.ErrorCode.BAD_MESSAGE)


def test_bad_port_index(kv, handler):
    # The port's index is past the end of the (missing) handles.
    buf, _ = encode(kv.ShareRequest(p=1234), 5)
    assert error_of(handler.dispatch(buf, 99)) == (5, 6, dipc.ErrorCode.BAD_MESSAGE)


def test_not_implemented(kv, handler):
    assert error_of(handler.dispatch(encode(kv.MissingRequest(), 5)[0], 99)) == \
        (5, 5, dipc.ErrorCode.NOT_IMPLEMENTED)


def test_handler_exception(kv, handler, caplog):
    reply = handler.dispatch(encode(kv.FailRequest(), 5)[0], 99)
    assert error_of(reply) == (5, 4, dipc.ErrorCode.INTERNAL_ERROR)
    assert reply.description == "ValueError: as requested"
    assert "Handler for FailRequest failed" in caplog.text


def test_wrong_response_type(kv, handler):
    handler.put = lambda request, source: kv.GetResponse()
    reply = handler.dispatch(encode(kv.PutRequest(), 5)[0], 99)
    assert error_of(reply) == (5, 1, dipc.ErrorCode.INTERNAL_ERROR)
    assert "returned GetResponse, not PutResponse" in reply.description


def test_one_way_failure_has_no_reply(kv, handler, caplog):
    handler.forget = lambda request, source: 1 / 0
    with caplog.at_level(logging.WARNING):
        assert handler.dispatch(encode(kv.Forget())[0], 99) is None
    assert "Failed to handle one-way message 3" in caplog.text


def test_short_message_dropped(handler):
    assert handler.dispatch(b"\0" * 7, 99) is None


################################################################
# Client side, with faked p-calls
################################################################

SERVICE = 50
REPLY_PORT = 10


class FakeSyscalls:
    """Stands in for darq.system's p-calls: replies come from a handler."""

    def __init__(self, handler):
        self.handler = handler
        self.opened = 0
        self.sent = []
        self.watched = []
        self.queue = collections.deque()

    def open_port(self):
        self.opened += 1
        return REPLY_PORT

    def notify_on_death(self, handle, notify_port):
        self.watched.append((handle, notify_port))

    def send_message(self, source, destination, buf, ports=()):
        self.sent.append((source, destination, buf, tuple(ports)))
        if destination != SERVICE:
            raise NonExistentPortError(destination)
        reply = self.handler.dispatch(buf, source, tuple(ports))
        if reply is not None:
            reply_buf, reply_handles = reply.encode()
            self.queue.append((SERVICE, reply_buf, tuple(reply_handles)))

    def receive_message(self, port, blocking=True, timeout=None):
        assert port == REPLY_PORT
        if self.queue:
            return self.queue.popleft()
        assert timeout is not None, "would block forever"
        return None


@pytest.fixture
def syscalls(handler, monkeypatch):
    fake = FakeSyscalls(handler)
    # Looked up as module globals, so the fakes are called at call time.
    for name in ("open_port", "notify_on_death", "send_message", "receive_message"):
        monkeypatch.setattr(system, name, getattr(fake, name))
    monkeypatch.setattr(system, "_local", system.threading.local())
    return fake


def test_client_calls(kv, syscalls):
    client = kv.KV.Client(SERVICE)
    assert client.put(key=b"k", value=b"v") == kv.PutResponse(system._last_txid)
    response = client.get(key=b"k")
    assert (response.value, response.found) == (b"v", 1)

    # Requests go from the one reply port, with distinct txids.
    assert syscalls.opened == 1
    txids = [dipc.decode_txid(buf) for _, _, buf, _ in syscalls.sent]
    assert all(txids) and txids[0] != txids[1]
    assert {(source, destination) for source, destination, _, _ in syscalls.sent} == {(REPLY_PORT, SERVICE)}


def test_client_one_way(kv, syscalls, handler):
    handler.store[b"k"] = b"v"
    assert kv.KV.Client(SERVICE).forget(key=b"k") is None
    assert handler.store == {}
    [(_, _, buf, _)] = syscalls.sent
    assert dipc.decode_txid(buf) == 0


def test_client_transfers_ports(kv, syscalls):
    response = kv.KV.Client(SERVICE).share(p=1234)
    assert response.echoed == 1234
    [(_, _, _, ports)] = syscalls.sent
    assert ports == (1234,)


@pytest.mark.parametrize("method, code", [
    ("fail", dipc.ErrorCode.INTERNAL_ERROR),
    ("missing", dipc.ErrorCode.NOT_IMPLEMENTED),
])
def test_client_error_reply_raises(kv, syscalls, method, code):
    with pytest.raises(RemoteError) as info:
        getattr(kv.KV.Client(SERVICE), method)()
    assert info.value.code == code


def test_call_by_message(kv, syscalls):
    assert system.call(SERVICE, kv.PutRequest(key=b"k", value=b"v")) == kv.PutResponse(system._last_txid)
    assert system.call(SERVICE, kv.Forget(key=b"k")) is None


def test_stray_replies_dropped(kv, syscalls, caplog):
    # A late reply to an earlier call, and a message from elsewhere.
    syscalls.queue.append((SERVICE, encode(kv.GetResponse(value=b"stale"), 999)[0], ()))
    syscalls.queue.append((51, encode(kv.GetResponse(value=b"other"), system._last_txid + 1)[0], ()))

    client = kv.KV.Client(SERVICE)
    client.put(key=b"k", value=b"v")
    assert client.get(key=b"k").value == b"v"
    assert caplog.text.count("Dropped stray message") == 2


def test_timeout(kv, syscalls, monkeypatch):
    monkeypatch.setattr(system, "send_message", lambda *args, **kwargs: None)    # never answered
    with pytest.raises(TimeoutError):
        kv.KV.Client(SERVICE, timeout=0.01).put()


def test_send_errors_propagate(kv, syscalls):
    with pytest.raises(NonExistentPortError):
        kv.KV.Client(SERVICE + 1).put()


def notification(handle):
    """A PortDestroyed notification from the p-kernel, as delivered."""
    return (0, dipc.PortDestroyed(handle).encode()[0], ())


def test_client_watches_each_service_once(kv, syscalls):
    client = kv.KV.Client(SERVICE)
    client.put(key=b"k", value=b"v")
    client.get(key=b"k")
    assert syscalls.watched == [(SERVICE, REPLY_PORT)]


def test_service_death_raises(kv, syscalls, monkeypatch):
    # The request is delivered, but the service dies before replying.
    monkeypatch.setattr(system, "send_message",
                        lambda *args, **kwargs: syscalls.queue.append(notification(SERVICE)))
    with pytest.raises(DeadPortError):
        kv.KV.Client(SERVICE).put()


def test_reply_before_death_returned(kv, syscalls, monkeypatch):
    client = kv.KV.Client(SERVICE)
    client.put(key=b"k", value=b"v")

    # It replies, and then dies: the reply comes first.
    def reply_then_die(source, destination, buf, ports=()):
        syscalls.send_message(source, destination, buf, ports)
        syscalls.queue.append(notification(SERVICE))
    monkeypatch.setattr(system, "send_message", reply_then_die)
    assert client.get(key=b"k").value == b"v"


def test_other_notifications_dropped(kv, syscalls, caplog):
    syscalls.queue.append(notification(SERVICE + 1))
    assert kv.KV.Client(SERVICE).put(key=b"k", value=b"v") == kv.PutResponse(system._last_txid)
    assert "Dropped stray message" not in caplog.text


def test_forged_notification_ignored(kv, syscalls, caplog):
    # Only the p-kernel sends from source 0: from elsewhere, it's a stray.
    _, buf, _ = notification(SERVICE)
    syscalls.queue.append((SERVICE, buf, ()))
    assert kv.KV.Client(SERVICE).put(key=b"k", value=b"v") == kv.PutResponse(system._last_txid)
    assert "Dropped stray message" in caplog.text


def test_txids_skip_zero(monkeypatch):
    monkeypatch.setattr(system, "_last_txid", 0xFFFFFFFE)
    assert [system._allocate_txid() for _ in range(3)] == [0xFFFFFFFF, 1, 2]


################################################################
# Server, deferred replies and timers, with faked p-calls
################################################################

SERVICE_PORT = 20
OTHER_PORT = 21
CLIENT = 99


class FakePorts:
    """Stands in for darq.system's port p-calls: messages are queued by
    port, and sent messages recorded."""

    def __init__(self):
        self.queues: dict[int, collections.deque] = collections.defaultdict(collections.deque)
        self.order = []
        self.sent = []

    def deliver(self, port, source, payload, handles=()):
        self.queues[port].append((source, payload, tuple(handles)))
        self.order.append(port)

    def receive_any(self, ports, blocking=True, timeout=None):
        ports = tuple(ports)
        for n, port in enumerate(self.order):
            if port in ports:
                del self.order[n]
                return (port, *self.queues[port].popleft())
        # Nothing will arrive: wait out the timeout.
        assert timeout is not None, "would block forever"
        time.sleep(timeout)
        return None

    def send_message(self, source, destination, buf, ports=()):
        self.sent.append((source, destination, buf, tuple(ports)))

    def replies(self, kv):
        """The sent replies, decoded: (from port, to, message)."""
        result = []
        for source, destination, buf, handles in self.sent:
            if dipc.decode_flags(buf) & dipc.ERROR:
                message = dipc.ErrorReply()
            else:
                message = kv.KV.RESPONSES[dipc.decode_method(buf)]()
            message.decode(buf, handles)
            result.append((source, destination, message))
        return result


@pytest.fixture
def fake_ports(monkeypatch):
    fake = FakePorts()
    monkeypatch.setattr(system, "receive_any", fake.receive_any)
    monkeypatch.setattr(system, "send_message", fake.send_message)
    return fake


@pytest.fixture
def server(fake_ports, handler):
    server = system.Server()
    server.add(SERVICE_PORT, handler)
    return server


def request(fake_ports, message, txid, port=SERVICE_PORT, source=CLIENT):
    buf, handles = encode(message, txid)
    fake_ports.deliver(port, source, buf, handles)


def test_server_replies_from_the_requests_port(kv, server, fake_ports, handler):
    request(fake_ports, kv.PutRequest(key=b"k", value=b"v"), 5)
    assert server.run_once(timeout=0) is True
    assert fake_ports.replies(kv) == [(SERVICE_PORT, CLIENT, kv.PutResponse(5))]
    assert handler.server is server
    assert server.run_once(timeout=0) is False


def test_server_dispatches_by_port(kv, server, fake_ports):
    class Other(kv.KV.Handler):
        def get(self, request, source):
            return kv.GetResponse(value=b"from other", found=1)

    server.add(OTHER_PORT, Other())
    request(fake_ports, kv.GetRequest(key=b"k"), 1, port=OTHER_PORT)
    request(fake_ports, kv.GetRequest(key=b"k"), 2)
    server.run_once(timeout=0)
    server.run_once(timeout=0)
    assert [(port, message.txid, message.value) for port, _, message in fake_ports.replies(kv)] == [
        (OTHER_PORT, 1, b"from other"), (SERVICE_PORT, 2, b"")]

    server.remove(OTHER_PORT)
    assert OTHER_PORT not in server.ports


@pytest.fixture
def deferring(kv, server, handler):
    """The handler defers every Get, until a Put arrives."""
    handler.waiting = []

    def get(request, source):
        pending = handler.defer()
        handler.waiting.append((request.key, pending))
        return pending

    def put(request, source):
        handler.store[request.key] = request.value
        for key, pending in handler.waiting:
            if key == request.key:
                pending.reply(kv.GetResponse(value=request.value, found=1))
        handler.waiting = [(k, p) for k, p in handler.waiting if not p.done]
        return kv.PutResponse()

    handler.get, handler.put = get, put
    return handler


def test_deferred_reply(kv, server, fake_ports, deferring):
    request(fake_ports, kv.GetRequest(key=b"k"), 7, source=CLIENT)
    request(fake_ports, kv.GetRequest(key=b"other"), 8, source=CLIENT + 1)
    server.run_once(timeout=0)
    server.run_once(timeout=0)
    assert fake_ports.sent == []

    # The Put's reply, and then the deferred Get's, both from the service port.
    request(fake_ports, kv.PutRequest(key=b"k", value=b"v"), 9, source=CLIENT + 2)
    server.run_once(timeout=0)
    assert fake_ports.replies(kv) == [
        (SERVICE_PORT, CLIENT, kv.GetResponse(7, value=b"v", found=1)),
        (SERVICE_PORT, CLIENT + 2, kv.PutResponse(9)),
    ]
    [(key, pending)] = deferring.waiting
    assert key == b"other" and not pending.done
    pending.error(dipc.ErrorCode.INTERNAL_ERROR, "gave up")


def test_deferred_completed_from_another_port(kv, server, fake_ports, deferring):
    # A request on one port is answered when a message arrives on another,
    # as the Type service answers Activate when an implementation is ready.
    class Ready(kv.KV.Handler):
        def forget(self, request, source):
            for _, pending in deferring.waiting:
                pending.reply(kv.GetResponse(value=b"ready", found=1))
            server.remove(OTHER_PORT)

    request(fake_ports, kv.GetRequest(key=b"k"), 3)
    server.run_once(timeout=0)
    server.add(OTHER_PORT, Ready())
    request(fake_ports, kv.Forget(), 0, port=OTHER_PORT, source=123)
    server.run_once(timeout=0)
    assert fake_ports.replies(kv) == [(SERVICE_PORT, CLIENT, kv.GetResponse(3, value=b"ready", found=1))]
    assert OTHER_PORT not in server.ports


def test_deferred_error(kv, server, fake_ports, deferring):
    request(fake_ports, kv.GetRequest(key=b"k"), 4)
    server.run_once(timeout=0)
    _, pending = deferring.waiting[0]
    pending.error(dipc.ErrorCode.INTERNAL_ERROR, "implementation didn't start")
    [(_, _, reply)] = fake_ports.replies(kv)
    assert isinstance(reply, dipc.ErrorReply)
    assert (reply.txid, reply.METHOD, reply.description) == (4, 2, "implementation didn't start")


def test_deferred_answered_once(kv, server, fake_ports, deferring):
    request(fake_ports, kv.GetRequest(key=b"k"), 4)
    server.run_once(timeout=0)
    _, pending = deferring.waiting[0]
    with pytest.raises(TypeError, match="not GetResponse"):
        pending.reply(kv.PutResponse())
    pending.reply(kv.GetResponse())
    with pytest.raises(RuntimeError, match="already been answered"):
        pending.reply(kv.GetResponse())
    with pytest.raises(RuntimeError, match="already been answered"):
        pending.error(dipc.ErrorCode.INTERNAL_ERROR, "again")
    assert len(fake_ports.sent) == 1


def test_handler_fails_after_deferring(kv, server, fake_ports, handler):
    deferred = []

    def get(request, source):
        deferred.append(handler.defer())
        raise ValueError("oops")

    handler.get = get
    request(fake_ports, kv.GetRequest(), 6)
    server.run_once(timeout=0)
    [(_, _, reply)] = fake_ports.replies(kv)
    assert error_of(reply) == (6, 2, dipc.ErrorCode.INTERNAL_ERROR)
    # The error reply answered it.
    with pytest.raises(RuntimeError, match="already been answered"):
        deferred[0].reply(kv.GetResponse())


def test_handler_answers_after_deferring(kv, server, fake_ports, handler):
    deferred = []

    def get(request, source):
        deferred.append(handler.defer())
        return kv.GetResponse(found=1)

    handler.get = get
    request(fake_ports, kv.GetRequest(), 6)
    server.run_once(timeout=0)
    assert fake_ports.replies(kv) == [(SERVICE_PORT, CLIENT, kv.GetResponse(6, found=1))]
    assert deferred[0].done


def test_defer_outside_handler(handler):
    with pytest.raises(RuntimeError, match="outside a handler"):
        handler.defer()


def test_defer_one_way(kv, server, fake_ports, handler, caplog):
    handler.forget = lambda request, source: handler.defer()
    request(fake_ports, kv.Forget(), 0)
    with caplog.at_level(logging.WARNING):
        server.run_once(timeout=0)
    assert "One-way Forget has no reply to defer" in caplog.text
    assert fake_ports.sent == []


def test_deferred_for_another_request(kv, server, fake_ports, deferring, handler):
    request(fake_ports, kv.GetRequest(key=b"k"), 4)
    server.run_once(timeout=0)
    _, earlier = deferring.waiting[0]
    handler.put = lambda request, source: earlier
    request(fake_ports, kv.PutRequest(), 5)
    server.run_once(timeout=0)
    [(_, _, reply)] = fake_ports.replies(kv)
    assert error_of(reply) == (5, 1, dipc.ErrorCode.INTERNAL_ERROR)
    assert not earlier.done
    earlier.reply(kv.GetResponse())


def test_unanswered_deferred_warns(kv, server, fake_ports, deferring, caplog):
    request(fake_ports, kv.GetRequest(key=b"k"), 4)
    server.run_once(timeout=0)
    with caplog.at_level(logging.WARNING):
        deferring.waiting.clear()
        gc.collect()
    assert "Request 4 (method 2) from 99 was never answered" in caplog.text


def test_timers(server, fake_ports):
    calls = []
    server.call_later(0.05, lambda: calls.append("later"))
    server.call_later(0, lambda: calls.append("now"))
    cancelled = server.call_later(0, lambda: calls.append("cancelled"))
    cancelled.cancel()

    assert server.run_once(timeout=0) is False
    assert calls == ["now"]

    # With no messages, run_once waits until the next timer is due.
    start = time.monotonic()
    server.run_once()
    assert calls == ["now", "later"]
    assert 0.03 <= time.monotonic() - start < 1


def test_timer_failure_logged(server, fake_ports, caplog):
    server.call_later(0, lambda: 1 / 0)
    server.call_later(0, lambda: None)
    server.run_once(timeout=0)
    assert "Timer callback failed" in caplog.text


def test_server_with_nothing_to_wait_for():
    with pytest.raises(RuntimeError, match="nothing|no ports"):
        system.Server().run_once()


def test_deferred_timeout(kv, server, fake_ports, handler):
    def get(request, source):
        return handler.defer(timeout=0.02)
    handler.get = get

    request(fake_ports, kv.GetRequest(key=b"k"), txid=5)
    server.run_once(timeout=0)
    assert fake_ports.sent == []

    server.run_once()
    [(_, _, reply)] = fake_ports.replies(kv)
    assert isinstance(reply, dipc.ErrorReply)
    assert (reply.txid, reply.code) == (5, dipc.ErrorCode.TIMEOUT)


def test_deferred_answered_before_timeout(kv, server, fake_ports, handler):
    pending = []

    def get(request, source):
        pending.append(handler.defer(timeout=0.02))
        return pending[-1]
    handler.get = get

    request(fake_ports, kv.GetRequest(key=b"k"), txid=5)
    server.run_once(timeout=0)
    pending[0].reply(kv.GetResponse(value=b"v", found=1))
    time.sleep(0.03)
    server.run_once(timeout=0)

    [(_, _, reply)] = fake_ports.replies(kv)
    assert reply == kv.GetResponse(5, value=b"v", found=1)


def test_defer_timeout_needs_server(kv, handler):
    def get(request, source):
        return handler.defer(timeout=1)
    handler.get = get

    reply = handler.dispatch(encode(kv.GetRequest(key=b"k"), 5)[0], CLIENT, port=SERVICE_PORT)
    assert isinstance(reply, dipc.ErrorReply)
    assert reply.code == dipc.ErrorCode.INTERNAL_ERROR
    assert "needs a Server" in reply.description


def test_port_destroyed_dispatched(kv, server, fake_ports, handler):
    dead = []
    handler.port_destroyed = dead.append

    fake_ports.deliver(SERVICE_PORT, *notification(77))
    assert server.run_once(timeout=0) is True
    assert dead == [77]
    assert fake_ports.sent == []


def test_port_destroyed_default_logs(handler, caplog):
    with caplog.at_level(logging.INFO):
        assert handler.dispatch(notification(77)[1], 0) is None
    assert "Port 77 destroyed" in caplog.text


def test_forged_port_destroyed_refused(kv, handler):
    dead = []
    handler.port_destroyed = dead.append
    buf = dipc.PortDestroyed(77).encode()[0]
    reply = handler.dispatch(buf, CLIENT)

    # It has txid 0, so it's taken as a one-way message: no reply.
    assert reply is None
    assert dead == []


################################################################
# Server attached to an event loop, with faked p-calls
################################################################

class FakeLoop:
    """Stands in for an event loop: posted functions and timers run
    only when told."""

    def __init__(self):
        self.posted = []
        self.timers = {}
        self.next_id = 1

    def post(self, function):
        self.posted.append(function)

    def add_timer(self, duration, listener):
        timer_id, self.next_id = self.next_id, self.next_id + 1
        self.timers[timer_id] = (duration, listener)
        return timer_id

    def cancel_timer(self, timer_id):
        return self.timers.pop(timer_id, None) is not None

    def run_posted(self):
        posted, self.posted = self.posted, []
        for function in posted:
            function()

    def fire_timers(self):
        """Fire every timer, in order of duration."""
        for timer_id, (duration, listener) in sorted(self.timers.items(), key=lambda t: t[1][0]):
            if timer_id in self.timers:
                listener.on_timeout(timer_id, 0.0, 0.0)


class FakeListeners:
    """Stands in for set_port_listener, posting delivered messages to
    each port's listener's loop."""

    def __init__(self, open_ports=(SERVICE_PORT, OTHER_PORT)):
        self.listeners = {}
        self.open_ports = set(open_ports)

    def set_port_listener(self, port, listener, loop=None):
        if port not in self.open_ports:
            raise NonExistentPortError(port)
        if listener is None:
            self.listeners.pop(port, None)
        else:
            self.listeners[port] = (listener, loop)

    def deliver(self, port, message, txid, source=CLIENT):
        listener, loop = self.listeners[port]
        buf, handles = encode(message, txid)
        loop.post(lambda: listener(port, source, buf, tuple(handles)))


@pytest.fixture
def listeners(monkeypatch):
    fake = FakeListeners()
    monkeypatch.setattr(system, "set_port_listener", fake.set_port_listener)
    return fake


@pytest.fixture
def fake_loop():
    return FakeLoop()


@pytest.fixture
def attached(server, listeners, fake_loop):
    server.attach(fake_loop)
    return server


def test_attached_server_replies_on_the_loop(kv, attached, listeners, fake_loop, fake_ports):
    assert set(listeners.listeners) == {SERVICE_PORT}
    listeners.deliver(SERVICE_PORT, kv.PutRequest(key=b"k", value=b"v"), 5)
    assert fake_ports.sent == []

    fake_loop.run_posted()
    assert fake_ports.replies(kv) == [(SERVICE_PORT, CLIENT, kv.PutResponse(5))]


def test_attached_server_adds_and_removes_ports(kv, attached, listeners, fake_loop, fake_ports, handler):
    attached.add(OTHER_PORT, handler)
    assert set(listeners.listeners) == {SERVICE_PORT, OTHER_PORT}
    listeners.deliver(OTHER_PORT, kv.GetRequest(key=b"k"), 3)
    fake_loop.run_posted()
    assert fake_ports.replies(kv) == [(OTHER_PORT, CLIENT, kv.GetResponse(3, found=0))]

    attached.remove(OTHER_PORT)
    assert set(listeners.listeners) == {SERVICE_PORT}

    # Removing a port that's already closed is fine.
    attached.add(OTHER_PORT, handler)
    listeners.open_ports.discard(OTHER_PORT)
    attached.remove(OTHER_PORT)
    assert OTHER_PORT not in attached.ports


def test_attached_server_handler_failure(kv, attached, listeners, fake_loop, fake_ports):
    listeners.deliver(SERVICE_PORT, kv.FailRequest(), 4)
    fake_loop.run_posted()
    [(_, _, reply)] = fake_ports.replies(kv)
    assert (reply.txid, reply.code) == (4, dipc.ErrorCode.INTERNAL_ERROR)


def test_attached_server_contains_failures(attached, listeners, fake_loop, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("broken")
    monkeypatch.setattr(attached, "_handle", broken)
    attached._on_message(SERVICE_PORT, CLIENT, b"", ())
    assert "Failed to handle a message on port 20" in caplog.text


def test_attached_server_timers(attached, fake_loop):
    calls = []
    attached.call_later(0.05, lambda: calls.append("later"))
    attached.call_later(0, lambda: calls.append("now"))
    cancelled = attached.call_later(0, lambda: calls.append("cancelled"))
    cancelled.cancel()

    # Cancelling removes the loop's timer.
    assert sorted(d for d, _ in fake_loop.timers.values()) == [0, pytest.approx(0.05, abs=0.01)]
    fake_loop.fire_timers()
    assert calls == ["now", "later"]
    assert fake_loop.timers == {}


def test_attached_timer_failure_logged(attached, fake_loop, caplog):
    attached.call_later(0, lambda: 1 / 0)
    fake_loop.fire_timers()
    assert "Timer callback failed" in caplog.text


def test_attach_moves_pending_timers(server, listeners, fake_loop):
    calls = []
    server.call_later(10, lambda: calls.append("pending"))
    server.call_later(0, lambda: calls.append("cancelled")).cancel()
    server.attach(fake_loop)
    [(duration, _)] = fake_loop.timers.values()
    assert 9 < duration <= 10
    fake_loop.fire_timers()
    assert calls == ["pending"]


def test_attached_deferred_timeout(kv, attached, listeners, fake_loop, fake_ports, handler):
    def get(request, source):
        return handler.defer(timeout=0.02)
    handler.get = get

    listeners.deliver(SERVICE_PORT, kv.GetRequest(key=b"k"), 5)
    fake_loop.run_posted()
    assert fake_ports.sent == []

    fake_loop.fire_timers()
    [(_, _, reply)] = fake_ports.replies(kv)
    assert (reply.txid, reply.code) == (5, dipc.ErrorCode.TIMEOUT)


def test_attached_server_has_no_run(attached, fake_loop):
    with pytest.raises(RuntimeError, match="attached"):
        attached.run_once(timeout=0)
    with pytest.raises(RuntimeError, match="already attached"):
        attached.attach(fake_loop)
