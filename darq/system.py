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
# - Each thread asks to be notified on its reply port when a service it
#   calls is destroyed (see notify_on_death), so a call whose service
#   dies before replying raises DeadPortError, rather than waiting.
# - Services handle requests one at a time, in order of delivery, and
#   run until they're killed.  A Server answers on several ports, each
#   with its own handler; a handler that can't answer yet defers the
#   reply, and answers it later (see Handler.defer).


import logging
import os
import threading
import time
import typing
import uuid

from . import dipc
from .dipc import ErrorCode, ErrorReply
from .process import EventListener, PortListener, SystemCallMode, SystemCallState
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
    NotifyOnDeathRequest,
    NotifyOnDeathResponse,
    NotifyResult,
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
    SpawnResult,
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
    SpawnError,
    TooManyHandlesError,
    ValueTooLargeError,
)
from .loop import EventLoopInterface, TimerListener


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
    "receive_any",
    "set_port_listener",
    "PortSet",
    "notify_on_death",
    "spawn_process",
    "spawn_process_cb",
    "get_bootstrap_port",
    "SpawnResult",
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
    "Deferred",
    "Handler",
    "Server",
    "Timer",
    "call",
    "connect",
    "release_reply_port",
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

    # Handle for the bootstrap port, if the parent gave one.
    bootstrap_port: int | None

    def queue_new_syscall(self, syscall_state: SystemCallState) -> None: ...

    def receive_message(self, port: int, blocking: bool,
                        timeout: float | None) -> tuple[int, bytes, tuple[int, ...]] | None: ...

    def receive_any(self, ports: tuple[int, ...], blocking: bool,
                    timeout: float | None) -> tuple[int, int, bytes, tuple[int, ...]] | None: ...

    def set_port_listener(self, port: int, listener: PortListener | None,
                          loop: EventLoopInterface | None) -> None: ...

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


def receive_any(ports: typing.Iterable[int], blocking: bool = True,
                timeout: float | None = None) -> tuple[int, int, bytes, tuple[int, ...]] | None:
    """Receive a message delivered to any of several ports.

    Messages are received in the order they were delivered, across all
    the ports.  Like receive_message(), this needs no request to the
    p-kernel, and can't be used with the callback model.

    :param ports: Open port numbers.
    :param blocking: If True, wait until a message is delivered;
                     otherwise, return None at once if none has been.
    :param timeout: If blocking, the longest to wait, in seconds, before
                    returning None; None waits indefinitely.
    :returns: (port, source handle, payload, transferred handles), or
              None if no message was delivered, when not blocking or by
              the timeout.
    :raises NonExistentPortError: if a port isn't open, or is closed
                                  while waiting.
    :raises ValueError: if there are no ports."""
    return _darq_process_state.receive_any(tuple(ports), blocking, timeout)


def set_port_listener(port: int, listener: PortListener | None,
                      loop: EventLoopInterface | None = None):
    """Post a port's messages to a listener on an event loop, rather than
    queuing them for receive_message().

    This lets an application whose thread is owned by an event loop
    (such as Qt's) handle messages on that thread, while its other
    ports, like the reply ports that synchronous calls wait on, are
    still queued.  Messages already queued are posted first, in order.

    :param port: Open port number.
    :param listener: Called on the loop's thread, as listener(port,
                     source, payload, handles), for each message; or
                     None to queue them again.
    :param loop: Event loop to call it on: required with a listener.
    :raises NonExistentPortError: if the port isn't open."""
    _darq_process_state.set_port_listener(port, listener, loop)
    return


