# DarqOS
# Copyright (C) 2022-2023 David Arnold

# p-Kernel System Calls
#
# How this will work:
# - There's a central router process that will ultimately be in the kernel
#   of the OS: it's called the pseudo-kernel, or p-kernel.
# - The p-kernel offers a single TCP endpoint on a well-known TCP port.
# - Any process wanting to use Darq IPC establishes a TCP session to the
#   p-kernel's TCP socket.
# - Processes can then request a new port, close an existing port, and
#   send and receive messages to/from those ports.
# - Messages sent to the p-kernel using a simple framing header.
# - LoginRqsuest / LoginReply / AddPort / RemovePort / Message / Chunk
# - Chunk messages are used to deliver stream chunks: their header
#   includes a stream identifier and offset.
# - Messages delivered to a port include the source and destination
#   port numbers, and total message size.
# - Multicast ports are possible; maybe even using the existing AddPort
#   API?

import logging
import socket
import struct
import typing

from .loop import EventLoopInterface
from .types import UInt8, UInt16, UInt32, UInt64


# Message type codes.
# FIXME: make this a proper enum?
MSG_OPEN_PORT_RQST = 1
MSG_OPEN_PORT_RESP = 2
MSG_REMOVE_PORT_RQST = 3
MSG_REMOVE_PORT_RESP = 4
MSG_SEND_MESSAGE = 5
MSG_SEND_CHUNK = 6
MSG_DELIVER_MESSAGE = 7
MSG_DELIVER_CHUNK = 8
MSG_REBOOT = 9
MSG_SHUTDOWN = 10

IPC_PORT = 11000
RECV_BUFLEN = 65535


########################################################################

class MessageDecodingError(Exception):
    """Failed to decode a message from the supplied buffer."""
    pass


class CannotAllocatePortError(Exception):
    """The ephemeral port range is exhausted."""
    pass


