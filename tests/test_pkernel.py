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

import pytest

import darq


PKERNEL_PATH = pathlib.Path(__file__).parent.parent / "kernel" / "pkernel"


def load_pkernel():
    """Import kernel/pkernel as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("pkernel", str(PKERNEL_PATH))
    spec = importlib.util.spec_from_loader("pkernel", loader)
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

    running = darq.Running(dpid=3)
    running.host_pid = 1234
    pkernel.handle_running(reader, running)

    assert kernel_sock not in pkernel.incoming_connections
    assert process.get_socket() is kernel_sock
    assert process.get_status() == module.KernelProcessStatus.RUNNING
    assert process.get_host_pid() == 1234


################################################################
# spawn_process
################################################################

class FakePopen:
    """Record the stub command line, rather than starting a process."""
    calls: list[list[str]] = []

    envs: list[dict[str, str]] = []

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
    kernel.handle_running(reader, darq.Running(dpid=dpid))


def test_spawn_process_starts_stub(pkernel, fake_popen, executable):
    parent, parent_peer = add_running_process(pkernel, 1)
    pkernel._next_dpid = 2

    send_to_kernel(pkernel, parent, darq.SpawnProcessRequest(5, executable))

    module = pkernel.module
    assert fake_popen.calls == [[module.PYTHON_IMAGE_LOADER, str(pkernel.socket.getsockname()[1]), "2", "1", executable]]
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
    """Run the p-kernel's event loop until condition() is true."""
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

    # The parent is unaffected.
    parent = kernel._process_table[parent_dpid]
    assert parent.get_status() == module.KernelProcessStatus.RUNNING
    assert parent.get_child().poll() is None


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


def test_running_with_unknown_dpid(pkernel):
    module = pkernel.module
    kernel_sock, peer = socket.socketpair()
    pkernel.peers += [kernel_sock, peer]

    # As if accepted from the listening socket.
    reader = module.TemporaryReader(kernel=pkernel, sock=kernel_sock)
    pkernel.incoming_connections[kernel_sock] = reader
    pkernel._loop.add_socket(kernel_sock, pkernel)

    pkernel.handle_running(reader, darq.Running(dpid=99))

    # The connection is dropped, and the loop no longer watches it.
    assert kernel_sock.fileno() == -1
    assert kernel_sock not in pkernel._loop.sockets
    assert kernel_sock not in pkernel.incoming_connections
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
    pkernel.handle_running(reader, darq.Running(dpid=3))

    # The impostor is dropped, and the running process is untouched.
    assert kernel_sock.fileno() == -1
    assert kernel_sock not in pkernel._loop.sockets
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
    pkernel.handle_running(reader, darq.Running(dpid=3))

    assert kernel_sock.fileno() == -1
    assert process.get_socket() is None
    assert process.get_status() == module.KernelProcessStatus[status]


################################################################
# Connections before RUNNING
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
    peer.sendall(darq.OpenPortRequest(1, 0).encode())

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_invalid_length_before_running(pkernel):
    reader, kernel_sock, peer = accept_connection(pkernel)
    peer.sendall(header_with_length(darq.MessageType.RUNNING, 4))

    reader.on_readable(kernel_sock)

    assert_incoming_dropped(pkernel, kernel_sock, peer)


def test_running_split_across_reads(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)
    running = darq.Running(dpid=3).encode()

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

    send_to_kernel(pkernel, process, darq.Running(dpid=3))

    # Ignored: the process keeps its connection and state.
    assert process.get_socket() is sock
    assert pkernel._processes[sock] is process
    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    assert receive(peer) == b""


def test_pcall_in_same_read_as_running(pkernel):
    process = add_spawned_process(pkernel, 3)
    reader, kernel_sock, peer = accept_connection(pkernel)

    peer.sendall(darq.Running(dpid=3).encode() + darq.OpenPortRequest(7, 0).encode())
    reader.on_readable(kernel_sock)

    assert process.get_status() == pkernel.module.KernelProcessStatus.RUNNING
    response = darq.OpenPortResponse()
    response.decode(peer.recv(65536))
    assert response.request_id == 7
    assert response.result == 0


################################################################
# Dispatch
################################################################

def test_dispatch_handles_every_message(pkernel):
    process, peer = add_running_process(pkernel, 1)

    process.get_buffer().append(darq.OpenPortRequest(1, 0).encode() +
                                darq.OpenPortRequest(2, 0).encode())
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


def test_pyloader_with_unknown_dpid_exits(live_pkernel, executable):
    kernel = live_pkernel
    port = kernel.socket.getsockname()[1]

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
    assert "Disconnected by p-kernel" in stderr
