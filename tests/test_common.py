# darqos
# Copyright (C) 2026 David Arnold

# Encode/decode tests for the p-kernel messages in darq.common.
#
# Tests marked xfail document known defects in darq.common.  They are
# strict, so once a defect is fixed the test will report XPASS and the
# marker should be removed.

import struct

import pytest

from darq.common import (
    ClosePortRequest,
    ClosePortResponse,
    DeliverChunk,
    DeliverMessage,
    Message,
    MessageType,
    OpenPortRequest,
    OpenPortResponse,
    Reboot,
    Running,
    SendChunk,
    SendMessage,
    Shutdown,
    SpawnRequest,
    SpawnResponse,
    UInt64,
)
from darq.errors import MessageDecodingError


HEADER_LENGTH = 8


def check_header(buf: bytes, message_type: int):
    """Check the wire-format header of an encoded message."""
    assert len(buf) >= HEADER_LENGTH
    assert Message.decode_type(buf[:HEADER_LENGTH]) == message_type
    assert Message.decode_length(buf[:HEADER_LENGTH]) == len(buf)


################################################################
# Message (common header)
################################################################

class TestMessage:
    def test_encode_header(self):
        m = Message(MessageType.OPEN_PORT_RQST)
        m.set_length(HEADER_LENGTH)
        buf = m.encode()
        assert buf == struct.pack(">BBBxL", 0, HEADER_LENGTH,
                                  MessageType.OPEN_PORT_RQST, HEADER_LENGTH)

    def test_roundtrip(self):
        m = Message(MessageType.SEND_MESSAGE)
        m.set_version(3)
        m.set_length(1234)

        d = Message()
        d.decode(m.encode())
        assert d.version == 3
        assert d.header_length == HEADER_LENGTH
        assert d.type == MessageType.SEND_MESSAGE
        assert d.length == 1234

    def test_init_copies_header(self):
        m = Message(MessageType.RUNNING)
        m.set_length(24)

        d = Message()
        d.init(m)
        assert d.encode() == m.encode()

    def test_decode_type_and_length(self):
        m = Message(MessageType.DELIVER_MESSAGE)
        m.set_length(0xDEADBEEF)
        buf = m.encode()
        assert Message.decode_type(buf) == MessageType.DELIVER_MESSAGE
        assert Message.decode_length(buf) == 0xDEADBEEF

    def test_decode_type_and_length_short_buffer(self):
        assert Message.decode_type(b"\x00" * 7) == 0
        assert Message.decode_length(b"\x00" * 7) == 0

    def test_decode_short_buffer_raises(self):
        with pytest.raises(MessageDecodingError):
            Message().decode(b"\x00" * 7)

    @pytest.mark.parametrize("raw, padded", [
        (b"", b""),
        (b"a", b"a\0\0\0"),
        (b"ab", b"ab\0\0"),
        (b"abc", b"abc\0"),
        (b"abcd", b"abcd"),
        (b"abcde", b"abcde\0\0\0"),
    ])
    def test_pad_bytes(self, raw, padded):
        assert Message.pad_bytes(raw) == padded
        assert Message.padded_length(raw) == len(padded)


################################################################
# Fixed-size messages
################################################################

class TestRunning:
    def test_roundtrip(self):
        m = Running(dpid=42)
        m.host_pid = 31337
        buf = m.encode()
        check_header(buf, MessageType.RUNNING)

        d = Running()
        d.decode(buf)
        assert d.dpid == 42
        assert d.host_pid == 31337

    def test_max_values(self):
        m = Running(dpid=UInt64.max())
        m.host_pid = UInt64.max()
        d = Running()
        d.decode(m.encode())
        assert d.dpid == UInt64.max()
        assert d.host_pid == UInt64.max()

    def test_truncated_raises(self):
        buf = Running(dpid=1).encode()
        with pytest.raises(MessageDecodingError):
            Running().decode(buf[:-1])


