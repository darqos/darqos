# darqos
# Copyright (c) 2026 Daid Arnold
#
# Application support.
#
# Uses darq.common and darq.loop to provide the foundation for
# Darq applicaations.

import collections
import threading
import typing

from enum import IntEnum
from warnings import deprecated

from .common import Buffer, Message
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
        # by the application, as (source port, payload).  Messages are
        # delivered on the I/O thread, and received on the application's,
        # so the queue is guarded by the lock.  A blocked receiver waits
        # for the event, which is set when a message is delivered, or the
        # port is closed.
        self.messages: collections.deque[tuple[int, bytes]] = collections.deque()
        self._lock = threading.Lock()
        self._event = threading.Event()

        # Reassembly buffer for received bytes.
        self.buffer = Buffer()
        return

    def append_bytes(self, buf: bytes):
        """Append received bytes to the reassembly buffer."""
        return self.buffer.append(buf)

    def deliver(self, source: int, payload: bytes):
        """Queue a delivered message, releasing a blocked receiver.

        :param source: Sending port number.
        :param payload: Message contents."""
        with self._lock:
            self.messages.append((source, payload))
            self._event.set()
        return

    def receive(self, blocking: bool = True) -> tuple[int, bytes] | None:
        """Take the next delivered message, in order of delivery.

        :param blocking: If True, wait for a message to be delivered;
                         otherwise, return None if there isn't one.
        :returns: (source port, payload), or None if not blocking and
                  no message has been delivered.
        :raises NonExistentPortError: if the port is closed, before or
                                      while waiting."""
        while True:
            with self._lock:
                if self.messages:
                    return self.messages.popleft()
                if not self.is_open:
                    raise NonExistentPortError(self.port_id)
                if not blocking:
                    return None

                # Cleared while holding the lock, so a message delivered
                # from here on sets it again.
                self._event.clear()

            self._event.wait()

    def close(self):
        """Mark the port closed, releasing any blocked receiver.

        Messages already delivered can still be received."""
        with self._lock:
            self.is_open = False
            self._event.set()
        return

    def wake(self):
        """Release a blocked receiver, without a message.

        It checks again, and waits again if there's still nothing for
        it: this lets a pending exception (such as a kill) be raised."""
        self._event.set()
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


