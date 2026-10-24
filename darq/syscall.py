# DarqOS
# Copyright (C) 2022-2026 David Arnold

# p-Kernel System Calls
#
# How this works:
# - There's a Unix process that provides the facilities to be provided
#   eventually by the Darq kernel.  It's called the Pseudo Kernel, or
#   p-kernel.
# - The p-kernel offers a single TCP endpoint on a well-known TCP port
#   (IPC_PORT).
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
import os
import typing
import uuid

from .application import *
from .common import *
from .errors import *
from .loop import EventLoopInterface


# Default exported symbols.
__all__ = [
    "get_loop",
    "getpid",
    "getppid",
    "init",
    "init_a",
    "init_cb",
    "open_port",
    "open_port_cb",
    "close_port",
    "close_port_cb",
    "send_message",
    "spawn",
    "spawn_cb",
    "shutdown",
    "reboot",
]


########################################################################
# Application process global state.
########################################################################
# Help Python's edit-time type-checking with an annotation on the
# injected name.
#
# This name is injected by pyloader, into darq.syscall.__dict__, as
# part of spawning a new process.  See pyloader.PythonPProcecss.start_app
# for the details.

if typing.TYPE_CHECKING:
    _darq_process_state: "PythonPProcess|None" = None


########################################################################
# Application Support API.
########################################################################
#
# Visible "system call" functions.

def init(event_loop: EventLoopInterface):
    """Initialise the process' runtime state."""
    _darq_process_state.loop = event_loop


def init_a(event_loop: EventLoopInterface):
    _darq_process_state.loop = event_loop


def init_cb(event_loop: EventLoopInterface, listener: EventListener):
    """Initialise the process' runtime state, using the callback model."""
    _darq_process_state.loop = event_loop
    _darq_process_state.listener = listener
    return


def get_loop():
    """Return a reference to the runtime event loop."""
    return _darq_process_state.loop


def open_port(port: int) -> int:
    """Synchronously allocate a new port for communication.

    :param port: Requested port number; Zero requests ephemeral port.
    :returns: Allocated port number."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = OpenPortRequest()
    request.request_id = _darq_process_state.get_next_request_id()
    request.requested_port = UInt64(port)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    # Extract result.
    allocated_port = syscall.get_response().port
    return allocated_port


async def open_port_a(port: int) -> int:
    return await _darq_process_state.open_port_a(port)


    """Synchronously allocate a new port for communication."""
def open_port_cb(port: int, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously allocate new port for communication.

    :param port: Requested port number; Zero requests ephemeral port.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(port, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = OpenPortRequest()
    request.request_id = _darq_process_state.get_next_request_id()
    request.requested_port = UInt64(port)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def close_port(port: int):
    """Close a previously-allocated communication port.

    :param port: Port number to be closed."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = ClosePortRequest()
    request.request_id = _darq_process_state.get_next_request_id()
    request.port = UInt64(port)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()
    return


def close_port_cb(port: int, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously close a previously-allocated communication port.

    :param port: Port number to be closed.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(port, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = ClosePortRequest()
    request.request_id = _darq_process_state.get_next_request_id()
    request.port = UInt64(port)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def send_message(source: int, destination: int, message: bytes):
    """Send a message to another port.

    :param source: Sending port number.
    :param destination: Target port number.
    :param message: Buffer to be sent."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SendMessage(source, destination)
    request.set_payload(message)
    syscall.set_request(request)

    # Enqueue.  There's no response to a SendMessage, so don't wait.
    _darq_process_state.queue_new_syscall(syscall)
    return


def receive_message(port: int, blocking: bool = True) -> bytes:
    """Receive a message from a port."""
    return _darq_process_state.receive_message(port, blocking)


def create_process():
    return _darq_process_state.create_process()


def destroy_process():
    return _darq_process_state.destroy_process()


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

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SpawnRequest(_darq_process_state.get_next_request_id(), exec_path)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    # Extract result.
    dpid = syscall.get_response().dpid
    return dpid


def spawn_cb(exec_path: str, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously spawn a darq process.

    :param exec_path: Path to executable.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(dpid, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = SpawnRequest(_darq_process_state.get_next_request_id(), exec_path)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def getpid() ->  int:
    """Return this process' DarqOS process identifier."""
    return _darq_process_state.getpid()


def getppid() -> int:
    """Return this process' parent's DarqOS process identifier."""
    pass


def shutdown():
    """Shut down the system."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = Shutdown()
    syscall.set_request(request)

    # Enqueue.  There's no response to a Shutdown, so don't wait.
    _darq_process_state.queue_new_syscall(syscall)
    return


def reboot():
    """Restart the system.

    All active services, tools, lens, etc, are shutdown.  Once these
    shutdown processes are complete, the system will re-initialise,
    as if starting afresh.

    M0_NOTE:
    The boot process is managed by a Python function, which
    spawns all DarqOS entities as processes, managed by the boot process.
    A restart will kill all those processes, before spawning a new boot
    process and then exiting."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = Reboot()
    syscall.set_request(request)

    # Enqueue.  There's no response to a Reboot, so don't wait.
    _darq_process_state.queue_new_syscall(syscall)
    return


def get_local_context() -> str:
    """Return the name of the local context."""

    # FIXME: it needs to be persistent, so should come from storage

    global _local_context_id
    context_path = os.path.expanduser("~/.darq-context-id")

    # Return cached value, if set.
    if _local_context_id:
        return _local_context_id

    # Load saved value, if it exists.
    if os.path.exists(context_path):
        f = open(context_path)
        s = f.read()
        f.close()

        _local_context_id = s.strip()
        return _local_context_id

    # Generate a unique context name, save it, and cache it.
    _local_context_id = uuid.uuid4().hex

    f = open(context_path, "w")
    f.write(_local_context_id + "\n")
    f.close()

    return _local_context_id


class Object:
    pass

class ObjectIdentifier:
    pass

class ObjectProxy:
    pass

class Type:
    pass

def create_type(type_id: str, name: str):
    """Create a new Type.

    :param type_id:
    :param name:

    The created Type is transient and inaccessible until registered with
    register_type(), which persists the information, and adds it to the
    runtime type registry."""

    #return Type(type_id, name)
    pass


def register_type(typedef: Type):
    # FIXME: see darq.rt.type.TypeRegistry.register()
    #return _type_api.register(typedef)
    pass

def deregister_type(type_id: str):
    #return _type_api.deregister(type_id)
    pass

def get_type(type_id: str):
    """Look up a Type definition by its UTI.

    :param type_id: Uniform Type Identifier for the requested type."""

    #return _type_api.get(type_id)
    pass

def create_object(type_id: str, is_transient: bool = False) -> ObjectProxy:
    """Create a new object.

    :param type_id: Identifier for the new object's Type, as registered with
    the Type Service.
    :param is_transient: if True, do not persist the Object's state.
    :returns: Proxy for newly-created Object."""

    # Check type_id is legit or error.
    # Have type implementation create new instance.
    # If not transient, persist newly-created instance.
    # Create proxy for instance and return to caller.
    pass


def lookup_object(object_id: typing.Union[str, ObjectIdentifier]) -> ObjectProxy:
    """Obtain a proxy for an existing object.

    If the specified Object identifier is unknown, return an error.

    :object_id: Object's unique identifier."""

    # If object_id is a string, convert it to structured form.

    # Check context
    # If context is unknown, look it up
    # If context is known, but remote, lookup object_id remotely

    # Lookup type_id.  If unknown, raise an error: we can't deal with
    # this type of object.

    # Get endpoint for type implementation from type service.

    # Ask type implementation to activate the object, and return a
    # proxy.
    pass