class TestOpenPortRequest:
    def test_roundtrip(self):
        m = OpenPortRequest(request_id=7, port=42)
        buf = m.encode()
        check_header(buf, MessageType.OPEN_PORT_RQST)

        d = OpenPortRequest()
        d.decode(buf)
        assert d.request_id == 7
        assert d.requested_port == 42

    def test_ephemeral_and_max_port(self):
        for port in (0, UInt64.max()):
            d = OpenPortRequest()
            d.decode(OpenPortRequest(1, port).encode())
            assert d.requested_port == port

    def test_truncated_raises(self):
        buf = OpenPortRequest(1, 2).encode()
        with pytest.raises(MessageDecodingError):
            OpenPortRequest().decode(buf[:-1])


class TestOpenPortResponse:
    def test_roundtrip(self):
        m = OpenPortResponse(request_id=7, result=3, port=16385)
        buf = m.encode()
        check_header(buf, MessageType.OPEN_PORT_RESP)

        d = OpenPortResponse()
        d.decode(buf)
        assert d.request_id == 7
        assert d.result == 3
        assert d.port == 16385

    def test_truncated_raises(self):
        buf = OpenPortResponse(1, 0, 2).encode()
        with pytest.raises(MessageDecodingError):
            OpenPortResponse().decode(buf[:-1])


class TestClosePortRequest:
    def test_roundtrip(self):
        m = ClosePortRequest(request_id=9, port=16385)
        buf = m.encode()
        check_header(buf, MessageType.CLOSE_PORT_RQST)

        d = ClosePortRequest()
        d.decode(buf)
        assert d.request_id == 9
        assert d.port == 16385

    def test_truncated_raises(self):
        buf = ClosePortRequest(1, 2).encode()
        with pytest.raises(MessageDecodingError):
            ClosePortRequest().decode(buf[:-1])


class TestClosePortResponse:
    def test_roundtrip(self):
        m = ClosePortResponse()
        m.request_id = 9
        m.port = 16385
        buf = m.encode()
        check_header(buf, MessageType.CLOSE_PORT_RESP)

        d = ClosePortResponse()
        d.decode(buf)
        assert d.request_id == 9
        assert d.port == 16385

    def test_truncated_raises(self):
        buf = ClosePortResponse().encode()
        with pytest.raises(MessageDecodingError):
            ClosePortResponse().decode(buf[:-1])


################################################################
# Variable-length messages
################################################################

PAYLOADS = [b"", b"x", b"hello world", bytes(range(256))]


@pytest.mark.parametrize("cls, message_type", [
    (SendMessage, MessageType.SEND_MESSAGE),
    (DeliverMessage, MessageType.DELIVER_MESSAGE),
])
class TestPortMessages:
    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_roundtrip(self, cls, message_type, payload):
        m = cls()
        m.source = 16385
        m.destination = 16386
        m.set_payload(payload)
        buf = m.encode()
        check_header(buf, message_type)
        assert len(buf) % 4 == 0

        d = cls()
        d.decode(buf)
        assert d.source == 16385
        assert d.destination == 16386
        assert d.payload == payload

    def test_get_length_tracks_payload(self, cls, message_type):
        m = cls()
        assert m.get_length() == HEADER_LENGTH + 24
        m.payload = b"abc"
        assert m.get_length() == HEADER_LENGTH + 24 + 4
        m.set_payload(b"abcde")
        assert m.length == HEADER_LENGTH + 24 + 8

    def test_truncated_raises(self, cls, message_type):
        m = cls()
        m.set_payload(b"payload")
        with pytest.raises(MessageDecodingError):
            cls().decode(m.encode()[:-1])


def test_send_message_constructor_ports():
    m = SendMessage(source=1, destination=2)
    d = SendMessage()
    d.decode(m.encode())
    assert (d.source, d.destination) == (1, 2)


