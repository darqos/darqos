# darqos
# Copyright (C) 2026 David Arnold

# Tests for the select event loop.

import socket
import time

import pytest

from darq.loop import SelectEventLoop, SocketListener, SocketNotFoundError, TimerListener


class Recorder(TimerListener):
    """Record each timeout, optionally running an action as well."""

    def __init__(self, action=None):
        self.calls = []
        self.action = action

    def on_timeout(self, timer_id: int, expiry_time: float, actual_time: float):
        self.calls.append((timer_id, expiry_time, actual_time))
        if self.action is not None:
            self.action(timer_id)


@pytest.fixture
def loop():
    return SelectEventLoop()


def run_for(loop, seconds: float):
    """Run the loop's iterations for about this long."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        loop.next()


def test_new_timer_not_due_yet(loop):
    recorder = Recorder()
    loop.add_timer(10.0, recorder)
    loop.run_timers()
    assert recorder.calls == []


def test_timeout_is_relative(loop):
    loop.add_timer(5.0, Recorder())
    assert 4.9 < loop.get_timeout() <= 5.0


def test_timeout_without_timers(loop):
    assert loop.get_timeout() == SelectEventLoop.MAX_TIMEOUT


def test_timeout_is_capped(loop):
    loop.add_timer(SelectEventLoop.MAX_TIMEOUT * 10, Recorder())
    assert loop.get_timeout() == SelectEventLoop.MAX_TIMEOUT


def test_overdue_timeout_is_zero(loop):
    loop.add_timer(0, Recorder())
    assert loop.get_timeout() == 0.0


def test_timeout_uses_earliest_timer(loop):
    loop.add_timer(5.0, Recorder())
    loop.add_timer(1.0, Recorder())
    loop.add_timer(3.0, Recorder())
    assert 0.9 < loop.get_timeout() <= 1.0


def test_select_waits_for_timer(loop):
    recorder = Recorder()
    loop.add_timer(0.1, recorder)

    start = time.monotonic()
    loop.next()
    elapsed = time.monotonic() - start

    assert 0.09 < elapsed < 1.0
    assert len(recorder.calls) == 1


def test_repeating_timer_fires_once_per_interval(loop):
    recorder = Recorder()
    loop.add_timer(0.05, recorder)
    run_for(loop, 0.275)
    assert 4 <= len(recorder.calls) <= 6


def test_listener_gets_scheduled_and_actual_times(loop):
    recorder = Recorder()
    before = time.monotonic()
    timer_id = loop.add_timer(0.05, recorder)
    loop.next()

    [(called_id, expiry, actual)] = recorder.calls
    assert called_id == timer_id
    assert before + 0.05 <= expiry <= actual


def test_missed_intervals_are_skipped(loop):
    recorder = Recorder()
    loop.add_timer(0.02, recorder)

    # Stall for several intervals: the timer fires once, not once per
    # missed interval, and its next expiry is in the future.
    time.sleep(0.1)
    loop.run_timers()
    loop.run_timers()
    assert len(recorder.calls) == 1
    assert loop.timers.get_next_expiry() > time.monotonic()


def test_timers_fire_in_expiry_order(loop):
    order = []
    loop.add_timer(0.03, Recorder(lambda _: order.append("late")))
    loop.add_timer(0.01, Recorder(lambda _: order.append("early")))
    time.sleep(0.05)
    loop.run_timers()
    assert order == ["early", "late"]


def test_cancelled_timer_does_not_fire(loop):
    recorder = Recorder()
    timer_id = loop.add_timer(0, recorder)
    assert loop.cancel_timer(timer_id)
    loop.run_timers()
    assert recorder.calls == []
    assert not loop.cancel_timer(timer_id)


def test_listener_cancels_another_due_timer(loop):
    victim = Recorder()
    victim_id = None

    def cancel_victim(_):
        loop.cancel_timer(victim_id)

    loop.add_timer(0, Recorder(cancel_victim))
    victim_id = loop.add_timer(0, victim)
    loop.run_timers()
    assert victim.calls == []
    assert len(loop.timers) == 1


def test_listener_cancels_itself(loop):
    # Cancelling must not make the loop skip the next due timer.
    other = Recorder()
    loop.add_timer(0, Recorder(lambda timer_id: loop.cancel_timer(timer_id)))
    loop.add_timer(0, other)
    loop.run_timers()
    assert len(other.calls) == 1


def test_timer_added_by_listener_waits_for_next_pass(loop):
    added = Recorder()
    loop.add_timer(0, Recorder(lambda timer_id: (loop.cancel_timer(timer_id),
                                                 loop.add_timer(0, added))))
    loop.run_timers()
    assert added.calls == []
    loop.run_timers()
    assert len(added.calls) == 1


def test_deferred_runs_once(loop):
    calls = []
    loop.add_deferred(lambda: calls.append(1))
    loop.next()
    loop.run_timers()
    assert calls == [1]
    assert len(loop.timers) == 0


def test_deferred_runs_without_waiting(loop):
    loop.add_deferred(lambda: None)
    start = time.monotonic()
    loop.next()
    assert time.monotonic() - start < 0.5


################################################################
# Socket monitoring
################################################################

class SocketRecorder(SocketListener):
    def __init__(self):
        self.readable = []
        self.writeable = []

    def on_readable(self, sock):
        self.readable.append(sock)
        sock.recv(65536)

    def on_writeable(self, sock):
        self.writeable.append(sock)


@pytest.fixture
def sockets():
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


def test_idle_socket_does_not_spin(loop, sockets):
    # A registered, writable socket must not wake select() by itself.
    a, _ = sockets
    recorder = SocketRecorder()
    loop.add_socket(a, recorder)
    loop.MAX_TIMEOUT = 0.1

    start = time.monotonic()
    loop.next()
    assert time.monotonic() - start >= 0.09
    assert recorder.writeable == []


def test_readable_socket(loop, sockets):
    a, b = sockets
    recorder = SocketRecorder()
    loop.add_socket(a, recorder)
    b.send(b"x")
    loop.next()
    assert recorder.readable == [a]


def test_write_interest(loop, sockets):
    a, _ = sockets
    recorder = SocketRecorder()
    loop.add_socket(a, recorder)
    loop.MAX_TIMEOUT = 0.1

    loop.set_write_interest(a, True)
    start = time.monotonic()
    loop.next()
    assert time.monotonic() - start < 0.09
    assert recorder.writeable == [a]

    loop.set_write_interest(a, False)
    loop.next()
    assert recorder.writeable == [a]


def test_cancel_socket_clears_write_interest(loop, sockets):
    a, _ = sockets
    loop.add_socket(a, SocketRecorder())
    loop.set_write_interest(a, True)
    loop.cancel_socket(a)
    assert a not in loop.writers


def test_write_interest_needs_registered_socket(loop, sockets):
    with pytest.raises(SocketNotFoundError):
        loop.set_write_interest(sockets[0], True)
