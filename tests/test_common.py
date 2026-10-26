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
    Exited,
    KillProcessRequest,
    KillProcessResponse,
    LookupNameRequest,
    LookupNameResponse,
    Message,
    MessageType,
    OpenPortRequest,
    OpenPortResponse,
    Reboot,
    RegisterNameRequest,
    RegisterNameResponse,
    RunRequest,
    RunResponse,
    SendChunk,
    SendMessageRequest,
    SendMessageResponse,
    Shutdown,
    SpawnProcessRequest,
    SpawnProcessResponse,
    UInt64,
    WaitRequest,
    WaitResponse,
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
        m = Message(MessageType.SEND_MESSAGE_RQST)
        m.set_version(3)
        m.set_length(1234)

        d = Message()
        d.decode(m.encode())
        assert d.version == 3
        assert d.header_length == HEADER_LENGTH
        assert d.type == MessageType.SEND_MESSAGE_RQST
        assert d.length == 1234

    def test_init_copies_header(self):
        m = Message(MessageType.RUN_REQUEST)
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

class TestRunRequest:
    def test_roundtrip(self):
        m = RunRequest(dpid=42)
        m.host_pid = 31337
        buf = m.encode()
        check_header(buf, MessageType.RUN_REQUEST)

        d = RunRequest()
        d.decode(buf)
        assert d.dpid == 42
        assert d.host_pid == 31337

    def test_max_values(self):
        m = RunRequest(dpid=UInt64.max())
        m.host_pid = UInt64.max()
        d = RunRequest()
        d.decode(m.encode())
        assert d.dpid == UInt64.max()
        assert d.host_pid == UInt64.max()

    def test_truncated_raises(self):
        buf = RunRequest(dpid=1).encode()
        with pytest.raises(MessageDecodingError):
            RunRequest().decode(buf[:-1])


class TestRunResponse:
    @pytest.mark.parametrize("result", [0, 1, 2 ** 32 - 1])
    def test_roundtrip(self, result):
        buf = RunResponse(result).encode()
        check_header(buf, MessageType.RUN_RESPONSE)
        assert len(buf) == HEADER_LENGTH + 8

        d = RunResponse()
        d.decode(buf)
        assert d.result == result

    def test_truncated_raises(self):
        with pytest.raises(MessageDecodingError):
            RunResponse().decode(RunResponse(0).encode()[:-1])


class TestOpenPortRequest:
    def test_roundtrip(self):
        m = OpenPortRequest(request_id=7)
        buf = m.encode()
        check_header(buf, MessageType.OPEN_PORT_RQST)
        assert len(buf) == 16

        d = OpenPortRequest()
        d.decode(buf)
        assert d.request_id == 7

    def test_truncated_raises(self):
        buf = OpenPortRequest(1).encode()
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


class TestRegisterNameRequest:
    def test_roundtrip(self):
        buf = RegisterNameRequest(7, "darq.storage", UInt64.max()).encode()
        check_header(buf, MessageType.REGISTER_NAME_RQST)
        assert len(buf) % 4 == 0

        d = RegisterNameRequest()
        d.decode(buf)
        assert (d.request_id, d.get_name(), d.port) == (7, "darq.storage", UInt64.max())

    def test_utf8_name(self):
        d = RegisterNameRequest()
        d.decode(RegisterNameRequest(1, "naïve.ポート", 2).encode())
        assert d.get_name() == "naïve.ポート"

    def test_name_overrun_raises(self):
        buf = bytearray(RegisterNameRequest(1, "abc", 2).encode())
        buf[12:16] = (99).to_bytes(4, "big")
        with pytest.raises(MessageDecodingError):
            RegisterNameRequest().decode(bytes(buf))

    def test_truncated_raises(self):
        buf = RegisterNameRequest(1, "abc", 2).encode()
        with pytest.raises(MessageDecodingError):
            RegisterNameRequest().decode(buf[:-1])


class TestRegisterNameResponse:
    def test_roundtrip(self):
        buf = RegisterNameResponse(7, 3).encode()
        check_header(buf, MessageType.REGISTER_NAME_RESP)

        d = RegisterNameResponse()
        d.decode(buf)
        assert (d.request_id, d.result) == (7, 3)