class TestSpawnRequest:
    @pytest.mark.parametrize("path", ["/a", "/darq/bin/init", "/abc"])
    def test_roundtrip(self, path):
        m = SpawnRequest(request_id=5, exec_path=path)
        buf = m.encode()
        check_header(buf, MessageType.SPAWN_PROCESS_RQST)

        d = SpawnRequest(0, "")
        d.decode(buf)
        assert d.request_id == 5
        assert d.get_exec_path() == path

    def test_length_set_on_construction(self):
        m = SpawnRequest(request_id=5, exec_path="/darq/bin/init")
        assert m.length == HEADER_LENGTH + 8 + 16

    def test_get_length(self):
        m = SpawnRequest(request_id=5, exec_path="/darq/bin/init")
        assert m.get_length() == HEADER_LENGTH + 8 + 16


class TestSpawnResponse:
    @pytest.mark.parametrize("error", [None, "", "E", "ENOENT", "no such file"])
    def test_roundtrip(self, error):
        m = SpawnResponse(request_id=5, result=0, dpid=17, error=error)
        buf = m.encode()
        check_header(buf, MessageType.SPAWN_PROCESS_RESP)

        d = SpawnResponse(0, 0, 0, None)
        d.decode(buf)
        assert d.request_id == 5
        assert d.result == 0
        assert d.dpid == 17
        assert d.get_error() == (error or "")

    def test_length_set_on_construction(self):
        m = SpawnResponse(request_id=5, result=0, dpid=17, error=None)
        assert m.length == HEADER_LENGTH + 20


################################################################
# Stream chunks
################################################################

@pytest.mark.parametrize("cls, message_type", [
    (SendChunk, MessageType.SEND_CHUNK),
    (DeliverChunk, MessageType.DELIVER_CHUNK),
])
class TestChunkMessages:
    def test_type(self, cls, message_type):
        assert cls().type == message_type

    @pytest.mark.parametrize("payload", PAYLOADS + [b"chunk"])
    def test_roundtrip(self, cls, message_type, payload):
        m = cls(source=16385, destination=16386, offset=4096)
        m.set_payload(payload)
        buf = m.encode()
        check_header(buf, message_type)
        assert len(buf) % 4 == 0

        d = cls()
        d.decode(buf)
        assert d.source == 16385
        assert d.destination == 16386
        assert d.offset == 4096
        assert d.payload == payload

    def test_max_offset(self, cls, message_type):
        d = cls()
        d.decode(cls(offset=UInt64.max()).encode())
        assert d.offset == UInt64.max()

    def test_get_length_tracks_payload(self, cls, message_type):
        m = cls()
        assert m.get_length() == HEADER_LENGTH + 32
        m.set_payload(b"abc")
        assert m.get_length() == HEADER_LENGTH + 32 + 4

    def test_truncated_raises(self, cls, message_type):
        m = cls()
        m.set_payload(b"payload")
        with pytest.raises(MessageDecodingError):
            cls().decode(m.encode()[:-1])


################################################################
# Header-only messages
################################################################

@pytest.mark.parametrize("cls, message_type", [
    (Reboot, MessageType.REBOOT),
    (Shutdown, MessageType.SHUTDOWN),
])
class TestHeaderOnlyMessages:
    def test_type(self, cls, message_type):
        assert cls().type == message_type

    def test_length(self, cls, message_type):
        assert cls().length == HEADER_LENGTH

    def test_roundtrip(self, cls, message_type):
        buf = cls().encode()
        check_header(buf, message_type)
        assert len(buf) == HEADER_LENGTH

        d = cls()
        d.decode(buf)
        assert d.type == message_type
        assert d.length == HEADER_LENGTH

    def test_decode(self, cls, message_type):
        m = Message(message_type)
        m.set_length(HEADER_LENGTH)
        d = cls()
        d.decode(m.encode())
        assert d.type == message_type
        assert d.length == HEADER_LENGTH

    def test_short_buffer_raises(self, cls, message_type):
        with pytest.raises(MessageDecodingError):
            cls().decode(b"\x00" * 7)
