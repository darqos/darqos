# darqos
# Copyright (C) 2022-2024 David Arnold

import select
import socket
import time
import typing

from PyQt5.QtCore import QEventLoop, QSocketNotifier, QTimer


class DuplicateSocketError(Exception):
    """The specified socket is already registered."""
    pass


class SocketNotFoundError(Exception):
    """The specified socket is not registered."""
    pass


class EventLoopInterface:
    """Abstraction of event loop to work across uvloop for CLI and Qt
    for GUI applications."""

    def add_socket(self, sock: socket.socket, listener: "SocketListener"):
        """Monitor a socket for readability."""
        pass

    def cancel_socket(self, sock: socket.socket):
        pass

    def set_write_interest(self, sock: socket.socket, enabled: bool):
        """Start or stop monitoring a registered socket for writability.

        Enable it only while there is data waiting to be sent: an idle
        socket is almost always writable."""
        pass

    def add_timer(self, duration: float, listener: "TimerListener") -> int:
        """Call the listener every duration seconds.

        :returns: Timer identifier, for cancel_timer()."""
        raise NotImplementedError

    def cancel_timer(self, timer_id: int) -> bool:
        """Cancel a timer.

        :returns: True if the timer was found and cancelled."""
        raise NotImplementedError

    def add_deferred(self, callback):
        pass

    def run(self):
        """Process events until stop() is called."""
        pass

    def stop(self):
        """Stop the running event loop."""
        pass

    def next(self):
        """Process the next event only."""
        pass

class TimerListener:
    def on_timeout(self, timer_id: int, expiry_time: float, actual_time: float):
        pass


class SocketListener:
    def on_readable(self, sock: socket.socket):
        pass

    def on_writeable(self, sock: socket.socket):
        pass

    def on_connected(self, sock: socket.socket):
        pass


class DeferredListener(TimerListener):
    def __init__(self, loop, function):
        self.loop: EventLoopInterface = loop
        self.function = function

    def on_timeout(self, timer_id: int, expiry_time: float, actual_time: float):
        self.loop.cancel_timer(timer_id)
        self.function()


class SelectTimerState:
    """Timer state for select-based event loop."""

    def __init__(self, timer_id: int, duration:float, listener: TimerListener):
        self.timer_id: int = timer_id
        self.duration: float = duration
        self.listener: TimerListener = listener
        self.expiry: float = 0

    def set_expiry(self, now: float) -> float:
        self.expiry = now + self.duration
        return self.expiry


class SelectTimerCollection:
    """Collection of timeouts managed by the select event loop.

    Expiry times are on the time.monotonic() clock."""

    def __init__(self):
        """Constructor."""
        self.timers: typing.List[SelectTimerState] = []
        self.next_id: int = 1

    def __len__(self):
        """Return size of the collection."""
        return len(self.timers)

    def __getitem__(self, item):
        return self.timers[item]

    def add_timer(self, interval: float, listener: TimerListener) -> int:
        """Add timer to collection.

        :param interval: Floating-point interval in seconds.
        :param listener: Callback instance.
        :returns: Timer identifier (used to cancel)."""

        timer_state = SelectTimerState(self.next_id, interval, listener)
        self.next_id += 1

        timer_state.set_expiry(time.monotonic())
        self.timers.append(timer_state)
        self.timers.sort(key=lambda x: x.expiry)

        return timer_state.timer_id

    def cancel_timer(self, timer_id: int) -> bool:
        """Cancel a timer from this collection.

        :param timer_id: Timer identifier.
        :returns: True if found and deleted; False otherwise."""

        i = 0
        while i < len(self.timers):
            if self.timers[i].timer_id == timer_id:
                del self.timers[i]
                return True
            i += 1
        return False

    def get_next_expiry(self) -> typing.Optional[float]:
        """Return the monotonic timestamp for the next due timer.

        :returns: Next expiry timestamp, or None if there are no timers."""
        if len(self.timers) == 0:
            return None

        return self.timers[0].expiry

    def get_due(self, now: float) -> typing.List[SelectTimerState]:
        """Return the timers due at or before now, earliest first.

        :param now: Current monotonic timestamp."""
        return [t for t in self.timers if t.expiry <= now]

    def is_active(self, timer_state: SelectTimerState) -> bool:
        """Return True if the timer has not been cancelled."""
        return any(t is timer_state for t in self.timers)

    def reschedule(self, timer_state: SelectTimerState, now: float):
        """Set a fired timer's next expiry.

        Missed intervals are skipped, rather than fired in a burst.

        :param timer_state: Timer that has just expired.
        :param now: Current monotonic timestamp."""
        timer_state.expiry += timer_state.duration
        if timer_state.expiry <= now:
            timer_state.set_expiry(now)
        self.timers.sort(key=lambda x: x.expiry)


