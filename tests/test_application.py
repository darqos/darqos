# darqos
# Copyright (C) 2026 David Arnold

# Tests for the process-side port state: receiving delivered messages.

import threading
import time

import pytest

import darq


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


def test_receives_delivered_message(port):
    port.deliver(16386, b"hello")
    assert port.receive(blocking=False) == (16386, b"hello")
    assert port.receive(blocking=False) is None


def test_messages_received_in_delivery_order(port):
    for i in range(3):
        port.deliver(16386, bytes([i]))
    assert [port.receive() for _ in range(3)] == [(16386, b"\x00"), (16386, b"\x01"), (16386, b"\x02")]


def test_blocking_receive_waits_for_delivery(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    assert thread.is_alive()

    port.deliver(16386, b"late")
    thread.join(TIMEOUT)
    assert result == {"value": (16386, b"late")}


def test_wake_without_message_keeps_waiting(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    port.wake()
    time.sleep(0.05)
    assert thread.is_alive()

    port.deliver(16386, b"after wake")
    thread.join(TIMEOUT)
    assert result == {"value": (16386, b"after wake")}


def test_close_releases_blocked_receiver(port):
    thread, result = receive_in_thread(port)
    time.sleep(0.05)
    port.close()
    thread.join(TIMEOUT)
    assert isinstance(result.get("error"), darq.NonExistentPortError)


def test_closed_port_still_returns_delivered_messages(port):
    port.deliver(16386, b"before close")
    port.close()
    assert port.receive() == (16386, b"before close")
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
