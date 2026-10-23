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
import typing

from .common import *
from .errors import *
from .loop import EventLoopInterface

# Default exported symbols.
__all__ = [
    "EventListener",
    "PortState",
    "SystemCallState",
]


########################################################################

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


