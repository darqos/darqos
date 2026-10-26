# darqos
# Copyright (C) 2026 David Arnold

# Tests for requests and responses over ports, in darq/system.py, using
# a module generated from a DIDL protocol.  The p-calls are faked here:
# test_pkernel.py runs a service and client through a real p-kernel.

import collections
import importlib.machinery
import importlib.util
import logging
import pathlib
import textwrap

import pytest

from darq import dipc, system
from darq.errors import NonExistentPortError, RemoteError


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
        self.queue = collections.deque()

    def open_port(self):
        self.opened += 1
        return REPLY_PORT

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
    for name in ("open_port", "send_message", "receive_message"):
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


def test_txids_skip_zero(monkeypatch):
    monkeypatch.setattr(system, "_last_txid", 0xFFFFFFFE)
    assert [system._allocate_txid() for _ in range(3)] == [0xFFFFFFFF, 1, 2]