class PortSet:
    """A set of ports, received from together (see receive_any).

    This is the process' own grouping: the p-kernel knows nothing of
    it, and ports can be added and removed at any time."""

    def __init__(self, ports: typing.Iterable[int] = ()):
        """Constructor.

        :param ports: Open port numbers."""
        self._ports: dict[int, None] = dict.fromkeys(ports)

    def add(self, port: int):
        """Add a port, if it isn't already in the set."""
        self._ports[port] = None

    def discard(self, port: int):
        """Remove a port, if it's in the set."""
        self._ports.pop(port, None)

    def __contains__(self, port: object) -> bool:
        return port in self._ports

    def __iter__(self) -> typing.Iterator[int]:
        return iter(list(self._ports))

    def __len__(self) -> int:
        return len(self._ports)

    def receive(self, blocking: bool = True,
                timeout: float | None = None) -> tuple[int, int, bytes, tuple[int, ...]] | None:
        """Receive a message delivered to any of the ports: see receive_any()."""
        return receive_any(self._ports, blocking, timeout)


# Exceptions for notify_on_death's result codes.
_NOTIFY_ERRORS: dict[int, type[DarqError]] = {
    NotifyResult.BAD_HANDLE: NonExistentPortError,
    NotifyResult.BAD_NOTIFY_PORT: PortRightError,
}


def notify_on_death(handle: int, notify_port: int):
    """Ask to be notified when a port is destroyed.

    When the port is destroyed (its receive right closed, or its owner
    finished), the p-kernel delivers a dipc.PortDestroyed notification
    to the notify port, from source handle 0, carrying the handle.  If
    it's already dead, the notification is queued before this returns.

    Each notification is delivered once.  Asking again for the same
    notify port changes nothing; closing the handle cancels it.

    :param handle: Handle for the port to watch: any right.
    :param notify_port: Handle for the port to notify: a receive right.
    :raises NonExistentPortError: if the handle isn't open.
    :raises PortRightError: if the notify port isn't a receive right."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = NotifyOnDeathRequest(0, handle, notify_port)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    result = typing.cast(NotifyOnDeathResponse, syscall.get_response()).result
    if result != NotifyResult.OK:
        try:
            reason = NotifyResult(result).name
        except ValueError:
            reason = f"result {result}"
        raise _NOTIFY_ERRORS.get(result, DarqError)(f"notify_on_death({handle}, {notify_port}): {reason}")
    return


def decode_port_destroyed(source: int, payload: bytes) -> int | None:
    """Return the dead handle, if a message is a PortDestroyed notification.

    Only the p-kernel sends from source handle 0, so a notification
    from anywhere else isn't one."""
    if source != 0 or len(payload) < dipc.HEADER_LENGTH:
        return None
    if dipc.decode_flags(payload) != dipc.NOTIFICATION or dipc.decode_method(payload) != dipc.PORT_DESTROYED:
        return None
    notification = dipc.PortDestroyed()
    try:
        notification.decode(payload)
    except MessageDecodingError:
        return None
    return notification.handle


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


