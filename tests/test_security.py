# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Security service, services/security: the password's
# store, and the service's handling of requests, with its replies and
# timers faked; and then started at boot through a real p-kernel.

import importlib.util
import json
import os
import pathlib
import stat
import typing

import pytest

from darq import dipc, system
from darq.protocols.security import (
    CheckPasswordRequest,
    CheckPasswordResponse,
    IsPasswordSetRequest,
    IsPasswordSetResponse,
    SetPasswordRequest,
    Status,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "security"

PORT = 16385
CLIENT = 16386


def load_service():
    spec = importlib.util.spec_from_file_location("security_service", SERVICE_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_service()


@pytest.fixture(autouse=True)
def quick_hashes(monkeypatch):
    """Cheap hashes, so the tests are quick: the parameters are stored
    with each one, so checking uses them too."""
    monkeypatch.setattr(module, "SCRYPT_N", 2 ** 10)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeServer:
    """Records call_later's timers: run them with advance()."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.timers: list[tuple[float, typing.Callable[[], None]]] = []

    def call_later(self, delay, callback):
        assert delay >= 0
        self.timers.append((self.clock.now + delay, callback))

    def advance(self, seconds: float):
        self.clock.now += seconds
        while due := [t for t in self.timers if t[0] <= self.clock.now]:
            self.timers.remove(due[0])
            due[0][1]()


@pytest.fixture
def service(tmp_path, monkeypatch):
    """The service, with its replies, deferred or not, collected in
    service.replies, by txid."""
    clock = Clock()
    handler = module.SecurityService(module.PasswordStore(str(tmp_path / "security.json")), clock)
    handler.server = FakeServer(clock)
    handler.replies = {}

    def send_reply(port, source, reply):
        assert (port, source) == (PORT, CLIENT)
        handler.replies[reply.txid] = reply
    monkeypatch.setattr(system, "_send_reply", send_reply)
    return handler


def ask(service, txid: int, request: dipc.Message):
    """Send a request: return its reply, if it's sent at once."""
    request.txid = txid
    payload, _ = request.encode()
    reply = service.dispatch(payload, CLIENT, (), PORT)
    if reply is not None:
        service.replies[txid] = reply
    return service.replies.pop(txid, None)


def check(service, txid: int, password: str):
    return ask(service, txid, CheckPasswordRequest(password=password))


def statuses(service) -> dict[int, Status]:
    replies, service.replies = service.replies, {}
    return {txid: reply.status for txid, reply in replies.items()}


################################################################
# The password's store
################################################################

def test_hash_and_verify():
    record = module.hash_password("swordfish")
    assert record["n"] == 2 ** 10 and len(record["salt"]) == 24
    assert module.verify_password("swordfish", record)
    assert not module.verify_password("Swordfish", record)

    # Each hash has its own salt.
    assert module.hash_password("swordfish")["hash"] != record["hash"]


def test_store(tmp_path):
    path = tmp_path / "security.json"
    store = module.PasswordStore(str(path))
    assert not store.is_set() and not store.check("")

    store.set("swordfish")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "swordfish" not in path.read_text()
    assert not (tmp_path / "security.json.new").exists()

    again = module.PasswordStore(str(path))
    assert again.is_set() and again.check("swordfish") and not again.check("x")


@pytest.mark.parametrize("contents", ["not json", json.dumps({"version": 2}), json.dumps({"version": 1})])
def test_broken_store_fails_closed(tmp_path, contents):
    path = tmp_path / "security.json"
    path.write_text(contents)
    store = module.PasswordStore(str(path))
    assert store.is_set()
    assert not store.check("") and not store.check("swordfish")


################################################################
# The service
################################################################

def test_first_password(service):
    assert ask(service, 1, IsPasswordSetRequest()) == IsPasswordSetResponse(txid=1, set=False)
    assert check(service, 2, "x").status == Status.NOT_SET

    # It's set without an old one; then, it can't be again.
    assert ask(service, 3, SetPasswordRequest(old_password="y", new_password="x")).status == Status.NOT_SET
    assert ask(service, 4, SetPasswordRequest(new_password="swordfish")).status == Status.OK
    assert ask(service, 5, IsPasswordSetRequest()).set
    assert ask(service, 6, SetPasswordRequest(new_password="other")).status == Status.ALREADY_SET
    assert check(service, 7, "swordfish") == CheckPasswordResponse(txid=7, status=Status.OK)


@pytest.mark.parametrize("password", ["", "x" * 1025, "é" * 513])
def test_invalid_password(service, password):
    assert ask(service, 1, SetPasswordRequest(new_password=password)).status == Status.INVALID
    assert not service.store.is_set()


def test_valid_password_at_the_limit(service):
    assert ask(service, 1, SetPasswordRequest(new_password="é" * 512)).status == Status.OK


def test_change_password(service):
    ask(service, 1, SetPasswordRequest(new_password="swordfish"))
    assert ask(service, 2, SetPasswordRequest(old_password="wrong", new_password="x")).status == \
        Status.WRONG_PASSWORD
    assert ask(service, 3, SetPasswordRequest(old_password="swordfish", new_password="x")).status == Status.OK
    assert check(service, 4, "x").status == Status.OK
    assert check(service, 5, "swordfish").status == Status.WRONG_PASSWORD


def test_wrong_passwords_slow_down(service):
    ask(service, 1, SetPasswordRequest(new_password="swordfish"))
    server = service.server

    # Three are answered at once.
    for txid in (2, 3, 4):
        assert check(service, txid, "guess").status == Status.WRONG_PASSWORD

    # The fourth waits a second, and the fifth two more: a right one
    # waits its turn, too.
    assert check(service, 6, "guess") is None
    assert check(service, 7, "swordfish") is None
    server.advance(0.9)
    assert statuses(service) == {}
    server.advance(0.1)
    assert statuses(service) == {6: Status.WRONG_PASSWORD}
    server.advance(1.9)
    assert statuses(service) == {}
    server.advance(0.1)
    assert statuses(service) == {7: Status.OK}

    # A right one resets the count.
    assert check(service, 8, "guess").status == Status.WRONG_PASSWORD


def test_delays_double_up_to_the_limit(service):
    backoff = service.backoff
    delays = []
    for _ in range(12):
        backoff.failed()
        delays.append(backoff.wait())
    assert delays == [0, 0, 1, 2, 4, 8, 16, 30, 30, 30, 30, 30]


def test_change_password_slowed_too(service):
    ask(service, 1, SetPasswordRequest(new_password="swordfish"))
    for txid in range(2, 5):
        check(service, txid, "guess")
    request = SetPasswordRequest(old_password="swordfish", new_password="new")
    assert ask(service, 6, request) is None
    service.server.advance(1)
    assert statuses(service) == {6: Status.OK}
    assert service.store.check("new")


################################################################
# At boot
################################################################

def test_security_at_boot(live_pkernel, tmp_path):  # noqa: F811
    # Started at boot: a client sets the password, and checks it.
    kernel = live_pkernel
    client_path = write_app(tmp_path / "client", """
        import darq
        from darq.protocols.security import Security, Status

        def darq_main():
            security = Security.Client(darq.connect("darq.security"), timeout=30)
            if security.is_password_set().set:
                return "already set"
            if security.set_password(old_password="", new_password="swordfish").status != Status.OK:
                return "not set"
            if security.check_password(password="swordfish").status != Status.OK:
                return "not right"
            if security.check_password(password="guess").status != Status.WRONG_PASSWORD:
                return "not wrong"
            return 0
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (pathlib.Path(kernel.run_dir) / "security.json").exists()
