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


# Runtime API functions.

def register_event_loop(interface: EventLoopInterface):
    """Register the application's event loop with the runtime.

    :param interface: Event loop implementation to be used by the runtime
    library."""
    pass


def open_port(self, listener: PortListener, port: int = -1) -> int:
    """Allocate a new port for communication from/to this application.

    :param listener: Interface for reporting events on this port.
    :param port: Optional requested port number.
    :returns: Allocated port number."""
    pass


def close_port(self, port: int):
    """Close a previously-allocated communication port.

    :param port: Port number to be closed."""
    pass


def send_message(self, source: int, destination: int, message: bytes):
    """Send a message to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param message: Buffer to be sent."""
    pass


def send_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
    """Send a stream chunk to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param stream: Stream identifier.
    :param offset: Offset from start of stream for first byte of chunk.
    :param message: Buffer to be sent."""
    pass


