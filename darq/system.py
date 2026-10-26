# DarqOS
# Copyright (C) 2022-2026 David Arnold

# p-Kernel System Calls, and Requests and Responses over Ports
#
# How the system calls work:
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
#
# How requests and responses work (see doc/ipc.rst):
# - A client calls a service with call(), or with the Client class
#   that didlpygen generates for each protocol.  A service answers
#   with serve(), and a subclass of the protocol's generated Handler.
# - Each thread has a reply port, opened on its first call: requests
#   are sent from it, so the service's replies come back to it.
# - Each request gets the next transaction identifier (txid), skipping
#   0, which means one-way.  A reply is the message on the reply port
#   from the service's handle, with the request's txid: anything else
#   is a late reply to an abandoned call, and is dropped.
# - A service answers a request it can't handle with an error reply
#   (see darq.dipc.ErrorReply), raised in the client as RemoteError.
#   One-way messages are never answered, even with an error.
# - Services handle requests one at a time, in order of delivery, and
#   run until they're killed.


import logging
import os
import threading
import time
import typing
import uuid

from . import dipc
from .dipc import ErrorCode, ErrorReply
from .process import EventListener, SystemCallMode, SystemCallState
from .pcall import (
    BootResult,
    ClosePortRequest,
    ExitReason,
    GetBootValueRequest,
    GetBootValueResponse,
    KillProcessRequest,
    MAX_BOOT_VALUE_LENGTH,
    MAX_BOOT_VALUES,
    MAX_HANDLES,
    MAX_NAME_LENGTH,
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
    SetBootValueRequest,
    SetBootValueResponse,
    Shutdown,
    SpawnProcessRequest,
    SpawnProcessResponse,
    UInt64,
    WaitRequest,
    WaitResponse,
    WaitResult,
)
from .errors import (
    BootVectorFullError,
    DarqError,
    DeadPortError,
    InvalidNameError,
    MessageDecodingError,
    NameTakenError,
    NoSuchChildError,
    NonExistentPortError,
    NotPermittedError,
    PortAlreadyNamedError,
    PortRightError,
    RemoteError,
    ReservedNameError,
    TooManyHandlesError,
    ValueTooLargeError,
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
    "exit",
    "wait",
    "ChildStatus",
    "register_name",
    "register_name_cb",
    "lookup_name",
    "lookup_name_cb",
    "get_boot_value",
    "set_boot_value",
    "shutdown",
    "reboot",
    "ExitReason",
    "MAX_HANDLES",
    "MAX_NAME_LENGTH",
    "MAX_BOOT_VALUES",
    "MAX_BOOT_VALUE_LENGTH",
    "Client",
    "Handler",
    "call",
    "connect",
    "send",
    "serve",
    "transact",
]


########################################################################
# Application process global state.
########################################################################
# Help Python's edit-time type-checking with an annotation on the
# injected name.
#
# This name is injected by pyloader, into darq.system.__dict__, as
# part of spawning a new process.  See pyloader.PythonPProcess.load_app
# for the details.

class ProcessState(typing.Protocol):
    """What this module needs from the injected process state."""

    # Event loop, and listener for the callback model (see init_cb).
    loop: EventLoopInterface
    listener: EventListener | None

    def queue_new_syscall(self, syscall_state: SystemCallState) -> None: ...

    def receive_message(self, port: int, blocking: bool,
                        timeout: float | None) -> tuple[int, bytes, tuple[int, ...]] | None: ...

    def exit(self, status: int) -> typing.NoReturn: ...

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
    SendResult.TOO_MANY_HANDLES: TooManyHandlesError,
}


def send_message(source: int, destination: int, message: bytes,
                 ports: typing.Sequence[int] = ()):
    """Send a message to a port.

    Returns once the p-kernel has delivered the message (or refused
    it).  A message to one of the sender's own ports is already queued
    for receive_message() when this returns.

    :param source: Handle for the sending port: a receive right, where
                   replies will go.
    :param destination: Handle for the destination port.
    :param message: Buffer to be sent.
    :param ports: Handles to transfer with the message: the receiver
                  gets a send right to each (see doc/ipc.rst).
    :raises NonExistentPortError: if any handle isn't open.
    :raises PortRightError: if the source isn't a receive right.
    :raises DeadPortError: if the destination port, or a transferred
                           port, has been destroyed.
    :raises TooManyHandlesError: if there are more than MAX_HANDLES
                                 ports."""
    if len(ports) > MAX_HANDLES:
        raise TooManyHandlesError(f"{len(ports)} ports, at most {MAX_HANDLES}")

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SendMessageRequest(source, destination)
    request.set_payload(message)
    request.set_handles(tuple(ports))
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


