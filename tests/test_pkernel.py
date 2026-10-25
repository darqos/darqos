# darqos
# Copyright (C) 2026 David Arnold

# Tests for p-kernel request handling.
#
# Each simulated process has a socket pair: the kernel holds one end,
# and the test reads the kernel's messages from the other.

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import typing

import pytest

import darq


PKERNEL_PATH = pathlib.Path(__file__).parent.parent / "kernel" / "pkernel"


def load_pkernel() -> typing.Any:
    """Import kernel/pkernel as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("pkernel", str(PKERNEL_PATH))
    spec = importlib.util.spec_from_loader("pkernel", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def pkernel():
    module = load_pkernel()

    # Listen on an ephemeral port, so as not to clash with a real p-kernel.
    module.TCP_PORT = 0
    kernel = module.PseudoKernel()
    kernel.module = module
    kernel.peers = []

    # Messages read from a process' peer socket, but not yet checked by
    # the test, by peer (see send() and delivered(pkernel, )).
    kernel.unread = {}

    # Tests run the loop to check it copes, not to wait for events: don't
    # let an idle iteration block for long.
    kernel._loop.MAX_TIMEOUT = 0.05

    yield kernel

    kernel.close()
    for peer in kernel.peers:
        peer.close()


def add_running_process(kernel, dpid: int):
    """Add a connected, running process.  Returns (process, peer socket)."""
    module = kernel.module
    kernel_sock, peer = socket.socketpair()
    peer.setblocking(False)
    kernel.peers += [kernel_sock, peer]

    process = module.KernelProcessState(kernel, dpid, "/unused")
    process.set_socket(kernel_sock)
    process.set_status(module.KernelProcessStatus.RUNNING)
    kernel._process_table[dpid] = process
    kernel._processes[kernel_sock] = process
    kernel._loop.add_socket(kernel_sock, kernel)
    return process, peer


def send_to_kernel(kernel, process, message: darq.Message):
    """Deliver a message from a process to the kernel."""
    process.get_buffer().append(message.encode())
    kernel.dispatch(process)


def receive(peer: socket.socket) -> bytes:
    """Return whatever the kernel has sent to a process (maybe nothing)."""
    try:
        return peer.recv(65536)
    except BlockingIOError:
        return b""


def read_run_response(peer: socket.socket) -> int:
    """Read the kernel's RunResponse to a process, and return its result."""
    response = darq.RunResponse()
    response.decode(peer.recv(response.get_length()))
    return response.result


def test_kill_process_forwarded_to_target(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, target_peer = add_running_process(pkernel, 2)

    send_to_kernel(pkernel, caller, darq.KillProcessRequest(5, 2))

    forwarded = darq.KillProcessRequest()
    forwarded.decode(receive(target_peer))
    assert forwarded.dpid == 2

    # No response until the target exits.
    assert receive(caller_peer) == b""


def test_kill_process_answered_on_target_exit(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, target_peer = add_running_process(pkernel, 2)

    send_to_kernel(pkernel, caller, darq.KillProcessRequest(5, 2))
    pkernel.handle_disconnect(target)

    response = darq.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert (response.request_id, response.result) == (5, 0)
    assert target.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE
    assert target.get_socket() is None


def test_kill_process_no_such_process(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, caller, darq.KillProcessRequest(5, 99))

    response = darq.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert response.request_id == 5
    assert response.result != 0


def test_kill_process_not_running(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, _ = add_running_process(pkernel, 2)
    pkernel.handle_disconnect(target)
    assert target.get_status() == pkernel.module.KernelProcessStatus.LOST

    send_to_kernel(pkernel, caller, darq.KillProcessRequest(5, 2))

    response = darq.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert response.result != 0


def test_kill_self(pkernel):
    process, peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, process, darq.KillProcessRequest(5, 1))
    forwarded = darq.KillProcessRequest()
    forwarded.decode(receive(peer))
    assert forwarded.dpid == 1

    # The requester is the process that exited: there's no-one to answer.
    pkernel.handle_disconnect(process)
    assert process.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE


def test_running_hands_socket_to_process(pkernel):
    module = pkernel.module
    kernel_sock, peer = socket.socketpair()
    pkernel.peers += [kernel_sock, peer]

    process = module.KernelProcessState(pkernel, 3, "/unused")
    process.set_status(module.KernelProcessStatus.SPAWNED)
    pkernel._process_table[3] = process
    reader = module.TemporaryReader(kernel=pkernel, sock=kernel_sock)
    pkernel.incoming_connections[kernel_sock] = reader

    running = darq.RunRequest(dpid=3)
    running.host_pid = 1234
    pkernel.handle_run_request(reader, running)

    assert kernel_sock not in pkernel.incoming_connections
    assert process.get_socket() is kernel_sock
    assert process.get_status() == module.KernelProcessStatus.RUNNING
    assert process.get_host_pid() == 1234

    # The process is told it may start its application.
    assert read_run_response(peer) == 0


################################################################
# spawn_process
################################################################

class FakePopen:
    """Record the stub command line, rather than starting a process."""
    calls: list[list[str]] = []

    envs: list[dict[str, str] | None] = []

    def __init__(self, args, **kwargs):
        FakePopen.calls.append(args)
        FakePopen.envs.append(kwargs.get("env"))
        assert "shell" not in kwargs


@pytest.fixture
def fake_popen(pkernel, monkeypatch):
    FakePopen.calls = []
    FakePopen.envs = []
    monkeypatch.setattr(pkernel.module.subprocess, "Popen", FakePopen)
    return FakePopen


@pytest.fixture
def executable(tmp_path):
    (tmp_path / "main.py").write_text("def darq_main():\n    pass\n")
    return str(tmp_path)


def connect_spawned_process(kernel, dpid: int):
    """Connect a spawned process, and have it report that it's running."""
    module = kernel.module
    kernel_sock, peer = socket.socketpair()
    kernel.peers += [kernel_sock, peer]

    reader = module.TemporaryReader(kernel=kernel, sock=kernel_sock)
    kernel.incoming_connections[kernel_sock] = reader
    kernel.handle_run_request(reader, darq.RunRequest(dpid=dpid))


def test_spawn_process_starts_stub(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    pkernel._next_dpid = 2

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, executable))

    module = pkernel.module
    port = str(pkernel.socket.getsockname()[1])
    assert fake_popen.calls == [[module.PYTHON_IMAGE_LOADER, port, "2", "1", executable]]
    child = pkernel._process_table[2]
    assert child.get_status() == module.KernelProcessStatus.SPAWNED
    assert child.get_parent_dpid() == 1

    # No response until the new process is running.
    assert receive(parent_peer) == b""


def test_spawn_process_answered_when_running(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    pkernel._next_dpid = 2

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, executable))
    connect_spawned_process(pkernel, 2)

    response = darq.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert (response.request_id, response.result, response.dpid) == (5, 0, 2)
    assert response.get_error() == ""


