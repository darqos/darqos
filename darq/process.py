# darqos
# Copyright (C) 2026 David Arnold
#
# Process support.
#
# Uses darq.pcall and darq.loop to provide the per-process state
# that pyloader and darq.system build on: p-call state, port state,
# and the callback model's listener interface.

import collections
import itertools
import threading
import time
import typing

from enum import IntEnum
from warnings import deprecated

from .pcall import Buffer, Message
from .errors import NonExistentPortError
from .loop import EventLoopInterface

# Default exported symbols.
__all__ = [
    "EventListener",
    "PortState",
    "SystemCallState",
    "SystemCallMode"
]


########################################################################

class SystemCallMode(IntEnum):
    """Interaction model flag."""

    # Synchronous, blocking model.
    SYNC = 1

    # Asynchronous, awaitable model, returning a future.
    ASYNC = 2

    # Asynchronous, callback supplied.
    CALLBACK = 3


class SystemCallState:
    """State of a p-kernel request."""

    def __init__(self, loop = None):

        # Flag interaction mode.
        self._mode = SystemCallMode.SYNC

        # Syscall number.
        self._type = 0

        # Request message structure containing parameters.
        self._request: Message | None = None

        # Response message structure containing results.
        self._response: Message | None = None

        # Event on which a synchronous call will wait for a response.
        self._sync_event = threading.Event()

        # Future to be signaled when asynchronous call has a response.
        if loop:
            self._async_future = loop.Future(loop=loop)

        # Callback, for CALLBACK mode.
        self._callback: typing.Callable|None = None

        self._loop: EventLoopInterface|None =  None


    def set_mode(self, mode: SystemCallMode):
        self._mode = mode

    def get_mode(self) -> SystemCallMode:
        return self._mode

    def set_request(self, request: Message):
        self._request = request

    def get_request(self) -> Message:
        assert self._request is not None, "p-call has no request"
        return self._request

    def set_response(self, response: Message):
        self._response = response

    def get_response(self) -> Message | None:
        """Return the response, or None if the p-call was abandoned."""
        return self._response

    def set_callback(self, loop: EventLoopInterface, cb: typing.Callable):
        self._loop = loop
        self._callback = cb

    def get_callback(self) -> tuple[EventLoopInterface|None, typing.Callable|None]:
        return self._loop, self._callback

    @deprecated("Use get_mode() instead")
    def is_sync(self):
        return self._mode == SystemCallMode.SYNC

    @deprecated("Shouldn't need this?")
    def is_complete(self):
        return self._response is not None

    @deprecated("Access request result")
    def is_success(self):
        return False # FIXME

    @deprecated("Do this manually?")
    def complete(self, result, message):
        self.result = result
        self.completed = True
        self.response_message = message
        return

    def notify(self):
        """Wake a synchronous caller blocked in sync_wait()."""
        self._sync_event.set()

    def sync_wait(self):
        # FIXME: should I put a timeout here, and actually have the syscall fail if it takes too long?
        self._sync_event.wait()



