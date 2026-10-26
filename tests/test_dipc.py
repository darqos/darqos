# darqos
# Copyright (C) 2026 David Arnold

# Tests for the common DIPC message classes, darq/dipc.py.

import struct

import pytest

from darq import dipc
from darq.errors import MessageDecodingError


class Ping(dipc.Message):
    METHOD = 3


class Point(dipc.Record):
    def __init__(self, x: int = 0, y: int = 0):
        self.x = x
        self.y = y


################################################################
# Header
################################################################

def test_header_layout():
    assert struct.calcsize(dipc.HEADER_FORMAT) == dipc.HEADER_LENGTH == 8
    assert dipc.HEADER_FORMAT.startswith(dipc.BYTE_ORDER)


def test_encode_header():
    assert Ping(0x01020304).encode()[0] == bytes.fromhex("04030201" "0000" "0300")


def test_decode_header():
    message = Ping()
    message.decode(bytes.fromhex("04030201" "0000" "0300"))
    assert message.txid == 0x01020304


def test_decode_txid_and_method():
    buf = Ping(7).encode()[0]
    assert dipc.decode_txid(buf) == 7
    assert dipc.decode_method(buf) == 3


@pytest.mark.parametrize("decode", [dipc.decode_txid, dipc.decode_method,
                                    lambda buf: Ping().decode(buf)])
def test_short_header(decode):
    with pytest.raises(MessageDecodingError, match="too short"):
        decode(b"\0" * 7)


def test_decode_wrong_method():
    with pytest.raises(MessageDecodingError, match="method 4, expected 3"):
        Ping().decode(bytes.fromhex("00000000" "0000" "0400"))


def test_decode_flags_set():
    with pytest.raises(MessageDecodingError, match="flags 0x1, expected 0x0"):
        Ping().decode(bytes.fromhex("00000000" "0100" "0300"))


################################################################
# Records
################################################################

def test_record_equality():
    assert Point(1, 2) == Point(1, 2)
    assert Point(1, 2) != Point(1, 3)
    assert Ping(1) != Point()


def test_record_repr():
    assert repr(Point(1, 2)) == "Point(x=1, y=2)"
    assert repr(Ping(5)) == "Ping(txid=5)"


################################################################
# Opaque values
################################################################

@pytest.mark.parametrize("value, padded", [
    (b"", b""),
    (b"abc", b"abc\0\0\0\0\0"),
    (b"12345678", b"12345678"),
    (b"123456789", b"123456789" + b"\0" * 7),
])
def test_pad_bytes(value, padded):
    assert dipc.Message.pad_bytes(value) == padded


def test_decode_opaque():
    buf = b"\0" * 16 + b"abc\0\0\0\0\0"
    assert dipc.Message.decode_opaque(buf, 16, 16, 3) == b"abc"
    assert dipc.Message.decode_opaque(buf, 16, 16, 0) == b""


@pytest.mark.parametrize("offset, length, error", [
    (17, 3, "not 8-byte aligned"),
    (8, 3, "out of range"),       # before the first value
    (16, 9, "out of range"),      # past the end
    (24, 1, "out of range"),
])
def test_decode_opaque_errors(offset, length, error):
    with pytest.raises(MessageDecodingError, match=error):
        dipc.Message.decode_opaque(b"\0" * 24, 16, offset, length)


################################################################
# Ports
################################################################

def test_encode_port():
    handles: list[int] = []
    assert dipc.Message.encode_port(handles, 500) == 0
    assert dipc.Message.encode_port(handles, None) == dipc.NO_PORT == 0xFFFF
    assert dipc.Message.encode_port(handles, 600) == 1
    # A handle already in the list keeps its index.
    assert dipc.Message.encode_port(handles, 500) == 0
    assert handles == [500, 600]


def test_decode_port():
    assert dipc.Message.decode_port((500, 600), 1) == 600
    assert dipc.Message.decode_port((), dipc.NO_PORT) is None
    with pytest.raises(MessageDecodingError, match="port index 2, but 2 handles"):
        dipc.Message.decode_port((500, 600), 2)


################################################################
# Error replies
################################################################

def test_error_reply_round_trip():
    reply = dipc.ErrorReply(7, 3, dipc.ErrorCode.INTERNAL_ERROR, "it broke: ü")
    buf, handles = reply.encode()
    assert handles == []
    assert dipc.decode_flags(buf) == dipc.ERROR
    assert (dipc.decode_txid(buf), dipc.decode_method(buf)) == (7, 3)
    assert len(buf) % 8 == 0

    decoded = dipc.ErrorReply()
    decoded.decode(buf)
    assert (decoded.txid, decoded.METHOD, decoded.code, decoded.description) == \
        (7, 3, dipc.ErrorCode.INTERNAL_ERROR, "it broke: ü")


def test_error_reply_is_not_a_response():
    # The flag stops an error reply decoding as an ordinary message.
    buf, _ = dipc.ErrorReply(7, 3, dipc.ErrorCode.UNKNOWN_METHOD, "").encode()
    with pytest.raises(MessageDecodingError, match="flags 0x1, expected 0x0"):
        Ping().decode(buf)
    with pytest.raises(MessageDecodingError, match="flags 0x0, expected 0x1"):
        dipc.ErrorReply().decode(Ping(7).encode()[0])


def test_error_reply_truncated():
    buf, _ = dipc.ErrorReply(7, 3, 1, "description").encode()
    with pytest.raises(MessageDecodingError):
        dipc.ErrorReply().decode(buf[:-8])
