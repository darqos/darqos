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
MSG_CLOSE_PORT_RQST = 3
MSG_CLOSE_PORT_RESP = 4
MSG_SEND_MESSAGE = 5
MSG_SEND_CHUNK = 6
MSG_DELIVER_MESSAGE = 7
MSG_DELIVER_CHUNK = 8
MSG_REBOOT = 9
MSG_SHUTDOWN = 10

IPC_PORT = 11000
RECV_BUFLEN = 65535


########################################################################


class DarqError(Exception):
    """Base class for all Darq exceptions."""
    pass


class MessageDecodingError(DarqError):
    """Failed to decode a message from the supplied buffer."""
    pass


class CannotAllocatePortError(DarqError):
    """The ephemeral port range is exhausted."""
    pass


class DuplicatePortError(DarqError):
    """The requested port number is already in use."""
    pass


class NonExistentPortError(DarqError):
    """The specified port does not exist."""
    pass


class PortNumberOutOfRange(DarqError):
    """The specified port number is out of range."""
    pass


# FIXME
EXCEPTION_MAP: dict[int, DarqError] = {}

def get_exception(error_code:int) -> DarqError:
    return EXCEPTION_MAP.get(error_code, DarqError)

########################################################################

class PendingRequest:
    def __init__(self, request_id: int, cb, request_message):
        self.request_id = request_id
        self.callback = cb
        self.request_message = request_message
        self.response_message = None
        self.completed = False
        self.result = 0

    def is_sync(self):
        return self.callback == None

    def is_complete(self):
        return True if self.completed else False

    def is_success(self):
        return self.result == 0

    def complete(self, result, message):
        self.result = result
        self.completed = True
        self.response_message = message
        return


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
        """Decode message legth from byte buffer."""
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


class OpenPortRequest(Message):
    """Message to request creation of a new port."""
    def __init__(self, request_id: int = 0, port: int = 0):
        """Request creation of new port.

        :param request_id: Request identifier.
        :param port: Requested port number, or zero for default."""
        super().__init__(MSG_OPEN_PORT_RQST)
        self.set_length(self.header_length + 16)
        self.request_id: UInt32 = UInt32(request_id)
        self.requested_port: UInt64 = UInt64(port)

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
        super().__init__(MSG_OPEN_PORT_RESP)
        self.set_length(self.header_length + 16)
        self.request_id: UInt32 = UInt32(request_id)
        self.result: UInt8 = UInt8(result)
        self.port: UInt64 = UInt64(port)
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
        super().__init__(MSG_CLOSE_PORT_RQST)
        self.set_length(self.header_length + 16)
        self.request_id: UInt32 = UInt32(request_id)
        self.port: UInt64 = UInt64(port)
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


class ClosePortResponse(Message):
    def __init__(self):
        super().__init__(MSG_CLOSE_PORT_RESP)
        self.set_length(self.header_length + 16)
        self.request_id: UInt32 = UInt32(0)
        self.port: UInt64 = UInt64(0)

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


class SendMessage(Message):
    def __init__(self, source: int = 0, destination: int = 0):
        super().__init__(MSG_SEND_MESSAGE)
        self.set_length(self.header_length + 24)  # FIXME: doesn't include payload
        self.source: UInt64 = UInt64(source)
        self.destination: UInt64 = UInt64(destination)
        self.payload = b''

    def set_payload(self, payload: bytes):
        self.set_length(self.header_length + 24 + len(payload))
        self.payload = payload

    def get_length(self) -> int:
        self.set_length(self.header_length + 24 + len(self.payload))  # FIXME: padding
        return self.length

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">QQLxxxx", self.source, self.destination, len(self.payload))
        buf += self.payload  # FIXME: padding
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        payload_start = self.header_length + 24
        bits = struct.unpack(">QQLxxxx", buffer[self.header_length:payload_start])
        self.source = bits[0]
        self.destination = bits[1]
        payload_length = bits[2]
        self.payload = buffer[payload_start:payload_start+payload_length]
        return


