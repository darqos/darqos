# darqos
# Copyright (C) 2026 David Arnold

# Common classes shared between application process runtime
# and the p-kernel code.


import struct
from enum import IntEnum

from .errors import MessageDecodingError


# Control exports
__all__ = [
    "Buffer",
    "ClosePortRequest",
    "ClosePortResponse",
    "DeliverChunk",
    "DeliverMessage",
    "Exited",
    "IPC_PORT",
    "KillProcessRequest",
    "KillProcessResponse",
    "Message",
    "MessageType",
    "OpenPortRequest",
    "OpenPortResponse",
    "Reboot",
    "RECV_BUFLEN",
    "RunRequest",
    "RunResponse",
    "SendChunk",
    "SendMessage",
    "Shutdown",
    "SpawnProcessRequest",
    "SpawnProcessResponse",
    "UInt8",
    "UInt16",
    "UInt32",
    "UInt64",
]


IPC_PORT = 11000
RECV_BUFLEN = 65535


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
    RUN_REQUEST = 13
    SPAWN_PROCESS_RQST = 14
    SPAWN_PROCESS_RESP = 15
    KILL_PROCESS_RQST = 16
    KILL_PROCESS_RESP = 17
    EXITED = 18
    RUN_RESPONSE = 19


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

    def get_length(self) -> int:
        """Return the total length of the message in bytes."""
        return self.length

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

    @staticmethod
    def pad_bytes(raw_bytes: bytes) -> bytes:
        pad_len = len(raw_bytes) % 4
        padding = b'' if pad_len == 0 else (4 - pad_len) * b'\0'
        return raw_bytes + padding

    @staticmethod
    def padded_length(raw_bytes: bytes) -> int:
        pad_len = len(raw_bytes) % 4
        return len(raw_bytes) if pad_len == 0 else len(raw_bytes) + 4 - pad_len


########################################################################
# p-Call Messages
########################################################################

class RunRequest(Message):
    """First message from a newly-spawned process, asking to run.

    The process must not start its application until the p-kernel
    accepts it with a RunResponse."""

    def __init__(self, dpid: int = 0):
        super().__init__(MessageType.RUN_REQUEST)
        self.set_length(self.header_length + 16)
        self.dpid: int = dpid
        self.host_pid: int = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">QQ", self.dpid, self.host_pid)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">QQ", buffer[self.header_length:self.length])
        self.dpid = bits[0]
        self.host_pid = bits[1]
        return


class RunResponse(Message):
    """The p-kernel's answer to a RunRequest: 0 means the process may run."""

    def __init__(self, result: int = 0):
        super().__init__(MessageType.RUN_RESPONSE)
        self.set_length(self.header_length + 8)
        self.result: int = result

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">Lxxxx", self.result)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">Lxxxx", buffer[self.header_length:self.length])
        self.result = bits[0]
        return


class SpawnProcessRequest(Message):
    def __init__(self, request_id: int, exec_path: str):
        """Constructor."""
        super().__init__(MessageType.SPAWN_PROCESS_RQST)
        self.request_id = request_id
        self.exec_path = exec_path.encode("utf-8")
        self.set_length(self.header_length + 8 + self.padded_length(self.exec_path))

    def get_exec_path(self) -> str:
        return self.exec_path.decode("utf-8")

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LL", self.request_id, len(self.exec_path))
        buf += self.pad_bytes(self.exec_path)
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


class SpawnProcessResponse(Message):
    def __init__(self, request_id: int, result: int, dpid: int,  error: str|None):
        super().__init__(MessageType.SPAWN_PROCESS_RESP)
        self.request_id = request_id
        self.result = result
        self.dpid = dpid
        self.error = b'' if error is None else error.encode("utf-8")
        self.set_length(self.header_length + 20 + self.padded_length(self.error))

    def get_error(self) -> str:
        return self.error.decode("utf-8")

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LLQL", self.request_id, self.result, self.dpid, len(self.error))
        buf += self.pad_bytes(self.error)
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


