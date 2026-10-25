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
# - Request identifiers are assigned by the I/O thread when it takes a
#   request from the queue, not by the application's (main) thread.
#   - Synchronous calls append a syscall object to a queue, and post a
#     call to the p-process' I/O event loop.  It then encodes the
#     request message and sends it to the p-kernel.
#     - The application thread blocks on a threading.Event
#     - When a response is received from the p-kernel, the I/O thread
#       updates the syscall object with the response data, and sets the
#       event, unblocking the application thread.
#   - Callback calls are queued the same way, but return immediately.
#     When the response is received, the I/O thread posts the callback
#     to the application's event loop, given with the call.
#   - Asynchronous calls also append a syscall object to the queue.
#     - The application thread returns an awaitable loop.Future.
#     - When a response is received from the p-kernel, the I/O thread
#       updates the syscall object with the response data, and signals
#       the Future.  (Not yet implemented.)

import os
import typing
import uuid

from .application import EventListener, SystemCallMode, SystemCallState
from .common import (
    ClosePortRequest,
    KillProcessRequest,
    LookupNameRequest,
    LookupNameResponse,
    NameResult,
    OpenPortRequest,
    OpenPortResponse,
    RegisterNameRequest,
    RegisterNameResponse,
    Reboot,
    SendMessageRequest,
    SendMessageResponse,
    SendResult,
    Shutdown,
    SpawnProcessRequest,
    SpawnProcessResponse,
    UInt64,
)
from .errors import (
    DarqError,
    DeadPortError,
    InvalidNameError,
    NameTakenError,
    NonExistentPortError,
    PortAlreadyNamedError,
    PortRightError,
    ReservedNameError,
)
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
    "receive_message",
    "spawn_process",
    "spawn_process_cb",
    "kill_process",
    "kill_process_cb",
    "register_name",
    "register_name_cb",
    "lookup_name",
    "lookup_name_cb",
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
# part of spawning a new process.  See pyloader.PythonPProcess.load_app
# for the details.

class ProcessState(typing.Protocol):
    """What this module needs from the injected process state."""

    # Event loop, and listener for the callback model (see init_cb).
    loop: EventLoopInterface
    listener: EventListener | None

    def queue_new_syscall(self, syscall_state: SystemCallState) -> None: ...

    def receive_message(self, port: int, blocking: bool) -> tuple[int, bytes] | None: ...

    def getpid(self) -> int: ...

    def getppid(self) -> int: ...


# Placeholder until pyloader injects the real process state.
_darq_process_state: ProcessState = typing.cast(ProcessState, None)


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


def open_port() -> int:
    """Synchronously create a new port for communication.

    The process holds the port's receive right: see doc/ipc.rst.

    :returns: This process' handle for the new port."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = OpenPortRequest()
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    # Extract result.
    allocated_port = typing.cast(OpenPortResponse, syscall.get_response()).port
    return allocated_port


async def open_port_a() -> int:
    # FIXME: the process runtime has no asyncio support yet.
    raise NotImplementedError("open_port_a")


def open_port_cb(loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously create a new port for communication.

    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(handle, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = OpenPortRequest()
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def close_port(port: int):
    """Close a handle.

    Closing a receive right (from open_port) destroys the port; closing
    a send right just gives it up.

    :param port: Handle to be closed."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = ClosePortRequest()
    request.port = UInt64(port)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()
    return