class DeliverMessage(Message):
    def __init__(self):
        super().__init__(MSG_DELIVER_MESSAGE)
        self.set_length(self.header_length + 24) ## FIXME: no payload
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.payload = b''

    def set_payload(self, payload: bytes):
        self.set_length(self.header_length + 24 + len(payload))
        self.payload = payload

    def get_length(self):
        self.set_length(self.header_length + 24 + len(payload))
        return self.length

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(">QQLxxxx", self.source, self.destination, len(self.payload))
        buf += self.payload  # FIXME: padding
        return buf

    def decode(self, buffer: bytes):
        super().decode(buffer)
        if len(buffer) < self.length:
            raise MessageDecodingError("buffer too short for packet")

        bits = struct.unpack(">QQLxxxx", buffer[self.header_length:self.header_length+24])
        self.source = bits[0]
        self.destination = bits[1]
        payload_length = bits[2]
        payload_start = self.header_length + 24
        self.payload = buffer[payload_start:payload_start + payload_length]
        return


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

    def encode(self) -> bytes:
        return super().encode()

    def decode(self, buffer: bytes):
        return super().decode(buffer)


class Shutdown(Message):
    def __init__(self):
        super().__init__()

    def encode(self) -> bytes:
        return super().encode()

    def decode(self, buffer: bytes):
        return super().decode(buffer)


########################################################################

class EventListener:
    """Interface required for asynchronous use of the p-Kernel API."""

    def on_open_port(self, port: int):
        pass

    def on_close_port(self, port: int):
        pass

    def on_send_message(self, port: int, request_id: int):
        pass

    def on_message(self, source: int, destination: int, message: bytes):
        """Handle a delivered message."""
        pass

    def on_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
        """Handle a delivered stream chunk."""
        pass

    def on_error(self, request: int, error: int, reason: str):
        """Handle a reported communications error."""
        pass


