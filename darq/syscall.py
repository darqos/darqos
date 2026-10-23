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

from .application import *
from .common import *
from .errors import *
from .loop import EventLoopInterface


# Default exported symbols.
__all__ = [
    "getpid",
    "getppid",
]


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


# One of these is created if you import this module (darq.kernel).  This
# should happen once per-process.

# FIXME: do I need to do more to make this a safe singleton?
_state = ProcessRuntimeState()


########################################################################

# Application Support API.
#
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


async def open_port_a(port: int) -> int:
    return await _state.open_port_a(port)


    """Synchronously allocate a new port for communication."""
def open_port_cb(port: int, cb: typing.Callable[[int, int], None]) -> None:
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


def getpid() ->  int:
    """Return this process' DarqOS process identifier."""
    pass

def getppid() -> int:
    """Return this process' parent's DarqOS process identifier."""
    pass

