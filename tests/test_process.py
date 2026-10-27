# darqos
# Copyright (C) 2026 David Arnold

# Tests for the process-side port state: receiving delivered messages.

import threading
import time

import pytest

import darq
from darq import process


TIMEOUT = 5.0


@pytest.fixture
def port():
    state = darq.PortState(16385)
    state.is_open = True
    return state


def receive_in_thread(port, blocking=True):
    """Start a receive on another thread, returning (thread, result)."""
    result = {}

    def target():
        try:
            result["value"] = port.receive(blocking)
        except BaseException as e:
            result["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, result


def test_non_blocking_without_message_returns_none(port):
    start = time.monotonic()
    assert port.receive(blocking=False) is None
    assert time.monotonic() - start < 0.1


def test_timeout_without_message_returns_none(port):
    start = time.monotonic()
    assert port.receive(timeout=0.1) is None
    assert 0.09 <= time.monotonic() - start < 1


def test_timeout_returns_message_delivered_while_waiting(port):
    threading.Timer(0.05, port.deliver, args=(16386, b"soon", (3,))).start()
    assert port.receive(timeout=TIMEOUT) == (16386, b"soon", (3,))


def test_timeout_ignores_wake(port):
    threading.Timer(0.02, port.wake).start()
    start = time.monotonic()
    assert port.receive(timeout=0.1) is None
    assert time.monotonic() - start >= 0.09


def test_receives_delivered_message(port):
    port.deliver(16386, b"hello")
    assert port.receive(blocking=False) == (16386, b"hello", ())
    assert port.receive(blocking=False) is None


def test_messages_received_in_delivery_order(port):
    for i in range(3):
        port.deliver(16386, bytes([i]))
    assert [port.receive() for _ in range(3)] == [(16386, b"\x00", ()), (16386, b"\x01", ()), (16386, b"\x02", ())]


def test_blocking_receive_waits_for_delivery(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    assert thread.is_alive()

    port.deliver(16386, b"late")
    thread.join(TIMEOUT)
    assert result == {"value": (16386, b"late", ())}


def test_wake_without_message_keeps_waiting(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    port.wake()
    time.sleep(0.05)
    assert thread.is_alive()

    port.deliver(16386, b"after wake")
    thread.join(TIMEOUT)
    assert result == {"value": (16386, b"after wake", ())}


def test_close_releases_blocked_receiver(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    port.close()
    thread.join(TIMEOUT)
    assert isinstance(result.get("error"), darq.NonExistentPortError)


def test_closed_port_still_returns_delivered_messages(port):
    port.deliver(16386, b"before close")
    port.close()
    assert port.receive() == (16386, b"before close", ())
    with pytest.raises(darq.NonExistentPortError):
        port.receive(blocking=False)


def test_many_deliveries_from_another_thread(port):
    count = 1000

    def deliver():
        for i in range(count):
            port.deliver(16386, i.to_bytes(2, "little"))

    threading.Thread(target=deliver).start()
    received = [int.from_bytes(port.receive()[1], "little") for _ in range(count)]
    assert received == list(range(count))


################################################################
# Receiving from several ports
################################################################

@pytest.fixture
def ports():
    states = [darq.PortState(16385 + n) for n in range(3)]
    for state in states:
        state.is_open = True
    return states


def test_receive_any_in_delivery_order(ports):
    a, b, c = ports
    b.deliver(1, b"first")
    a.deliver(2, b"second")
    b.deliver(3, b"third")
    received = [process.receive_any(ports, blocking=False) for _ in range(3)]
    assert received == [(b, (1, b"first", ())), (a, (2, b"second", ())), (b, (3, b"third", ()))]
    assert process.receive_any(ports, blocking=False) is None


def test_receive_any_waits_for_any_port(ports):
    result = {}

    def target():
        result["value"] = process.receive_any(ports)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()

    ports[2].deliver(9, b"late", (4,))
    thread.join(TIMEOUT)
    assert result == {"value": (ports[2], (9, b"late", (4,)))}
    # The receiver's waiter is removed when it returns.
    assert all(not p._waiters for p in ports)


def test_receive_any_timeout(ports):
    start = time.monotonic()
    assert process.receive_any(ports, timeout=0.1) is None
    assert 0.09 <= time.monotonic() - start < 1


def test_receive_any_ignores_wake(ports):
    threading.Timer(0.02, ports[0].wake).start()
    assert process.receive_any(ports, timeout=0.1) is None


def test_receive_any_closed_port(ports):
    ports[1].deliver(1, b"before close")
    ports[1].close()
    assert process.receive_any(ports) == (ports[1], (1, b"before close", ()))
    with pytest.raises(darq.NonExistentPortError):
        process.receive_any(ports, blocking=False)


def test_receive_any_released_by_close(ports):
    result = {}

    def target():
        try:
            process.receive_any(ports)
        except BaseException as e:
            result["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    time.sleep(0.05)
    ports[0].close()
    thread.join(TIMEOUT)
    assert isinstance(result.get("error"), darq.NonExistentPortError)


def test_receive_any_needs_ports():
    with pytest.raises(ValueError):
        process.receive_any([])


def test_single_and_any_receivers_share_order(ports):
    a, b, _ = ports
    a.deliver(1, b"x")
    b.deliver(2, b"y")
    assert a.receive(blocking=False) == (1, b"x", ())
    assert process.receive_any(ports, blocking=False) == (b, (2, b"y", ()))


################################################################
# Port listeners
################################################################

class PostingLoop:
    """Stands in for an event loop: posted functions wait until run."""

    def __init__(self):
        self.posted = []
        self.closed = False

    def post(self, function):
        if self.closed:
            raise RuntimeError("event loop is closed")
        self.posted.append(function)

    def run_posted(self):
        posted, self.posted = self.posted, []
        for function in posted:
            function()


@pytest.fixture
def loop():
    return PostingLoop()


def test_listener_gets_messages_on_its_loop(port, loop):
    received = []
    port.set_listener(lambda *message: received.append(message), loop)
    assert port.has_listener()
    port.deliver(1, b"a", (7,))
    port.deliver(2, b"b")

    # Posted, not queued, and not called until the loop runs.
    assert received == [] and len(loop.posted) == 2
    assert port.receive(blocking=False) is None
    loop.run_posted()
    assert received == [(16385, 1, b"a", (7,)), (16385, 2, b"b", ())]


def test_listener_gets_queued_messages_first(port, loop):
    port.deliver(1, b"a")
    port.deliver(2, b"b")
    received = []
    port.set_listener(lambda *message: received.append(message[1:3]), loop)
    port.deliver(3, b"c")
    loop.run_posted()
    assert received == [(1, b"a"), (2, b"b"), (3, b"c")]
    assert port.receive(blocking=False) is None


def test_clearing_listener_queues_again(port, loop):
    received = []
    port.set_listener(lambda *message: received.append(message), loop)
    port.deliver(1, b"a")
    port.set_listener(None)
    assert not port.has_listener()
    port.deliver(2, b"b")
    assert port.receive(blocking=False) == (2, b"b", ())

    # Already posted: still goes to the listener.
    loop.run_posted()
    assert [m[2] for m in received] == [b"a"]


def test_listener_needs_a_loop(port):
    with pytest.raises(ValueError):
        port.set_listener(lambda *message: None)


def test_closed_loop_drops_messages(port, loop, caplog):
    port.set_listener(lambda *message: None, loop)
    loop.closed = True
    port.deliver(1, b"a")
    assert "event loop is closed" in caplog.text
    assert port.receive(blocking=False) is None


def test_listener_messages_from_another_thread(port, loop):
    received = []
    port.set_listener(lambda *message: received.append(message[2]), loop)
    threads = [threading.Thread(target=port.deliver, args=(n, bytes([n]))) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)
    loop.run_posted()
    assert sorted(received) == [bytes([n]) for n in range(20)]
