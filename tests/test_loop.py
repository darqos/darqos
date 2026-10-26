# darqos
# Copyright (C) 2026 David Arnold

# Tests for the select event loop.

import socket
import threading
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
    loop = SelectEventLoop()
    yield loop
    loop.close()


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


################################################################
# Posting from other threads
################################################################

def test_post_from_another_thread_wakes_loop(loop):
    # Without the wakeup, select() would wait for MAX_TIMEOUT.
    calls = []
    poster = threading.Timer(0.05, loop.post, args=(lambda: calls.append(threading.current_thread()),))
    poster.start()

    start = time.monotonic()
    while not calls:
        assert time.monotonic() - start < 2.0, "posted call not run"
        loop.next()
    poster.join()

    assert calls == [threading.current_thread()]


def test_posted_calls_run_in_order(loop):
    calls = []
    for i in range(5):
        loop.post(lambda i=i: calls.append(i))
    loop.next()
    assert calls == [0, 1, 2, 3, 4]


def test_call_posted_by_posted_call_runs_later(loop):
    calls = []

    def first():
        calls.append("first")
        loop.post(lambda: calls.append("second"))

    loop.post(first)
    loop.next()
    assert calls == ["first"]
    loop.next()
    assert calls == ["first", "second"]


def test_post_stops_running_loop(loop):
    threading.Timer(0.05, loop.post, args=(loop.stop,)).start()
    start = time.monotonic()
    loop.run()
    assert time.monotonic() - start < 2.0


################################################################
# Qt event loop
################################################################

@pytest.fixture
def qt_loop():
    from PyQt5.QtCore import QCoreApplication
    from darq.loop import QtEventLoop

    # Keep the application referenced while the loop is used: Qt can't
    # deliver events once it's gone.
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    loop = QtEventLoop()
    yield loop
    loop.close()
    del app


def run_qt(loop, seconds: float = 2.0):
    """Run the Qt loop until stopped, or for at most this long."""
    guard = threading.Timer(seconds, loop.post, args=(loop.stop,))
    guard.start()
    loop.run()
    guard.cancel()


def test_qt_post_from_another_thread(qt_loop):
    calls = []

    def call():
        calls.append(threading.current_thread())
        qt_loop.stop()

    poster = threading.Timer(0.05, qt_loop.post, args=(call,))
    poster.start()
    run_qt(qt_loop)
    poster.join()

    assert calls == [threading.current_thread()]


def test_qt_timer_repeats_at_its_interval(qt_loop):
    def stop_after_three(timer_id):
        if len(recorder.calls) == 3:
            qt_loop.cancel_timer(timer_id)
            qt_loop.stop()

    recorder = Recorder(stop_after_three)
    start = time.monotonic()
    timer_id = qt_loop.add_timer(0.05, recorder)
    run_qt(qt_loop)
    elapsed = time.monotonic() - start

    # The duration is in seconds: three 50ms intervals.
    assert len(recorder.calls) == 3
    assert 0.14 <= elapsed < 1.0
    assert {call[0] for call in recorder.calls} == {timer_id}

    # Scheduled times are one interval apart, and not after the actual times.
    expiries = [call[1] for call in recorder.calls]
    assert expiries == pytest.approx([start + 0.05 * n for n in (1, 2, 3)], abs=0.02)
    assert all(expiry <= actual for _, expiry, actual in recorder.calls)


def test_qt_cancelled_timer_does_not_fire(qt_loop):
    recorder = Recorder()
    timer_id = qt_loop.add_timer(0.01, recorder)
    assert qt_loop.cancel_timer(timer_id)
    assert not qt_loop.cancel_timer(timer_id)

    qt_loop.add_timer(0.1, Recorder(lambda timer_id: qt_loop.stop()))
    run_qt(qt_loop)
    assert recorder.calls == []


def test_qt_timer_ids_not_reused(qt_loop):
    first = qt_loop.add_timer(1.0, Recorder())
    qt_loop.cancel_timer(first)
    second = qt_loop.add_timer(1.0, Recorder())
    assert second != first

    # A stale identifier can't cancel the newer timer.
    assert not qt_loop.cancel_timer(first)
    assert qt_loop.cancel_timer(second)


def test_qt_deferred_runs_once(qt_loop):
    calls = []
    qt_loop.add_deferred(lambda: calls.append(threading.current_thread()))

    # Stop after the deferred call has had several chances to repeat.
    qt_loop.add_timer(0.05, Recorder(lambda timer_id: qt_loop.stop()))
    run_qt(qt_loop)

    assert calls == [threading.current_thread()]
    assert len(qt_loop.timers) == 1


################################################################
# Closing
################################################################

def test_close_releases_wakeup_sockets(loop):
    reader, writer = loop.posted.reader, loop.posted.writer
    loop.close()
    assert reader.fileno() == -1 and writer.fileno() == -1
    assert reader not in loop.sockets


def test_close_is_idempotent(loop):
    loop.close()
    loop.close()


def test_post_to_closed_loop_raises(loop):
    loop.close()
    with pytest.raises(RuntimeError):
        loop.post(lambda: None)


def test_close_leaves_registered_sockets_open(loop, sockets):
    a, _ = sockets
    loop.add_socket(a, SocketRecorder())
    loop.close()
    assert a.fileno() != -1


def test_qt_close(qt_loop):
    timer_id = qt_loop.add_timer(1.0, Recorder())
    reader = qt_loop.posted.reader
    qt_loop.close()

    assert reader.fileno() == -1
    assert qt_loop.timers == {}
    assert not qt_loop.cancel_timer(timer_id)
    with pytest.raises(RuntimeError):
        qt_loop.post(lambda: None)
    qt_loop.close()
