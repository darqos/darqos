# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Storage service, services/storage/main.py: its handler
# directly, on a temporary database, and then as a service, through a
# real p-kernel (see test_pkernel.py for the fixtures).

import importlib.util
import pathlib

import pytest

from darq.protocols.storage import (
    CreateRequest,
    DeleteRequest,
    GetRequest,
    Key,
    PutRequest,
    StatRequest,
    Status,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "storage"


def load_service():
    spec = importlib.util.spec_from_file_location("storage_service", SERVICE_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_service()


@pytest.fixture
def database(tmp_path):
    return str(tmp_path / "storage.sqlite")


@pytest.fixture
def service(database):
    service = module.StorageService(database)
    yield service
    service.db.close()


def create(service, value: bytes) -> Key:
    response = service.create(CreateRequest(value=value), 0)
    assert (response.status, response.generation) == (Status.OK, 1)
    return response.key


################################################################
# Handler
################################################################

def test_create_and_get(service):
    key = create(service, b"hello")
    response = service.get(GetRequest(key=key), 0)
    assert (response.status, response.value, response.generation) == (Status.OK, b"hello", 1)


def test_keys_are_random_and_distinct(service):
    keys = {(k.high, k.low) for k in (create(service, b"") for _ in range(50))}
    assert len(keys) == 50
    # They use the whole 64 bits of each half, which SQLite integers can't hold.
    assert any(high >= 2 ** 63 for high, _ in keys)


def test_put_replaces_and_counts_generations(service):
    key = create(service, b"one")
    assert (service.put(PutRequest(key=key, value=b"two"), 0).generation) == 2
    response = service.put(PutRequest(key=key, expected=2, value=b"three"), 0)
    assert (response.status, response.generation) == (Status.OK, 3)
    assert service.get(GetRequest(key=key), 0).value == b"three"


def test_put_conflict(service):
    key = create(service, b"one")
    service.put(PutRequest(key=key, value=b"two"), 0)

    # Expecting generation 1, but it's at 2: nothing is written.
    response = service.put(PutRequest(key=key, expected=1, value=b"stale"), 0)
    assert (response.status, response.generation) == (Status.CONFLICT, 2)
    current = service.get(GetRequest(key=key), 0)
    assert (current.value, current.generation) == (b"two", 2)


def test_stat(service):
    key = create(service, bytes(1000))
    response = service.stat(StatRequest(key=key), 0)
    assert (response.status, response.size, response.generation) == (Status.OK, 1000, 1)


def test_delete(service):
    key = create(service, b"x")
    assert service.delete(DeleteRequest(key=key), 0).status == Status.OK
    assert service.get(GetRequest(key=key), 0).status == Status.NOT_FOUND
    assert service.delete(DeleteRequest(key=key), 0).status == Status.NOT_FOUND


@pytest.mark.parametrize("call", [
    lambda s, k: s.get(GetRequest(key=k), 0),
    lambda s, k: s.put(PutRequest(key=k, value=b"x"), 0),
    lambda s, k: s.stat(StatRequest(key=k), 0),
    lambda s, k: s.delete(DeleteRequest(key=k), 0),
])
def test_not_found(service, call):
    # Put never creates a blob.
    key = Key(1, 2)
    assert call(service, key).status == Status.NOT_FOUND
    assert service.stat(StatRequest(key=key), 0).status == Status.NOT_FOUND


def test_empty_value(service):
    key = create(service, b"")
    assert service.get(GetRequest(key=key), 0).value == b""
    assert service.stat(StatRequest(key=key), 0).size == 0


def test_persists_across_restart(database):
    first = module.StorageService(database)
    key = create(first, b"kept")
    first.put(PutRequest(key=key, value=b"kept, changed"), 0)
    first.db.close()

    second = module.StorageService(database)
    try:
        response = second.get(GetRequest(key=key), 0)
        assert (response.value, response.generation) == (b"kept, changed", 2)
    finally:
        second.db.close()


################################################################
# As a service
################################################################

def test_storage_service(live_pkernel, tmp_path):  # noqa: F811
    # The service is spawned by the p-kernel, keeps its database in the
    # run directory, and is used through the generated client.
    kernel = live_pkernel
    client_path = write_app(tmp_path / "client", """
        import darq
        from darq.protocols.storage import Status, Storage

        def darq_main():
            storage = Storage.Client(darq.connect("darq.storage"), timeout=10)
            created = storage.create(value=b"hello")
            if created.status != Status.OK:
                return f"create: {created!r}"
            key = created.key

            got = storage.get(key=key)
            if (got.value, got.generation) != (b"hello", 1):
                return f"get: {got!r}"

            put = storage.put(key=key, expected=1, value=b"hello, again")
            if (put.status, put.generation) != (Status.OK, 2):
                return f"put: {put!r}"
            if storage.put(key=key, expected=1, value=b"stale").status != Status.CONFLICT:
                return "stale put succeeded"

            stat = storage.stat(key=key)
            if (stat.size, stat.generation) != (12, 2):
                return f"stat: {stat!r}"

            if storage.delete(key=key).status != Status.OK:
                return "delete failed"
            if storage.get(key=key).status != Status.NOT_FOUND:
                return "deleted blob still there"
            return 0
        """)

    kernel.do_spawn_process(None, str(SERVICE_PATH))
    dpid = kernel.do_spawn_process(None, client_path)
    client = kernel._process_table[dpid]
    run_kernel_until(kernel, lambda: client.get_status() == kernel.module.KernelProcessStatus.ZOMBIE,
                     "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    assert (pathlib.Path(kernel.run_dir) / "storage.sqlite").exists()