class SelectEventLoop(EventLoopInterface):
    """A simple select-based event loop."""

    # Longest wait in select() when no timer is due sooner.
    MAX_TIMEOUT = 10.0

    def __init__(self):
        """Constructor."""
        self.sockets: typing.Dict[socket.socket, typing.Any] = {}
        self.writers: typing.Set[socket.socket] = set()
        self.timers: SelectTimerCollection = SelectTimerCollection()
        self.active: bool = False

    def add_socket(self, sock: socket.socket, listener: SocketListener):
        """Add socket to event loop for monitoring.

        The socket is monitored for readability; see set_write_interest()
        for writability.

        :param sock: Socket to monitor.
        :param listener: Listener class for callback to report events."""
        self.sockets[sock] = listener

    def cancel_socket(self, sock: socket.socket):
        """Cancel monitoring of a socket.

        :param sock: Socket for which to cancel monitoring."""
        del self.sockets[sock]
        self.writers.discard(sock)

    def set_write_interest(self, sock: socket.socket, enabled: bool):
        """Start or stop monitoring a registered socket for writability.

        :param sock: Registered socket.
        :param enabled: True to report writability via on_writeable()."""
        if sock not in self.sockets:
            raise SocketNotFoundError(sock)

        if enabled:
            self.writers.add(sock)
        else:
            self.writers.discard(sock)

    def add_timer(self, duration: float, listener: TimerListener) -> int:
        """Add timer to event loop.

        :param duration: Interval in seconds between calls.
        :param listener: Listener for callbacks."""
        return self.timers.add_timer(duration, listener)

    def cancel_timer(self, timer_id: int) -> bool:
        """Cancel timeout notifications.

        :param timer_id: Identifier for registered timeout."""
        return self.timers.cancel_timer(timer_id)

    def add_deferred(self, callback):
        """Call this listener at the end of this loop iteration.

        :param callback: Function to execute."""
        self.add_timer(0, DeferredListener(self, callback))

    def run(self):
        """Enter event loop and begin processing events."""

        self.active = True
        while self.active:
            self.next()

    def get_timeout(self) -> float:
        """Return seconds until the next timer is due, for select()."""
        expiry = self.timers.get_next_expiry()
        if expiry is None:
            return self.MAX_TIMEOUT
        return min(max(0.0, expiry - time.monotonic()), self.MAX_TIMEOUT)

    def next(self):
        """Process the next event."""

        rr, rw, _ = select.select(self.sockets, self.writers, [], self.get_timeout())

        for s in rr:
            listener = self.sockets.get(s)
            if listener is not None:
                listener.on_readable(s)

        for s in rw:
            listener = self.sockets.get(s)
            if listener is not None:
                listener.on_writeable(s)

        self.run_timers()

    def run_timers(self):
        """Call the listeners of all due timers.

        Each timer is rescheduled before its listener is called, so the
        listener can cancel it.  Timers cancelled by an earlier listener
        in the same pass are not called, and timers added during the
        pass wait for the next."""

        now = time.monotonic()
        for t in self.timers.get_due(now):
            if not self.timers.is_active(t):
                continue

            expiry = t.expiry
            self.timers.reschedule(t, now)
            t.listener.on_timeout(t.timer_id, expiry, now)

    def stop(self):
        """Exit event loop at next iteration."""
        self.active = False


class QtSocketState:
    def __init__(self, sock: socket.socket, listener: SocketListener):
        self.socket = sock
        self.listener = listener

        # PyQt5's stubs declare the descriptor as a voidptr, but it takes an int.
        self.read_notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Type.Read)  # pyright: ignore[reportArgumentType]
        self.read_notifier.activated.connect(self.on_readable)
        self.read_notifier.setEnabled(True)

        # Writability is monitored only on request: see set_write_interest().
        self.write_notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Type.Write)  # pyright: ignore[reportArgumentType]
        self.write_notifier.activated.connect(self.on_writeable)
        self.write_notifier.setEnabled(False)
        return

    def on_readable(self, sock):
        """Slot callback for event notifications."""

        self.listener.on_readable(self.socket)

    def on_writeable(self, sock):
        self.listener.on_writeable(self.socket)


class QtEventLoop(EventLoopInterface):

    def __init__(self):
        super().__init__()
        self.loop: QEventLoop | None = None
        self.timers = {}
        self.sockets: dict[int, QtSocketState] = {}
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

        # Convert duration to milliseconds
        ms_duration = int(duration / 1000)

        timer = QTimer()
        timer.timeout.connect(listener.on_timeout)
        timer.start(ms_duration)

        self.timers[timer.timerId()] = timer
        return timer.timerId()

    def cancel_timer(self, timer_id: int) -> bool:
        timer = self.timers.pop(timer_id, None)
        if timer is None:
            return False

        timer.stop()
        return True

    def add_deferred(self, callback):
        pass

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
