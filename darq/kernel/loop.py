# darqos
# Copyright (C) 2022 David Arnold

import select
import socket
import time
import typing


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
                del self.timers[timer_id]
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

    def add_deferred(self, listener: TimerListener):
        """Call this listener at the end of this loop iteration.

        :param listener: Listner for callbacks."""
        self.add_timer(0, listener)

    def run(self):
        """Enter event loop and begin processing events."""

        self.active = True

        while self.active:
            if len(self.timers) > 0:
                timeout = self.timers[0]
                for t in self.timers:
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
                    t.listener.on_timeout(t.timer_id, t.expiry)

    def stop(self):
        """Exit event loop at next iteration."""
        self.active = False