class PortState:
    """Process-side state for an open port."""

    def __init__(self, port_id: int):
        """Constructor.

        :param port_id: Integer port identifier."""

        # Unique identifier for this port.
        self.port_id = port_id

        # True if port is open.
        self.is_open: bool = False

        # Queue of messages received for this port, but not yet
        # retrieved by the application.
        self.messages = []
        return

    def append_bytes(self, buf: bytes):
        """Append received bytes to the reassembly buffer."""
        return self.buffer.append(buf)

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
        self.socket: typing.Optional[socket.socket] = None

        # Set of local ports registered with p-kernel.
        self.ports: dict[int, PortState] = {}

        # Transaction identifiers for p-kernel requests.
        self.request_id: int = 0

        # Pending requests.
        # FIXME: do I want a dedicated type here?
        self.requests: dict[int, PendingRequest] = {}

        # Re-assembly buffer.
        self.recv_buffer = Buffer()

        # Event loop.
        self.loop: typing.Optional[EventLoopInterface] = None
        return

    def get_next_request_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def connect_to_p_kernel(self):
        """Establish the TCP connection to the p-kerenl."""

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.loop.add_socket(self.socket, self)
        self.socket.connect(('localhost', IPC_PORT))
        return

    def on_readable(self, sock: socket.socket):
        print("Callback: socket is readable")

        data = sock.recv(65536)
        self.handle_bytes_from_p_kernel(data)

    def on_writeable(self, sock: socket.socket):
        #print("Callback: socket is writeable")
        pass

    def on_connected(self, sock: socket.socket):
        print("Callback: socket is connected")
        pass

    def send_to_p_kernel(self, message: Message):
        """Send a "syscall" message to the p-Kernel.

        :param message: Message to send."""
        buffer = message.encode()

        # FIXME: in an async world, this should queue and return if it can't
        # write immediately
        self.socket.sendall(buffer)
        return

    def handle_bytes_from_p_kernel(self, buffer: bytes):

        # Append bytes to process-wide reassembly buffer.
        self.recv_buffer.append(buffer)

        while True:
            # Attempt to get message: if message is incomplete, just return.
            if self.recv_buffer.length() < 8:
                return

            header_buf = self.recv_buffer.peek(8)  # FIXME: check version, etc, first.
            message_length = Message.decode_length(header_buf)
            if self.recv_buffer.length() < message_length:
                # Wait for more data to be delivered so we can decode message.
                return

            message_buf = self.recv_buffer.peek(message_length)
            self.recv_buffer.consume(message_length)  # FIXME: make this one operation
            message_type = Message.decode_type(message_buf)

            self.dispatch(message_type, message_buf)

    def dispatch(self, message_type, message_buf):
        if message_type == MSG_DELIVER_MESSAGE:
            message = DeliverMessage()
            message.decode(message_buf)
            self.handle_deliver_message(message)

        elif message_type == MSG_OPEN_PORT_RESP:
            message = OpenPortResponse()
            message.decode(message_buf)
            self.handle_open_port_response(message)

        elif message_type == MSG_CLOSE_PORT_RESP:
            message = ClosePortResponse()
            message.decode(message_buf)
            self.handle_close_port_response(message)

        else:
            logging.warning(f"Unhandled message type: {message_type}")

    def handle_deliver_message(self, message: DeliverMessage):
        # Check destination.
        if message.destination not in self.ports:
            self.listener.on_error(0, 0, "Bad port")
            return

        self.listener.on_message(message.source, message.destination, message.payload)
        return

    def handle_open_port_response(self, message: OpenPortResponse):
        # Look up the request.
        pending_request = self.requests.get(message.request_id)
        if pending_request is None:
            self.listener.on_error(0, 0, "response to unknown request")
            return

        pending_request.complete(message.result, message)
        del self.requests[message.request_id]

        # Check result: if error, discard state.
        if not pending_request.is_success():
            if message.port in self.ports:
                del self.ports[message.port]

            if not pending_request.is_sync():
                # Report error via callback
                pending_request.callback(pending_request.port, message.result)
            return

        # Set or overwrite port state.
        port_state = PortState(message.port)
        port_state.is_open = True
        self.ports[message.port] = port_state

        if not pending_request.is_sync():
            pending_request.callback(message.port, 0)
        return

    def _open_port_request(self, port: int, callback) -> PendingRequest:
        """(Internal)."""
        # Validate requested port.
        if port < UInt64.min() or port > UInt64.max():
            raise PortNumberOutOfRange(port)

        # Check this isn't a duplicate port number (locally).
        if port in self.ports:
            raise DuplicatePortError(port)

        # Reserve this port locally.
        if port != 0:
            self.ports[port] = None  # FIXME

        # If we don't have a p-kernel TCP session already, open one.
        if self.socket is None:
            # FIXME: should be async
            self.connect_to_p_kernel()

        # Send an open_port request to the p-Kernel.
        request_id = self.get_next_request_id()
        request_message = OpenPortRequest(request_id, port)
        pending = PendingRequest(request_id, callback, request_message)
        self.requests[request_id] = pending
        self.send_to_p_kernel(request_message)

        return pending

    def open_port_a(self, port: int, cb: typing.Callable[[int, int], None]):
        self._open_port_request(port, cb)
        return

    def open_port_s(self, port: int) -> int:
        """Allocate a new port for communication from/to this application.

        :param port: Optional requested port number.  Zero means ephemeral port.
        :returns: Allocated port number."""

        # Send the open port request.
        pending_request = self._open_port_request(port, None)

        # Run the event loop until the open port request is completed.
        while not pending_request.is_complete():
            self.loop.next()

        # Check response.
        if pending_request.is_success():
            return pending_request.result
        else:
            raise get_exception(pending_request.result)

    def handle_close_port_response(self, message: ClosePortResponse):

        assert message.port in self.ports

        self.listener.on_close_port(message.port)

        del self.ports[message.port]
        del self.requests[message.request_id]
        return

    def close_port(self, port: int):
        """Close an existing port."""
        port_state = self.ports.get(port)
        if port_state is None:   # FIXME: need a better way to claim HALF_OPEN port numbers
            raise NonExistentPortError(port)

        port_state.is_open = False  # FIXME: need HALF_CLOSED here.

        request_id = self.get_next_request_id()
        request = ClosePortRequest(request_id, port)
        self.requests[request_id] = request
        self.send_to_p_kernel(request)
        return

    def send_message(self, source: int, destination: int, message: bytes):
        """Send a message between ports.

        :param source: Source (local) port.
        :param destination: Destination (remote) port.
        :param message: Payload to be delivered."""

        # Validate source port.
        if source not in self.ports:
            raise NonExistentPortError(source)

        request = SendMessage(source, destination)
        request.set_payload(message)

        self.send_to_p_kernel(request)

        # FIXME: once sending is properly async, this can be (re)moved.
        self.listener.on_send_message(0, 0)  ## FIXME: these params make no sense
        return
