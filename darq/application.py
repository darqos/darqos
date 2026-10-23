# darqos
# Copyright (c) 2026 Daid Arnold
#
# Application support.
#
# Uses darq.common and darq.loop to provide the foundation for
# Darq applicaations.

import logging
import socket
import threading

from .common import *
from .errors import *
from .loop import EventLoopInterface

# Default exported symbols.
__all__ = [
    "EventListener",
    "ProcessRuntimeState",
    "SystemCallState",
]


########################################################################

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

    def get_message(self) -> Message | None:
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
        self.socket: socket.socket | None = None

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
        self.loop: EventLoopInterface|None = None
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
        if message_type == MessageType.DELIVER_MESSAGE:
            message = DeliverMessage()
            message.decode(message_buf)
            self.handle_deliver_message(message)

        elif message_type == MessageType.OPEN_PORT_RESP:
            message = OpenPortResponse()
            message.decode(message_buf)
            self.handle_open_port_response(message)

        elif message_type == MessageType.CLOSE_PORT_RESP:
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