def receive_message(port: int, blocking: bool = True,
                    timeout: float | None = None) -> tuple[int, bytes, tuple[int, ...]] | None:
    """Receive a message delivered to a port.

    Messages are received in the order they were delivered.  This needs
    no request to the p-kernel: delivered messages are queued locally.
    Messages for a process using the callback model (see init_cb) go to
    its listener instead, so they can't be received here.

    :param port: Open port number.
    :param blocking: If True, wait until a message is delivered;
                     otherwise, return None at once if none has been.
    :param timeout: If blocking, the longest to wait, in seconds, before
                    returning None; None waits indefinitely.
    :returns: (source handle, payload, transferred handles), or None if
              no message was delivered, when not blocking or by the
              timeout.
    :raises NonExistentPortError: if the port isn't open, or is closed
                                  while waiting."""
    return _darq_process_state.receive_message(port, blocking, timeout)


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


def exit(status: int = 0) -> typing.NoReturn:
    """End the application, with an exit status.

    The application's main thread is ended by raising SystemExit in it,
    so its finally blocks and context managers run.  This can be called
    from any of the application's threads.  The status is reported to the
    p-kernel (and collected by the parent's wait()), and becomes the host
    process' exit status, if it's from 0 to 255 (or 1, otherwise).

    :param status: Exit status: zero for success.
    :raises ValueError: if the status isn't a 32 bit signed integer."""
    _darq_process_state.exit(status)


class ChildStatus(typing.NamedTuple):
    """How a child process finished, as collected by wait()."""

    # The child's dpid.
    dpid: int

    # Whether it exited, was killed, or was lost.
    reason: ExitReason

    # Its application's exit status, if it EXITED; otherwise, -1.
    status: int

    # Description of its application's failure (such as a traceback), if any.
    error: str | None


def wait(dpid: int = 0, blocking: bool = True) -> ChildStatus | None:
    """Collect a finished child process.

    A finished child (one that exited, was killed, or was lost) stays in
    the p-kernel's process table until its parent collects it.

    :param dpid: Child to wait for, or zero for any child (the earliest
                 spawned, if several have finished).
    :param blocking: If True, wait until a matching child finishes;
                     otherwise, return None at once if none has.
    :returns: How the child finished, or None if not blocking, and no
              matching child has finished.
    :raises NoSuchChildError: if the process isn't a child of this one,
                              or (for any child) there are none."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)
    syscall.set_request(WaitRequest(0, dpid, blocking))

    _darq_process_state.queue_new_syscall(syscall)
    syscall.sync_wait()

    response = typing.cast(WaitResponse, syscall.get_response())
    if response.result == WaitResult.NOT_FINISHED:
        return None
    if response.result == WaitResult.NO_SUCH_CHILD:
        raise NoSuchChildError(f"no such child process: {dpid or 'any'}")
    if response.result != WaitResult.OK:
        raise DarqError(f"wait({dpid}): result {response.result}")
    return ChildStatus(response.dpid, ExitReason(response.reason), response.status, response.get_error())


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


_BOOT_ERRORS: dict[int, type[DarqError]] = {
    BootResult.NOT_PERMITTED: NotPermittedError,
    BootResult.INVALID_NAME: InvalidNameError,
    BootResult.TOO_LARGE: ValueTooLargeError,
    BootResult.FULL: BootVectorFullError,
}


def _boot_error(result: int, name: str) -> DarqError:
    try:
        reason = BootResult(result).name
    except ValueError:
        reason = f"result {result}"
    return _BOOT_ERRORS.get(result, DarqError)(f"boot value {name!r}: {reason}")


def get_boot_value(name: str) -> bytes | None:
    """Return a name's value in the boot vector.

    The boot vector is a small, persistent name:value store, kept by the
    p-kernel for the system services it spawns (see doc/bootstrap.rst).

    :param name: Name to look up.
    :returns: Its value, or None if it isn't set.
    :raises NotPermittedError: if the p-kernel didn't spawn this process.
    :raises InvalidNameError: if the name is empty or too long."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)
    syscall.set_request(GetBootValueRequest(0, name))

    _darq_process_state.queue_new_syscall(syscall)
    syscall.sync_wait()

    response = typing.cast(GetBootValueResponse, syscall.get_response())
    if response.result == BootResult.NOT_FOUND:
        return None
    if response.result != BootResult.OK:
        raise _boot_error(response.result, name)
    return response.value