def spawn_process(exec_path: str, bootstrap: int | None = None) -> int:
    """Spawn a darq process, and wait until it's running.

    :param exec_path: Path to executable.
    :param bootstrap: Handle for a port to give the new process: it gets
                      a send right to it, as its bootstrap port (see
                      get_bootstrap_port).  The caller must hold a right
                      to it.
    :returns: DPID of new process.
    :raises SpawnError: if it couldn't be spawned, or exited before
                        running; its result is SpawnResult.BAD_BOOTSTRAP
                        if the bootstrap handle isn't open, or is dead."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.SYNC)

    # Populate the request message
    request = SpawnProcessRequest(0, exec_path, bootstrap or 0)
    syscall.set_request(request)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)

    # Wait for response.
    syscall.sync_wait()

    # Extract result.
    response = typing.cast(SpawnProcessResponse, syscall.get_response())
    if response.result != SpawnResult.OK:
        raise SpawnError(response.result, response.get_error())
    return response.dpid


def spawn_process_cb(exec_path: str, loop: EventLoopInterface, cb: typing.Callable[[int, int], None],
                     bootstrap: int | None = None) -> None:
    """Asynchronously spawn a darq process.

    :param exec_path: Path to executable.
    :param loop: Event loop in which the callable should be invoked.
    :param cb: Completion callback: cb(dpid, error_code), where the
               error code is a SpawnResult.
    :param bootstrap: Handle for the new process' bootstrap port: see
                      spawn_process."""

    # Get a SystemCallState
    syscall = SystemCallState()
    syscall.set_mode(SystemCallMode.CALLBACK)

    # Populate the request message
    request = SpawnProcessRequest(0, exec_path, bootstrap or 0)
    syscall.set_request(request)
    syscall.set_callback(loop, cb)

    # Enqueue.
    _darq_process_state.queue_new_syscall(syscall)
    return


def get_bootstrap_port() -> int | None:
    """Return the handle for this process' bootstrap port.

    It's a send right to the port the parent named when spawning this
    process, given to it by the p-kernel as it started running: a way
    for the two to talk, before either knows anything else of the
    other.  What they say over it is up to them.

    :returns: The handle, or None if the parent named no port, or the
              port was destroyed before this process ran.  Processes the
              p-kernel spawns at boot have none."""
    return _darq_process_state.bootstrap_port


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


def release_reply_port():
    """Close this thread's reply port, if it has one.

    Each thread that makes calls (see transact) opens a port for their
    replies, kept until the thread exits; one that's about to exit should
    close it, or the port is left open."""
    port = getattr(_local, "reply_port", None)
    if port is None:
        return
    _local.reply_port = None
    _local.watched = set()
    try:
        close_port(port)
    except DarqError as e:
        logging.warning(f"Can't close reply port {port}: {e!r}")


def _allocate_txid() -> int:
    """Return the next transaction identifier: never 0."""
    global _last_txid
    with _txid_lock:
        _last_txid = _last_txid % 0xFFFFFFFF + 1
        return _last_txid


def _watch(service: int, port: int):
    """Ask to be notified on a reply port when a service is destroyed.

    Asked once for each service handle on each thread: handles are
    never reused, so the notification stays in place until it's sent."""
    watched = getattr(_local, "watched", None)
    if watched is None:
        watched = _local.watched = set()
    if service not in watched:
        notify_on_death(service, port)
        watched.add(service)
    return


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
    :raises DeadPortError: if the service's port is destroyed before it
                           replies.
    :raises MessageDecodingError: if the response can't be decoded.
    :raises DarqError: if the request can't be sent (see send_message)."""
    port = _reply_port()
    _watch(service, port)
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
        dead = decode_port_destroyed(source, payload)
        if dead == service:
            raise DeadPortError(f"{service} was destroyed before replying to {type(request).__name__}")
        if dead is not None:
            logging.debug(f"Dropped notification on reply port {port}: {dead} is dead")
            continue

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


class Deferred:
    """A request whose reply is sent later (see Handler.defer).

    It's answered exactly once, with reply() or error().  The client is
    still waiting meanwhile: a service should make sure each one is
    answered, even when what it's waiting for fails."""

    def __init__(self, port: int | None, source: int, txid: int, method: int,
                 response_class: type[dipc.Message]):
        """Constructor.

        :param port: Port the request arrived on, which replies are sent from.
        :param source: Handle for the client's port.
        :param txid: The request's transaction identifier.
        :param method: The request's method number.
        :param response_class: Class of the response."""
        self.port = port
        self.source = source
        self.txid = txid
        self.method = method
        self.response_class = response_class
        self.done = False

        # Timer to fail it, if it was deferred with a timeout.
        self.timer: "Timer | None" = None

    def reply(self, response: dipc.Message):
        """Send the response.

        :raises RuntimeError: if it's already been answered.
        :raises TypeError: if the response isn't of the request's response class."""
        if not isinstance(response, self.response_class):
            raise TypeError(f"reply is {type(response).__name__}, not {self.response_class.__name__}")
        self._finish()
        response.txid = self.txid
        _send_reply(self.port, self.source, response)
        return

    def error(self, code: ErrorCode, description: str):
        """Send an error reply, in place of the response.

        :raises RuntimeError: if it's already been answered."""
        self._finish()
        logging.warning(f"Error reply to method {self.method}: {code.name}: {description}")
        _send_reply(self.port, self.source, ErrorReply(self.txid, self.method, code, description))
        return

    def _finish(self):
        if self.done:
            raise RuntimeError(f"Request {self.txid} (method {self.method}) has already been answered")
        if self.port is None:
            raise RuntimeError("Can't reply: the request's port isn't known")
        self._discard()

    def _discard(self):
        """Mark it answered, without sending anything."""
        self.done = True
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _expire(self, timeout: float):
        """Fail it with a TIMEOUT error reply, if it's still unanswered."""
        if not self.done:
            self.error(ErrorCode.TIMEOUT, f"no reply within {timeout}s")

    def __del__(self):
        if not self.done:
            logging.warning(f"Request {self.txid} (method {self.method}) from {self.source} "
                            f"was never answered")


