# DarqOS
# Copyright (C) 2022-2026 David Arnold

# p-Kernel System Calls
#
# How this works:
# - There's a Unix process that provides the facilities to be provided
#   eventually  by the Darq kernel.  It's called the Pseudo Kernel, or
#   p-kernel.
# - The p-kernel offers a single TCP endpoint on a well-known TCP port.
# - When the p-kernel spawns an emulated Darq process (a pseudo-process,
#   p-process, or pproc), it (the p-kernel) accepts a TCP session from
#   the newly-spawned process, initiated by the language-specific runtime,
# - Darq system calls are implemented in the p-kernel and p-processes by
#   passing a message to the p-kernel via this TCP socket.  The system
#   calls are exposed to application code via the darq namespace.
# - Messages sent to the p-kernel using a simple framing header, a
#   message header, and a basic binary structure for the syscall
#   parameters.
# - System calls can have synchronous and asynchronous variants.
#   - Synchronous calls append a syscall object to a queue, and set an
#     eventfd to wakeup the p-process' runtime event loop.  It then
#     encodes the request messages and sends it to the p-kernel.
#     - The application thread blocks an a threading.Event
#     - When a response is received from the p-kernel, the runtime
#       thread updates the syscall object with the response data, and
#       sets the event, unblocking the application thread.
#   - Asynchronous calls also append a syscall object to a queue, and
#     set an eventfd to wakeup the p-process' runtime event loop.  It
#     then encodes the request message and sends it to the p-kernel.
#     - The application thread returns an awaitable loop.Future.
#     - When a response is received from the p-kernel, the runtime
#     thread updates the syscall object with the response data, and
#     signals the Future.

import logging
import socket
import struct
import threading
import typing

from .errors import *
from .loop import EventLoopInterface
from .common import Buffer, UInt8, UInt16, UInt32, UInt64, Message

IPC_PORT = 11000
RECV_BUFLEN = 65535

# p-call type codes.
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
MSG_GET_PID = 11
MSG_KEEP_ALIVE = 12
MSG_RUNNING = 13


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


class SystemCallState:
    """State of a p-kernel request."""

    def __init__(self, loop = None):

        # Flag async syscall.
        self._async = False

        # Syscall number.
        self._type = 0

        # Request message structure containing parameters.
        self._request = None

        # Response message structure containing results.
        self._response = None

        # Event on which a synchronous call will wait for a response.
        self._sync_event = threading.Event()

        # Future to be signaled when asynchronous call has a response.
        if loop:
            self._async_future = loop.Future(loop=loop)

    def is_sync(self):
        return not self._async

    def is_complete(self):
        return self._response is not None

    def is_success(self):
        return False # FIXME

    def complete(self, result, message):
        self.result = result
        self.completed = True
        self.response_message = message
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


# Process-wide IPC state.
#
# One of these is created if you import this module (darq.kernel).  This
# should happen once per-process.

# FIXME: do I need to do more to make this a safe singleton?
_state = ProcessRuntimeState()


# Visible "system call" functions.

def init(event_loop: EventLoopInterface):
    """Initialise the process' runtime state."""
    _state.loop = event_loop


def init_callbacks(event_loop: EventLoopInterface, listener: EventListener):
    """Initialise the process' runtime state, using the callback model."""
    _state.loop = event_loop
    _state.listener = listener
    return


def loop():
    """Return a reference to the runtime event loop."""
    return _state.loop


def open_port(port: int) -> int:
    """Synchronously allocate a new port for communication.

    :param port: Requested port number; Zero requests ephemeral port.
    :returns: Allocated port number."""

    return _state.open_port_s(port)


def open_port_a(port: int, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously allocate new port for communication.

    :param port: Requested port number; Zero requests ephemeral port.
    :param cb: Completion callback: cb(port, error_code)."""
    pass


def close_port(port: int):
    """Close a previously-allocated communication port.

    :param port: Port number to be closed."""

    return _state.close_port(port)


def send_message(source: int, destination: int, message: bytes):
    """Send a message to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param message: Buffer to be sent."""
    return _state.send_message(source, destination, message)


def receive_message(port: int, blocking: bool = True) -> bytes:
    """Receive a message from a port."""
    return _state.receive_message(port, blocking)


def create_process():
    return _state.create_process()


def destroy_process():
    return _state.destroy_process()


def register_process(self, pid):
    """Register an externally-created process.

    For now, all darq processes are actually Unix processes, and in
    some cases, they're created outside of darq's 'system calls'.
    In order to enable darq to manage these processes, they need to
    be registered, using this function."""

    # FIXME!
    pass


# def send_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
#     """Send a stream chunk to another port.
#
#     :param source: Sending port number.
#     :param destination: Target port number.
#     :param stream: Stream identifier.
#     :param offset: Offset from start of stream for first byte of chunk.
#     :param message: Buffer to be sent."""
#
#     # Basically same as message, but extra chunky.
#     pass


def spawn(exec_path: str) -> int:
    """Spawn a darq process.

    :param exec_path: Path to executable.
    :returns: DPID of new process."""

    # Create syscall state.
    call_state = SystemCallState()

    # Append to queue.
    # Wake up event loop.
    # Wait for response.
    call_state._sync_event.wait()

    return


def get_dpid() ->  int:
    """Return this process' DarqOS process identifier."""