class OpenPortRequest(Message):
    """Message to request creation of a new port."""
    def __init__(self, request_id: int = 0, port: int = 0):
        """Request creation of new port.

        :param request_id: Request identifier.
        :param port: Requested port number, or zero for default."""
        super().__init__(MessageType.OPEN_PORT_RQST)
        self.set_length(self.header_length + 16)
        self.request_id: int = UInt32(request_id)
        self.requested_port: int = UInt64(port)

    def encode(self) -> bytes:
        buf = super().encode()
        print(f"request_id = {self.request_id}, requested_port = {self.requested_port}")
        buf += struct.pack(">LxxxxQ", self.request_id, self.requested_port)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">LxxxxQ", buffer[self.header_length:self.length])
        self.request_id = bits[0]
        self.requested_port = bits[1]
        return


class OpenPortResponse(Message):
    """Message to report result of port creation."""
    def __init__(self, request_id: int = 0, result: int = 0, port: int = 0):
        """Report result of port creation.

        :param result: Zero means success, otherwise error code
        :param port: Created port number."""
        super().__init__(MessageType.OPEN_PORT_RESP)
        self.set_length(self.header_length + 16)
        self.request_id: int = UInt32(request_id)
        self.result: int = UInt8(result)
        self.port: int = UInt64(port)
        return

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LBxxxQ", self.request_id, self.result, self.port)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        buffer_tail = buffer[self.header_length:self.length]
        bits = struct.unpack(">LBxxxQ", buffer_tail)
        self.request_id = bits[0]
        self.result = bits[1]
        self.port = bits[2]
        return


class ClosePortRequest(Message):
    def __init__(self, request_id: int = 0, port: int = 0):
        super().__init__(MessageType.CLOSE_PORT_RQST)
        self.set_length(self.header_length + 16)
        self.request_id: int = UInt32(request_id)
        self.port: int = UInt64(port)
        return

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LxxxxQ", self.request_id, self.port)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">LxxxxQ", buffer[self.header_length:self.length])
        self.request_id = bits[0]
        self.port = bits[1]
        return


class KillProcessRequest(Message):
    def __init__(self, request_id: int = 0, dpid: int = 0):
        super().__init__(MessageType.KILL_PROCESS_RQST)
        self.set_length(self.header_length + 16)
        self.request_id: int = UInt32(request_id)
        self.dpid: int = UInt64(dpid)
        return

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LxxxxQ", self.request_id, self.dpid)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">LxxxxQ", buffer[self.header_length:self.length])
        self.request_id = bits[0]
        self.dpid = bits[1]
        return


class KillProcessResponse(Message):
    def __init__(self, request_id: int = 0, result: int = 0):
        super().__init__(MessageType.KILL_PROCESS_RESP)
        self.set_length(self.header_length + 8)
        self.request_id: int = UInt32(request_id)
        self.result: int = UInt32(result)
        return

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LL", self.request_id, self.result)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">LL", buffer[self.header_length:self.length])
        self.request_id = bits[0]
        self.result = bits[1]
        return


class Exited(Message):
    """Report from a process that its application has finished.

    The status is the application's exit status: 0 for success.  The
    error is a description of the failure (such as a traceback), or
    empty if there was none."""

    def __init__(self, status: int = 0, error: str | None = None):
        super().__init__(MessageType.EXITED)
        self.status = status
        self.error = b'' if error is None else error.encode("utf-8")
        self.set_length(self.header_length + 8 + self.padded_length(self.error))

    def get_error(self) -> str | None:
        return self.error.decode("utf-8") if self.error else None

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">lL", self.status, len(self.error))
        buf += self.pad_bytes(self.error)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        error_start = self.header_length + 8
        bits = struct.unpack(">lL", buffer[self.header_length:error_start])
        self.status = bits[0]
        error_length = bits[1]
        if error_start + error_length > self.length:
            raise MessageDecodingError("error string overruns message")
        self.error = buffer[error_start:error_start + error_length]
        return


