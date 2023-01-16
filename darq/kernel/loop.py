# darqos
# Copyright (C) 2022 David Arnold



import logging
import select
import socket
import time
import typing

from PyQt5.QtCore import QEventLoop, QSocketNotifier, QTimer
from PyQt5.QtWidgets import QApplication


class DuplicateSocketError(Exception):
    """The specified socket is already registered."""
    pass


class SocketNotFoundError(Exception):
    """The specified socket is not registered."""
    pass


class EventLoopInterface:
    """Abstraction of event loop to work across uvloop for CLI and Qt
    for GUI applications."""

    def add_socket(self, sock: socket.socket, callback):
        pass

    def cancel_socket(self, sock: socket.socket):
        pass

    def add_timer(self, duration: float, callback) -> int:
        pass

    def cancel_timer(self, timer_id: int):
        pass

    def add_deferred(self, callback):
        pass

    def run(self):
        pass

    def stop(self):
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


class DeferredListener:
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

    def set_epxiry(self, now: float) -> float:
        self.expiry = now + self.duration
        return self.expiry


class SelectTimerCollection:
    "Collection of timeouts managed by the select event loop."""

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

    def get_next_expiry(self) -> float:
        """Return the absolute timestamp for the next due timer.

        :returns: Next expiry timestamp, or 30s in future if none."""
        if len(self.timers) == 0:
            return time.time() + 30.0

        return self.timers[0].expiry


class SelectEventLoop(EventLoopInterface):
    """A simple select-based event loop."""

    def __init__(self):
        """Constructor."""
        self.sockets: typing.Dict[socket.socket, typing.Any] = {}
        self.timers: SelectTimerCollection = SelectTimerCollection()
        self.active: bool = False

    def add_socket(self, sock: socket.socket, listener: SocketListener):
        """Add socket to event loop for monitoring.

        :param sock: Socket to monitor.
        :param listener: Listener class for callback to report events."""
        self.sockets[sock] = listener

    def cancel_socket(self, sock: socket.socket):
        """Cancel monitoring of a socket.

        :param sock: Socket for which to cancel monitoring."""
        del self.sockets[sock]

    def add_timer(self, duration, listener) -> int:
        """Add timer to event loop.

        :param duration: Interval in seconds between calls.
        :param listener: Listener for callbacks."""
        return self.timers.add_timer(duration, listener)

    def cancel_timer(self, timer_id: int):
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
            if len(self.timers) > 0:
                timeout = self.timers[0].expiry
                for t in self.timers[1:]:
                    # FIXMD: get next timer expiry time, or fallback timeout
                    pass
            else:
                timeout = 30.0

            rr, rw, _ = select.select(self.sockets, self.sockets, [], timeout)

            for s in rr:
                listener = self.sockets[s]
                listener.on_readable(s)

            for s in rw:
                listener = self.sockets[s]
                listener.on_writeable(s)

            for t in self.timers:
                now = time.time()
                if t.expiry < now:
                    t.expiry += t.duration
                    t.listener.on_timeout(t.timer_id, t.expiry, now)

    def stop(self):
        """Exit event loop at next iteration."""
        self.active = False


class QtSocketState:
    def __init__(self, sock: socket.socket, listener: SocketListener):
        self.socket = sock
        self.listener = listener

        self.read_notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Type.Read)
        self.read_notifier.activated.connect(self.on_readable)
        self.read_notifier.setEnabled(True)

        self.write_notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Type.Write)
        self.write_notifier.activated.connect(self.on_writeable)
        self.read_notifier.setEnabled(True)
        return

    def on_readable(self, sock):
        """Slot callback for event notifications."""

        self.listener.on_readable(self.socket)

    def on_writeable(self, sock):
        self.listener.on_writeable(self.socket)


class QtEventLoop(EventLoopInterface):

    def __init__(self):
        super().__init__()
        self.loop = None
        self.timers = {}
        self.sockets: dict[int, QtSocketState] = {}
        return

    def add_socket(self, sock: socket.socket, callback: SocketListener):
        # All sockets have both read and write monitoring; no sockets
        # support exception monitoring.
        if sock.fileno() in self.sockets:
            raise DuplicateSocketError(sock)

        state = QtSocketState(sock, callback)
        self.sockets[sock.fileno()] = state
        return

    def cancel_socket(self, sock: socket.socket):
        if sock.fileno() not in self.sockets:
            raise SocketNotFoundError(sock)

        state = self.sockets[sock.fileno()]
        state.read_notifier.setEnabled(False)
        state.read_notifier = None

        state.write_notifier.setEnabled(False)
        state.write_notifier = None

        del self.sockets[sock.fileno()]
        return

    def add_timer(self, duration: float, callback: TimerListener) -> int:

        # Convert duration to milliseconds
        ms_duration = int(duration / 1000)

        timer = QTimer()
        timer.timeout.connect(callback.on_timeout)
        timer.start(ms_duration)

        self.timers[timer.timerId()] = timer
        return timer.timerId()

    def cancel_timer(self, timer_id: int):
        pass

    def add_deferred(self, callback):
        pass

    def run(self):
        """Enter event loop.  Run until stop() is called."""
        loop = QEventLoop()
        loop.run()

    def stop(self):
        """Exit inner-most event loop instance."""
        loop.stop()
