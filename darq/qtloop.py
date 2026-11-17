# darqos
# Copyright (C) 2022-2026 David Arnold

"""Qt's event loop, for a DarqOS process with windows: a lens, a tool,
the Terminal.  It's darq.loop's interface, on Qt's loop, so DarqOS's
calls and messages, and Qt's events, are handled on one thread.  It's a
module of its own, so that what has no windows doesn't import Qt."""

import socket
import time
import typing

from PyQt6.QtCore import QEventLoop, QSocketNotifier, Qt, QTimer
from PyQt6.sip import voidptr

from .loop import DeferredListener, EventLoopInterface, PostedCalls, SocketListener, TimerListener
from .errors import DuplicateSocketError, SocketNotFoundError


# Control exports
__all__ = ["QtEventLoop"]


class QtSocketState:
    def __init__(self, sock: socket.socket, listener: SocketListener):
        self.socket = sock
        self.listener = listener

        # PyQt6 takes the descriptor as a voidptr.
        self.read_notifier = QSocketNotifier(voidptr(sock.fileno()), QSocketNotifier.Type.Read)
        self.read_notifier.activated.connect(self.on_readable)
        self.read_notifier.setEnabled(True)

        # Writability is monitored only on request: see set_write_interest().
        self.write_notifier = QSocketNotifier(voidptr(sock.fileno()), QSocketNotifier.Type.Write)
        self.write_notifier.activated.connect(self.on_writeable)
        self.write_notifier.setEnabled(False)
        return

    def on_readable(self, sock):
        """Slot callback for event notifications."""

        self.listener.on_readable(self.socket)

    def on_writeable(self, sock):
        self.listener.on_writeable(self.socket)


class QtTimerState:
    """Timer state for the Qt event loop.

    Expiry times are on the time.monotonic() clock, as for the select
    event loop.  Qt's default, coarse, timers can fire up to 5% early:
    precise timers keep to its millisecond resolution."""

    def __init__(self, timer_id: int, duration: float, listener: TimerListener):
        self.timer_id = timer_id
        self.duration = duration
        self.listener = listener
        self.expiry = time.monotonic() + duration

        self.timer = QTimer()
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.on_timeout)
        self.timer.start(int(duration * 1000))
        return

    def on_timeout(self):
        """Slot callback: call the listener with the scheduled and actual times.

        Missed intervals are skipped, rather than reported in a burst."""
        now = time.monotonic()
        expiry = self.expiry
        self.expiry += self.duration
        if self.expiry <= now:
            self.expiry = now + self.duration

        self.listener.on_timeout(self.timer_id, expiry, now)

    def stop(self):
        self.timer.stop()


class QtEventLoop(EventLoopInterface):

    def __init__(self):
        super().__init__()
        self.loop: QEventLoop | None = None
        self.timers: dict[int, QtTimerState] = {}
        self.next_timer_id: int = 1
        self.sockets: dict[int, QtSocketState] = {}

        # Functions posted from other threads.  The notifiers are created
        # now, so they belong to the thread that owns the loop.
        self.posted = PostedCalls()
        self.add_socket(self.posted.reader, self.posted)
        return

    def add_socket(self, sock: socket.socket, listener: SocketListener):
        # Sockets are monitored for reading, and for writing only on
        # request; no sockets support exception monitoring.
        if sock.fileno() in self.sockets:
            raise DuplicateSocketError(sock)

        state = QtSocketState(sock, listener)
        self.sockets[sock.fileno()] = state
        return

    def cancel_socket(self, sock: socket.socket):
        if sock.fileno() not in self.sockets:
            raise SocketNotFoundError(sock)

        state = self.sockets.pop(sock.fileno())
        state.read_notifier.setEnabled(False)
        state.write_notifier.setEnabled(False)
        return

    def set_write_interest(self, sock: socket.socket, enabled: bool):
        if sock.fileno() not in self.sockets:
            raise SocketNotFoundError(sock)

        self.sockets[sock.fileno()].write_notifier.setEnabled(enabled)
        return

    def add_timer(self, duration: float, listener: TimerListener) -> int:
        """Call the listener every duration seconds.

        :param duration: Interval in seconds between calls.
        :param listener: Listener for callbacks.
        :returns: Timer identifier, for cancel_timer()."""

        # Qt's own timer identifiers are reused once a timer is stopped:
        # these aren't, so a stale identifier can't cancel a newer timer.
        timer_id = self.next_timer_id
        self.next_timer_id += 1
        self.timers[timer_id] = QtTimerState(timer_id, duration, listener)
        return timer_id

    def cancel_timer(self, timer_id: int) -> bool:
        """Cancel a timer.

        :param timer_id: Identifier from add_timer().
        :returns: True if the timer was found and cancelled."""
        state = self.timers.pop(timer_id, None)
        if state is None:
            return False

        state.stop()
        return True

    def add_deferred(self, callback):
        """Call this function once, soon, from the event loop.

        :param callback: Function to execute."""
        self.add_timer(0, DeferredListener(self, callback))

    def post(self, function: typing.Callable[[], None]):
        """Call function on the loop's thread, from any thread.

        :param function: Function to call, with no arguments."""
        self.posted.post(function)

    def get_loop(self) -> QEventLoop:
        """Return the Qt event loop, creating it on first use."""
        if self.loop is None:
            self.loop = QEventLoop()
        return self.loop

    def run(self):
        """Enter event loop.  Run until stop() is called."""
        self.get_loop().exec()

    def next(self):
        # FIXME FIXME FIXME
        self.get_loop().processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)

    def stop(self):
        """Exit inner-most event loop instance."""
        if self.loop is not None:
            self.loop.exit(0)

    def close(self):
        """Stop the loop's timers, and release its wakeup sockets."""
        for timer_id in list(self.timers):
            self.cancel_timer(timer_id)

        if self.posted.closed:
            return
        self.cancel_socket(self.posted.reader)
        self.posted.close()