class PortState:
    """Process-side state for an open port."""

    def __init__(self, port_id: int):
        """Constructor.

        :param port_id: Integer port identifier."""

        # Unique identifier for this port.
        self.port_id = port_id

        # True if port is open.
        self.is_open: bool = False

        # Queue of messages delivered to this port, but not yet received
        # by the application, as (delivery order, source port, payload,
        # handles).  Messages are delivered on the I/O thread, and
        # received on the application's, so the queue is guarded by the
        # lock.  A blocked receiver waits for the event, which is set
        # when a message is delivered, or the port is closed; receivers
        # waiting on several ports (see receive_any) add their own
        # events to the waiters.
        self.messages: collections.deque[tuple[int, int, bytes, tuple[int, ...]]] = collections.deque()
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._waiters: set[threading.Event] = set()

        # Reassembly buffer for received bytes.
        self.buffer = Buffer()
        return

    def append_bytes(self, buf: bytes):
        """Append received bytes to the reassembly buffer."""
        return self.buffer.append(buf)

    def deliver(self, source: int, payload: bytes, handles: tuple[int, ...] = ()):
        """Queue a delivered message, releasing a blocked receiver.

        :param source: Sending port number.
        :param payload: Message contents.
        :param handles: Handles transferred with the message."""
        with self._lock:
            self.messages.append((next(_delivery_order), source, payload, handles))
            self._notify()
        return

    def _notify(self):
        """Release blocked receivers.  Called with the lock held."""
        self._event.set()
        for waiter in self._waiters:
            waiter.set()

    def receive(self, blocking: bool = True,
                timeout: float | None = None) -> tuple[int, bytes, tuple[int, ...]] | None:
        """Take the next delivered message, in order of delivery.

        :param blocking: If True, wait for a message to be delivered;
                         otherwise, return None if there isn't one.
        :param timeout: If blocking, the longest to wait, in seconds;
                        None waits indefinitely.
        :returns: (source port, payload, handles), or None if no message
                  was delivered, when not blocking or by the timeout.
        :raises NonExistentPortError: if the port is closed, before or
                                      while waiting."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                if self.messages:
                    return self.messages.popleft()[1:]
                if not self.is_open:
                    raise NonExistentPortError(self.port_id)
                if not blocking:
                    return None

                # Cleared while holding the lock, so a message delivered
                # from here on sets it again.
                self._event.clear()

            if deadline is None:
                self._event.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._event.wait(remaining):
                # Timed out: take a message delivered at the last moment.
                blocking = False

    def close(self):
        """Mark the port closed, releasing any blocked receiver.

        Messages already delivered can still be received."""
        with self._lock:
            self.is_open = False
            self._notify()
        return

    def wake(self):
        """Release a blocked receiver, without a message.

        It checks again, and waits again if there's still nothing for
        it: this lets a pending exception (such as a kill) be raised."""
        with self._lock:
            self._notify()
        return


# Order of delivery, across all ports, so a receiver waiting on several
# gets their messages in the order they were delivered.  Messages are
# only delivered on the I/O thread.
_delivery_order = itertools.count()


def receive_any(ports: typing.Sequence[PortState], blocking: bool = True,
                timeout: float | None = None) -> tuple[PortState, tuple[int, bytes, tuple[int, ...]]] | None:
    """Take the next message delivered to any of several ports.

    Messages are taken in the order they were delivered, across the
    ports.

    :param ports: Ports to receive from.
    :param blocking: If True, wait for a message to be delivered;
                     otherwise, return None if there isn't one.
    :param timeout: If blocking, the longest to wait, in seconds;
                    None waits indefinitely.
    :returns: (port, (source port, payload, handles)), or None if no
              message was delivered, when not blocking or by the timeout.
    :raises NonExistentPortError: if one of the ports is closed, and
                                  has no messages left, before or while
                                  waiting.
    :raises ValueError: if there are no ports."""
    if not ports:
        raise ValueError("No ports to receive from")

    event = threading.Event()
    for port in ports:
        with port._lock:
            port._waiters.add(event)
    try:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Cleared before looking, so a message delivered from here on
            # sets it again.
            event.clear()
            earliest: PortState | None = None
            earliest_order = 0
            for port in ports:
                with port._lock:
                    if port.messages:
                        if earliest is None or port.messages[0][0] < earliest_order:
                            earliest, earliest_order = port, port.messages[0][0]
                    elif not port.is_open:
                        raise NonExistentPortError(port.port_id)
            if earliest is not None:
                with earliest._lock:
                    if earliest.messages:
                        return earliest, earliest.messages.popleft()[1:]
                # Taken by another receiver: look again.
                continue
            if not blocking:
                return None

            if deadline is None:
                event.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not event.wait(remaining):
                # Timed out: take a message delivered at the last moment.
                blocking = False
    finally:
        for port in ports:
            with port._lock:
                port._waiters.discard(event)


class EventListener:
    """Interface required for asynchronous use of the p-Kernel API."""

    def on_open_port(self, port: int):
        pass

    def on_close_port(self, port: int):
        pass

    def on_send_message(self, port: int, request_id: int):
        pass

    def on_message(self, source: int, destination: int, message: bytes,
                   handles: tuple[int, ...] = ()):
        """Handle a delivered message.

        :param source: Handle for the sending port.
        :param destination: Port it was delivered to.
        :param message: Payload.
        :param handles: Handles transferred with it: this process' send
                        rights (see doc/ipc.rst)."""
        pass

    def on_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
        """Handle a delivered stream chunk."""
        pass

    def on_error(self, request: int, error: int, reason: str):
        """Handle a reported communications error."""
        pass