########################################################################

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
        """Initialise this message from another.

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


class OpenPortRequest(Message):
    """Message to request creation of a new port."""
    def __init__(self, port: int = 0):
        """Request creation of new port.

        :param port: Requested port number, or zero for default."""
        super().__init__(MSG_OPEN_PORT_RQST)
        self.set_length(8 + 8)
        self.requested_port: UInt64 = UInt64(port)


class OpenPortResponse(Message):
    """Message to report result of port creation."""
    def __init__(self, result: int = 0, port: int = 0):
        """Report result of port creation.

        :param result: Zero means success, otherwise error code
        :param port: Created port number."""
        super().__init__(MSG_OPEN_PORT_RESP)
        self.set_length(8 + 8 + 1)
        self.port: UInt64 = UInt64(port)
        self.result: UInt8 = UInt8(result)


class ClosePortRequest(Message):
    def __init__(self):
        super().__init__()
        self.port: UInt64 = UInt64(0)


class ClosePortResponse(Message):
    def __init__(self):
        super().__init__()
        self.port: UInt64 = UInt64(0)


class SendMessage(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.payload = b''


class DeliverMessage(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.payload = b''


class SendChunk(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.offset: UInt64 = UInt64(0)
        self.payload = b''


class DeliverChunk(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.offset: UInt64 = UInt64(0)
        self.payload = b''


class Reboot(Message):
    def __init__(self):
        super().__init__()


class Shutdown(Message):
    def __init__(self):
        super().__init__()


########################################################################

class Codec:
    """Message encode/decoder."""
    def __init__(self):
        """Constructor."""
        self.registry = {}
        return

    def register(self, message_type: int, klass):
        """Register the class associated with a message type code."""
        self.registry[message_type] = klass

    @staticmethod
    def pad_for_encode(buf: bytes, n: int):
        buf += (n - len(buf) % n) * b'\x00'

    @staticmethod
    def pad_for_decode(offset: int, field_type) -> int:
        """Return additional padding required to append typed value."""
        # Type alignments.
        if field_type in (bool, UInt8):
            alignment = 1
        elif field_type == UInt16:
            alignment = 2
        elif field_type in(bytes, UInt32):
            alignment = 4
        elif field_type == UInt64:
            alignment = 8
        else:
            raise Exception("unknown field type")

        # If current offset % alignment is zero, no padding required.
        # Otherwise, pad to next multiple of alignment.
        if (offset % alignment) == 0:
            return 0

        padding = alignment - (offset % alignment)
        return padding

    def encode(self, message: 'Message') -> bytes:
        """Encode the supplied message, and return a byte buffer."""
        # Accumulated output buffer.
        buf = b''

        for value in message.__dict__.values():
            value_type = type(value)

            if value_type == UInt8:
                buf += value.to_bytes(1, 'big', signed=False)
            elif value_type == UInt16:
                self.pad_for_encode(buf, 2)
                buf += value.to_bytes(2, 'big', signed=False)
            elif value_type == UInt32:
                self.pad_for_encode(buf, 4)
                buf += value.to_bytes(4, 'big', signed=False)
            elif value_type == UInt64:
                self.pad_for_encode(buf, 8)
                buf += value.to_bytes(8, 'big', signed=False)
            elif value_type == bool:
                x = 1 if value else 0
                buf += x.to_bytes(1, 'big', signed=False)
            elif value_type == bytes:
                self.pad_for_encode(buf, 4)
                length = len(value)
                buf += length.to_bytes(4, 'big', signed=False)
                buf += value
            else:
                raise Exception('unhandled value type in message')

        return buf

    def decode(self, buf: Buffer):

        # Get version.
        if buf.length() < 1:
            return None

        version = buf[0]
        if version != 1:
            raise Exception("unhandled protocol version")

        # Version 1
        if buf.length() < 2:
            return None

        header_length = buf[1]
        if buf.length() < header_length:
            return None

        type_code = buf[2]
        message_type = self.registry.get(type_code)
        if message_type is None:
            raise Exception("unhandled message type")

        message = message_type()
        offset = 0
        for name, value in message.__dict__.items():
            ft = type(value)

            if ft == UInt8:
                raw = buf.peek_slice(offset, 1)
                offset += 1
                setattr(message, name, UInt8(raw[0]))
            elif ft == UInt16:
                offset += self.pad_for_decode(offset, ft)
                raw = buf.peek_slice(offset, 2)
                offset += 2
                setattr(message, name, UInt16(struct.unpack('!S', raw)[0]))
            elif ft == UInt32:
                offset += self.pad_for_decode(offset, ft)
                raw = buf.peek_slice(offset, 4)
                offset += 4
                setattr(message, name, UInt32(struct.unpack('!L', raw)[0]))
            elif ft == UInt64:
                offset += self.pad_for_decode(offset, ft)
                raw = buf.peek_slice(offset, 8)
                offset += 8
                setattr(message, name, UInt64(struct.unpack('!Q', raw)[0]))
            elif ft == bool:
                raw = buf.peek_slice(offset, 1)
                offset += 1
                setattr(message, name, raw[0] == 1)
            elif ft == bytes:
                offset += self.pad_for_decode(offset, ft)
                raw_length = buf.peek_slice(offset, 4)
                string_length = struct.unpack('!L', raw_length)[0]
                offset += 4
                raw_string = buf.peek_slice(offset, string_length)
                setattr(message, name, raw_string)
                offset += string_length
            else:
                raise Exception('unhandled value type in message')

        buf.consume(offset)
        return message

    def encode_uint8(self, value: int, buf: memoryview):
        buf[0] = value
        return 1

    def encode_uint16(self, value: int, buf: memoryview):
        buf[0:2] = value.to_bytes(2, byteorder='big', signed=False)
        return 2

    def encode_uint32(self, value: int, buf: memoryview) -> int:
        buf[0:4] = value.to_bytes(4, byteorder='big', signed=False)
        return 4

    def encode_uint64(self, value: int, buf: memoryview) -> int:
        buf[0] = value.to_bytes(8, byteorder='big', signed=False]
        return 8

    def encode_bool(self, value: bool, buf: memoryview) -> int:
        buf[0] = 1 if value else 0
        return 1

    def encode_header(self, message: Message, buf: memoryview):
        buf[0] = 1  # version
        buf[1] = 8  # header length
        buf[2] = message.type
        buf[3] = 0  # always zero
        buf[4:8] = message.length.to_bytes(4, byteorder='big', signed=False)
        return 8

    def encode_open_port_request(self, message: OpenPortRequest) -> bytes:
        buf = bytearray(16)
        offset = self.encode_header(message, memoryview(buf)
        self.encode_uint64(message.requested_port, memoryview(buf)[offset:])
        return buf

########################################################################

class PortListener:
    """Interface required for applications to use the p-kernel IPC."""

    def on_message(self, source: int, destination: int, message: bytes):
        """Handle a delivered message."""
        pass

    def on_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
        """Handle a delivered stream chunk."""
        pass

    def on_error(self, port: int, error: int, reason: str):
        """Handle a reported communications error."""
        pass

class PortState:
    """Process-side state for an open port."""
    def __init__(self, port_id: int):
        """Constructor.

        :param port_id: Integer port identifier."""

        # Unique identifier for this port.
        self.port_id = port_id

        self.is_open: bool = False

        # Reassembly buffer.
        self.buffer = Buffer()

        # Queue of messages received for this port, but not yet
        # retrieved by the application.
        self.messages = []
        return

    def append_bytes(self, buf: bytes):
        """Append received bytes to the reassembly buffer."""
        return

    def get_message(self) -> typing.Optional[Message]:
        """Return a queued, received Message, if one is available.

        :returns: a Message, or None if none is available."""
        return


class ProcessRuntimeState:
    """The process-side state for the p-Kernel.

    This class maintains all the process-side state of the p-Kernel
    interface.  The "system calls" provided by darq.kernel use this
    state, plus interactions with the remote p-Kernel, to implement
    their functionality."""

    def __init__(self):
        # Socket connected to p-kernel message router.
        self.socket = None

        # Set of local ports registered with p-kernel.
        self.ports = {}

        # Transaction identifiers for p-kernel requests.
        self.request_id = 0

        # Re-assembly buffer.
        self.recv_buffer = Buffer()

        # Protocol encoding.
        self.codec = Codec()

    def connect_to_p_kernel(self) -> int:
        """Establish the TCP connection to the p-kerenl."""

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.connect(('localhost', IPC_PORT))
        return 0

    def send_to_p_kernel(self, message):
        buffer = self.codec.encode(message)
        self.socket.sendall(buffer)
        return

    def handle_bytes_from_p_kernel(self, buffer: bytes):
        pass

    def run_until_response_from_p_kernel(self):
        pass

    def open_port(self, listener: PortListener, port: int = -1) -> int:
        """Allocate a new port for communication from/to this application.

        :param listener: Interface for reporting events on this port.
        :param port: Optional requested port number.
        :returns: Allocated port number."""

        # FIXME: how should an error be reported?  Exception?

        # FIXME: validate requested port number

        # If we don't have a p-kernel TCP session already, open one.
        if self.socket is None:
            if result := self.connect_to_p_kernel():
                return result

        # Claim this port locally.
        if port != 0:
            self.ports[port] = PortState(port)

        # Send an open_port request to the p-Kernel.
        request = OpenPortRequest(port)
        self.send_to_p_kernel(request)

        # FIXME: what I *want* to do here is enter a nested event loop instance, blocking until we get a reply.
        # FIXME: that'd need to work with Qt (for apps) and the service event loop (which we control).
        # FIXME: is that possible with Qt?  Can we just call _exec() again?  Looks like yes: just create a QEventLoop.

        # Wait for the response: ack or error.
        message = _state.receive_from_port()
        assert (message.type == MSG_OPEN_PORT_RESP)
        response: OpenPortResponse = message

        # FIXME: check errors
        if response.result != 0:
            return response.result

        # FIXME: create local port state
        _state.ports[response.port] = None

        # FIXME: return port_id
        return response.port

    def receive_from_port(self, port: int) -> Message:
        # Service a port, waiting for a reply, and delivering Messages
        # and Chunks to their appropriate port queues.

        )
        # FIXME: need a switch here, because we could get deliveries.
        # FIXME: handle C-c
        # FIXME: handle zero-length recv
        recv_buf = _state.socket.recv(RECV_BUFLEN)
        _state.recv_buffer.append(recv_buf)

        response = _state.codec.decode(_state.recv_buffer)

        return response