class TestLookupNameRequest:
    @pytest.mark.parametrize("blocking", [True, False])
    def test_roundtrip(self, blocking):
        buf = LookupNameRequest(7, "darq.history", blocking).encode()
        check_header(buf, MessageType.LOOKUP_NAME_RQST)
        assert len(buf) % 4 == 0

        d = LookupNameRequest(0, "", not blocking)
        d.decode(buf)
        assert (d.request_id, d.get_name(), d.blocking) == (7, "darq.history", blocking)

    def test_name_overrun_raises(self):
        buf = bytearray(LookupNameRequest(1, "abc").encode())
        buf[12:16] = (99).to_bytes(4, "big")
        with pytest.raises(MessageDecodingError):
            LookupNameRequest().decode(bytes(buf))


class TestLookupNameResponse:
    def test_roundtrip(self):
        buf = LookupNameResponse(7, 0, UInt64.max()).encode()
        check_header(buf, MessageType.LOOKUP_NAME_RESP)

        d = LookupNameResponse()
        d.decode(buf)
        assert (d.request_id, d.result, d.port) == (7, 0, UInt64.max())


class TestWaitRequest:
    @pytest.mark.parametrize("blocking", [True, False])
    def test_roundtrip(self, blocking):
        buf = WaitRequest(7, UInt64.max(), blocking).encode()
        check_header(buf, MessageType.WAIT_RQST)

        d = WaitRequest(0, 0, not blocking)
        d.decode(buf)
        assert (d.request_id, d.dpid, d.blocking) == (7, UInt64.max(), blocking)


class TestWaitResponse:
    @pytest.mark.parametrize("status, error", [(0, None), (-1, None), (-2 ** 31, "Traceback ...")])
    def test_roundtrip(self, status, error):
        buf = WaitResponse(7, 0, 9, 2, status, error).encode()
        check_header(buf, MessageType.WAIT_RESP)
        assert len(buf) % 4 == 0

        d = WaitResponse()
        d.decode(buf)
        assert (d.request_id, d.result, d.dpid, d.reason, d.status, d.get_error()) == \
            (7, 0, 9, 2, status, error)

    def test_error_overrun_raises(self):
        buf = bytearray(WaitResponse(1, 0, 2, 0, 0, "abc").encode())
        buf[32:36] = (99).to_bytes(4, "big")
        with pytest.raises(MessageDecodingError):
            WaitResponse().decode(bytes(buf))


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


class TestKillProcessRequest:
    def test_roundtrip(self):
        m = KillProcessRequest(request_id=11, dpid=UInt64.max())
        buf = m.encode()
        check_header(buf, MessageType.KILL_PROCESS_RQST)
        assert len(buf) == HEADER_LENGTH + 16

        d = KillProcessRequest()
        d.decode(buf)
        assert d.request_id == 11
        assert d.dpid == UInt64.max()

    def test_truncated_raises(self):
        buf = KillProcessRequest(1, 2).encode()
        with pytest.raises(MessageDecodingError):
            KillProcessRequest().decode(buf[:-1])


class TestKillProcessResponse:
    def test_roundtrip(self):
        m = KillProcessResponse(request_id=11, result=3)
        buf = m.encode()
        check_header(buf, MessageType.KILL_PROCESS_RESP)
        assert len(buf) == HEADER_LENGTH + 8

        d = KillProcessResponse()
        d.decode(buf)
        assert d.request_id == 11
        assert d.result == 3

    def test_truncated_raises(self):
        buf = KillProcessResponse(1, 0).encode()
        with pytest.raises(MessageDecodingError):
            KillProcessResponse().decode(buf[:-1])


################################################################
# Variable-length messages
################################################################

PAYLOADS = [b"", b"x", b"hello world", bytes(range(256))]