def close_port_cb(port: int, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously close a handle: see close_port().

    :param port: Handle to be closed.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(port, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = ClosePortRequest()
    request.port = UInt64(port)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


# Exceptions for send_message's result codes.
_SEND_ERRORS: dict[int, type[DarqError]] = {
    SendResult.BAD_HANDLE: NonExistentPortError,
    SendResult.NOT_RECEIVE_RIGHT: PortRightError,
    SendResult.DEAD_PORT: DeadPortError,
}


def send_message(source: int, destination: int, message: bytes):
    """Send a message to a port.

    Returns once the p-kernel has delivered the message (or refused
    it).  A message to one of the sender's own ports is already queued
    for receive_message() when this returns.

    :param source: Handle for the sending port: a receive right, where
                   replies will go.
    :param destination: Handle for the destination port.
    :param message: Buffer to be sent.
    :raises NonExistentPortError: if either handle isn't open.
    :raises PortRightError: if the source isn't a receive right.
    :raises DeadPortError: if the destination port has been destroyed."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SendMessageRequest(source, destination)
    request.set_payload(message)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    result = typing.cast(SendMessageResponse, syscall.get_response()).result
    if result != SendResult.OK:
        try:
            reason = SendResult(result).name
        except ValueError:
            reason = f"result {result}"
        raise _SEND_ERRORS.get(result, DarqError)(f"send from {source} to {destination}: {reason}")
    return


def receive_message(port: int, blocking: bool = True) -> tuple[int, bytes] | None:
    """Receive a message delivered to a port.

    Messages are received in the order they were delivered.  This needs
    no request to the p-kernel: delivered messages are queued locally.
    Messages for a process using the callback model (see init_cb) go to
    its listener instead, so they can't be received here.

    :param port: Open port number.
    :param blocking: If True, wait until a message is delivered;
                     otherwise, return None at once if none has been.
    :returns: (source port, payload), or None if not blocking and no
              message has been delivered.
    :raises NonExistentPortError: if the port isn't open, or is closed
                                  while waiting."""
    return _darq_process_state.receive_message(port, blocking)


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


def spawn_process(exec_path: str) -> int:
    """Spawn a darq process.

    :param exec_path: Path to executable.
    :returns: DPID of new process."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SpawnProcessRequest(0, exec_path)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    # Extract result.
    dpid = typing.cast(SpawnProcessResponse, syscall.get_response()).dpid
    return dpid


def spawn_process_cb(exec_path: str, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously spawn a darq process.

    :param exec_path: Path to executable.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(dpid, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = SpawnProcessRequest(0, exec_path)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def kill_process(dpid: int):
    """Kill a darq process.

    :param dpid: DPID of process to be killed."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = KillProcessRequest()
    request.dpid = UInt64(dpid)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()
    return


def kill_process_cb(dpid: int, loop: EventLoopInterface, cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously kill a darq process.

    :param dpid: DPID of process to be killed.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(dpid, error_code)."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = KillProcessRequest()
    request.dpid = UInt64(dpid)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


# Exceptions for the name registry's result codes.
_NAME_ERRORS: dict[int, type[DarqError]] = {
    NameResult.BAD_HANDLE: NonExistentPortError,
    NameResult.INVALID_NAME: InvalidNameError,
    NameResult.NAME_TAKEN: NameTakenError,
    NameResult.RESERVED_NAME: ReservedNameError,
    NameResult.PORT_ALREADY_NAMED: PortAlreadyNamedError,
}


def _name_error(result: int, name: str) -> DarqError:
    """Return the exception for a name registry result code."""
    try:
        reason = NameResult(result).name
    except ValueError:
        reason = f"result {result}"
    return _NAME_ERRORS.get(result, DarqError)(f"{name!r}: {reason}")


def register_name(name: str, port: int) -> None:
    """Register a name for a port, so other processes can look it up.

    A port can have one name, which is removed when the port is
    destroyed.  Names beginning with 'darq.' are reserved for system
    services: see doc/ipc.rst.

    :param name: Name to register: 1 to 255 bytes of UTF-8.
    :param port: Handle for the port's receive right.
    :raises NonExistentPortError: if the handle isn't a receive right.
    :raises InvalidNameError: if the name is empty or too long.
    :raises NameTakenError: if another port has the name.
    :raises ReservedNameError: if the name is reserved.
    :raises PortAlreadyNamedError: if the port already has a name."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)
    syscall.set_request(RegisterNameRequest(0, name, port))

    _darq_process_state.queue_new_syscall(syscall)
    syscall.sync_wait()

    result = typing.cast(RegisterNameResponse, syscall.get_response()).result
    if result != NameResult.OK:
        raise _name_error(result, name)
    return


def register_name_cb(name: str, port: int, loop: EventLoopInterface,
                     cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously register a name for a port: see register_name().

    :param name: Name to register.
    :param port: Handle for the port's receive right.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(port, error_code), with a
               NameResult code."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)
    syscall.set_request(RegisterNameRequest(0, name, port))
    syscall.set_callback(loop, cb)

    _darq_process_state.queue_new_syscall(syscall)
    return


def lookup_name(name: str, blocking: bool = True) -> int | None:
    """Look up a registered name, for a send right to its port.

    :param name: Registered name.
    :param blocking: If True, wait until the name is registered;
                     otherwise, return None at once if it isn't.
    :returns: Handle for a send right to the port (or this process'
              existing handle, if it has one), or None if not blocking
              and the name isn't registered.
    :raises InvalidNameError: if the name is empty or too long."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)
    syscall.set_request(LookupNameRequest(0, name, blocking))

    _darq_process_state.queue_new_syscall(syscall)
    syscall.sync_wait()

    response = typing.cast(LookupNameResponse, syscall.get_response())
    if response.result == NameResult.NOT_FOUND:
        return None
    if response.result != NameResult.OK:
        raise _name_error(response.result, name)
    return response.port


def lookup_name_cb(name: str, blocking: bool, loop: EventLoopInterface,
                   cb: typing.Callable[[int, int], None]) -> None:
    """Asynchronously look up a registered name: see lookup_name().

    :param name: Registered name.
    :param blocking: If True, call back once the name is registered;
                     otherwise, at once.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(handle, error_code), with a
               NameResult code (NOT_FOUND if not blocking, and the name
               isn't registered)."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)
    syscall.set_request(LookupNameRequest(0, name, blocking))
    syscall.set_callback(loop, cb)

    _darq_process_state.queue_new_syscall(syscall)
    return


def getpid() ->  int:
    """Return this process' DarqOS process identifier."""
    return _darq_process_state.getpid()


def getppid() -> int:
    """Return this process' parent's DarqOS process identifier."""
    return _darq_process_state.getppid()


def shutdown():
    """Shut down the system.

    All active services, tools, lens, etc, are shutdown.  Once these
    shutdown processes are complete, the system will exit, and
    power-off the host hardware."""

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
    as if starting afresh."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = Reboot()
    syscall.set_request(request)

    # Enqueue.  There's no response to a Reboot, so don't wait.
    _darq_process_state.queue_new_syscall(syscall)
    return


# Cached name of the local context (see get_local_context).
_local_context_id: str | None = None


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
    raise NotImplementedError("create_object")


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
    raise NotImplementedError("lookup_object")
