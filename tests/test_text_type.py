# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Text type implementation, types/text/main.py: its
# handlers, through a Server with faked ports and Storage, and then the
# whole system, through a real p-kernel (see test_pkernel.py for the
# fixtures).

import importlib.util
import logging
import pathlib

import pytest

import darq
from darq import dipc, system
from darq.protocols import storage
from darq.protocols.text import CloseRequest, GetRequest, ReplaceRequest, StatRequest
from darq.protocols.text import Status as TextStatus
from darq.protocols.type import CreateRequest, DestroyRequest, OpenRequest, Status

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_rpc import FakePorts


ROOT = pathlib.Path(__file__).parent.parent
TEXT_PATH = ROOT / "types" / "text"


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


text_module = load("text_type", TEXT_PATH)

IMPLEMENTATION_PORT = 50
CLIENT = 99


class FakeStorage:
    """Stands in for storage.Storage.Client, checking generations."""

    def __init__(self):
        self.blobs: dict[tuple[int, int], tuple[bytes, int]] = {}
        self.next_low = 0

    def create(self, value):
        self.next_low += 1
        key = storage.Key(0x0123456789abcdef, self.next_low)
        self.blobs[(key.high, key.low)] = (value, 1)
        return storage.CreateResponse(key=key, generation=1, status=storage.Status.OK)

    def get(self, key):
        entry = self.blobs.get((key.high, key.low))
        if entry is None:
            return storage.GetResponse(status=storage.Status.NOT_FOUND)
        return storage.GetResponse(value=entry[0], generation=entry[1], status=storage.Status.OK)

    def put(self, key, expected, value):
        entry = self.blobs.get((key.high, key.low))
        if entry is None:
            return storage.PutResponse(status=storage.Status.NOT_FOUND)
        if expected not in (0, entry[1]):
            return storage.PutResponse(status=storage.Status.CONFLICT)
        self.blobs[(key.high, key.low)] = (value, entry[1] + 1)
        return storage.PutResponse(generation=entry[1] + 1, status=storage.Status.OK)

    def text(self, iid: str) -> tuple[str, int]:
        """An item's saved text and flags."""
        key = text_module.parse_item_id(iid)
        return text_module.decode_blob(self.blobs[(key.high, key.low)][0])


class FakeHandles:
    """Stands in for open_port and close_port."""

    def __init__(self):
        self.next_port = 100
        self.closed = []

    def open_port(self):
        self.next_port += 1
        return self.next_port

    def close_port(self, port):
        self.closed.append(port)


@pytest.fixture
def fake_ports(monkeypatch):
    fake = FakePorts()
    monkeypatch.setattr(system, "receive_any", fake.receive_any)
    monkeypatch.setattr(system, "send_message", fake.send_message)
    return fake


@pytest.fixture
def handles(monkeypatch):
    fake = FakeHandles()
    monkeypatch.setattr(darq, "open_port", fake.open_port)
    monkeypatch.setattr(darq, "close_port", fake.close_port)
    return fake


@pytest.fixture
def blobs():
    return FakeStorage()


@pytest.fixture
def implementation(blobs):
    return text_module.TextImplementation(blobs)


@pytest.fixture
def server(implementation, fake_ports, handles):
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


class Caller:
    """Sends requests to the server's ports, and returns its replies."""

    def __init__(self, fake_ports, server):
        self.fake_ports = fake_ports
        self.server = server
        self.txid = 0

    def __call__(self, port, request):
        self.txid += 1
        request.txid = self.txid
        buf, request_handles = request.encode()
        self.fake_ports.deliver(port, CLIENT, buf, request_handles)
        self.server.run_once(timeout=0)

        [(source, destination, reply, reply_handles)] = self.fake_ports.sent
        self.fake_ports.sent.clear()
        assert (source, destination) == (port, CLIENT)
        assert not dipc.decode_flags(reply) & dipc.ERROR, "error reply"
        response = request.RESPONSE()
        response.decode(reply, reply_handles)
        assert response.txid == self.txid
        return response


@pytest.fixture
def call(fake_ports, server):
    return Caller(fake_ports, server)


def create(call):
    """Create an item: returns its identifier and port."""
    response = call(IMPLEMENTATION_PORT, CreateRequest(type_id=text_module.TYPE_ID))
    assert response.status == Status.OK
    return response.item_id, response.item


def test_create_only_its_type(call):
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.png")).status == Status.NOT_FOUND
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id="bad:id")).status == Status.INVALID_ID


def replace(call, port, start, end, text, expected=0):
    return call(port, ReplaceRequest(start=start, end=end, text=text, expected=expected))


def get(call, port):
    response = call(port, GetRequest())
    return response.text, response.version


################################################################
# Blobs and item identifiers
################################################################