def set_boot_value(name: str, value: bytes | None):
    """Set a name's value in the boot vector, or remove it.

    Returns once the p-kernel has saved the change.

    :param name: Name to set.
    :param value: Value, of at most MAX_BOOT_VALUE_LENGTH bytes; None
                  removes the name.
    :raises NotPermittedError: if the p-kernel didn't spawn this process.
    :raises InvalidNameError: if the name is empty or too long.
    :raises ValueTooLargeError: if the value is too long.
    :raises BootVectorFullError: if there are already MAX_BOOT_VALUES
                                 names set.
    :raises DarqError: if the p-kernel couldn't save it."""

    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)
    syscall.set_request(SetBootValueRequest(0, name, value))

    _darq_process_state.queue_new_syscall(syscall)
    syscall.sync_wait()

    result = typing.cast(SetBootValueResponse, syscall.get_response()).result
    if result != BootResult.OK:
        raise _boot_error(result, name)
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
    power-off the host hardware.

    Only system processes (those the p-kernel spawned itself) may shut
    down the system.  There's no response: the p-kernel ignores (and
    logs) a request from any other process."""

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

    Only system processes (those the p-kernel spawned itself) may reboot
    the system.  There's no response: the p-kernel ignores (and logs) a
    request from any other process."""

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


########################################################################
# Requests and responses over ports
########################################################################

# Per-thread reply port.
_local = threading.local()

# Last transaction identifier allocated.
_txid_lock = threading.Lock()
_last_txid = 0


def _reply_port() -> int:
    """Return this thread's reply port, opening it if needed."""
    port = getattr(_local, "reply_port", None)
    if port is None:
        port = open_port()
        _local.reply_port = port
    return port


def _allocate_txid() -> int:
    """Return the next transaction identifier: never 0."""
    global _last_txid
    with _txid_lock:
        _last_txid = _last_txid % 0xFFFFFFFF + 1
        return _last_txid


def connect(name: str) -> int:
    """Return a handle for a registered service, waiting until it's registered.

    :param name: Service name, such as "darq.storage"."""
    handle = lookup_name(name, blocking=True)
    assert handle is not None, "blocking lookup returned nothing"
    return handle


def send(service: int, message: dipc.Message):
    """Send a one-way message to a service.

    :param service: Handle for the service's port.
    :param message: dipc.Message to send: its txid is set to 0."""
    message.txid = 0
    buf, handles = message.encode()
    send_message(_reply_port(), service, buf, handles)
    return


def transact[R: dipc.Message](service: int, request: dipc.Message, response_class: type[R],
                         timeout: float | None = None) -> R:
    """Send a request to a service, and wait for its response.

    :param service: Handle for the service's port.
    :param request: Request to send: its txid is set.
    :param response_class: Class of the expected response.
    :param timeout: Longest to wait for the response, in seconds; None
                    waits indefinitely.
    :returns: The decoded response.
    :raises RemoteError: if the service sent an error reply.
    :raises TimeoutError: if there's no reply within the timeout.
    :raises MessageDecodingError: if the response can't be decoded.
    :raises DarqError: if the request can't be sent (see send_message)."""
    port = _reply_port()
    request.txid = _allocate_txid()
    buf, handles = request.encode()
    send_message(port, service, buf, handles)

    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        message = receive_message(port, timeout=remaining)
        if message is None:
            raise TimeoutError(f"no reply to {type(request).__name__} from {service} within {timeout}s")

        source, payload, reply_handles = message
        try:
            txid = dipc.decode_txid(payload)
        except MessageDecodingError:
            txid = None
        if source != service or txid != request.txid:
            logging.warning(f"Dropped stray message on reply port {port}, from {source} (txid {txid})")
            continue

        if dipc.decode_flags(payload) & dipc.ERROR:
            error = ErrorReply()
            error.decode(payload, reply_handles)
            raise RemoteError(error.code, error.description)

        response = response_class()
        response.decode(payload, reply_handles)
        return response