@pytest.mark.parametrize("cls, message_type", [
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


class TestSendMessageRequest:
    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_roundtrip(self, payload):
        m = SendMessageRequest(source=UInt64.max(), destination=16386, request_id=7)
        m.set_payload(payload)
        buf = m.encode()
        check_header(buf, MessageType.SEND_MESSAGE_RQST)
        assert len(buf) % 4 == 0
        assert len(buf) == HEADER_LENGTH + 24 + m.padded_length(payload)

        d = SendMessageRequest()
        d.decode(buf)
        assert (d.request_id, d.source, d.destination, d.payload) == (7, UInt64.max(), 16386, payload)

    def test_payload_overrun_raises(self):
        m = SendMessageRequest(1, 2, 3)
        m.set_payload(b"abc")
        buf = bytearray(m.encode())
        buf[12:16] = (99).to_bytes(4, "big")
        with pytest.raises(MessageDecodingError):
            SendMessageRequest().decode(bytes(buf))

    def test_truncated_raises(self):
        m = SendMessageRequest(1, 2, 3)
        m.set_payload(b"payload")
        with pytest.raises(MessageDecodingError):
            SendMessageRequest().decode(m.encode()[:-1])


class TestSendMessageResponse:
    def test_roundtrip(self):
        buf = SendMessageResponse(7, 3).encode()
        check_header(buf, MessageType.SEND_MESSAGE_RESP)

        d = SendMessageResponse()
        d.decode(buf)
        assert (d.request_id, d.result) == (7, 3)


class TestSpawnProcessRequest:
    @pytest.mark.parametrize("path", ["/a", "/darq/bin/init", "/abc"])
    def test_roundtrip(self, path):
        m = SpawnProcessRequest(request_id=5, exec_path=path)
        buf = m.encode()
        check_header(buf, MessageType.SPAWN_PROCESS_RQST)

        d = SpawnProcessRequest(0, "")
        d.decode(buf)
        assert d.request_id == 5
        assert d.get_exec_path() == path

    def test_length_set_on_construction(self):
        m = SpawnProcessRequest(request_id=5, exec_path="/darq/bin/init")
        assert m.length == HEADER_LENGTH + 8 + 16

    def test_get_length(self):
        m = SpawnProcessRequest(request_id=5, exec_path="/darq/bin/init")
        assert m.get_length() == HEADER_LENGTH + 8 + 16


class TestSpawnProcessResponse:
    @pytest.mark.parametrize("error", [None, "", "E", "ENOENT", "no such file"])
    def test_roundtrip(self, error):
        m = SpawnProcessResponse(request_id=5, result=0, dpid=17, error=error)
        buf = m.encode()
        check_header(buf, MessageType.SPAWN_PROCESS_RESP)

        d = SpawnProcessResponse(0, 0, 0, None)
        d.decode(buf)
        assert d.request_id == 5
        assert d.result == 0
        assert d.dpid == 17
        assert d.get_error() == (error or "")

    def test_length_set_on_construction(self):
        m = SpawnProcessResponse(request_id=5, result=0, dpid=17, error=None)
        assert m.length == HEADER_LENGTH + 20


class TestExited:
    @pytest.mark.parametrize("status", [0, 1, 255, -1, 2 ** 31 - 1, -2 ** 31])
    def test_roundtrip_status(self, status):
        buf = Exited(status).encode()
        check_header(buf, MessageType.EXITED)

        d = Exited()
        d.decode(buf)
        assert d.status == status
        assert d.get_error() is None

    @pytest.mark.parametrize("error", ["E", "ValueError: boom", "Traceback (most recent call last):\n  ...\n", "échec"])
    def test_roundtrip_error(self, error):
        buf = Exited(1, error).encode()
        assert len(buf) % 4 == 0

        d = Exited()
        d.decode(buf)
        assert d.status == 1
        assert d.get_error() == error

    def test_length_set_on_construction(self):
        assert Exited(0).length == HEADER_LENGTH + 8
        assert Exited(1, "abcde").length == HEADER_LENGTH + 8 + 8

    def test_truncated_raises(self):
        with pytest.raises(MessageDecodingError):
            Exited().decode(Exited(1, "error").encode()[:-1])

    def test_error_overrun_raises(self):
        buf = bytearray(Exited(1, "abc").encode())
        struct.pack_into(">L", buf, HEADER_LENGTH + 4, 100)
        with pytest.raises(MessageDecodingError):
            Exited().decode(bytes(buf))


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
