# darqos
# Copyright (C) 2022-2023 David Arnold
import typing

# Implementation of the DarqOS kernel interface.
#
# For early editions, the "kernel" is actually implemented as a Linux
# daemon, and processes invoke "system calls" using messages over a TCP
# connection to this pseudo-kernel daemon.  The darq.kernel namespace
# exposes a set of functions that _behave_ like system calls, and whose
# implementation is hidden from application processes.

from .ipc import ProcessRuntimeState, EventListener
from .loop import EventLoopInterface


# Process-wide IPC state.
#
# One of these is created if you import this module (darq.kernel).  This
# should happen once per-process.

# FIXME: do I need to do more to make this a safe singleton?
_state = ProcessRuntimeState()


# Visible "system call" functions.

def init(loop: EventLoopInterface):
    """Initialise the process' runtime state."""
    _state.loop = loop


def init_callbacks(loop: EventLoopInterface, listener: EventListener):
    """Initialise the process' runtime state, using the callback model."""
    _state.loop = loop
    _state.listener = listener
    return


def loop():
    """Return a reference to the runtime event loop."""
    return _state.loop


def open_port(port: int) -> int:
    """Synchronously allocate a new port for communication.

    :param port: Requested port number; Zero requests ephemeral port.
    :returns: Allocated port number."""

    return _state.open_port(port)

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
    return _state.destory_process()


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