def call(service: int, request: dipc.Message, timeout: float | None = None) -> dipc.Message | None:
    """Send a request to a service, and return its response.

    A one-way message (whose class has no RESPONSE) is just sent, and
    None returned.  See transact() for the parameters and exceptions."""
    if request.RESPONSE is None:
        send(service, request)
        return None
    return transact(service, request, request.RESPONSE, timeout)


class Client:
    """Base class for the generated client of each protocol.

    Its methods, one per protocol method, take the request's fields,
    and return the response (or None, for a one-way message)."""

    def __init__(self, service: int, timeout: float | None = None):
        """Constructor.

        :param service: Handle for the service's port (see connect).
        :param timeout: Longest to wait for each response, in seconds;
                        None waits indefinitely."""
        self.service = service
        self.timeout = timeout

    def call[R: dipc.Message](self, request: dipc.Message, response_class: type[R]) -> R:
        """Send a request, and return its response: see transact()."""
        return transact(self.service, request, response_class, self.timeout)

    def send(self, message: dipc.Message):
        """Send a one-way message."""
        send(self.service, message)
        return


class Handler:
    """Base class for the generated handler of each protocol.

    A service subclasses it, overriding a method for each protocol
    method it implements: each takes the decoded request, and the
    handle for the client's port, and returns the response (or None,
    for a one-way message)."""

    # Request class, and handler method name, by method number.
    METHODS: dict[int, tuple[type[dipc.Message], str]] = {}

    def dispatch(self, payload: bytes, source: int, handles: tuple[int, ...] = ()) -> dipc.Message | None:
        """Handle a request.

        :param payload: Received message.
        :param source: Handle for the client's port.
        :param handles: Handles transferred with the message.
        :returns: The reply to send: the response, or an error reply;
                  None for a one-way message."""
        try:
            txid = dipc.decode_txid(payload)
            method = dipc.decode_method(payload)
        except MessageDecodingError:
            logging.warning(f"Dropped message from {source}: too short for a header")
            return None

        entry = self.METHODS.get(method)
        if entry is None:
            return self.error(txid, method, ErrorCode.UNKNOWN_METHOD, f"no method {method}")
        request_class, name = entry

        request = request_class()
        try:
            request.decode(payload, handles)
        except MessageDecodingError as e:
            return self.error(txid, method, ErrorCode.BAD_MESSAGE, f"{request_class.__name__}: {e}")

        try:
            response = getattr(self, name)(request, source)
        except NotImplementedError:
            return self.error(txid, method, ErrorCode.NOT_IMPLEMENTED, f"{name} isn't implemented")
        except Exception as e:
            logging.exception(f"Handler for {request_class.__name__} failed")
            return self.error(txid, method, ErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")

        response_class = request_class.RESPONSE
        if response_class is None:
            if response is not None:
                logging.warning(f"Handler for one-way {request_class.__name__} returned a response: dropped")
            return None
        if not isinstance(response, response_class):
            return self.error(txid, method, ErrorCode.INTERNAL_ERROR,
                              f"{name} returned {type(response).__name__}, not {response_class.__name__}")
        response.txid = txid
        return response

    @staticmethod
    def error(txid: int, method: int, code: ErrorCode, description: str) -> ErrorReply | None:
        """Return an error reply, or None for a one-way message."""
        if txid == 0:
            logging.warning(f"Failed to handle one-way message {method}: {description}")
            return None
        logging.warning(f"Error reply to method {method}: {code.name}: {description}")
        return ErrorReply(txid, method, code, description)


def serve(name: str | None, handler: Handler) -> typing.NoReturn:
    """Answer requests on a new port, one at a time, until killed.

    :param name: Name to register for the port, or None to leave it
                 unnamed.
    :param handler: Handler for the protocol."""
    port = open_port()
    if name is not None:
        register_name(name, port)
    logging.info(f"Serving {name or 'unnamed port'} on port {port}")

    while True:
        message = receive_message(port)
        assert message is not None, "blocking receive returned nothing"
        source, payload, handles = message

        reply = handler.dispatch(payload, source, handles)
        if reply is None:
            continue
        buf, reply_handles = reply.encode()
        try:
            send_message(port, source, buf, reply_handles)
        except DarqError as e:
            # Most likely, the client has gone.
            logging.warning(f"Reply to {source} failed: {e!r}")
