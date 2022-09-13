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
import dataclasses
import select
import socket
import struct
import sys
import time
import typing


# Message type codes.
MSG_OPEN_PORT_RQST = 1
MSG_OPEN_PORT_RESP = 2
MSG_REMOVE_PORT_RQST = 3
MSG_REMOVE_PORT_RESP = 4
MSG_SEND_MESSAGE = 5
MSG_SEND_CHUNK = 6
MSG_DELIVER_MESSAGE = 7
MSG_DELIVER_CHUNK = 8


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


class Message:
    """Common header for all messages to/from the Message Service."""

    # Header format.
    FORMAT = '!LBBxx'

    # Header length (in bytes).
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        """Constructor."""
        self.length = 0
        self.version = 0
        self.type = 0

    def encode(self) -> bytes:
        """Return a byte buffer encoding this message header."""
        buf = struct.pack(Message.FORMAT,
                          self.length,
                          self.version,
                          self.type)
        return buf

    @staticmethod
    def peek(buf: Buffer) -> typing.Optional['Message']:
        """Peek at the message header.

        :param buf: Byte buffer to decode the header from."""

        if buf.length() < Message.LENGTH:
            return None

        try:
            header = buf.peek(Message.LENGTH)
            bits = struct.unpack(Message.FORMAT, header)

            msg = Message()
            msg.length = bits[0]
            msg.version = bits[1]
            msg.type = bits[2]
            return msg

        except:
            return None

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['Message']:
        msg = Message.peek(buf)
        if msg:
            buf.consume(Message.LENGTH)
        return msg

    def init(self, base: 'Message'):
        self.length = base.length
        self.version = base.version
        self.type = base.type


class OpenPortRequest(Message):

    FORMAT = '!L'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.requested_port: int = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(OpenPortRequest.FORMAT, self.requested_port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['OpenPortRequest']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = OpenPortRequest()
        msg.init(base)
        try:
            bits = struct.unpack(OpenPortRequest.FORMAT,
                                 buf.peek(OpenPortRequest.LENGTH))
            msg.requested_port = bits[0]
            buf.consume(OpenPortRequest.LENGTH)
            return msg

        except:
            return None


class OpenPortResponse(Message):

    FORMAT = '!?L'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.result: bool = False
        self.port: int = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(OpenPortResponse.FORMAT, self.result, self.port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['OpenPortResponse']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = OpenPortResponse()
        super(msg).init(base)
        try:
            bits = struct.unpack(OpenPortResponse.FORMAT,
                                 buf.peek(OpenPortResponse.LENGTH))

            msg.result = bits[0]
            msg.port = bits[1]

            buf.consume(OpenPortResponse.LENGTH)
            return msg

        except:
            return None


class ClosePortRequest(Message):
    FORMAT = '!L'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.port: int = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(ClosePortRequest.FORMAT, self.port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['ClosePortRequest']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = ClosePortRequest()
        super(msg).init(base)
        try:
            bits = struct.unpack(ClosePortRequest.FORMAT,
                                 buf.peek(ClosePortRequest.LENGTH))
            msg.port = bits[0]

            buf.consume(ClosePortRequest.LENGTH)
            return msg

        except:
            return None


class ClosePortResponse(Message):
    FORMAT = '!?L'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.port: int = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(ClosePortResponse.FORMAT, self.port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['ClosePortResponse']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = ClosePortResponse()
        super(msg).init(base)
        try:
            bits = struct.unpack(ClosePortResponse.FORMAT,
                                 buf.peek(ClosePortResponse.LENGTH))
            msg.port = bits[0]

            buf.consume(ClosePortResponse.LENGTH)
            return msg

        except:
            return None


class SendMessage(Message):

    FORMAT = '!QQL'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.source = 0
        self.destination = 0
        self.payload_length = 0
        self.payload = b''

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(SendMessage.FORMAT,
                           self.source,
                           self.destination,
                           len(self.payload))
        buf += self.payload
        buf += '\0' * (4 - (len(self.payload) % 4))
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['SendMessage']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = SendMessage()
        msg.init(base)
        try:
            bits = struct.unpack(SendMessage.FORMAT,
                                 buf.peek(SendMessage.LENGTH))
            msg.source = bits[0]
            msg.destination = bits[1]
            msg.payload_length = bits[2]
            buf.consume(SendMessage.LENGTH)
            msg.payload = buf.peek(msg.payload_length)
            buf.consume(msg.payload_length)
            return msg

        except:
            return None


class DeliverMessage(Message):

    FORMAT = '!QQL'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.source = 0
        self.destination = 0
        self.payload_length = 0
        self.payload = b''

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(DeliverMessage.FORMAT,
                           self.source,
                           self.destination,
                           len(self.payload))
        buf += self.payload
        buf += b'\0' * (4 - (len(self.payload) % 4))
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['DeliverMessage']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = DeliverMessage()
        msg.init(base)
        try:
            bits = struct.unpack(DeliverMessage.FORMAT,
                                 buf.peek(DeliverMessage.LENGTH))
            msg.source = bits[0]
            msg.destination = bits[1]
            msg.payload_length = bits[2]
            buf.consume(SendMessage.LENGTH)
            msg.payload = buf.peek(msg.payload_length)
            buf.consume(msg.payload_length)
            return msg

        except:
            return None


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
    def __init__(self, duration, listener):
        self.duration = duration
        self.listener = listener
        self.expiry = 0


class SelectEventLoop(EventLoopInterface):

    def __init__(self):
        self.sockets = {}
        self.timers = {}
        self.active = False

    def add_socket(self, sock, listener):
        self.sockets[sock] = listener

    def cancel_socket(self, sock):
        del self.sockets[sock]
        pass

    def add_timer(self, duration, listener) -> int:
        new_id = len(self.timers)
        self.timers[new_id] = SelectTimerState(time.now() + duration, listener)
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