def test_blob_round_trip():
    blob = text_module.encode_blob("café \U0001F600", text_module.DESTROYED)
    assert blob[:4] == b"DTXT"
    assert text_module.decode_blob(blob) == ("café \U0001F600", text_module.DESTROYED)


@pytest.mark.parametrize("blob", [b"DTX", b"XXXX\x00\x01\x00\x00", b"DTXT\x00\x02\x00\x00",
                                  b"DTXT\x00\x01\x00\x00\xff"])
def test_bad_blob(blob):
    with pytest.raises(text_module.FormatError):
        text_module.decode_blob(blob)


def test_item_id_round_trip():
    key = storage.Key(0x0123456789abcdef, 42)
    iid = text_module.item_id(key)
    assert iid == "public.utf8-plain-text:0123456789abcdef000000000000002a"
    assert darq.is_valid_item_id(iid)
    assert text_module.parse_item_id(iid) == key

    # The type identifier is canonicalized, but not the specifier.
    assert text_module.parse_item_id(iid.replace("public.utf8", "Public.UTF8")) == key
    assert text_module.parse_item_id(iid.upper()) is None


@pytest.mark.parametrize("iid", ["public.plain-text:0123456789abcdef000000000000002a",
                                 "public.utf8-plain-text:2a", "public.utf8-plain-text:my-notes"])
def test_item_id_not_ours(iid):
    assert text_module.parse_item_id(iid) is None


def test_item_id_invalid():
    with pytest.raises(darq.InvalidIdentifierError):
        text_module.parse_item_id("no colon")


################################################################
# Items
################################################################

def test_create(call, server, blobs):
    iid, port = create(call)
    assert iid.startswith("public.utf8-plain-text:")
    assert port in server.ports
    assert blobs.text(iid) == ("", 0)
    assert get(call, port) == ("", 1)


def test_edit(call):
    iid, port = create(call)
    assert replace(call, port, 0, 0, "hello").version == 2
    assert replace(call, port, 5, 5, " world").version == 3
    assert replace(call, port, 0, 1, "H").version == 4
    assert get(call, port) == ("Hello world", 4)

    stat = call(port, StatRequest())
    assert (stat.length, stat.version) == (11, 4)

    # Delete.
    replace(call, port, 5, 11, "")
    assert get(call, port) == ("Hello", 5)


def test_positions_are_code_points(call):
    iid, port = create(call)

    # e and a combining acute accent, an emoji outside the BMP, and x.
    replace(call, port, 0, 0, "é\U0001F600x")
    assert call(port, StatRequest()).length == 4
    replace(call, port, 2, 3, "")
    assert get(call, port)[0] == "éx"


@pytest.mark.parametrize("start, end", [(3, 2), (0, 6), (6, 6)])
def test_replace_out_of_range(call, start, end):
    iid, port = create(call)
    replace(call, port, 0, 0, "hello")
    response = replace(call, port, start, end, "!")
    assert (response.status, response.version) == (TextStatus.OUT_OF_RANGE, 2)
    assert get(call, port) == ("hello", 2)


def test_replace_conflict(call):
    iid, port = create(call)
    assert replace(call, port, 0, 0, "a", expected=1).status == TextStatus.OK

    # Made against version 1, which has gone.
    response = replace(call, port, 0, 0, "b", expected=1)
    assert (response.status, response.version) == (TextStatus.CONFLICT, 2)
    assert replace(call, port, 1, 1, "c", expected=2).status == TextStatus.OK
    assert get(call, port) == ("ac", 3)


def test_autosave(call, server, blobs, monkeypatch):
    monkeypatch.setattr(text_module, "SAVE_DELAY", 0.01)
    iid, port = create(call)
    replace(call, port, 0, 0, "draft")
    assert blobs.text(iid) == ("", 0)

    server.run_once(timeout=0.05)
    assert blobs.text(iid) == ("draft", 0)


def test_autosave_not_put_off_forever(call, server, blobs, monkeypatch):
    # Each change puts the save off, but only until MAX_SAVE_DELAY.
    monkeypatch.setattr(text_module, "SAVE_DELAY", 10.0)
    monkeypatch.setattr(text_module, "MAX_SAVE_DELAY", 0.01)
    iid, port = create(call)
    replace(call, port, 0, 0, "a")
    replace(call, port, 1, 1, "b")

    server.run_once(timeout=0.05)
    assert blobs.text(iid) == ("ab", 0)


def test_close_saves_and_forgets(call, server, implementation, blobs, handles, fake_ports, monkeypatch):
    iid, port = create(call)
    replace(call, port, 0, 0, "kept")

    # The port is closed after the reply's sent from it.
    replied_first = []
    monkeypatch.setattr(darq, "close_port", lambda p: replied_first.append((p, len(fake_ports.sent) == 1)))
    call(port, CloseRequest())
    assert replied_first == [(port, True)]

    assert blobs.text(iid) == ("kept", 0)
    assert implementation.buffers == {}
    assert port not in server.ports


