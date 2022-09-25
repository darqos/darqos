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


class SelectTimerState:
    """Timer state for select-based event loop."""

    def __init__(self, duration, listener):
        self.duration = duration
        self.listener = listener
        self.expiry = 0




class SelectEventLoop(EventLoopInterface):
    """A simple select-based event loop."""

    def __init__(self):
        """Constructor."""
        self.sockets: typing.Dict[socket.socket, typing.Any] = {}
        self.timers: typing.Dict[int, SelectTimerState] = {}
        self.active: bool = False

    def add_socket(self, sock: socket.socket, callback):
        self.sockets[sock] = callback

    def cancel_socket(self, sock: socket.socket):
        del self.sockets[sock]
        pass

    def add_timer(self, duration, callback) -> int:
        timer_id = len(self.timers)
        self.timers[timer_id] = SelectTimerState(time.time() + duration, callback)
        return timer_id

    def cancel_timer(self, timer_id: int):
        timer_info = self.timers[timer_id]
        del self.timers[timer_id]
        return

    def add_deferred(self, callback):
        self.add_timer(0, callback)

    def run(self):
        self.active = True

        while self.active:
            for t in self.timers.values():
                # FIXMD: get next timer expiry time, or fallback timeout
                pass

            rr, rw, _ = select.select(self.sockets, self.sockets, [], 0)

            for s in rr:
                listener = self.sockets[s]
                listener(s)

            for s in rw:
                listener = self.sockets[s]
                listener(s)

            for t in self.timers.values():
                now = time.time()
                if t.expiry < now:
                    t.expiry += t.duration
                    t.listener()

    def stop(self):
        self.active = False

