# DarqOS
# Copyright (C) 2022 David Arnold

# How this will work:
#
# - There's a central router process that will ultimately be in the kernel
#   of the OS: it's called the pseudo-kernel, or p-kernel.
# - The p-kernel offers a single TCP endpoint on a well-known TCP port.
# - Any process wanting to use IPC establishes a TCP session to the
#   p-kernel's TCP port.
# - Clients can then request a new port, close an existing port, and
#   send and receive messages to/from those ports.
# - Messages sent to the p-kernel using a simple framing header.
# - LoginRqsuest / LoginReply / AddPort / RemovePort / Message / Chunk
# - Chunk messages are used to deliver stream chunks: their header
#   includes a stream identifier and offset.
# - Messages delivered to a port include the source and destination
#   port numbers, and total message size.
# - Multicast ports are possible; maybe even using the existing AddPort
#   API?

import select
import socket
import struct
import time
import typing

from .types import UInt8, UInt16, UInt32, UInt64


class MessageDecodingError(Exception):
    """Failed to decode a message from the supplied buffer."""
    pass


class CannotAllocatePortError(Exception):
    """The ephemeral port range is exhausted."""
    pass


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
        return self.buffer[offset: offset+length]

    def consume(self, length: int) -> int:
        """Remove the start of the buffer.

        :param length: Number of bytes to remove."""
        self.buffer = self.buffer[length:]
        return len(self.buffer)

    def append(self, buffer: bytes) -> int:
        """Add more bytes to the end of the buffer.

        :param buffer: Bytes to add."""
        self.buffer += buffer
        return len(self.buffer)

    def raw(self) -> bytes:
        """Return reference to the internal assembly buffer."""
        return self.buffer

    def length(self) -> int:
        """Return count of bytes in the buffer."""
        return len(self.buffer)

    def __getitem__(self, n: int) -> int:
        """Return the integer value of the byte of offset.

        :param n: Zero-based offset from start of buffer.
        :returns: Integer value of byte at offset 'n'."""
        return self.buffer[n]


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



class EventLoopInterface:
    """Abstraction of event loop to work across uvloop for CLI and Qt
    for GUI applications."""

    def add_socket(self, sock, listener):
        pass

    def cancel_socket(self, sock):
        pass

    def add_timer(self, duration, listener) -> int:
        pass

    def cancel_timer(self, timer_id: int):
        pass

    def run(self):
        pass

    def stop(self):
        pass


class SelectTimerState:
    """Timer state for select-based event loop."""

    def __init__(self, duration, listener):
        self.duration = duration
        self.listener = listener
        self.expiry = 0


class SelectEventLoop(EventLoopInterface):
    """A simple select-based event loop."""

    def __init__(self):
        """Constructor."""
        self.sockets: typing.Dict[int, socket] = {}
        self.timers: typing.Dict[int, SelectTimerState] = {}
        self.active: bool = False

    def add_socket(self, sock, listener):
        self.sockets[sock] = listener

    def cancel_socket(self, sock):
        del self.sockets[sock]
        pass

    def add_timer(self, duration, listener) -> int:
        new_id = len(self.timers)
        self.timers[new_id] = SelectTimerState(time.time() + duration, listener)
        # FIXME
        pass

    def cancel_timer(self, timer_id: int):
        pass

    def run(self):
        self.active = True

        while self.active:
            rr, rw, _ = select.select(self.sockets, self.sockets, [], 0)

            for s in rr:
                listener = self.sockets[s]
                listener(s)

            for s in rw:
                listener = self.sockets[s]
                listener(s)

            for t in self.timers.values():
                now = time.time()
                if t.expiry < now:
                    t.expiry += t.duration
                    t.listener()

    def stop(self):
        self.active = False
        pass


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


class IpcRuntime:
    def __init__(self):
        # Socket connected to p-kernel message router.
        self.socket = None

        # Set of local ports registered with p-kernel.
        self.ports = {}

        # Transaction identifiers for p-kernel requests.
        self.request_id = 0

        # Re-assembly buffer.
        self.recv_buffer = Buffer()


# Process-wide IPC state.
_state = IpcRuntime()
IPC_PORT = 11000
RECV_BUFLEN = 65535


def _block_and_dispatch():
    """(Internal)

    This function should run the event loop until
    something happens, and then deal with things that happen,
    until we hit an exit condition, at which time it should exit
    the event loop.

    It's possible that while waiting for a reply to an RPC, a
    delivered message or chunk might arrive.  If they do, they
    should be dispatched.  While processing the arrived message
    of course a further RPC might be made"""
    pass

# Runtime API functions.

def register_event_loop(interface: EventLoopInterface):
    """Register the application's event loop with the IPC runtime.

    :param interface: Event loop implementation to be used by the runtime
    library."""
    pass


def open_port(listener: PortListener, port: int = -1) -> int:
    """Allocate a new port for communication from/to this application.

    :param listener: Interface for reporting events on this port.
    :param port: Optional requested port number.
    :returns: Allocated port number."""

    # FIXME: how should an error be reported?  Exception?

    # If we don't have a TCP session already, open one.
    if _state.socket is None:
        _state.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _state.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # FIXME: this should probably be non-blocking
        _state.socket.connect(('localhost', IPC_PORT))

        _state.socket.setblocking(False)

    # Send an open_port request.
    request = OpenPortRequest()
    buffer = request.encode()
    _state.socket.send(buffer)

    # Wait for the response: ack or error.
    # FIXME: need a switch here, because we could get deliveries.
    # FIXME: handle C-c
    # FIXME: handle zero-length recv
    recv_buf = _state.socket.recv(RECV_BUFLEN)
    _state.recv_buffer.append(recv_buf)

    response = OpenPortResponse.decode(_state.recv_buffer)
    _state.ports[response.port] = listener

    # Save state, and return.
    return response.port


def close_port(port: int):
    """Close a previously-allocated communication port.

    :param port: Port number to be closed."""

    # Send close_port request.
    # Wait for response.
    # Save state and return.
    pass


def send_message(source: int, destination: int, message: bytes):
    """Send a message to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param message: Buffer to be sent."""

    # Check source port matches one we've opened.
    # Send send_message request.
    # return
    pass


def send_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
    """Send a stream chunk to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param stream: Stream identifier.
    :param offset: Offset from start of stream for first byte of chunk.
    :param message: Buffer to be sent."""

    # Basically same as message, but extra chunky.
    pass