def test_reopen(call, implementation):
    iid, port = create(call)
    replace(call, port, 0, 0, "kept")
    call(port, CloseRequest())

    response = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid))
    assert response.status == Status.OK
    assert response.item != port
    assert get(call, response.item) == ("kept", 1)


def test_clients_share_an_item(call, implementation, blobs):
    iid, first = create(call)
    second = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).item
    assert second != first

    replace(call, first, 0, 0, "shared")
    assert get(call, second) == ("shared", 2)

    # It's kept until its last client closes it.
    call(first, CloseRequest())
    assert len(implementation.buffers) == 1
    assert get(call, second) == ("shared", 2)
    call(second, CloseRequest())
    assert implementation.buffers == {}
    assert blobs.text(iid) == ("shared", 0)


@pytest.mark.parametrize("iid, status", [
    ("public.utf8-plain-text:0123456789abcdef00000000000000ff", Status.NOT_FOUND),
    ("public.plain-text:0123456789abcdef0000000000000001", Status.NOT_FOUND),
    ("public.utf8-plain-text:notes", Status.NOT_FOUND),
    ("bad id", Status.INVALID_ID),
])
def test_open_not_found(call, iid, status):
    create(call)
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == status


def test_open_corrupt_blob(call, blobs, caplog):
    key = blobs.create(b"not a text item").key
    response = call(IMPLEMENTATION_PORT, OpenRequest(item_id=text_module.item_id(key)))
    assert response.status == Status.FAILED
    assert "Can't open" in caplog.text


def test_destroy(call, blobs):
    iid, port = create(call)
    replace(call, port, 0, 0, "gone")
    call(port, CloseRequest())

    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert blobs.text(iid) == ("gone", text_module.DESTROYED)
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.NOT_FOUND
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id="bad id")).status == Status.INVALID_ID


def test_destroy_while_open(call, blobs):
    iid, port = create(call)
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK

    # Its client can go on, but it can't be opened again.
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND
    replace(call, port, 0, 0, "last words")
    call(port, CloseRequest())
    assert blobs.text(iid) == ("last words", text_module.DESTROYED)


def test_save_conflict(call, implementation, blobs, caplog):
    iid, port = create(call)
    replace(call, port, 0, 0, "mine")

    # Something else writes the blob.
    key = text_module.parse_item_id(iid)
    blobs.put(key, 0, text_module.encode_blob("theirs"))

    with caplog.at_level(logging.ERROR):
        call(port, CloseRequest())
    assert "CONFLICT" in caplog.text
    assert blobs.text(iid) == ("theirs", 0)
    assert implementation.buffers == {}


################################################################
# End to end
################################################################

def test_text_type(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # A client that creates an item, edits it, closes it, and opens it again.
    client_path = write_app(tmp_path / "client", """
        import struct
        import time
        import darq
        from darq.protocols import storage
        from darq.protocols.text import Status as TextStatus, Text
        from darq.protocols.type import Implementation, Status, Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)

            # Wait for the sync to register the implementation.
            deadline = time.monotonic() + 30
            while not types.describe(type_id="public.utf8-plain-text").implementation:
                if time.monotonic() > deadline:
                    return "public.utf8-plain-text never registered"
                time.sleep(0.05)

            activated = types.activate(type_id="public.utf8-plain-text")
            if activated.status != Status.OK:
                return f"activate: {activated!r}"
            implementation = Implementation.Client(activated.implementation, timeout=10)

            created = implementation.create(type_id="public.utf8-plain-text")
            if created.status != Status.OK:
                return f"create: {created!r}"
            item = Text.Client(created.item, timeout=10)
            item.replace(start=0, end=0, text="Hello, world")
            if item.replace(start=7, end=12, text="DarqOS \\U0001F600").status != TextStatus.OK:
                return "replace failed"
            item.close()

            opened = implementation.open(item_id=created.item_id)
            if opened.status != Status.OK:
                return f"open: {opened!r}"
            again = Text.Client(opened.item, timeout=10)
            text = again.get().text
            if text != "Hello, DarqOS \\U0001F600":
                return f"reopened text is {text!r}"
            if again.stat().length != 15:
                return "wrong length"
            again.close()

            # It's in Storage, after a header.
            specifier = created.item_id.split(":", 1)[1]
            key = storage.Key(int(specifier[:16], 16), int(specifier[16:], 16))
            blob = storage.Storage.Client(darq.connect("darq.storage"), timeout=10).get(key=key)
            if blob.value[8:].decode("utf-8") != text or struct.unpack(">4sHH", blob.value[:8])[0] != b"DTXT":
                return f"saved blob is {blob.value!r}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