def test_spawn_process_no_main(pkernel, fake_popen, tmp_path):
    parent, parent_peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, str(tmp_path)))

    response = darq.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert response.request_id == 5
    assert response.result != 0
    assert "main.py" in response.get_error()
    assert fake_popen.calls == []


def test_spawn_process_stub_fails(pkernel, executable, monkeypatch):
    def popen_fails(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(pkernel.module.subprocess, "Popen", popen_fails)

    parent, parent_peer = add_running_process(pkernel, 1)
    table_size = len(pkernel._process_table)

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, executable))

    response = darq.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert response.result != 0
    assert "No such file" in response.get_error()
    assert len(pkernel._process_table) == table_size


def test_spawn_process_sets_pythonpath(pkernel, fake_popen, executable, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/existing")
    parent, _ = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, executable))

    darq_root = pathlib.Path(darq.__file__).resolve().parent.parent
    paths = fake_popen.envs[0]["PYTHONPATH"].split(os.pathsep)
    assert paths == [str(darq_root), "/existing"]


def test_loader_environment_without_pythonpath(pkernel, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = pkernel.get_loader_environment()
    assert env["PYTHONPATH"] == pkernel.module.DARQ_PYTHON_PATH


def test_python_image_loader_beside_pkernel(pkernel):
    assert pathlib.Path(pkernel.module.PYTHON_IMAGE_LOADER) == PKERNEL_PATH.resolve().parent / "pyloader"
    assert os.access(pkernel.module.PYTHON_IMAGE_LOADER, os.X_OK)


################################################################
# End-to-end: real pyloader processes
################################################################

E2E_TIMEOUT = 15.0


@pytest.fixture
def live_pkernel(pkernel, monkeypatch):
    """The p-kernel, spawning real pyloader processes with this interpreter."""
    monkeypatch.setenv("PATH", os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"])

    yield pkernel

    for process in pkernel._process_table.values():
        child = process.get_child()
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()


def run_kernel_until(kernel, condition, what: str):
    """Run the p-kernel's event loop until condition() is true.

    The condition may not depend on any loop event: it's polled each
    iteration, which the pkernel fixture keeps short."""
    deadline = time.monotonic() + E2E_TIMEOUT
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        kernel._loop.next()


def write_app(directory: pathlib.Path, source: str) -> str:
    directory.mkdir()
    (directory / "main.py").write_text(textwrap.dedent(source))
    return str(directory)


def test_kill_spawned_process(live_pkernel, tmp_path):
    kernel = live_pkernel
    module = kernel.module
    results = tmp_path / "results.json"

    # The child just sleeps until it's killed.
    child_path = write_app(tmp_path / "child", """
        import time

        def darq_main():
            while True:
                time.sleep(0.01)
        """)

    # The parent spawns the child, kills it, and reports the results.
    parent_path = write_app(tmp_path / "parent", f"""
        import json
        import darq

        def darq_main():
            dpid = darq.spawn_process({child_path!r})
            darq.kill_process(dpid)
            with open({str(results)!r} + ".tmp", "w") as f:
                json.dump({{"dpid": dpid}}, f)
            import os
            os.rename({str(results)!r} + ".tmp", {str(results)!r})
        """)

    parent_dpid = kernel.do_spawn_process(None, parent_path)
    run_kernel_until(kernel, results.exists, "the parent to kill its child")

    child_dpid = json.loads(results.read_text())["dpid"]
    assert child_dpid != parent_dpid
    child = kernel._process_table[child_dpid]

    # The kernel has cleaned up its state for the child.
    assert child.get_parent_dpid() == parent_dpid
    assert child.get_status() == module.KernelProcessStatus.ZOMBIE
    assert child.get_socket() is None
    assert child not in kernel._processes.values()
    assert child.take_kill_requests() == []

    # The kernel has reaped the child's host process, which exited cleanly.
    run_kernel_until(kernel, lambda: child.get_child().returncode is not None,
                     "the kernel to reap the child")
    assert child.get_child().returncode == 0

    # The parent is unaffected: it finishes normally, and reports success.
    parent = kernel._process_table[parent_dpid]
    run_kernel_until(kernel, lambda: parent.get_child().returncode is not None,
                     "the parent to exit")
    assert parent.get_status() == module.KernelProcessStatus.ZOMBIE
    assert (parent.get_exit_status(), parent.get_exit_error()) == (0, None)
    assert parent.get_child().returncode == 0


def test_spawn_process_exits_before_running(live_pkernel, executable):
    kernel = live_pkernel
    module = kernel.module
    parent, parent_peer = add_running_process(kernel, 1)
    kernel._next_dpid = 2

    # A loader that exits at once, without connecting to the p-kernel.
    module.PYTHON_IMAGE_LOADER = shutil.which("false")
    send_to_kernel(kernel, parent, darq.SpawnProcessRequest(5, executable))

    child = kernel._process_table[2]
    run_kernel_until(kernel, lambda: child.get_child().returncode is not None,
                     "the kernel to reap the child")
    assert child.get_status() == module.KernelProcessStatus.ZOMBIE

    response = darq.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert response.request_id == 5
    assert response.result != 0
    assert "before running" in response.get_error()


def test_getppid_of_spawned_process(live_pkernel, tmp_path):
    kernel = live_pkernel
    parent_ids = tmp_path / "parent.json"
    child_ids = tmp_path / "child.json"

    # Each process reports its own getpid() and getppid().
    report = """
        import json
        import os
        import darq

        def report(path):
            with open(path + ".tmp", "w") as f:
                json.dump({"pid": darq.getpid(), "ppid": darq.getppid()}, f)
            os.rename(path + ".tmp", path)
        """

    child_path = write_app(tmp_path / "child", report + f"""
        def darq_main():
            report({str(child_ids)!r})
        """)

    parent_path = write_app(tmp_path / "parent", report + f"""
        def darq_main():
            darq.spawn_process({child_path!r})
            report({str(parent_ids)!r})
        """)

    parent_dpid = kernel.do_spawn_process(None, parent_path)
    run_kernel_until(kernel, lambda: parent_ids.exists() and child_ids.exists(),
                     "both processes to report their identifiers")

    parent = json.loads(parent_ids.read_text())
    child = json.loads(child_ids.read_text())

    # Started by the p-kernel itself, the parent's parent is zero.
    assert parent == {"pid": parent_dpid, "ppid": 0}
    assert child["pid"] != parent_dpid
    assert child["ppid"] == parent_dpid


def test_exited_recorded(pkernel):
    process, peer = add_running_process(pkernel, 3)

    send_to_kernel(pkernel, process, darq.Exited(7, "it broke"))
    assert process.get_exit_status() == 7
    assert process.get_exit_error() == "it broke"

    # The process then closes its session: it's a zombie, not lost.
    pkernel.handle_disconnect(process)
    assert process.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE
    assert process.get_socket() is None
    assert receive(peer) == b""


def test_exited_without_error(pkernel):
    process, _ = add_running_process(pkernel, 3)
    send_to_kernel(pkernel, process, darq.Exited(0))
    assert (process.get_exit_status(), process.get_exit_error()) == (0, None)


@pytest.mark.parametrize("body, status, error_end", [
    ("return 7", 7, None),
    ("raise ValueError('boom')", 1, "ValueError: boom"),
])
def test_spawned_process_exit(live_pkernel, tmp_path, body, status, error_end):
    kernel = live_pkernel
    module = kernel.module
    path = write_app(tmp_path / "app", f"""
        def darq_main():
            {body}
        """)

    dpid = kernel.do_spawn_process(None, path)
    process = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: process.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the process to exit")

    assert process.get_exit_status() == status
    if error_end is None:
        assert process.get_exit_error() is None
    else:
        assert process.get_exit_error().rstrip().endswith(error_end)

    # The loader exits by itself, with the application's status.
    run_kernel_until(kernel, lambda: process.get_child().returncode is not None,
                     "the kernel to reap the process")
    assert process.get_child().returncode == status


def test_spawned_process_receives_its_own_message(live_pkernel, tmp_path):
    # A message sent through the p-kernel, back to the sending port.
    kernel = live_pkernel
    module = kernel.module
    path = write_app(tmp_path / "app", """
        import darq
        def darq_main():
            port = darq.open_port()
            darq.send_message(port, port, b"ping")
            source, payload = darq.receive_message(port)
            if (source, payload) != (port, b"ping"):
                return f"received {(source, payload)!r}"
            if darq.receive_message(port, blocking=False) is not None:
                return "received a second message"
            return 0
        """)

    dpid = kernel.do_spawn_process(None, path)
    process = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: process.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the process to exit")
    assert (process.get_exit_status(), process.get_exit_error()) == (0, None)


def test_running_with_unknown_dpid(pkernel):
    module = pkernel.module
    kernel_sock, peer = socket.socketpair()
    pkernel.peers += [kernel_sock, peer]

    # As if accepted from the listening socket.
    reader = module.TemporaryReader(kernel=pkernel, sock=kernel_sock)
    pkernel.incoming_connections[kernel_sock] = reader
    pkernel._loop.add_socket(kernel_sock, pkernel)

    pkernel.handle_run_request(reader, darq.RunRequest(dpid=99))

    # The process is refused, then its connection is dropped, and the loop
    # no longer watches it.
    assert kernel_sock.fileno() == -1
    assert kernel_sock not in pkernel._loop.sockets
    assert kernel_sock not in pkernel.incoming_connections
    assert read_run_response(peer) != 0
    assert peer.recv(1) == b""
    pkernel._loop.next()


def accept_connection(kernel):
    """Add a connection, as if accepted from the listening socket."""
    kernel_sock, peer = socket.socketpair()
    kernel.peers += [kernel_sock, peer]
    reader = kernel.module.TemporaryReader(kernel=kernel, sock=kernel_sock)
    kernel.incoming_connections[kernel_sock] = reader
    kernel._loop.add_socket(kernel_sock, kernel)
    return reader, kernel_sock, peer


def test_running_for_running_process_rejected(pkernel):
    process, process_peer = add_running_process(pkernel, 3)
    original_sock = process.get_socket()

    reader, kernel_sock, peer = accept_connection(pkernel)
    pkernel.handle_run_request(reader, darq.RunRequest(dpid=3))

    # The impostor is refused and dropped, and the running process is untouched.
    assert kernel_sock.fileno() == -1
    assert kernel_sock not in pkernel._loop.sockets
    assert read_run_response(peer) != 0
    assert peer.recv(1) == b""
    assert process.get_socket() is original_sock
    assert pkernel._processes[original_sock] is process
    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    pkernel._loop.next()


@pytest.mark.parametrize("status", ["ALLOCATED", "ZOMBIE", "LOST"])
def test_running_for_non_spawned_process_rejected(pkernel, status):
    module = pkernel.module
    process = module.KernelProcessState(pkernel, 3, "/unused")
    process.set_status(module.KernelProcessStatus[status])
    pkernel._process_table[3] = process

    reader, kernel_sock, peer = accept_connection(pkernel)
    pkernel.handle_run_request(reader, darq.RunRequest(dpid=3))

    assert kernel_sock.fileno() == -1
    assert read_run_response(peer) != 0
    assert process.get_socket() is None
    assert process.get_status() == module.KernelProcessStatus[status]


################################################################
# Connections before RUN_REQUEST
################################################################

def add_spawned_process(kernel, dpid: int):
    module = kernel.module
    process = module.KernelProcessState(kernel, dpid, "/unused")
    process.set_status(module.KernelProcessStatus.SPAWNED)
    kernel._process_table[dpid] = process
    return process


def assert_incoming_dropped(kernel, kernel_sock, peer):
    assert kernel_sock.fileno() == -1
    assert kernel_sock not in kernel._loop.sockets
    assert kernel_sock not in kernel.incoming_connections
    assert peer.recv(1) == b""
    kernel._loop.next()


def header_with_length(message_type: int, length: int) -> bytes:
    message = darq.Message(message_type)
    message.set_length(length)
    return message.encode()


def test_disconnect_before_running(pkernel):
    reader, kernel_sock, peer = accept_connection(pkernel)
    peer.shutdown(socket.SHUT_WR)

    reader.on_readable(kernel_sock)

    assert kernel_sock.fileno() == -1
    assert kernel_sock not in pkernel._loop.sockets
    assert kernel_sock not in pkernel.incoming_connections
    pkernel._loop.next()


def test_other_message_before_running(pkernel):
    reader, kernel_sock, peer = accept_connection(pkernel)
    peer.sendall(darq.OpenPortRequest(1).encode())

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_invalid_length_before_running(pkernel):
    reader, kernel_sock, peer = accept_connection(pkernel)
    peer.sendall(header_with_length(darq.MessageType.RUN_REQUEST, 4))

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_running_split_across_reads(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)
    running = darq.RunRequest(dpid=3).encode()

    peer.sendall(running[:5])
    reader.on_readable(kernel_sock)
    assert process.get_status() == pkernel.module.KernelProcessStatus.SPAWNED

    peer.sendall(running[5:])
    reader.on_readable(kernel_sock)
    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert process.get_socket() is kernel_sock


def test_second_running_from_registered_process(pkernel):
    process, peer = add_running_process(pkernel, 3)
    sock = process.get_socket()

    send_to_kernel(pkernel, process, darq.RunRequest(dpid=3))

    # Ignored: the process keeps its connection and state.
    assert process.get_socket() is sock
    assert pkernel._processes[sock] is process
    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert receive(peer) == b""


def test_pcall_in_same_read_as_running(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)

    peer.sendall(darq.RunRequest(dpid=3).encode() + darq.OpenPortRequest(7).encode())
    reader.on_readable(kernel_sock)

    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert read_run_response(peer) == 0
    response = darq.OpenPortResponse()
    response.decode(peer.recv(65536))
    assert response.request_id == 7
    assert response.result == 0


################################################################
# Ports and handles
################################################################

def received_messages(peer: socket.socket) -> list[tuple[int, bytes]]:
    """Return the messages the kernel has sent to a process, as (type, bytes)."""
    buf = darq.Buffer()
    buf.append(receive(peer))
    messages = []
    while buf.length() >= 8:
        length = darq.Message.decode_length(buf.peek(8))
        message = buf.peek(length)
        buf.consume(length)
        messages.append((darq.Message.decode_type(message), message))
    return messages


def open_port(kernel, process, peer, request_id: int = 1) -> int:
    """Open a port for a process, returning its handle."""
    send_to_kernel(kernel, process, darq.OpenPortRequest(request_id))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == darq.MessageType.OPEN_PORT_RESP
    response = darq.OpenPortResponse()
    response.decode(buf)
    assert (response.request_id, response.result) == (request_id, 0)
    return response.port


def close_port(kernel, process, peer, handle: int) -> int:
    """Close a process' handle, returning the result."""
    send_to_kernel(kernel, process, darq.ClosePortRequest(1, handle))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == darq.MessageType.CLOSE_PORT_RESP
    response = darq.ClosePortResponse()
    response.decode(buf)
    assert response.port == handle
    return response.result


def send(kernel, process, source: int, destination: int, payload: bytes) -> int:
    """Send a message from a process, returning the SendResult.

    Anything else the process received meanwhile (such as a message to
    its own port) is kept for delivered(pkernel, )."""
    message = darq.SendMessageRequest(source, destination, request_id=9)
    message.set_payload(payload)
    send_to_kernel(kernel, process, message)

    results = []
    peer = process_peer(kernel, process)
    for message_type, buf in received_messages(peer):
        if message_type == darq.MessageType.SEND_MESSAGE_RESP:
            response = darq.SendMessageResponse()
            response.decode(buf)
            assert response.request_id == 9
            results.append(response.result)
        else:
            kernel.unread.setdefault(peer, []).append((message_type, buf))
    [result] = results
    return result


def process_peer(kernel, process) -> socket.socket:
    """Return the test's end of a process' p-call session."""
    kernel_sock = process.get_socket()
    index = kernel.peers.index(kernel_sock)
    return kernel.peers[index + 1]


def delivered(kernel, peer: socket.socket) -> list[tuple[int, int, bytes]]:
    """Return the messages delivered to a process, as (source, destination, payload)."""
    messages = []
    for message_type, buf in kernel.unread.pop(peer, []) + received_messages(peer):
        assert message_type == darq.MessageType.DELIVER_MESSAGE
        message = darq.DeliverMessage()
        message.decode(buf)
        messages.append((message.source, message.destination, message.payload))
    return messages


def grant_send_right(kernel, holder, owner, handle: int) -> int:
    """Give a process a send right to another's port (until there's a registry)."""
    port = owner.get_handle(handle).port
    return holder.add_right(port, kernel.module.PortRight.SEND)


def test_open_port_gives_receive_right(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    entry = process.get_handle(handle)
    assert entry.right == pkernel.module.PortRight.RECEIVE
    assert entry.port.owner is process
    assert entry.port.alive
    assert list(pkernel._ports.values()) == [entry.port]


def test_handles_count_from_one_and_are_not_reused(pkernel):
    process, peer = add_running_process(pkernel, 1)
    assert [open_port(pkernel, process, peer, i) for i in (1, 2)] == [1, 2]

    assert close_port(pkernel, process, peer, 1) == 0
    assert open_port(pkernel, process, peer, 3) == 3


def test_handles_are_per_process(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)

    # Each process' first handle is 1, but they're different ports.
    assert open_port(pkernel, a, a_peer) == 1
    assert open_port(pkernel, b, b_peer) == 1
    assert a.get_handle(1).port is not b.get_handle(1).port


def test_close_receive_right_destroys_port(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    port = process.get_handle(handle).port

    assert close_port(pkernel, process, peer, handle) == 0
    assert process.get_handle(handle) is None
    assert not port.alive
    assert pkernel._ports == {}


def test_close_send_right_keeps_port(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert close_port(pkernel, a, a_peer, a_to_b) == 0
    assert a.get_handle(a_to_b) is None
    assert b.get_handle(b_port).port.alive


def test_close_bad_handle(pkernel):
    process, peer = add_running_process(pkernel, 1)
    assert close_port(pkernel, process, peer, 99) == 1


def test_send_translates_handles(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)

    # Give A's first handle a different number from B's, to show the
    # translation.
    open_port(pkernel, a, a_peer, 2)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    assert a_to_b == 3

    assert send(pkernel, a, a_port, a_to_b, b"ping") == darq.SendResult.OK

    # B receives on its own handle, and gets a send right to A's port.
    [(source, destination, payload)] = delivered(pkernel, b_peer)
    assert (destination, payload) == (b_port, b"ping")
    entry = b.get_handle(source)
    assert entry.right == pkernel.module.PortRight.SEND
    assert entry.port is a.get_handle(a_port).port

    # B replies to the source: A sees its existing handle for B's port.
    assert send(pkernel, b, b_port, source, b"pong") == darq.SendResult.OK
    assert delivered(pkernel, a_peer) == [(a_to_b, a_port, b"pong")]


def test_reply_right_reuses_handle(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert send(pkernel, a, a_port, a_to_b, b"one") == darq.SendResult.OK
    assert send(pkernel, a, a_port, a_to_b, b"two") == darq.SendResult.OK

    [(first, _, _), (second, _, _)] = delivered(pkernel, b_peer)
    assert first == second


def test_send_answered_after_delivery(pkernel):
    # A message to the sender's own port is delivered before the send is
    # answered, so it's queued by the time send_message() returns.
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    message = darq.SendMessageRequest(handle, handle, request_id=4)
    send_to_kernel(pkernel, process, message)
    types = [message_type for message_type, _ in received_messages(peer)]
    assert types == [darq.MessageType.DELIVER_MESSAGE, darq.MessageType.SEND_MESSAGE_RESP]


def test_send_to_own_port(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert send(pkernel, process, handle, handle, b"self") == darq.SendResult.OK
    assert delivered(pkernel, peer) == [(handle, handle, b"self")]


def test_send_from_send_right_refused(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    # A can't send from B's port: replies would go to B.
    assert send(pkernel, a, a_to_b, a_to_b, b"spoof") == darq.SendResult.NOT_RECEIVE_RIGHT
    assert delivered(pkernel, b_peer) == []


@pytest.mark.parametrize("bad_handle", [0, 99])
def test_send_with_bad_handle_refused(pkernel, bad_handle):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert send(pkernel, process, handle, bad_handle, b"nowhere") == darq.SendResult.BAD_HANDLE
    assert send(pkernel, process, bad_handle, handle, b"from nowhere") == darq.SendResult.BAD_HANDLE
    assert delivered(pkernel, peer) == []


def test_send_to_closed_port_refused(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    close_port(pkernel, b, b_peer, b_port)

    assert send(pkernel, a, a_port, a_to_b, b"too late") == darq.SendResult.DEAD_PORT
    assert delivered(pkernel, b_peer) == []

    # A's handle remains, but is dead.
    assert not a.get_handle(a_to_b).port.alive


def test_disconnect_destroys_owned_ports(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    port = b.get_handle(b_port).port

    pkernel.handle_disconnect(b)

    assert not port.alive
    assert list(pkernel._ports.values()) == [a.get_handle(a_port).port]
    assert b.get_handle(b_port) is None
    assert send(pkernel, a, a_port, a_to_b, b"gone") == darq.SendResult.DEAD_PORT
    assert delivered(pkernel, b_peer) == []


################################################################
# Name registry
################################################################

def register_name(kernel, process, peer, name: str, handle: int) -> int:
    """Register a name for a process' port, returning the result."""
    send_to_kernel(kernel, process, darq.RegisterNameRequest(1, name, handle))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == darq.MessageType.REGISTER_NAME_RESP
    response = darq.RegisterNameResponse()
    response.decode(buf)
    return response.result


def lookup_responses(peer) -> list[tuple[int, int, int]]:
    """Return the lookup responses sent to a process, as (request_id, result, handle)."""
    responses = []
    for message_type, buf in received_messages(peer):
        assert message_type == darq.MessageType.LOOKUP_NAME_RESP
        response = darq.LookupNameResponse()
        response.decode(buf)
        responses.append((response.request_id, response.result, response.port))
    return responses


def lookup_name(kernel, process, peer, name: str, blocking: bool = False,
                request_id: int = 1) -> tuple[int, int] | None:
    """Look up a name for a process: (result, handle), or None if unanswered."""
    send_to_kernel(kernel, process, darq.LookupNameRequest(request_id, name, blocking))
    responses = lookup_responses(peer)
    if not responses:
        return None
    [(response_id, result, handle)] = responses
    assert response_id == request_id
    return result, handle


def user_process(kernel, dpid: int):
    """Add a running process spawned by another process, not the kernel."""
    process, peer = add_running_process(kernel, dpid)
    process.set_parent_dpid(1)
    return process, peer


def test_register_and_lookup(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    client, client_peer = user_process(pkernel, 2)
    service_port = open_port(pkernel, service, service_peer)
    client_port = open_port(pkernel, client, client_peer)

    assert register_name(pkernel, service, service_peer, "test.service", service_port) == 0
    answer = lookup_name(pkernel, client, client_peer, "test.service")
    assert answer is not None
    result, handle = answer
    assert result == darq.NameResult.OK
    entry = client.get_handle(handle)
    assert entry.right == pkernel.module.PortRight.SEND
    assert entry.port is service.get_handle(service_port).port

    # The looked-up handle reaches the service.
    assert send(pkernel, client, client_port, handle, b"hello") == darq.SendResult.OK
    [(source, destination, payload)] = delivered(pkernel, service_peer)
    assert (destination, payload) == (service_port, b"hello")


def test_repeated_lookup_returns_same_handle(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    client, client_peer = user_process(pkernel, 2)
    register_name(pkernel, service, service_peer, "test.service", open_port(pkernel, service, service_peer))

    first = lookup_name(pkernel, client, client_peer, "test.service")
    second = lookup_name(pkernel, client, client_peer, "test.service", request_id=2)
    assert first == second


def test_lookup_of_own_name_returns_receive_handle(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    register_name(pkernel, process, peer, "test.self", handle)

    assert lookup_name(pkernel, process, peer, "test.self") == (darq.NameResult.OK, handle)


def test_non_blocking_lookup_not_found(pkernel):
    process, peer = add_running_process(pkernel, 1)
    assert lookup_name(pkernel, process, peer, "test.missing") == (darq.NameResult.NOT_FOUND, 0)


def test_blocking_lookup_answered_on_registration(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    a, a_peer = user_process(pkernel, 2)
    b, b_peer = user_process(pkernel, 3)

    assert lookup_name(pkernel, a, a_peer, "test.later", blocking=True, request_id=5) is None
    assert lookup_name(pkernel, b, b_peer, "test.later", blocking=True, request_id=6) is None

    service_port = open_port(pkernel, service, service_peer)
    assert register_name(pkernel, service, service_peer, "test.later", service_port) == 0

    port = service.get_handle(service_port).port
    [(request_id, result, handle)] = lookup_responses(a_peer)
    assert (request_id, result) == (5, darq.NameResult.OK)
    assert a.get_handle(handle).port is port
    [(request_id, result, handle)] = lookup_responses(b_peer)
    assert (request_id, result) == (6, darq.NameResult.OK)
    assert b.get_handle(handle).port is port
    assert pkernel._name_waiters == {}


def test_waiting_lookup_abandoned_on_disconnect(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    client, client_peer = user_process(pkernel, 2)
    lookup_name(pkernel, client, client_peer, "test.later", blocking=True)

    pkernel.handle_disconnect(client)
    assert pkernel._name_waiters == {}

    # Registering afterwards doesn't try to answer it.
    assert register_name(pkernel, service, service_peer, "test.later",
                         open_port(pkernel, service, service_peer)) == 0


def test_name_taken(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    register_name(pkernel, a, a_peer, "test.name", open_port(pkernel, a, a_peer))

    result = register_name(pkernel, b, b_peer, "test.name", open_port(pkernel, b, b_peer))
    assert result == darq.NameResult.NAME_TAKEN


def test_port_already_named(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    register_name(pkernel, process, peer, "test.first", handle)

    assert register_name(pkernel, process, peer, "test.second", handle) == darq.NameResult.PORT_ALREADY_NAMED


def test_register_needs_receive_right(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert register_name(pkernel, a, a_peer, "test.stolen", a_to_b) == darq.NameResult.BAD_HANDLE
    assert register_name(pkernel, a, a_peer, "test.nothing", 99) == darq.NameResult.BAD_HANDLE


@pytest.mark.parametrize("name", ["", "x" * 256])
def test_invalid_name(pkernel, name):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert register_name(pkernel, process, peer, name, handle) == darq.NameResult.INVALID_NAME
    assert lookup_name(pkernel, process, peer, name) == (darq.NameResult.INVALID_NAME, 0)


def test_name_not_utf8(pkernel):
    process, peer = add_running_process(pkernel, 1)
    request = darq.RegisterNameRequest(1, "", open_port(pkernel, process, peer))
    request.name = b"\xff\xfe"
    request.set_length(request.header_length + 16 + request.padded_length(request.name))
    send_to_kernel(pkernel, process, request)

    response = darq.RegisterNameResponse()
    response.decode(receive(peer))
    assert response.result == darq.NameResult.INVALID_NAME


def test_longest_name(pkernel):
    process, peer = add_running_process(pkernel, 1)
    name = "x" * darq.MAX_NAME_LENGTH
    assert register_name(pkernel, process, peer, name, open_port(pkernel, process, peer)) == 0


@pytest.mark.parametrize("name", ["darq", "darq.storage"])
def test_reserved_names(pkernel, name):
    # A user process can't register a reserved name ...
    user, user_peer = user_process(pkernel, 2)
    result = register_name(pkernel, user, user_peer, name, open_port(pkernel, user, user_peer))
    assert result == darq.NameResult.RESERVED_NAME

    # ... but a process the kernel spawned (parent dpid 0) can.
    system, system_peer = add_running_process(pkernel, 1)
    result = register_name(pkernel, system, system_peer, name, open_port(pkernel, system, system_peer))
    assert result == darq.NameResult.OK


def test_names_like_reserved_are_not(pkernel):
    user, user_peer = user_process(pkernel, 2)
    assert register_name(pkernel, user, user_peer, "darqos.thing", open_port(pkernel, user, user_peer)) == 0


def test_name_removed_with_port(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    register_name(pkernel, process, peer, "test.name", handle)

    close_port(pkernel, process, peer, handle)
    assert lookup_name(pkernel, process, peer, "test.name") == (darq.NameResult.NOT_FOUND, 0)

    # The name can be registered again, for another port.
    assert register_name(pkernel, process, peer, "test.name", open_port(pkernel, process, peer, 2)) == 0


def test_name_removed_on_disconnect(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    client, client_peer = user_process(pkernel, 2)
    register_name(pkernel, service, service_peer, "test.name", open_port(pkernel, service, service_peer))

    pkernel.handle_disconnect(service)
    assert lookup_name(pkernel, client, client_peer, "test.name") == (darq.NameResult.NOT_FOUND, 0)


def test_two_processes_ping_pong(live_pkernel, tmp_path):
    # Phase 1's exit test: a service registers a name, and a client
    # looks it up, then they exchange messages, each replying to the
    # other's source handle.
    kernel = live_pkernel
    module = kernel.module
    client_path = write_app(tmp_path / "client", """
        import darq
        def darq_main():
            # Only processes the kernel spawned can use the darq namespace.
            try:
                darq.register_name("darq.impostor", darq.open_port())
                return "registered a reserved name"
            except darq.ReservedNameError:
                pass

            service = darq.lookup_name("darq.test.ping")
            port = darq.open_port()
            for n in (b"1", b"2", b"3"):
                darq.send_message(port, service, b"ping " + n)
                source, payload = darq.receive_message(port)
                if (source, payload) != (service, b"pong " + n):
                    return f"received {(source, payload)!r}"
            darq.send_message(port, service, b"done")
            return 0
        """)
    service_path = write_app(tmp_path / "service", f"""
        import darq
        def darq_main():
            port = darq.open_port()
            darq.register_name("darq.test.ping", port)
            darq.spawn_process({client_path!r})
            while True:
                source, payload = darq.receive_message(port)
                if payload == b"done":
                    return 0
                if not payload.startswith(b"ping "):
                    return f"received {{payload!r}}"
                darq.send_message(port, source, b"pong " + payload[5:])
        """)

    dpid = kernel.do_spawn_process(None, service_path)
    service = kernel._process_table[dpid]
    zombie = module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: len(kernel._process_table) == 2 and
                     all(p.get_status() == zombie for p in kernel._process_table.values()),
                     "both processes to exit")

    [client] = [p for p in kernel._process_table.values() if p is not service]
    assert client.get_parent_dpid() == service.get_dpid()
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (service.get_exit_status(), service.get_exit_error()) == (0, None)
    assert kernel._names == {}


################################################################
# Dispatch
################################################################

def test_dispatch_handles_every_message(pkernel):
    process, peer = add_running_process(pkernel, 1)

    process.get_buffer().append(darq.OpenPortRequest(1).encode() +
                                darq.OpenPortRequest(2).encode())
    pkernel.dispatch(process)

    received = receive(peer)
    response = darq.OpenPortResponse()
    ids = []
    while received:
        response.decode(received)
        ids.append(response.request_id)
        received = received[response.length:]
    assert ids == [1, 2]


def test_dispatch_invalid_length_disconnects(pkernel):
    process, peer = add_running_process(pkernel, 1)

    process.get_buffer().append(header_with_length(darq.MessageType.OPEN_PORT_RQST, 0))
    pkernel.dispatch(process)

    assert process.get_socket() is None
    assert process.get_status() == pkernel.module.KernelProcessStatus.LOST


@pytest.mark.parametrize("body", [
    "pass",
    "while True:\n                time.sleep(0.01)",
])
def test_pyloader_with_unknown_dpid_exits(live_pkernel, tmp_path, body):
    kernel = live_pkernel
    port = kernel.socket.getsockname()[1]

    # Whether the app would finish at once or run until stopped, it never
    # starts: the p-kernel refuses to let the process run.
    executable = write_app(tmp_path / "app", f"""
        import time

        def darq_main():
            {body}
        """)

    # As if run manually, with a DPID the p-kernel didn't allocate.
    loader = subprocess.Popen([kernel.module.PYTHON_IMAGE_LOADER, str(port), "99", "0", executable],
                              env=kernel.get_loader_environment(),
                              stderr=subprocess.PIPE, text=True)
    try:
        run_kernel_until(kernel, lambda: loader.poll() is not None,
                         "pyloader to exit")
    finally:
        if loader.poll() is None:
            loader.kill()
        stderr = loader.communicate()[1]

    assert loader.returncode == 1
    assert "refused to run this process" in stderr