class ClosePortResponse(Message):
    """Message to report result of closing a port."""
    def __init__(self, request_id: int = 0, result: int = 0, port: int = 0):
        """Report result of closing a port.

        :param result: Zero means success, otherwise error code
        :param port: Closed port number."""
        super().__init__(MessageType.CLOSE_PORT_RESP)
        self.set_length(self.header_length + 16)
        self.request_id: int = UInt32(request_id)
        self.result: int = UInt32(result)
        self.port: int = UInt64(port)

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">LLQ", self.request_id, self.result, self.port)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">LLQ", buffer[self.header_length:self.length])
        self.request_id = bits[0]
        self.result = bits[1]
        self.port = bits[2]
        return


class PortMessage(Message):
    """Common implementation for port-to-port messages."""

    # Length of fixed fields following the header.
    BODY_LENGTH = 24

    def __init__(self, message_type: int, source: int = 0, destination: int = 0):
        super().__init__(message_type)
        self.source: int = UInt64(source)
        self.destination: int = UInt64(destination)
        self.payload = b''
        self.set_length(self.header_length + self.BODY_LENGTH)

    def set_payload(self, payload: bytes):
        self.payload = payload
        self.set_length(self.header_length + self.BODY_LENGTH + self.padded_length(payload))

    def get_length(self) -> int:
        self.set_length(self.header_length + self.BODY_LENGTH + self.padded_length(self.payload))
        return self.length

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">QQLxxxx", self.source, self.destination, len(self.payload))
        buf += self.pad_bytes(self.payload)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        payload_start = self.header_length + self.BODY_LENGTH
        bits = struct.unpack(">QQLxxxx", buffer[self.header_length:payload_start])
        self.source = bits[0]
        self.destination = bits[1]
        payload_length = bits[2]
        self.payload = buffer[payload_start:payload_start + payload_length]
        return


class SendMessage(PortMessage):
    def __init__(self, source: int = 0, destination: int = 0):
        super().__init__(MessageType.SEND_MESSAGE, source, destination)


class DeliverMessage(PortMessage):
    def __init__(self, source: int = 0, destination: int = 0):
        super().__init__(MessageType.DELIVER_MESSAGE, source, destination)


class ChunkMessage(Message):
    """Common implementation for stream chunk messages."""

    # Length of fixed fields following the header.
    BODY_LENGTH = 32

    def __init__(self, message_type: int, source: int = 0, destination: int = 0, offset: int = 0):
        super().__init__(message_type)
        self.source: int = UInt64(source)
        self.destination: int = UInt64(destination)
        self.offset: int = UInt64(offset)
        self.payload = b''
        self.set_length(self.header_length + self.BODY_LENGTH)

    def set_payload(self, payload: bytes):
        self.payload = payload
        self.set_length(self.header_length + self.BODY_LENGTH + self.padded_length(payload))

    def get_length(self) -> int:
        return self.length

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">QQQLxxxx", self.source, self.destination, self.offset, len(self.payload))
        buf += self.pad_bytes(self.payload)
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        payload_start = self.header_length + self.BODY_LENGTH
        bits = struct.unpack(">QQQLxxxx", buffer[self.header_length:payload_start])
        self.source = bits[0]
        self.destination = bits[1]
        self.offset = bits[2]
        payload_length = bits[3]
        self.payload = buffer[payload_start:payload_start + payload_length]
        return


class SendChunk(ChunkMessage):
    def __init__(self, source: int = 0, destination: int = 0, offset: int = 0):
        super().__init__(MessageType.SEND_CHUNK, source, destination, offset)


class DeliverChunk(ChunkMessage):
    def __init__(self, source: int = 0, destination: int = 0, offset: int = 0):
        super().__init__(MessageType.DELIVER_CHUNK, source, destination, offset)


class Reboot(Message):
    def __init__(self):
        super().__init__(MessageType.REBOOT)
        self.set_length(self.header_length)

    def encode(self) -> bytes:
        return super().encode()

    def decode(self, buffer: bytes):
        return super().decode(buffer)


class Shutdown(Message):
    def __init__(self):
        super().__init__(MessageType.SHUTDOWN)
        self.set_length(self.header_length)

    def encode(self) -> bytes:
        return super().encode()

    def decode(self, buffer: bytes):
        return super().decode(buffer)
