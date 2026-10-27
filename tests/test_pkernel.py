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
from darq import dipc
from darq import pcall as pk


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
def pkernel(tmp_path, monkeypatch):
    module = load_pkernel()

    # Keep the run directory (and boot vector) out of /darq/run.
    monkeypatch.setenv("DARQ_RUN", str(tmp_path / "run"))

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


def send_to_kernel(kernel, process, message: pk.Message):
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
    response = pk.RunResponse()
    response.decode(peer.recv(response.get_length()))
    return response.result


def test_kill_process_forwarded_to_target(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, target_peer = add_running_process(pkernel, 2)

    send_to_kernel(pkernel, caller, pk.KillProcessRequest(5, 2))

    forwarded = pk.KillProcessRequest()
    forwarded.decode(receive(target_peer))
    assert forwarded.dpid == 2

    # No response until the target exits.
    assert receive(caller_peer) == b""


def test_kill_process_answered_on_target_exit(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, target_peer = add_running_process(pkernel, 2)

    send_to_kernel(pkernel, caller, pk.KillProcessRequest(5, 2))
    pkernel.handle_disconnect(target)

    response = pk.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert (response.request_id, response.result) == (5, 0)
    assert target.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE
    assert target.get_socket() is None


def test_kill_process_no_such_process(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, caller, pk.KillProcessRequest(5, 99))

    response = pk.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert response.request_id == 5
    assert response.result != 0


def test_kill_process_not_running(pkernel):
    caller, caller_peer = add_running_process(pkernel, 1)
    target, _ = add_running_process(pkernel, 2)
    pkernel.handle_disconnect(target)
    assert target.get_status() == pkernel.module.KernelProcessStatus.LOST

    send_to_kernel(pkernel, caller, pk.KillProcessRequest(5, 2))

    response = pk.KillProcessResponse()
    response.decode(receive(caller_peer))
    assert response.result != 0


def test_kill_self(pkernel):
    process, peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, process, pk.KillProcessRequest(5, 1))
    forwarded = pk.KillProcessRequest()
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

    running = pk.RunRequest(dpid=3)
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
    kernel.handle_run_request(reader, pk.RunRequest(dpid=dpid))


def test_spawn_process_starts_stub(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    pkernel._next_dpid = 2

    send_to_kernel(pkernel, parent, pk.SpawnProcessRequest(5, executable))

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

    send_to_kernel(pkernel, parent, pk.SpawnProcessRequest(5, executable))
    connect_spawned_process(pkernel, 2)

    response = pk.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert (response.request_id, response.result, response.dpid) == (5, 0, 2)
    assert response.get_error() == ""


def test_spawn_process_no_main(pkernel, fake_popen, tmp_path):
    parent, parent_peer = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, parent, pk.SpawnProcessRequest(5, str(tmp_path)))

    response = pk.SpawnProcessResponse(0, 0, 0, None)
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

    send_to_kernel(pkernel, parent, pk.SpawnProcessRequest(5, executable))

    response = pk.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    assert response.result != 0
    assert "No such file" in response.get_error()
    assert len(pkernel._process_table) == table_size


def test_spawn_process_sets_pythonpath(pkernel, fake_popen, executable, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/existing")
    parent, _ = add_running_process(pkernel, 1)

    send_to_kernel(pkernel, parent, pk.SpawnProcessRequest(5, executable))

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

    children = [p.get_child() for p in pkernel._process_table.values()] + pkernel._unreaped
    for child in children:
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

    # The parent spawns the child, kills it, collects it, and reports the
    # results.
    parent_path = write_app(tmp_path / "parent", f"""
        import json
        import os
        import darq

        def darq_main():
            dpid = darq.spawn_process({child_path!r})
            darq.kill_process(dpid)
            child = darq.wait(dpid)
            with open({str(results)!r} + ".tmp", "w") as f:
                json.dump({{"dpid": dpid, "child": list(child)}}, f)
            os.rename({str(results)!r} + ".tmp", {str(results)!r})
        """)

    parent_dpid = kernel.do_spawn_process(None, parent_path)
    run_kernel_until(kernel, results.exists, "the parent to kill its child")

    report = json.loads(results.read_text())
    child_dpid = report["dpid"]
    assert child_dpid != parent_dpid
    assert report["child"] == [child_dpid, pk.ExitReason.KILLED, -1, None]

    # Collecting the child removed it from the process table, and the
    # kernel reaps its host process.
    assert child_dpid not in kernel._process_table
    assert not any(p.get_dpid() == child_dpid for p in kernel._processes.values())
    run_kernel_until(kernel, lambda: kernel._unreaped == [], "the kernel to reap the child")

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
    send_to_kernel(kernel, parent, pk.SpawnProcessRequest(5, executable))

    child = kernel._process_table[2]
    run_kernel_until(kernel, lambda: child.get_child().returncode is not None,
                     "the kernel to reap the child")

    # Its spawn failed, so it isn't a child to wait for.
    assert 2 not in kernel._process_table

    response = pk.SpawnProcessResponse(0, 0, 0, None)
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

    send_to_kernel(pkernel, process, pk.Exited(7, "it broke"))
    assert process.get_exit_status() == 7
    assert process.get_exit_error() == "it broke"

    # The process then closes its session: it's a zombie, not lost.
    pkernel.handle_disconnect(process)
    assert process.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE
    assert process.get_socket() is None
    assert receive(peer) == b""


def test_exited_without_error(pkernel):
    process, _ = add_running_process(pkernel, 3)
    send_to_kernel(pkernel, process, pk.Exited(0))
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
            source, payload, _ = darq.receive_message(port)
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

    pkernel.handle_run_request(reader, pk.RunRequest(dpid=99))

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
    pkernel.handle_run_request(reader, pk.RunRequest(dpid=3))

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
    pkernel.handle_run_request(reader, pk.RunRequest(dpid=3))

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
    message = pk.Message(message_type)
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
    peer.sendall(pk.OpenPortRequest(1).encode())

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_invalid_length_before_running(pkernel):
    reader, kernel_sock, peer = accept_connection(pkernel)
    peer.sendall(header_with_length(pk.MessageType.RUN_REQUEST, 4))

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_running_split_across_reads(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)
    running = pk.RunRequest(dpid=3).encode()

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

    send_to_kernel(pkernel, process, pk.RunRequest(dpid=3))

    # Ignored: the process keeps its connection and state.
    assert process.get_socket() is sock
    assert pkernel._processes[sock] is process
    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert receive(peer) == b""


def test_pcall_in_same_read_as_running(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)

    peer.sendall(pk.RunRequest(dpid=3).encode() + pk.OpenPortRequest(7).encode())
    reader.on_readable(kernel_sock)

    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert read_run_response(peer) == 0
    response = pk.OpenPortResponse()
    response.decode(peer.recv(65536))
    assert response.request_id == 7
    assert response.result == 0


################################################################
# Ports and handles
################################################################

def received_messages(peer: socket.socket) -> list[tuple[int, bytes]]:
    """Return the messages the kernel has sent to a process, as (type, bytes)."""
    buf = pk.Buffer()
    buf.append(receive(peer))
    messages = []
    while buf.length() >= 8:
        length = pk.Message.decode_length(buf.peek(8))
        message = buf.peek(length)
        buf.consume(length)
        messages.append((pk.Message.decode_type(message), message))
    return messages


def open_port(kernel, process, peer, request_id: int = 1) -> int:
    """Open a port for a process, returning its handle."""
    send_to_kernel(kernel, process, pk.OpenPortRequest(request_id))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == pk.MessageType.OPEN_PORT_RESP
    response = pk.OpenPortResponse()
    response.decode(buf)
    assert (response.request_id, response.result) == (request_id, 0)
    return response.port


def close_port(kernel, process, peer, handle: int) -> int:
    """Close a process' handle, returning the result."""
    send_to_kernel(kernel, process, pk.ClosePortRequest(1, handle))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == pk.MessageType.CLOSE_PORT_RESP
    response = pk.ClosePortResponse()
    response.decode(buf)
    assert response.port == handle
    return response.result


def send(kernel, process, source: int, destination: int, payload: bytes, handles=()) -> int:
    """Send a message from a process, returning the SendResult.

    Anything else the process received meanwhile (such as a message to
    its own port) is kept for delivered(pkernel, )."""
    message = pk.SendMessageRequest(source, destination, request_id=9)
    message.set_payload(payload)
    message.set_handles(handles)
    send_to_kernel(kernel, process, message)

    results = []
    peer = process_peer(kernel, process)
    for message_type, buf in received_messages(peer):
        if message_type == pk.MessageType.SEND_MESSAGE_RESP:
            response = pk.SendMessageResponse()
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
        assert message_type == pk.MessageType.DELIVER_MESSAGE
        message = pk.DeliverMessage()
        message.decode(buf)
        messages.append((message.source, message.destination, message.payload))
    return messages


def delivered_handles(kernel, peer: socket.socket) -> list[tuple[int, ...]]:
    """Return the handles transferred with each message delivered to a process."""
    messages = []
    for message_type, buf in kernel.unread.pop(peer, []) + received_messages(peer):
        assert message_type == pk.MessageType.DELIVER_MESSAGE
        message = pk.DeliverMessage()
        message.decode(buf)
        messages.append(message.handles)
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

    assert send(pkernel, a, a_port, a_to_b, b"ping") == pk.SendResult.OK

    # B receives on its own handle, and gets a send right to A's port.
    [(source, destination, payload)] = delivered(pkernel, b_peer)
    assert (destination, payload) == (b_port, b"ping")
    entry = b.get_handle(source)
    assert entry.right == pkernel.module.PortRight.SEND
    assert entry.port is a.get_handle(a_port).port

    # B replies to the source: A sees its existing handle for B's port.
    assert send(pkernel, b, b_port, source, b"pong") == pk.SendResult.OK
    assert delivered(pkernel, a_peer) == [(a_to_b, a_port, b"pong")]


def test_reply_right_reuses_handle(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert send(pkernel, a, a_port, a_to_b, b"one") == pk.SendResult.OK
    assert send(pkernel, a, a_port, a_to_b, b"two") == pk.SendResult.OK

    [(first, _, _), (second, _, _)] = delivered(pkernel, b_peer)
    assert first == second


def test_send_answered_after_delivery(pkernel):
    # A message to the sender's own port is delivered before the send is
    # answered, so it's queued by the time send_message() returns.
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    message = pk.SendMessageRequest(handle, handle, request_id=4)
    send_to_kernel(pkernel, process, message)
    types = [message_type for message_type, _ in received_messages(peer)]
    assert types == [pk.MessageType.DELIVER_MESSAGE, pk.MessageType.SEND_MESSAGE_RESP]


def test_send_to_own_port(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert send(pkernel, process, handle, handle, b"self") == pk.SendResult.OK
    assert delivered(pkernel, peer) == [(handle, handle, b"self")]


def test_transferred_handles_become_send_rights(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    c, c_peer = add_running_process(pkernel, 3)
    a_port = open_port(pkernel, a, a_peer)
    extra = open_port(pkernel, a, a_peer, request_id=2)
    b_port = open_port(pkernel, b, b_peer)
    c_port = open_port(pkernel, c, c_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    a_to_c = grant_send_right(pkernel, a, c, c_port)

    # A receive right and a send right, both transferred as send rights.
    assert send(pkernel, a, a_port, a_to_b, b"x", (extra, a_to_c)) == pk.SendResult.OK
    [(b_extra, b_to_c)] = delivered_handles(pkernel, b_peer)
    PortRight = pkernel.module.PortRight
    assert b.get_handle(b_extra).port is a.get_handle(extra).port
    assert b.get_handle(b_extra).right == PortRight.SEND
    assert b.get_handle(b_to_c).port is c.get_handle(c_port).port
    assert b.get_handle(b_to_c).right == PortRight.SEND


def test_transferred_handles_coalesce(pkernel):
    # The receiver's own port comes back as its receive handle, and the
    # source port as the same handle as the message's source.
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert send(pkernel, a, a_port, a_to_b, b"x", (a_to_b, a_port, a_port)) == pk.SendResult.OK
    [message] = received_messages(b_peer)
    deliver = pk.DeliverMessage()
    deliver.decode(message[1])
    assert deliver.handles == (b_port, deliver.source, deliver.source)


@pytest.mark.parametrize("problem", ["bad", "dead", "too many"])
def test_bad_transfer_refused(pkernel, problem):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    c, c_peer = add_running_process(pkernel, 3)
    c_port = open_port(pkernel, c, c_peer)
    a_to_c = grant_send_right(pkernel, a, c, c_port)
    handles: tuple[int, ...]
    if problem == "bad":
        handles = (a_to_c, 999)
        expected = pk.SendResult.BAD_HANDLE
    elif problem == "dead":
        extra = open_port(pkernel, b, b_peer, request_id=2)
        a_to_extra = grant_send_right(pkernel, a, b, extra)
        assert close_port(pkernel, b, b_peer, extra) == 0
        handles = (a_to_c, a_to_extra)
        expected = pk.SendResult.DEAD_PORT
    else:
        handles = (a_to_c,) * (pk.MAX_HANDLES + 1)
        expected = pk.SendResult.TOO_MANY_HANDLES

    assert send(pkernel, a, a_port, a_to_b, b"x", handles) == expected
    assert delivered(pkernel, b_peer) == []
    # Nothing was granted: not even the rights that could be.
    assert b.handle_for(c.get_handle(c_port).port) is None
    assert b.handle_for(a.get_handle(a_port).port) is None


def test_send_from_send_right_refused(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    # A can't send from B's port: replies would go to B.
    assert send(pkernel, a, a_to_b, a_to_b, b"spoof") == pk.SendResult.NOT_RECEIVE_RIGHT
    assert delivered(pkernel, b_peer) == []


@pytest.mark.parametrize("bad_handle", [0, 99])
def test_send_with_bad_handle_refused(pkernel, bad_handle):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert send(pkernel, process, handle, bad_handle, b"nowhere") == pk.SendResult.BAD_HANDLE
    assert send(pkernel, process, bad_handle, handle, b"from nowhere") == pk.SendResult.BAD_HANDLE
    assert delivered(pkernel, peer) == []


def test_send_to_closed_port_refused(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    close_port(pkernel, b, b_peer, b_port)

    assert send(pkernel, a, a_port, a_to_b, b"too late") == pk.SendResult.DEAD_PORT
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
    assert send(pkernel, a, a_port, a_to_b, b"gone") == pk.SendResult.DEAD_PORT
    assert delivered(pkernel, b_peer) == []


################################################################
# Dead port notifications
################################################################

def notify_on_death(kernel, process, handle: int, notify_port: int) -> int:
    """Ask the kernel to notify a process when a port dies, returning the
    NotifyResult.  Anything delivered meanwhile is kept for delivered()."""
    send_to_kernel(kernel, process, pk.NotifyOnDeathRequest(8, handle, notify_port))

    results = []
    peer = process_peer(kernel, process)
    for message_type, buf in received_messages(peer):
        if message_type == pk.MessageType.NOTIFY_ON_DEATH_RESP:
            response = pk.NotifyOnDeathResponse()
            response.decode(buf)
            assert response.request_id == 8
            results.append(response.result)
        else:
            kernel.unread.setdefault(peer, []).append((message_type, buf))
    [result] = results
    return result


def notification(handle: int) -> bytes:
    """The payload of a PortDestroyed notification."""
    return dipc.PortDestroyed(handle).encode()[0]


@pytest.fixture
def watched(pkernel):
    """A watches a port of B's, notifying a port of its own."""
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)
    assert notify_on_death(pkernel, a, a_to_b, a_port) == pk.NotifyResult.OK
    return a, a_peer, a_port, a_to_b, b, b_peer, b_port


def test_notified_when_port_closed(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    assert delivered(pkernel, a_peer) == []

    close_port(pkernel, b, b_peer, b_port)
    assert delivered(pkernel, a_peer) == [(0, a_port, notification(a_to_b))]


def test_notified_when_owner_disconnects(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    pkernel.handle_disconnect(b)
    assert delivered(pkernel, a_peer) == [(0, a_port, notification(a_to_b))]


def test_notified_once(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    assert notify_on_death(pkernel, a, a_to_b, a_port) == pk.NotifyResult.OK   # again: no change
    close_port(pkernel, b, b_peer, b_port)
    assert delivered(pkernel, a_peer) == [(0, a_port, notification(a_to_b))]


def test_notified_on_each_notify_port(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    other = open_port(pkernel, a, a_peer, 2)
    assert notify_on_death(pkernel, a, a_to_b, other) == pk.NotifyResult.OK
    close_port(pkernel, b, b_peer, b_port)
    assert sorted(delivered(pkernel, a_peer)) == [(0, a_port, notification(a_to_b)),
                                                  (0, other, notification(a_to_b))]


def test_already_dead_notified_at_once(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    close_port(pkernel, b, b_peer, b_port)
    assert delivered(pkernel, a_peer) == [(0, a_port, notification(a_to_b))]

    # Queued before the response.
    send_to_kernel(pkernel, a, pk.NotifyOnDeathRequest(8, a_to_b, a_port))
    assert [t for t, _ in received_messages(a_peer)] == [pk.MessageType.DELIVER_MESSAGE,
                                                         pk.MessageType.NOTIFY_ON_DEATH_RESP]


def test_closing_watched_handle_cancels(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    close_port(pkernel, a, a_peer, a_to_b)
    close_port(pkernel, b, b_peer, b_port)
    assert delivered(pkernel, a_peer) == []
    assert b.get_handle(b_port) is None


def test_closed_notify_port_not_notified(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    close_port(pkernel, a, a_peer, a_port)
    close_port(pkernel, b, b_peer, b_port)
    assert delivered(pkernel, a_peer) == []


def test_watcher_disconnect_cancels(pkernel, watched):
    a, a_peer, a_port, a_to_b, b, b_peer, b_port = watched
    port = b.get_handle(b_port).port
    pkernel.handle_disconnect(a)
    assert port.death_watchers == {}


def test_own_port_watched(pkernel):
    process, peer = add_running_process(pkernel, 1)
    watched_port = open_port(pkernel, process, peer, 1)
    notify_port = open_port(pkernel, process, peer, 2)
    assert notify_on_death(pkernel, process, watched_port, notify_port) == pk.NotifyResult.OK

    # Closing the watched handle cancels, even though it destroys the port.
    close_port(pkernel, process, peer, watched_port)
    assert delivered(pkernel, peer) == []


@pytest.mark.parametrize("problem", ["bad handle", "bad notify port", "notify send right"])
def test_notify_on_death_refused(pkernel, problem):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    a_port = open_port(pkernel, a, a_peer)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    handle, notify_port, result = {
        "bad handle": (99, a_port, pk.NotifyResult.BAD_HANDLE),
        "bad notify port": (a_to_b, 99, pk.NotifyResult.BAD_NOTIFY_PORT),
        "notify send right": (a_port, a_to_b, pk.NotifyResult.BAD_NOTIFY_PORT),
    }[problem]
    assert notify_on_death(pkernel, a, handle, notify_port) == result
    assert b.get_handle(b_port).port.death_watchers == {}


################################################################
# Name registry
################################################################

def register_name(kernel, process, peer, name: str, handle: int) -> int:
    """Register a name for a process' port, returning the result."""
    send_to_kernel(kernel, process, pk.RegisterNameRequest(1, name, handle))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == pk.MessageType.REGISTER_NAME_RESP
    response = pk.RegisterNameResponse()
    response.decode(buf)
    return response.result


def lookup_responses(peer) -> list[tuple[int, int, int]]:
    """Return the lookup responses sent to a process, as (request_id, result, handle)."""
    responses = []
    for message_type, buf in received_messages(peer):
        assert message_type == pk.MessageType.LOOKUP_NAME_RESP
        response = pk.LookupNameResponse()
        response.decode(buf)
        responses.append((response.request_id, response.result, response.port))
    return responses


def lookup_name(kernel, process, peer, name: str, blocking: bool = False,
                request_id: int = 1) -> tuple[int, int] | None:
    """Look up a name for a process: (result, handle), or None if unanswered."""
    send_to_kernel(kernel, process, pk.LookupNameRequest(request_id, name, blocking))
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
    assert result == pk.NameResult.OK
    entry = client.get_handle(handle)
    assert entry.right == pkernel.module.PortRight.SEND
    assert entry.port is service.get_handle(service_port).port

    # The looked-up handle reaches the service.
    assert send(pkernel, client, client_port, handle, b"hello") == pk.SendResult.OK
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

    assert lookup_name(pkernel, process, peer, "test.self") == (pk.NameResult.OK, handle)


def test_non_blocking_lookup_not_found(pkernel):
    process, peer = add_running_process(pkernel, 1)
    assert lookup_name(pkernel, process, peer, "test.missing") == (pk.NameResult.NOT_FOUND, 0)


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
    assert (request_id, result) == (5, pk.NameResult.OK)
    assert a.get_handle(handle).port is port
    [(request_id, result, handle)] = lookup_responses(b_peer)
    assert (request_id, result) == (6, pk.NameResult.OK)
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
    assert result == pk.NameResult.NAME_TAKEN


def test_port_already_named(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    register_name(pkernel, process, peer, "test.first", handle)

    assert register_name(pkernel, process, peer, "test.second", handle) == pk.NameResult.PORT_ALREADY_NAMED


def test_register_needs_receive_right(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)
    b_port = open_port(pkernel, b, b_peer)
    a_to_b = grant_send_right(pkernel, a, b, b_port)

    assert register_name(pkernel, a, a_peer, "test.stolen", a_to_b) == pk.NameResult.BAD_HANDLE
    assert register_name(pkernel, a, a_peer, "test.nothing", 99) == pk.NameResult.BAD_HANDLE


@pytest.mark.parametrize("name", ["", "x" * 256])
def test_invalid_name(pkernel, name):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)

    assert register_name(pkernel, process, peer, name, handle) == pk.NameResult.INVALID_NAME
    assert lookup_name(pkernel, process, peer, name) == (pk.NameResult.INVALID_NAME, 0)


def test_name_not_utf8(pkernel):
    process, peer = add_running_process(pkernel, 1)
    request = pk.RegisterNameRequest(1, "", open_port(pkernel, process, peer))
    request.name = b"\xff\xfe"
    request.set_length(request.header_length + 16 + request.padded_length(request.name))
    send_to_kernel(pkernel, process, request)

    response = pk.RegisterNameResponse()
    response.decode(receive(peer))
    assert response.result == pk.NameResult.INVALID_NAME


def test_longest_name(pkernel):
    process, peer = add_running_process(pkernel, 1)
    name = "x" * pk.MAX_NAME_LENGTH
    assert register_name(pkernel, process, peer, name, open_port(pkernel, process, peer)) == 0


@pytest.mark.parametrize("name", ["darq", "darq.storage"])
def test_reserved_names(pkernel, name):
    # A user process can't register a reserved name ...
    user, user_peer = user_process(pkernel, 2)
    result = register_name(pkernel, user, user_peer, name, open_port(pkernel, user, user_peer))
    assert result == pk.NameResult.RESERVED_NAME

    # ... but a process the kernel spawned (parent dpid 0) can.
    system, system_peer = add_running_process(pkernel, 1)
    result = register_name(pkernel, system, system_peer, name, open_port(pkernel, system, system_peer))
    assert result == pk.NameResult.OK


def test_names_like_reserved_are_not(pkernel):
    user, user_peer = user_process(pkernel, 2)
    assert register_name(pkernel, user, user_peer, "darqos.thing", open_port(pkernel, user, user_peer)) == 0


def test_name_removed_with_port(pkernel):
    process, peer = add_running_process(pkernel, 1)
    handle = open_port(pkernel, process, peer)
    register_name(pkernel, process, peer, "test.name", handle)

    close_port(pkernel, process, peer, handle)
    assert lookup_name(pkernel, process, peer, "test.name") == (pk.NameResult.NOT_FOUND, 0)

    # The name can be registered again, for another port.
    assert register_name(pkernel, process, peer, "test.name", open_port(pkernel, process, peer, 2)) == 0


def test_name_removed_on_disconnect(pkernel):
    service, service_peer = add_running_process(pkernel, 1)
    client, client_peer = user_process(pkernel, 2)
    register_name(pkernel, service, service_peer, "test.name", open_port(pkernel, service, service_peer))

    pkernel.handle_disconnect(service)
    assert lookup_name(pkernel, client, client_peer, "test.name") == (pk.NameResult.NOT_FOUND, 0)


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
                source, payload, _ = darq.receive_message(port)
                if (source, payload) != (service, b"pong " + n):
                    return f"received {(source, payload)!r}"
            darq.send_message(port, service, b"done")
            return 3
        """)
    service_path = write_app(tmp_path / "service", f"""
        import darq
        def darq_main():
            port = darq.open_port()
            darq.register_name("darq.test.ping", port)
            client = darq.spawn_process({client_path!r})
            while True:
                source, payload, _ = darq.receive_message(port)
                if payload == b"done":
                    break
                if not payload.startswith(b"ping "):
                    return f"received {{payload!r}}"
                darq.send_message(port, source, b"pong " + payload[5:])

            # The client exits with status 3, and its parent collects it.
            finished = darq.wait(client)
            if finished != (client, darq.ExitReason.EXITED, 3, None):
                return f"collected {{finished!r}}"
            return 0
        """)

    dpid = kernel.do_spawn_process(None, service_path)
    service = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: service.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the service to exit")

    # The client was collected by the service; the service, spawned by
    # the kernel, remains for the kernel.
    assert list(kernel._process_table) == [dpid]
    assert (service.get_exit_status(), service.get_exit_error()) == (0, None)
    assert kernel._names == {}


def write_didl_module(directory: pathlib.Path, source: str):
    """Compile DIDL source, with didlc and didlpygen, into a module in directory."""
    bin_dir = pathlib.Path(__file__).parent.parent / "bin"
    didl = directory / "protocol.didl"
    didl.write_text(textwrap.dedent(source))
    subprocess.run([sys.executable, str(bin_dir / "didlc"), str(didl)], check=True)
    subprocess.run([sys.executable, str(bin_dir / "didlpygen"), str(didl.with_suffix(".dir"))], check=True)


def test_rpc_service_and_client(live_pkernel, tmp_path):
    # Phase 3's exit test: a service answers a DIDL protocol with
    # darq.serve(), and a client calls it with the generated Client,
    # through the p-kernel.
    kernel = live_pkernel
    module = kernel.module
    protocol = """
        library test_kv;
        protocol KV {
            1: Put(opaque key, opaque value) -> ();
            2: Get(opaque key) -> (opaque value, uint32 found);
            3: Forget(opaque key);
            4: Fail() -> ();
            5: Missing() -> ();
            6: Share(port p) -> (port echoed);
        }
        """
    service_path = write_app(tmp_path / "service", """
        import darq
        from test_kv import KV, GetResponse, PutResponse, ShareResponse

        class Handler(KV.Handler):
            def __init__(self):
                self.store = {}

            def put(self, request, source):
                self.store[request.key] = request.value
                return PutResponse()

            def get(self, request, source):
                if request.key not in self.store:
                    return GetResponse(found=0)
                return GetResponse(value=self.store[request.key], found=1)

            def forget(self, request, source):
                self.store.pop(request.key, None)

            def fail(self, request, source):
                raise ValueError("as requested")

            def share(self, request, source):
                # The client's own port, back to it.
                return ShareResponse(echoed=request.p)

        def darq_main():
            darq.serve("darq.test.kv", Handler())
        """)
    client_path = write_app(tmp_path / "client", """
        import darq
        from darq import dipc
        from test_kv import KV, GetRequest, GetResponse

        def darq_main():
            kv = KV.Client(darq.connect("darq.test.kv"), timeout=10)
            kv.put(key=b"k", value=b"v")
            response = kv.get(key=b"k")
            if (response.value, response.found) != (b"v", 1):
                return f"get returned {response!r}"

            # A one-way message is handled before the next request.
            kv.forget(key=b"k")
            if kv.get(key=b"k").found != 0:
                return "forget didn't"

            for method, code in (("fail", dipc.ErrorCode.INTERNAL_ERROR),
                                 ("missing", dipc.ErrorCode.NOT_IMPLEMENTED)):
                try:
                    getattr(kv, method)()
                    return f"{method} succeeded"
                except darq.RemoteError as e:
                    if e.code != code:
                        return f"{method} raised {e!r}"

            # A port transferred to the service, and back, is the same
            # handle again.
            port = darq.open_port()
            if kv.share(p=port).echoed != port:
                return "port didn't come back"

            response = darq.call(kv.service, GetRequest(key=b"k"))
            if not isinstance(response, GetResponse):
                return f"call returned {response!r}"
            return 0
        """)
    for path in (service_path, client_path):
        write_didl_module(pathlib.Path(path), protocol)

    kernel.do_spawn_process(None, service_path)
    dpid = kernel.do_spawn_process(None, client_path)
    client = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: client.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


def test_rpc_service_dies_before_replying(live_pkernel, tmp_path):
    # The client is notified that the service's port has died, rather
    # than waiting for a reply that will never come.
    kernel = live_pkernel
    module = kernel.module
    protocol = """
        library test_crash;
        protocol Crash {
            1: Crash() -> ();
        }
        """
    service_path = write_app(tmp_path / "service", """
        import darq
        from test_crash import Crash

        class Handler(Crash.Handler):
            def crash(self, request, source):
                darq.exit(1)

        def darq_main():
            darq.serve("darq.test.crash", Handler())
        """)
    client_path = write_app(tmp_path / "client", """
        import darq
        from test_crash import Crash

        def darq_main():
            crash = Crash.Client(darq.connect("darq.test.crash"), timeout=10)
            try:
                crash.crash()
                return "crash returned"
            except darq.DeadPortError:
                return 0
        """)
    for path in (service_path, client_path):
        write_didl_module(pathlib.Path(path), protocol)

    kernel.do_spawn_process(None, service_path)
    dpid = kernel.do_spawn_process(None, client_path)
    client = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: client.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


################################################################
# Run directory and boot vector
################################################################

def test_run_directory_from_environment(pkernel, tmp_path):
    assert pkernel.run_dir == str(tmp_path / "run")
    assert (tmp_path / "run").is_dir()
    assert pkernel.get_loader_environment()["DARQ_RUN"] == pkernel.run_dir


def test_relative_run_directory(tmp_path, monkeypatch):
    # Resolved against the p-kernel's working directory, since spawned
    # processes run in /.
    module = load_pkernel()
    module.TCP_PORT = 0
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DARQ_RUN", "./run")
    kernel = module.PseudoKernel()
    try:
        assert kernel.run_dir == str(tmp_path / "run")
        assert kernel.get_loader_environment()["DARQ_RUN"] == str(tmp_path / "run")
    finally:
        kernel.close()


def test_default_run_directory(pkernel):
    assert pkernel.module.DEFAULT_RUN_DIR == "/darq/run"


def get_boot_value(kernel, process, peer, name: str) -> tuple[int, bytes]:
    """Get a boot vector value for a process, returning (result, value)."""
    send_to_kernel(kernel, process, pk.GetBootValueRequest(3, name))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == pk.MessageType.GET_BOOT_VALUE_RESP
    response = pk.GetBootValueResponse()
    response.decode(buf)
    assert response.request_id == 3
    return response.result, response.value


def set_boot_value(kernel, process, peer, name: str, value: bytes | None) -> int:
    """Set a boot vector value for a process, returning the result."""
    send_to_kernel(kernel, process, pk.SetBootValueRequest(4, name, value))
    [(message_type, buf)] = received_messages(peer)
    assert message_type == pk.MessageType.SET_BOOT_VALUE_RESP
    response = pk.SetBootValueResponse()
    response.decode(buf)
    assert response.request_id == 4
    return response.result


def test_boot_value_set_get_remove(pkernel):
    process, peer = add_running_process(pkernel, 1)
    assert get_boot_value(pkernel, process, peer, "darq.type") == (pk.BootResult.NOT_FOUND, b"")
    assert set_boot_value(pkernel, process, peer, "darq.type", b"\x01\x02") == pk.BootResult.OK
    assert get_boot_value(pkernel, process, peer, "darq.type") == (pk.BootResult.OK, b"\x01\x02")

    # An empty value is still a value.
    assert set_boot_value(pkernel, process, peer, "darq.type", b"") == pk.BootResult.OK
    assert get_boot_value(pkernel, process, peer, "darq.type") == (pk.BootResult.OK, b"")

    assert set_boot_value(pkernel, process, peer, "darq.type", None) == pk.BootResult.OK
    assert get_boot_value(pkernel, process, peer, "darq.type") == (pk.BootResult.NOT_FOUND, b"")
    # Removing a name that isn't set is fine.
    assert set_boot_value(pkernel, process, peer, "darq.type", None) == pk.BootResult.OK


def test_boot_vector_persists(pkernel):
    process, peer = add_running_process(pkernel, 1)
    set_boot_value(pkernel, process, peer, "a", b"one")
    set_boot_value(pkernel, process, peer, "b", b"\xff")
    path = os.path.join(pkernel.run_dir, pkernel.module.BOOT_VECTOR_FILE)
    assert json.loads(pathlib.Path(path).read_text()) == {"a": "6f6e65", "b": "ff"}
    assert pkernel.module.BootVector(path).values == {"a": b"one", "b": b"\xff"}
    assert not os.path.exists(path + ".tmp")


def test_boot_value_not_permitted(pkernel):
    # Only for processes the p-kernel spawned.
    system, system_peer = add_running_process(pkernel, 1)
    set_boot_value(pkernel, system, system_peer, "a", b"one")
    user, user_peer = user_process(pkernel, 2)
    assert get_boot_value(pkernel, user, user_peer, "a") == (pk.BootResult.NOT_PERMITTED, b"")
    assert set_boot_value(pkernel, user, user_peer, "a", b"two") == pk.BootResult.NOT_PERMITTED
    assert set_boot_value(pkernel, user, user_peer, "a", None) == pk.BootResult.NOT_PERMITTED
    assert get_boot_value(pkernel, system, system_peer, "a") == (pk.BootResult.OK, b"one")


@pytest.mark.parametrize("name", ["", "x" * (pk.MAX_NAME_LENGTH + 1)])
def test_boot_value_invalid_name(pkernel, name):
    process, peer = add_running_process(pkernel, 1)
    assert get_boot_value(pkernel, process, peer, name)[0] == pk.BootResult.INVALID_NAME
    assert set_boot_value(pkernel, process, peer, name, b"v") == pk.BootResult.INVALID_NAME


def test_boot_value_too_large(pkernel):
    process, peer = add_running_process(pkernel, 1)
    value = bytes(pk.MAX_BOOT_VALUE_LENGTH)
    assert set_boot_value(pkernel, process, peer, "a", value) == pk.BootResult.OK
    assert set_boot_value(pkernel, process, peer, "a", value + b"x") == pk.BootResult.TOO_LARGE
    assert get_boot_value(pkernel, process, peer, "a") == (pk.BootResult.OK, value)


def test_boot_vector_full(pkernel):
    process, peer = add_running_process(pkernel, 1)
    for n in range(pk.MAX_BOOT_VALUES):
        assert set_boot_value(pkernel, process, peer, f"name{n}", b"v") == pk.BootResult.OK
    assert set_boot_value(pkernel, process, peer, "another", b"v") == pk.BootResult.FULL
    # Existing names can still be changed, or removed to make room.
    assert set_boot_value(pkernel, process, peer, "name0", b"w") == pk.BootResult.OK
    assert set_boot_value(pkernel, process, peer, "name0", None) == pk.BootResult.OK
    assert set_boot_value(pkernel, process, peer, "another", b"v") == pk.BootResult.OK


def test_boot_value_save_failure(pkernel, monkeypatch):
    process, peer = add_running_process(pkernel, 1)
    set_boot_value(pkernel, process, peer, "a", b"one")

    def fail():
        raise OSError("disc full")

    monkeypatch.setattr(pkernel._boot_vector, "save", fail)
    assert set_boot_value(pkernel, process, peer, "a", b"two") == pk.BootResult.IO_ERROR
    assert set_boot_value(pkernel, process, peer, "b", b"new") == pk.BootResult.IO_ERROR
    # Nothing changed.
    assert pkernel._boot_vector.values == {"a": b"one"}


def test_unreadable_boot_vector_kept(pkernel, tmp_path):
    path = tmp_path / "bootvec.json"
    path.write_text("not json")
    vector = pkernel.module.BootVector(str(path))
    assert vector.values == {}
    assert (tmp_path / "bootvec.json.bad").read_text() == "not json"
    assert not path.exists()


def test_boot_values_end_to_end(live_pkernel, tmp_path):
    kernel = live_pkernel
    module = kernel.module
    child_path = write_app(tmp_path / "child", """
        import darq
        def darq_main():
            try:
                darq.get_boot_value("test.key")
                return "child could read the boot vector"
            except darq.NotPermittedError:
                return 0
        """)
    path = write_app(tmp_path / "service", f"""
        import darq
        def darq_main():
            if darq.get_boot_value("test.key") is not None:
                return "already set"
            darq.set_boot_value("test.key", b"\\x00\\x01")
            if darq.get_boot_value("test.key") != b"\\x00\\x01":
                return "didn't read back"
            try:
                darq.set_boot_value("test.big", bytes(darq.MAX_BOOT_VALUE_LENGTH + 1))
                return "set a value that's too large"
            except darq.ValueTooLargeError:
                pass
            child = darq.spawn_process({child_path!r})
            finished = darq.wait(child)
            if finished != (child, darq.ExitReason.EXITED, 0, None):
                return f"child finished {{finished!r}}"
            return 0
        """)

    dpid = kernel.do_spawn_process(None, path)
    service = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: service.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the service to exit")
    assert (service.get_exit_status(), service.get_exit_error()) == (0, None)
    assert kernel._boot_vector.values == {"test.key": b"\x00\x01"}


################################################################
# Waiting for children
################################################################

def add_child(kernel, parent, dpid: int):
    """Add a running process, spawned by parent.  Returns (process, peer)."""
    process, peer = add_running_process(kernel, dpid)
    process.set_parent_dpid(parent.get_dpid())
    return process, peer


def finish(kernel, process, how: str = "exited", status: int = 0, error: str | None = None):
    """Have a process finish: it exited (with a status), was killed, or was lost."""
    if how == "exited":
        send_to_kernel(kernel, process, pk.Exited(status, error))
    elif how == "killed":
        process.add_kill_request(None, 0)
    kernel.handle_disconnect(process)


def wait_responses(peer) -> list[pk.WaitResponse]:
    responses = []
    for message_type, buf in received_messages(peer):
        assert message_type == pk.MessageType.WAIT_RESP
        response = pk.WaitResponse()
        response.decode(buf)
        responses.append(response)
    return responses


def wait(kernel, process, peer, dpid: int = 0, blocking: bool = False,
         request_id: int = 1) -> pk.WaitResponse | None:
    """Send a wait for a process: its response, or None if unanswered."""
    send_to_kernel(kernel, process, pk.WaitRequest(request_id, dpid, blocking))
    responses = wait_responses(peer)
    if not responses:
        return None
    [response] = responses
    assert response.request_id == request_id
    return response


def waited(kernel, process, peer, dpid: int = 0, request_id: int = 1) -> pk.WaitResponse:
    """Send a non-blocking wait for a process, returning its response."""
    response = wait(kernel, process, peer, dpid, request_id=request_id)
    assert response is not None
    return response


def collected(response) -> tuple:
    return (response.result, response.dpid, response.reason, response.status, response.get_error())


def test_zombie_kept_until_collected(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    child, _ = add_child(pkernel, parent, 2)
    finish(pkernel, child, "exited", 3, "failed")

    assert pkernel._process_table[2] is child
    assert child.get_status() == pkernel.module.KernelProcessStatus.ZOMBIE

    response = wait(pkernel, parent, parent_peer, 2)
    assert collected(response) == (pk.WaitResult.OK, 2, pk.ExitReason.EXITED, 3, "failed")
    assert 2 not in pkernel._process_table


@pytest.mark.parametrize("how, reason", [
    ("killed", pk.ExitReason.KILLED),
    ("lost", pk.ExitReason.LOST),
])
def test_wait_reports_how_child_finished(pkernel, how, reason):
    parent, parent_peer = add_running_process(pkernel, 1)
    child, _ = add_child(pkernel, parent, 2)
    finish(pkernel, child, how)

    assert collected(wait(pkernel, parent, parent_peer)) == (pk.WaitResult.OK, 2, reason, -1, None)


def test_wait_for_any_child_collects_earliest(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    later, _ = add_child(pkernel, parent, 3)
    earlier, _ = add_child(pkernel, parent, 2)
    finish(pkernel, later)
    finish(pkernel, earlier)

    assert waited(pkernel, parent, parent_peer).dpid == 2
    assert waited(pkernel, parent, parent_peer, request_id=2).dpid == 3


def test_non_blocking_wait_not_finished(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    add_child(pkernel, parent, 2)

    assert waited(pkernel, parent, parent_peer).result == pk.WaitResult.NOT_FINISHED
    assert 2 in pkernel._process_table


def test_wait_without_children(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    other, _ = add_running_process(pkernel, 2)
    grandchild_parent, _ = add_child(pkernel, parent, 3)
    add_child(pkernel, grandchild_parent, 4)

    for dpid in (2, 4, 99):
        assert waited(pkernel, other, process_peer(pkernel, other), dpid).result == pk.WaitResult.NO_SUCH_CHILD
        assert waited(pkernel, parent, parent_peer, dpid).result == pk.WaitResult.NO_SUCH_CHILD
    assert waited(pkernel, other, process_peer(pkernel, other)).result == pk.WaitResult.NO_SUCH_CHILD


def test_blocking_wait_answered_when_child_finishes(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    first, _ = add_child(pkernel, parent, 2)
    second, _ = add_child(pkernel, parent, 3)

    # One wait for the second child, one for any.
    assert wait(pkernel, parent, parent_peer, 3, blocking=True, request_id=5) is None
    assert wait(pkernel, parent, parent_peer, 0, blocking=True, request_id=6) is None

    finish(pkernel, first)
    [response] = wait_responses(parent_peer)
    assert (response.request_id, response.dpid) == (6, 2)

    finish(pkernel, second)
    [response] = wait_responses(parent_peer)
    assert (response.request_id, response.dpid) == (5, 3)
    assert pkernel._wait_requests == {}


def test_waits_abandoned_on_disconnect(pkernel):
    parent, parent_peer = add_running_process(pkernel, 1)
    add_child(pkernel, parent, 2)
    wait(pkernel, parent, parent_peer, blocking=True)

    pkernel.handle_disconnect(parent)
    assert pkernel._wait_requests == {}


def test_orphans_removed(pkernel):
    parent, _ = add_running_process(pkernel, 1)
    finished, _ = add_child(pkernel, parent, 2)
    running, _ = add_child(pkernel, parent, 3)
    finish(pkernel, finished)

    # The parent finishes: its finished child can never be collected.
    finish(pkernel, parent)
    assert 2 not in pkernel._process_table

    # Its running child keeps its parent's dpid (it doesn't become the
    # kernel's), and is removed when it finishes.
    assert running.get_parent_dpid() == 1
    finish(pkernel, running)
    assert 3 not in pkernel._process_table


def test_kernel_spawned_processes_kept(pkernel):
    process, _ = add_running_process(pkernel, 1)
    finish(pkernel, process)
    assert pkernel._process_table[1] is process


################################################################
# Shutdown and reboot
################################################################

def test_shutdown_kills_processes(pkernel):
    a, a_peer = add_running_process(pkernel, 1)
    b, b_peer = add_running_process(pkernel, 2)

    send_to_kernel(pkernel, a, pk.Shutdown())
    for process, peer in ((a, a_peer), (b, b_peer)):
        [(message_type, buf)] = received_messages(peer)
        assert message_type == pk.MessageType.KILL_PROCESS_RQST
        request = pk.KillProcessRequest()
        request.decode(buf)
        assert request.dpid == process.get_dpid()

    # Shutdown completes once they've all gone.
    pkernel.handle_disconnect(a)
    assert not pkernel._stopped
    pkernel.handle_disconnect(b)
    assert pkernel._stopped
    assert not pkernel.reboot_requested()
    assert b.get_exit_reason() == pk.ExitReason.KILLED


def test_shutdown_without_processes(pkernel):
    pkernel.begin_shutdown(reboot=False)
    assert pkernel._stopped


def test_reboot_requested(pkernel):
    process, _ = add_running_process(pkernel, 1)
    send_to_kernel(pkernel, process, pk.Reboot())
    pkernel.handle_disconnect(process)
    assert pkernel._stopped
    assert pkernel.reboot_requested()


def test_restart_replaces_process(pkernel, monkeypatch):
    calls = []
    monkeypatch.setattr(pkernel.module.os, "execv", lambda path, argv: calls.append((path, argv)))
    monkeypatch.setattr(pkernel.module.logging, "shutdown", lambda: None)
    monkeypatch.setattr(sys, "argv", ["pkernel"])

    pkernel.restart()
    assert calls == [(sys.executable, [sys.executable, str(PKERNEL_PATH.resolve())])]


def test_shutdown_forces_stuck_processes(pkernel):
    process, _ = add_running_process(pkernel, 1)
    stuck = subprocess.Popen(["sleep", "60"])
    process.set_child(stuck)

    pkernel.begin_shutdown(reboot=False)
    assert not pkernel._stopped

    # After the timeout, its host process is killed, ending its session.
    pkernel.force_shutdown()
    assert stuck.wait(E2E_TIMEOUT) != 0
    pkernel.handle_disconnect(process)
    assert pkernel._stopped


def test_shutdown_ends_sessions_without_host_process(pkernel):
    add_running_process(pkernel, 1)
    pkernel.begin_shutdown(reboot=False)
    pkernel.force_shutdown()
    assert pkernel._stopped


def test_no_spawning_during_shutdown(pkernel, executable):
    process, peer = add_running_process(pkernel, 1)
    pkernel.begin_shutdown(reboot=False)
    receive(peer)

    send_to_kernel(pkernel, process, pk.SpawnProcessRequest(5, executable))
    response = pk.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(peer))
    assert response.result != 0
    assert response.get_error() == "shutting down"


def test_interrupt_shuts_down(pkernel):
    def interrupt():
        raise KeyboardInterrupt

    pkernel._loop.add_deferred(interrupt)
    pkernel.run()
    assert pkernel._stopped


def test_live_shutdown(live_pkernel, tmp_path):
    kernel = live_pkernel
    module = kernel.module
    path = write_app(tmp_path / "app", """
        import time
        def darq_main():
            while True:
                time.sleep(0.01)
        """)
    dpid = kernel.do_spawn_process(None, path)
    process = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: process.get_status() == module.KernelProcessStatus.RUNNING,
                     "the process to run")

    kernel.begin_shutdown(reboot=False)
    run_kernel_until(kernel, lambda: kernel._stopped, "shutdown to complete")
    assert process.get_exit_reason() == pk.ExitReason.KILLED
    run_kernel_until(kernel, lambda: process.get_child().returncode is not None,
                     "the kernel to reap the process")


################################################################
# Timeouts
################################################################

@pytest.fixture
def silent_loader(pkernel, tmp_path, monkeypatch):
    """Make spawned processes run a loader that never connects."""
    loader = tmp_path / "silent-loader"
    loader.write_text("#!/bin/sh\nexec sleep 60\n")
    loader.chmod(0o755)
    monkeypatch.setattr(pkernel.module, "PYTHON_IMAGE_LOADER", str(loader))


def run_until_received(kernel, peer, count: int) -> list[tuple[int, bytes]]:
    """Run the kernel's loop until a process has been sent this many messages."""
    messages = []
    run_kernel_until(kernel, lambda: len(messages.__iadd__(received_messages(peer))) >= count,
                     f"{count} messages")
    return messages


def test_spawn_timeout(live_pkernel, silent_loader, executable, caplog, monkeypatch):
    kernel = live_pkernel
    monkeypatch.setattr(kernel.module, "SPAWN_TIMEOUT", 0.2)
    parent, parent_peer = add_running_process(kernel, 1)
    kernel._next_dpid = 2

    send_to_kernel(kernel, parent, pk.SpawnProcessRequest(5, executable))
    [(message_type, buf)] = run_until_received(kernel, parent_peer, 1)

    # The spawn fails, as if the process had exited before running.
    assert message_type == pk.MessageType.SPAWN_PROCESS_RESP
    response = pk.SpawnProcessResponse(0, 0, 0, None)
    response.decode(buf)
    assert (response.request_id, response.result) == (5, 1)
    assert "before running" in response.get_error()
    assert "didn't send RUN_REQUEST" in caplog.text
    assert 2 not in kernel._process_table


def test_kill_before_running(live_pkernel, silent_loader, executable):
    kernel = live_pkernel
    parent, parent_peer = add_running_process(kernel, 1)
    kernel._next_dpid = 2
    send_to_kernel(kernel, parent, pk.SpawnProcessRequest(5, executable))
    assert kernel._process_table[2].get_status() == kernel.module.KernelProcessStatus.SPAWNED

    # Killing it kills its host process: the kill succeeds, and the spawn fails.
    send_to_kernel(kernel, parent, pk.KillProcessRequest(6, 2))
    messages = run_until_received(kernel, parent_peer, 2)
    assert sorted(message_type for message_type, _ in messages) == \
        [pk.MessageType.SPAWN_PROCESS_RESP, pk.MessageType.KILL_PROCESS_RESP]
    for message_type, buf in messages:
        if message_type == pk.MessageType.KILL_PROCESS_RESP:
            response = pk.KillProcessResponse()
            response.decode(buf)
            assert (response.request_id, response.result) == (6, 0)
    assert 2 not in kernel._process_table


def test_kill_timeout(live_pkernel, tmp_path, caplog, monkeypatch):
    # The application is stuck in native code, so pyloader can't stop it
    # (it would give up after a second); the kernel kills its host first.
    kernel = live_pkernel
    module = kernel.module
    monkeypatch.setattr(module, "KILL_TIMEOUT", 0.2)
    started = tmp_path / "started"
    path = write_app(tmp_path / "app", f"""
        import time
        def darq_main():
            open({str(started)!r}, "w").close()
            time.sleep(60)
        """)
    requester, requester_peer = add_running_process(kernel, 1)
    kernel._next_dpid = 2
    dpid = kernel.do_spawn_process(None, path)
    target = kernel._process_table[dpid]
    run_kernel_until(kernel, started.exists, "the application to start")

    start = time.monotonic()
    send_to_kernel(kernel, requester, pk.KillProcessRequest(6, dpid))
    [(message_type, buf)] = run_until_received(kernel, requester_peer, 1)
    assert time.monotonic() - start < 1.0

    response = pk.KillProcessResponse()
    response.decode(buf)
    assert (message_type, response.request_id, response.result) == \
        (pk.MessageType.KILL_PROCESS_RESP, 6, 0)
    assert target.get_exit_reason() == pk.ExitReason.KILLED
    assert "didn't exit within" in caplog.text


def test_slow_lookup_warning(pkernel, caplog):
    process, peer = add_running_process(pkernel, 1)
    start = time.monotonic()
    lookup_name(pkernel, process, peer, "test.later", blocking=True)

    pkernel.warn_slow_lookups(start + 1)
    assert "still waiting" not in caplog.text

    pkernel.warn_slow_lookups(start + pkernel.module.LOOKUP_WARNING_INTERVAL + 1)
    assert "lookup_name('test.later') still waiting" in caplog.text

    # It's still waiting: a slow lookup isn't failed.
    assert pkernel._name_waiters["test.later"][0][0] is process


@pytest.mark.parametrize("message", [pk.Shutdown(), pk.Reboot()])
def test_user_process_cannot_shut_down(pkernel, message, caplog):
    process, _ = user_process(pkernel, 2)
    send_to_kernel(pkernel, process, message)

    assert not pkernel._stopping
    assert "refused: not a system process" in caplog.text


################################################################
# Sockets
################################################################

def test_listens_on_loopback(pkernel):
    assert pkernel.socket.getsockname()[0] == "127.0.0.1"


def test_sockets_not_inherited(pkernel):
    process, _ = add_running_process(pkernel, 1)
    for sock in (pkernel.socket, pkernel._sigchld_reader, pkernel._sigchld_writer, process.get_socket()):
        assert not sock.get_inheritable()


def test_spawned_process_inherits_no_kernel_sockets(live_pkernel, tmp_path):
    # A spawned process' open sockets don't include any of the kernel's.
    kernel = live_pkernel
    module = kernel.module
    other, _ = add_running_process(kernel, 99)
    report = tmp_path / "sockets.json"
    path = write_app(tmp_path / "app", f"""
        import json
        import os
        def darq_main():
            inodes = []
            for fd in os.listdir("/proc/self/fd"):
                try:
                    target = os.readlink(f"/proc/self/fd/{{fd}}")
                except OSError:
                    continue
                if target.startswith("socket:["):
                    inodes.append(int(target[8:-1]))
            with open({str(report)!r}, "w") as f:
                json.dump(inodes, f)
        """)
    kernel._next_dpid = 2
    dpid = kernel.do_spawn_process(None, path)
    process = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: process.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the process to exit")

    inodes = set(json.loads(report.read_text()))
    kernel_sockets = (kernel.socket, kernel._sigchld_reader, kernel._sigchld_writer, other.get_socket())
    assert inodes.isdisjoint(os.fstat(sock.fileno()).st_ino for sock in kernel_sockets)


################################################################
# Exit
################################################################

def test_spawned_process_exit_call(live_pkernel, tmp_path):
    # darq.exit() from another thread of the application, while the main
    # thread is blocked: the status reaches the parent, and the host
    # process exits with it.
    kernel = live_pkernel
    module = kernel.module
    path = write_app(tmp_path / "app", """
        import threading
        import darq
        def darq_main():
            port = darq.open_port()
            threading.Timer(0.1, darq.exit, args=(7,)).start()
            try:
                darq.receive_message(port)
            finally:
                # Cleanup runs on exit.
                darq.close_port(port)
            return "not reached"
        """)
    dpid = kernel.do_spawn_process(None, path)
    process = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: process.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the process to exit")
    assert (process.get_exit_reason(), process.get_exit_status(), process.get_exit_error()) == \
        (pk.ExitReason.EXITED, 7, None)
    run_kernel_until(kernel, lambda: process.get_child().returncode is not None,
                     "the kernel to reap the process")
    assert process.get_child().returncode == 7


################################################################
# Dispatch
################################################################

def test_dispatch_handles_every_message(pkernel):
    process, peer = add_running_process(pkernel, 1)

    process.get_buffer().append(pk.OpenPortRequest(1).encode() +
                                pk.OpenPortRequest(2).encode())
    pkernel.dispatch(process)

    received = receive(peer)
    response = pk.OpenPortResponse()
    ids = []
    while received:
        response.decode(received)
        ids.append(response.request_id)
        received = received[response.length:]
    assert ids == [1, 2]


def test_dispatch_invalid_length_disconnects(pkernel):
    process, peer = add_running_process(pkernel, 1)

    process.get_buffer().append(header_with_length(pk.MessageType.OPEN_PORT_RQST, 0))
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


################################################################
# Bootstrap ports
################################################################

def spawn_with_bootstrap(kernel, parent, executable, bootstrap: int, dpid: int = 2):
    """Spawn a child of parent naming a bootstrap handle, returning it."""
    kernel._next_dpid = dpid
    send_to_kernel(kernel, parent, pk.SpawnProcessRequest(5, executable, bootstrap))
    return kernel._process_table[dpid]


def spawn_refused(kernel, parent, parent_peer, executable, bootstrap: int) -> pk.SpawnProcessResponse:
    """Spawn a child of parent naming a bad bootstrap handle, returning the refusal."""
    kernel._next_dpid = 2
    send_to_kernel(kernel, parent, pk.SpawnProcessRequest(5, executable, bootstrap))
    assert 2 not in kernel._process_table
    response = pk.SpawnProcessResponse(0, 0, 0, None)
    response.decode(receive(parent_peer))
    return response


def run_child(kernel, dpid: int) -> pk.RunResponse:
    """Connect a spawned child, and return the RunResponse it's sent."""
    module = kernel.module
    kernel_sock, peer = socket.socketpair()
    kernel.peers += [kernel_sock, peer]
    reader = module.TemporaryReader(kernel=kernel, sock=kernel_sock)
    kernel.incoming_connections[kernel_sock] = reader
    kernel.handle_run_request(reader, pk.RunRequest(dpid=dpid))
    response = pk.RunResponse()
    response.decode(peer.recv(response.get_length()))
    return response


def test_bootstrap_port_given_when_running(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    port = open_port(pkernel, parent, parent_peer)
    child = spawn_with_bootstrap(pkernel, parent, executable, port)

    # No right until it runs.
    assert child.handle_for(parent.get_handle(port).port) is None

    response = run_child(pkernel, 2)
    assert response.result == 0
    entry = child.get_handle(response.bootstrap)
    assert entry.port is parent.get_handle(port).port
    assert entry.right == pkernel.module.PortRight.SEND


def test_bootstrap_port_from_send_right(pkernel, fake_popen, executable):
    # Any right will do: the child gets a send right to another's port.
    parent, parent_peer = add_running_process(pkernel, 1)
    other, other_peer = add_running_process(pkernel, 3)
    other_port = open_port(pkernel, other, other_peer)
    handle = grant_send_right(pkernel, parent, other, other_port)
    child = spawn_with_bootstrap(pkernel, parent, executable, handle)

    response = run_child(pkernel, 2)
    assert child.get_handle(response.bootstrap).port is other.get_handle(other_port).port


def test_no_bootstrap_port(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    child = spawn_with_bootstrap(pkernel, parent, executable, 0)
    assert run_child(pkernel, 2).bootstrap == 0
    assert child.take_handles() == []


def test_bad_bootstrap_handle(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    response = spawn_refused(pkernel, parent, parent_peer, executable, 42)
    assert (response.request_id, response.result) == (5, pk.SpawnResult.BAD_BOOTSTRAP)
    assert "bootstrap handle 42 isn't open" in response.get_error()
    assert fake_popen.calls == []


def test_dead_bootstrap_port(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    other, other_peer = add_running_process(pkernel, 3)
    other_port = open_port(pkernel, other, other_peer)
    handle = grant_send_right(pkernel, parent, other, other_port)
    close_port(pkernel, other, other_peer, other_port)

    response = spawn_refused(pkernel, parent, parent_peer, executable, handle)
    assert response.result == pk.SpawnResult.BAD_BOOTSTRAP
    assert "is dead" in response.get_error()


def test_bootstrap_port_destroyed_before_running(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    port = open_port(pkernel, parent, parent_peer)
    child = spawn_with_bootstrap(pkernel, parent, executable, port)
    close_port(pkernel, parent, parent_peer, port)

    assert run_child(pkernel, 2).bootstrap == 0
    assert child.take_handles() == []


RENDEZVOUS_DIDL = """
    library rendezvous;
    protocol Registry { 1: Activate(string name) -> (port service); }
    protocol Bootstrap { 1: Ready(port service); }
    protocol Worker { 1: Hello(string who) -> (string greeting); }
    """


def test_activation_through_bootstrap_port(live_pkernel, tmp_path):
    # A registry starts a worker on demand, deferring the client's
    # Activate until the worker reports, over its bootstrap port, that
    # it's ready: as the Type service will start type implementations.
    from test_rpc import load_script
    didlc, didlpygen = load_script("didlc"), load_script("didlpygen")
    ir = didlc.compile_ir(didlc.Parser(didlc.lex(textwrap.dedent(RENDEZVOUS_DIDL))).parse())
    protocols = tmp_path / "protocols"
    protocols.mkdir()
    (protocols / "rendezvous.py").write_text(didlpygen.Generator(ir, "rendezvous.dir").generate())
    preamble = f"import sys\nsys.path.insert(0, {str(protocols)!r})\n"

    kernel = live_pkernel
    module = kernel.module
    worker_path = write_app(tmp_path / "worker", preamble + textwrap.dedent("""
        import darq
        from rendezvous import Bootstrap, HelloResponse, Worker

        class Greeter(Worker.Handler):
            def hello(self, request, source):
                return HelloResponse(greeting=f"hello, {request.who}")

        def darq_main():
            port = darq.open_port()
            server = darq.Server()
            server.add(port, Greeter())
            Bootstrap.Client(darq.get_bootstrap_port()).ready(service=port)
            server.run()
        """))
    registry_path = write_app(tmp_path / "registry", preamble + textwrap.dedent(f"""
        import darq
        from rendezvous import ActivateResponse, Bootstrap, Registry

        class Ready(Bootstrap.Handler):
            def __init__(self, registry, port):
                self.registry = registry
                self.port = port

            def ready(self, request, source):
                self.registry.running = request.service
                for pending in self.registry.waiting:
                    pending.reply(ActivateResponse(service=request.service))
                self.registry.waiting = []
                self.server.remove(self.port)
                darq.close_port(self.port)

        class Activator(Registry.Handler):
            running = None
            waiting = []

            def activate(self, request, source):
                if self.running is not None:
                    return ActivateResponse(service=self.running)
                pending = self.defer()
                if not self.waiting:
                    bootstrap = darq.open_port()
                    self.server.add(bootstrap, Ready(self, bootstrap))
                    darq.spawn_process({worker_path!r}, bootstrap=bootstrap)
                self.waiting.append(pending)
                return pending

        def darq_main():
            darq.serve("test.registry", Activator())
        """))
    client_path = write_app(tmp_path / "client", preamble + textwrap.dedent("""
        import darq
        from rendezvous import Registry, Worker

        def darq_main():
            registry = Registry.Client(darq.connect("test.registry"), timeout=10)
            service = registry.activate(name="worker").service
            if service is None:
                return "no service port"
            greeting = Worker.Client(service, timeout=10).hello(who="client").greeting
            if greeting != "hello, client":
                return f"greeting {greeting!r}"
            # Already running: answered at once, with the same port.
            if registry.activate(name="worker").service != service:
                return "a second worker"
            if darq.get_bootstrap_port() is not None:
                return "the client has a bootstrap port"
            return 0
        """))

    kernel.do_spawn_process(None, registry_path)
    dpid = kernel.do_spawn_process(None, client_path)
    client = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: client.get_status() == module.KernelProcessStatus.ZOMBIE,
                     "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