def _send_reply(port: int | None, source: int, reply: dipc.Message):
    """Send a reply from a port, logging a failure."""
    assert port is not None, "reply without a port"
    buf, handles = reply.encode()
    try:
        send_message(port, source, buf, handles)
    except DarqError as e:
        # Most likely, the client has gone.
        logging.warning(f"Reply to {source} failed: {e!r}")
    return


class Handler:
    """Base class for the generated handler of each protocol.

    A service subclasses it, overriding a method for each protocol
    method it implements: each takes the decoded request, and the
    handle for the client's port, and returns the response (or None,
    for a one-way message).  A method that can't answer yet can defer
    the reply, and return the Deferred (see defer()).

    Notifications from the p-kernel on the handler's port aren't
    requests: they're passed to port_destroyed()."""

    # Request class, and handler method name, by method number.
    METHODS: dict[int, tuple[type[dipc.Message], str]] = {}

    # The Server this handler was added to, if any (see Server.add).
    server: "Server | None" = None

    # The request being handled, while dispatch() calls its method:
    # (port, source, txid, method, request class).
    _current: tuple[int | None, int, int, int, type[dipc.Message]] | None = None

    # Requests deferred while handling the current request.
    _deferred: list[Deferred] | None = None

    def defer(self, timeout: float | None = None) -> Deferred:
        """Defer the reply to the request being handled.

        The handler method returns the Deferred, and dispatch() sends
        nothing: the reply is sent later, with its reply() or error().

        :param timeout: Seconds to wait for the reply: if it hasn't been
                        sent by then, the server sends a TIMEOUT error
                        reply instead.  None waits indefinitely.
        :raises RuntimeError: if no request is being handled, or it's
                              a one-way message, which has no reply; or
                              there's a timeout, but no server to run
                              its timer."""
        if self._current is None or self._deferred is None:
            raise RuntimeError("defer() called outside a handler method")
        port, source, txid, method, request_class = self._current
        if request_class.RESPONSE is None:
            raise RuntimeError(f"One-way {request_class.__name__} has no reply to defer")
        if timeout is not None and self.server is None:
            raise RuntimeError("defer() with a timeout needs a Server")
        deferred = Deferred(port, source, txid, method, request_class.RESPONSE)
        if timeout is not None:
            assert self.server is not None
            deferred.timer = self.server.call_later(timeout, lambda: deferred._expire(timeout))
        self._deferred.append(deferred)
        return deferred

    def port_destroyed(self, handle: int):
        """Called when a port, watched with notify_on_death on the
        handler's port, is destroyed.

        Override it to stop using the handle (and close it), and to
        answer any deferred requests waiting on it.

        :param handle: The dead handle."""
        logging.info(f"Port {handle} destroyed: no port_destroyed handler")

    def dispatch(self, payload: bytes, source: int, handles: tuple[int, ...] = (),
                 port: int | None = None) -> dipc.Message | None:
        """Handle a request.

        :param payload: Received message.
        :param source: Handle for the client's port.
        :param handles: Handles transferred with the message.
        :param port: Port it arrived on, which a deferred reply is sent from.
        :returns: The reply to send: the response, or an error reply;
                  None for a one-way message, a deferred reply, or a
                  notification."""
        dead = decode_port_destroyed(source, payload)
        if dead is not None:
            self.port_destroyed(dead)
            return None

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

        # Requests deferred by this call, but then not returned, are
        # answered by whatever dispatch() returns instead.
        deferred: list[Deferred] = []
        response = None
        self._current, self._deferred = (port, source, txid, method, request_class), deferred
        try:
            try:
                response = getattr(self, name)(request, source)
            finally:
                self._current, self._deferred = None, None
        except NotImplementedError:
            reply = self.error(txid, method, ErrorCode.NOT_IMPLEMENTED, f"{name} isn't implemented")
        except Exception as e:
            logging.exception(f"Handler for {request_class.__name__} failed")
            reply = self.error(txid, method, ErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        else:
            reply = self._check_response(request_class, name, txid, method, response, deferred)

        for d in deferred:
            if d is not response:
                d._discard()
        return reply

    def _check_response(self, request_class: type[dipc.Message], name: str, txid: int, method: int,
                        response: typing.Any, deferred: list[Deferred]) -> dipc.Message | None:
        """Return the reply for what a handler method returned."""
        if isinstance(response, Deferred):
            if not any(d is response for d in deferred):
                return self.error(txid, method, ErrorCode.INTERNAL_ERROR,
                                  f"{name} returned a Deferred for another request")
            return None

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


class Timer:
    """A callback scheduled by Server.call_later()."""

    def __init__(self, when: float, callback: typing.Callable[[], None]):
        self.when = when
        self.callback = callback
        self.cancelled = False

        # Cancels its event loop timer, once it has one (see Server.attach).
        self._cancel_loop_timer: typing.Callable[[], None] | None = None

    def cancel(self):
        """Stop the callback being called, if it hasn't been already."""
        self.cancelled = True
        if self._cancel_loop_timer is not None:
            self._cancel_loop_timer()
            self._cancel_loop_timer = None

    def fire(self):
        """Call the callback, unless it's cancelled or been called."""
        if self.cancelled:
            return
        self.cancelled = True
        self._cancel_loop_timer = None
        try:
            self.callback()
        except Exception:
            logging.exception("Timer callback failed")


class _LoopTimer(TimerListener):
    """Fires a Timer once, from an event loop's repeating timer."""

    def __init__(self, loop: EventLoopInterface, timer: Timer):
        self.loop = loop
        self.timer = timer

    def on_timeout(self, timer_id: int, expiry_time: float, actual_time: float):
        self.loop.cancel_timer(timer_id)
        self.timer.fire()


class Server:
    """Answers requests on a set of ports, each with its own handler.

    Requests are handled one at a time, in order of delivery across the
    ports, on the calling thread.  Handlers can add and remove ports
    while handling a request, and defer replies until a message arrives
    on another port, or a timer runs.

    It's single-threaded: only the serving thread may use it, or its
    handlers' deferred requests.  Another thread can ask it to act by
    sending a message to one of its ports.  Timers run between
    messages, so a handler that blocks (in spawn_process, say) delays
    them.

    It's run either by run() or run_once(), or by an application's
    event loop, once attached to it (see attach())."""

    def __init__(self):
        self.ports = PortSet()
        self._handlers: dict[int, Handler] = {}
        self._timers: list[Timer] = []

        # Event loop it's attached to, if any.
        self._loop: EventLoopInterface | None = None

    def add(self, port: int, handler: Handler):
        """Answer requests on a port, with a handler.

        :param port: Open port: the handler's replies are sent from it.
        :param handler: Handler for the port's protocol."""
        handler.server = self
        self._handlers[port] = handler
        if self._loop is not None and port not in self.ports:
            set_port_listener(port, self._on_message, self._loop)
        self.ports.add(port)

    def remove(self, port: int):
        """Stop answering requests on a port.  It isn't closed."""
        self._handlers.pop(port, None)
        if self._loop is not None and port in self.ports:
            try:
                set_port_listener(port, None)
            except NonExistentPortError:
                pass
        self.ports.discard(port)

    def attach(self, loop: EventLoopInterface):
        """Answer requests, and run timers, on an event loop, rather than
        with run() or run_once().

        Each port's messages are posted to the loop (see
        set_port_listener), and handled on its thread: the application
        runs the loop.  Other ports, such as the reply ports synchronous
        calls wait on, are unaffected, so handlers can still make them.

        :param loop: Event loop, run on this thread."""
        if self._loop is not None:
            raise RuntimeError("Server is already attached to an event loop")
        self._loop = loop
        for port in self.ports:
            set_port_listener(port, self._on_message, loop)
        timers, self._timers = self._timers, []
        for timer in timers:
            if not timer.cancelled:
                self._schedule(timer)
        return

    def call_later(self, delay: float, callback: typing.Callable[[], None]) -> Timer:
        """Call a function, on the serving thread, after a delay.

        :param delay: Seconds to wait.
        :param callback: Called with no arguments.
        :returns: A Timer, which can be cancelled."""
        timer = Timer(time.monotonic() + delay, callback)
        if self._loop is None:
            self._timers.append(timer)
        else:
            self._schedule(timer)
        return timer

    def _schedule(self, timer: Timer):
        """Have the event loop fire a timer when it's due."""
        loop = self._loop
        assert loop is not None
        timer_id = loop.add_timer(max(0.0, timer.when - time.monotonic()), _LoopTimer(loop, timer))

        def cancel():
            loop.cancel_timer(timer_id)

        timer._cancel_loop_timer = cancel
        return

    def _handle(self, port: int, source: int, payload: bytes, handles: tuple[int, ...]):
        """Dispatch a message to its port's handler, and send the reply."""
        handler = self._handlers.get(port)
        if handler is None:
            logging.warning(f"Dropped message for port {port}: no handler")
            return
        reply = handler.dispatch(payload, source, handles, port)
        if reply is not None:
            _send_reply(port, source, reply)
        return

    def _on_message(self, port: int, source: int, payload: bytes, handles: tuple[int, ...]):
        """Handle a message posted to the event loop."""
        try:
            self._handle(port, source, payload, handles)
        except Exception:
            # Not raised into the loop, which would drop the other posted calls.
            logging.exception(f"Failed to handle a message on port {port}")
        return

    def run_once(self, timeout: float | None = None) -> bool:
        """Handle one message, or run the timers that are due.

        :param timeout: Longest to wait, in seconds; None waits until a
                        message arrives, or a timer is due.
        :returns: True if a message was handled."""
        if self._loop is not None:
            raise RuntimeError("Server is attached to an event loop: run the loop")
        self._timers = [t for t in self._timers if not t.cancelled]
        wait = timeout
        if self._timers:
            due = max(0.0, min(t.when for t in self._timers) - time.monotonic())
            wait = due if wait is None else min(wait, due)

        handled = False
        if self.ports:
            message = self.ports.receive(timeout=wait)
        elif wait is None:
            raise RuntimeError("Server has no ports, and no timers, to wait for")
        else:
            message = None
            time.sleep(wait)
        if message is not None:
            self._handle(*message)
            handled = True

        now = time.monotonic()
        for timer in [t for t in self._timers if t.when <= now and not t.cancelled]:
            timer.fire()
        return handled

    def run(self) -> typing.NoReturn:
        """Handle messages, and run timers, until killed."""
        while True:
            self.run_once()


def serve(name: str | None, handler: Handler) -> typing.NoReturn:
    """Answer requests on a new port, one at a time, until killed.

    The handler can add other ports, and timers, through its server
    (see Server).

    :param name: Name to register for the port, or None to leave it
                 unnamed.
    :param handler: Handler for the protocol."""
    port = open_port()
    if name is not None:
        register_name(name, port)
    logging.info(f"Serving {name or 'unnamed port'} on port {port}")

    server = Server()
    server.add(port, handler)
    server.run()
