# darqos
# Copyright (C) 2026 David Arnold

# Common classes shared between application process runtime
# and the p-kernel code.


import struct
from enum import IntEnum
from multiprocessing.context import SpawnProcess

from .errors import MessageDecodingError


# Control exports
__all__ = [
    "Buffer",

    "Message",
    "MessageType",
    "Running",
    "SpawnRequest",
    "SpawnResponse",
    "UInt8",
    "UInt16",
    "UInt32",
    "UInt64",
]


################################################################
# p-Call Message Codes
################################################################

class MessageType(IntEnum):
    OPEN_PORT_RQST = 1
    OPEN_PORT_RESP = 2
    CLOSE_PORT_RQST = 3
    CLOSE_PORT_RESP = 4
    SEND_MESSAGE = 5
    SEND_CHUNK = 6
    DELIVER_MESSAGE = 7
    DELIVER_CHUNK = 8
    REBOOT = 9
    SHUTDOWN = 10
    GET_PID = 11
    KEEP_ALIVE = 12
    RUNNING = 13
    SPAWN_PROCESS_RQST = 14
    SPAWN_PROCESS_RESP = 15


################################################################
# p-Kernel API -- Base Types
################################################################

class UInt8(int):
    """8-bit unsigned integer field value type."""
    pass


class UInt16(int):
    """16-bit unsigned integer field value type."""
    pass


class UInt32(int):
    """32-bit unsigned integer field value type."""
    pass


class UInt64(int):
    """64-bit unsigned integer field value type."""

    @staticmethod
    def max():
        return (2 ** 64) - 1

    @staticmethod
    def min():
        return 0



class Buffer:
    """Basic byte buffer wrapper."""

    def __init__(self, buffer: bytes = b''):
        """Constructor.

        :param buffer: Optional initial contents."""
        self.buffer = buffer

    def peek(self, length: int) -> bytes:
        """Return a copy of the start of the buffer.

        :param length: Number of bytes to return."""
        return self.buffer[:length]

    def peek_slice(self, offset: int, length: int) -> bytes:
        """Return a copy of a slice from the buffer.

        :param offset: Index of first byte to return
        :param length: Number of bytes to return
        :returns: Copied array of bytes."""
        return self.buffer[offset: offset+length]

    def consume(self, length: int) -> int:
        """Discard the start of the buffer.

        :param length: Number of bytes to remove
        :returns: Number of bytes remaining in buffer."""
        self.buffer = self.buffer[length:]
        return len(self.buffer)

    def append(self, buffer: bytes) -> int:
        """Add more bytes to the end of the buffer.

        :param buffer: Bytes to add
        :returns: Number of bytes now in buffer."""
        self.buffer += buffer
        return len(self.buffer)

    def raw(self) -> bytes:
        """Return reference to the internal assembly buffer.

        :returns: Reference to internal buffer storage."""
        return self.buffer

    def length(self) -> int:
        """Return count of bytes in the buffer.

        :returns: Number of bytes in the buffer."""
        return len(self.buffer)

    def __getitem__(self, n: int) -> int:
        """Return the integer value of the byte of offset.

        :param n: Zero-based offset from start of buffer.
        :returns: Integer value of byte at offset 'n'."""
        return self.buffer[n]


########################################################################

class Message:
    """Common header for all messages to/from the p-kernel."""

    def __init__(self, message_type: int = 0):
        """Constructor."""

        # Message version.
        self.version = UInt8(0)

        # Length of header in bytes.
        self.header_length = UInt8(8)

        # Message type code.
        self.type = UInt8(message_type)

        # Padding.
        self._hpad0 = UInt8(0)

        # Message length, including header, in bytes.
        self.length = UInt32(0)

    def get_header_length(self) -> int:
        """Return length of this message's header."""
        return self.header_length

    def init(self, base: 'Message'):
        """Initialize this message from another.

        :param base: Message to copy."""
        self.version = base.version
        self.header_length = base.header_length
        self.type = base.type
        self._hpad0 = 0
        self.length = base.length

    def set_version(self, version: int):
        """Set the version number header field.

        :param version: Integer version number for message format."""
        self.version = UInt8(version)

    def set_header_length(self, length: int):
        """Set the header length field.

        :param length: Number of bytes in the header."""
        self.header_length = UInt8(length)

    def set_type(self, type_code: int):
        """Set the message type code.

        :param type_code: Integer message type code."""
        self.type = UInt8(type_code)

    def set_length(self, length: int):
        """Set the total message length.

        :param length: Total number of bytes in the message."""
        self.length = UInt32(length)

    def encode(self) -> bytes:
        """Encode message to wire format."""
        buf = struct.pack(">BBBxL",
                          self.version,
                          self.header_length,
                          self.type,
                          self.length)
        return buf

    @staticmethod
    def decode_type(buffer: bytes) -> int:
        """Decode message type code from byte buffer."""
        if len(buffer) < 8:
            return 0

        return buffer[2]

    @staticmethod
    def decode_length(buffer: bytes) -> int:
        """Decode message length from byte buffer."""
        if len(buffer) < 8:
            return 0

        bits = struct.unpack(">xxxxL", buffer)
        return bits[0]

    def decode(self, buffer: bytes):
        """Decode message from byte buffer."""
        if len(buffer) < 8:
            raise MessageDecodingError("buffer too short for header")

        bits = struct.unpack(">BBBxL", buffer[:8])
        self.version = bits[0]
        self.header_length = bits[1]
        self.type = bits[2]
        self.length = bits[3]
        return


########################################################################
# p-Call Messages
########################################################################

class Running(Message):
    def __init__(self, dpid: int = 0):
        super().__init__(MessageType.RUNNING)
        self.set_length(self.header_length + 8)
        self.dpid: UInt64 = dpid

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">Q", self.dpid)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">Q", buffer[self.header_length:self.length])
        self.dpid = bits[0]
        return


class SpawnRequest(Message):
    def __init__(self, request_id: int, exec_path: str):
        """Constructor."""
        super().__init__(MessageType.SPAWN_PROCESS_RQST)
        self.request_id = request_id
        self.exec_path = exec_path

    def get_length(self) -> int:
        self.set_length(self.header_length + 8 + len(self.exec_path))
        return self.length

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LL", self.request_id, len(self.exec_path))
        buf += self.exec_path  # FIXME: padding to 4/8 bytes?
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        exec_path_start = self.header_length + 8
        bits = struct.unpack(">LL", buffer[self.header_length:exec_path_start])
        self.request_id = bits[0]
        exec_path_length = bits[1]
        self.exec_path = buffer[exec_path_start:exec_path_start + exec_path_length]
        return


class SpawnResponse(Message):
    def __init__(self, request_id: int, result: int, dpid: int,  error: str|None):
        super().__init__(MessageType.SPAWN_PROCESS_RESP)
        self.request_id = request_id
        self.result = result
        self.dpid = dpid
        self.error = error

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LLQL", self.request_id, self.result, dpid, len(self.error))
        buf += self.error  # FIXME: padding to 4/8 bytes
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        error_start = self.header_length + 20
        bits = struct.unpack(">LLQL", buffer[self.header_length:error_start])
        self.request_id = bits[0]
        self.result = bits[1]
        self.dpid = bits[2]
        error_length = bits[3]
        self.error = buffer[error_start:error_start + error_length]
        return
