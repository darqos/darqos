# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Type service, services/type/main.py, and its catalogue
# sync, tools/type-sync/main.py: the handler directly, with an
# in-memory catalogue and faked p-calls, and then the whole system,
# through a real p-kernel (see test_pkernel.py for the fixtures).

import importlib.util
import json
import pathlib

import pytest

import darq
from darq import dipc, system
from darq.protocols import storage
from darq.protocols.type import (
    ActivateRequest,
    ActivateResponse,
    DeregisterLensRequest,
    DeregisterRequest,
    DescribeRequest,
    Description,
    FindLensesRequest,
    ListTypesRequest,
    Ready,
    RegisterLensRequest,
    RegisterLensResponse,
    RegisterRequest,
    RegisterResponse,
    Status,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_rpc import FakePorts


ROOT = pathlib.Path(__file__).parent.parent
SERVICE_PATH = ROOT / "services" / "type"
SYNC_PATH = ROOT / "tools" / "type-sync"


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service_module = load("type_service", SERVICE_PATH)
sync_module = load("type_sync", SYNC_PATH)


class MemoryCatalogue:
    """Stands in for the Storage-backed catalogue."""

    def __init__(self, data=None):
        self.data = data
        self.saves = 0

    def load(self):
        return self.data

    def save(self, data):
        # Saved as JSON, so a round trip through it must work.
        self.data = json.loads(json.dumps(data))
        self.saves += 1


@pytest.fixture
def catalogue():
    return MemoryCatalogue()


@pytest.fixture
def service(catalogue):
    return service_module.TypeService(catalogue)


def register(service, type_id, implementation="", **fields):
    request = RegisterRequest(type_id=type_id, description=Description(**fields), implementation=implementation)
    return service.register(request, 0).status


################################################################
# Catalogue
################################################################

def test_register_and_describe(service, catalogue):
    assert register(service, "Public.Text", name="Text", conforms_to=["Public.Data"]) == Status.OK
    assert catalogue.saves == 1

    response = service.describe(DescribeRequest(type_id="public.text"), 0)
    assert response.status == Status.OK
    assert response.description.name == "Text"
    assert response.description.conforms_to == ["public.data"]
    assert response.implementation == ""


def test_implementation_kept_apart(service):
    register(service, "test.thing", "/darq/types/thing", name="Thing")
    assert service.implementations == {"test.thing": "/darq/types/thing"}
    assert service.describe(DescribeRequest(type_id="test.thing"), 0).implementation == "/darq/types/thing"

    # Registering without one removes it.
    register(service, "test.thing", name="Thing")
    assert service.implementations == {}


def test_unchanged_registration_not_saved(service, catalogue):
    register(service, "test.thing", "/path", name="Thing", conforms_to=["public.data"])
    register(service, "Test.Thing", "/path", name="Thing", conforms_to=["Public.Data"])
    assert catalogue.saves == 1
    register(service, "test.thing", "/path", name="Thing, renamed", conforms_to=["public.data"])
    assert catalogue.saves == 2


@pytest.mark.parametrize("type_id, conforms_to", [("bad:id", []), ("", []), ("test.ok", ["bad id"])])
def test_register_invalid_ids(service, catalogue, type_id, conforms_to):
    assert register(service, type_id, conforms_to=conforms_to) == Status.INVALID_ID
    assert service.descriptions == {}
    assert catalogue.saves == 0


def test_describe_unknown(service):
    assert service.describe(DescribeRequest(type_id="test.nothing"), 0).status == Status.NOT_FOUND
    assert service.describe(DescribeRequest(type_id="bad:id"), 0).status == Status.INVALID_ID


def test_list_types(service):
    assert service.list_types(ListTypesRequest(implemented=False), 0).type_ids == []
    register(service, "test.b", "/b")
    register(service, "test.a", "")
    register(service, "test.c", "/c")
    assert service.list_types(ListTypesRequest(implemented=False), 0).type_ids == ["test.a", "test.b", "test.c"]
    assert service.list_types(ListTypesRequest(implemented=True), 0).type_ids == ["test.b", "test.c"]


def test_deregister(service, catalogue):
    register(service, "test.thing", "/path", name="Thing")
    assert service.deregister(DeregisterRequest(type_id="TEST.THING"), 0).status == Status.OK
    assert (service.descriptions, service.implementations, catalogue.saves) == ({}, {}, 2)
    assert service.deregister(DeregisterRequest(type_id="test.thing"), 0).status == Status.NOT_FOUND


def test_catalogue_reloaded(catalogue):
    first = service_module.TypeService(catalogue)
    register(first, "test.thing", "/path", name="Thing", mime_types=["text/x-thing"])
    first.register_lens(RegisterLensRequest(lens="/lens", type_id="test.thing", context="terminal"), 0)

    second = service_module.TypeService(catalogue)
    assert second.descriptions == first.descriptions
    assert second.implementations == {"test.thing": "/path"}
    assert second.lenses == [("/lens", "test.thing", "terminal")]


def test_catalogue_version_checked():
    with pytest.raises(ValueError, match="version"):
        service_module.TypeService(MemoryCatalogue({"version": 99}))


################################################################
# Lenses
################################################################

def find(service, type_id, context="terminal"):
    response = service.find_lenses(FindLensesRequest(type_id=type_id, context=context), 0)
    return response.status, response.lenses


@pytest.fixture
def text_types(service):
    register(service, "public.data")
    register(service, "public.content")
    register(service, "public.text", conforms_to=["public.data", "public.content"])
    register(service, "public.plain-text", conforms_to=["public.text"])
    register(service, "public.utf8-plain-text", conforms_to=["public.plain-text"])
    return service


def add_lens(service, lens, type_id, context="terminal"):
    request = RegisterLensRequest(lens=lens, type_id=type_id, context=context)
    return service.register_lens(request, 0).status


def test_lens_for_type(text_types):
    add_lens(text_types, "/plain", "public.plain-text")
    assert find(text_types, "Public.Plain-Text") == (Status.OK, ["/plain"])


def test_lens_falls_back_to_nearest(text_types):
    add_lens(text_types, "/text", "public.text")
    add_lens(text_types, "/hex", "public.data")
    assert find(text_types, "public.utf8-plain-text") == (Status.OK, ["/text"])

    # A specific lens hides the more general ones.
    add_lens(text_types, "/utf8", "public.utf8-plain-text")
    assert find(text_types, "public.utf8-plain-text") == (Status.OK, ["/utf8"])


def test_lens_fallback_level_together(text_types):
    # Both of public.text's general types are as near as each other.
    add_lens(text_types, "/content", "public.content")
    add_lens(text_types, "/hex", "public.data")
    assert find(text_types, "public.text") == (Status.OK, ["/content", "/hex"])


def test_lens_context(text_types):
    add_lens(text_types, "/text", "public.text", context="voice")
    assert find(text_types, "public.text") == (Status.NOT_FOUND, [])
    assert find(text_types, "public.text", "voice") == (Status.OK, ["/text"])


def test_lens_conformance_cycle(service):
    register(service, "test.a", conforms_to=["test.b"])
    register(service, "test.b", conforms_to=["test.a"])
    assert find(service, "test.a") == (Status.NOT_FOUND, [])


def test_lens_registered_once(text_types, catalogue):
    saves = catalogue.saves
    add_lens(text_types, "/text", "public.text")
    add_lens(text_types, "/text", "Public.Text")
    assert text_types.lenses == [("/text", "public.text", "terminal")]
    assert catalogue.saves == saves + 1


def test_deregister_lens(text_types):
    add_lens(text_types, "/text", "public.text")
    request = DeregisterLensRequest(lens="/text", type_id="public.text", context="terminal")
    assert text_types.deregister_lens(request, 0).status == Status.OK
    assert text_types.deregister_lens(request, 0).status == Status.NOT_FOUND
    assert find(text_types, "public.text") == (Status.NOT_FOUND, [])


################################################################
# Activation, with faked p-calls
################################################################

SERVICE_PORT = 20
BOOTSTRAP_PORT = 30
IMPLEMENTATION = 40
CLIENT = 99


class FakeProcesses:
    """Stands in for the p-calls the Type service makes, besides
    sending and receiving."""

    def __init__(self):
        self.spawned = []
        self.closed = []
        self.watched = []
        self.next_port = BOOTSTRAP_PORT
        self.fail_spawn = False

    def open_port(self):
        self.next_port += 1
        return self.next_port

    def spawn_process(self, path, bootstrap=None):
        if self.fail_spawn:
            raise darq.SpawnError(1, "no such program")
        self.spawned.append((path, bootstrap))
        return 1000 + len(self.spawned)

    def close_port(self, port):
        self.closed.append(port)

    def notify_on_death(self, handle, notify_port):
        self.watched.append((handle, notify_port))


@pytest.fixture
def fake_ports(monkeypatch):
    fake = FakePorts()
    monkeypatch.setattr(system, "receive_any", fake.receive_any)
    monkeypatch.setattr(system, "send_message", fake.send_message)
    return fake


@pytest.fixture
def processes(monkeypatch):
    fake = FakeProcesses()
    for name in ("open_port", "spawn_process", "close_port", "notify_on_death"):
        monkeypatch.setattr(darq, name, getattr(fake, name))
    return fake


@pytest.fixture
def served(service, fake_ports, processes):
    """The service, answering on SERVICE_PORT, with a thing implementation."""
    server = darq.Server()
    service.add(server, SERVICE_PORT)
    register(service, "test.thing", "/darq/types/thing", name="Thing")
    return service, server


def activate(fake_ports, txid, type_id="test.thing", source=CLIENT):
    buf, handles = ActivateRequest(txid, type_id=type_id).encode()
    fake_ports.deliver(SERVICE_PORT, source, buf, handles)


def ready(fake_ports, bootstrap, port=IMPLEMENTATION):
    buf, handles = Ready(implementation=port).encode()
    fake_ports.deliver(bootstrap, 77, buf, handles)


def replies(fake_ports):
    """The Activate replies sent: (to, txid, status, port)."""
    result = []
    for source, destination, buf, handles in fake_ports.sent:
        assert source == SERVICE_PORT
        response = ActivateResponse()
        response.decode(buf, handles)
        result.append((destination, response.txid, response.status, response.implementation))
    fake_ports.sent.clear()
    return result


def test_activate_unknown(served, fake_ports, processes):
    service, server = served
    activate(fake_ports, 1, "test.nothing")
    activate(fake_ports, 2, "bad:id")
    server.run_once(timeout=0)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 1, Status.NOT_FOUND, None), (CLIENT, 2, Status.INVALID_ID, None)]
    assert processes.spawned == []


def test_activate_starts_implementation(served, fake_ports, processes):
    service, server = served
    activate(fake_ports, 1)
    server.run_once(timeout=0)

    # Spawned with a bootstrap port, and the reply deferred.
    [(path, bootstrap)] = processes.spawned
    assert path == "/darq/types/thing"
    assert bootstrap in server.ports
    assert replies(fake_ports) == []

    # A second Activate while it starts waits too, without a respawn.
    activate(fake_ports, 2, "Test.Thing", source=CLIENT + 1)
    server.run_once(timeout=0)
    assert len(processes.spawned) == 1

    ready(fake_ports, bootstrap)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 1, Status.OK, IMPLEMENTATION),
                                   (CLIENT + 1, 2, Status.OK, IMPLEMENTATION)]

    # Its port is watched, and the bootstrap port finished with.
    assert processes.watched == [(IMPLEMENTATION, SERVICE_PORT)]
    assert bootstrap not in server.ports
    assert processes.closed == [bootstrap]
    assert service.running == {"/darq/types/thing": IMPLEMENTATION}

    # Once it's running, Activate answers at once.
    activate(fake_ports, 3)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 3, Status.OK, IMPLEMENTATION)]


def test_types_share_an_implementation(served, fake_ports, processes):
    service, server = served
    register(service, "test.other", "/darq/types/thing", name="Other")
    activate(fake_ports, 1)
    server.run_once(timeout=0)

    # Another of its types, while it starts, waits for the same one.
    activate(fake_ports, 2, "test.other")
    server.run_once(timeout=0)
    assert len(processes.spawned) == 1
    ready(fake_ports, processes.spawned[0][1])
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 1, Status.OK, IMPLEMENTATION), (CLIENT, 2, Status.OK, IMPLEMENTATION)]

    # And once it's running.
    activate(fake_ports, 3, "test.other")
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 3, Status.OK, IMPLEMENTATION)]
    assert len(processes.spawned) == 1


def test_dead_implementation_restarted(served, fake_ports, processes):
    service, server = served
    activate(fake_ports, 1)
    server.run_once(timeout=0)
    ready(fake_ports, processes.spawned[0][1])
    server.run_once(timeout=0)
    replies(fake_ports)

    fake_ports.deliver(SERVICE_PORT, 0, dipc.PortDestroyed(IMPLEMENTATION).encode()[0])
    server.run_once(timeout=0)
    assert service.running == {}
    assert IMPLEMENTATION in processes.closed

    activate(fake_ports, 2)
    server.run_once(timeout=0)
    assert len(processes.spawned) == 2
    ready(fake_ports, processes.spawned[1][1], IMPLEMENTATION + 1)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 2, Status.OK, IMPLEMENTATION + 1)]


def test_spawn_failure(served, fake_ports, processes):
    service, server = served
    processes.fail_spawn = True
    activate(fake_ports, 1)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 1, Status.FAILED, None)]
    assert processes.closed == [BOOTSTRAP_PORT + 1]
    assert service.starting == {}


def test_not_ready_in_time(served, fake_ports, processes, monkeypatch):
    service, server = served
    monkeypatch.setattr(service_module, "ACTIVATION_TIMEOUT", 0.01)
    activate(fake_ports, 1)
    server.run_once(timeout=0)
    bootstrap = processes.spawned[0][1]

    server.run_once()
    assert replies(fake_ports) == [(CLIENT, 1, Status.FAILED, None)]
    assert bootstrap not in server.ports
    assert service.starting == {}

    # A late Ready is never received: the port is closed.
    assert processes.closed == [bootstrap]


def test_ready_without_port(served, fake_ports, processes):
    service, server = served
    activate(fake_ports, 1)
    server.run_once(timeout=0)
    buf, handles = Ready(implementation=None).encode()
    fake_ports.deliver(processes.spawned[0][1], 77, buf, handles)
    server.run_once(timeout=0)
    assert replies(fake_ports) == [(CLIENT, 1, Status.FAILED, None)]
    assert service.running == {}


################################################################
# Saving the catalogue in Storage
################################################################

class FakeStorage:
    """Stands in for storage.Storage.Client."""

    def __init__(self):
        self.blobs = {}

    def create(self, value):
        key = storage.Key(1, 2)
        self.blobs[(key.high, key.low)] = value
        return storage.CreateResponse(key=key, generation=1, status=storage.Status.OK)

    def get(self, key):
        value = self.blobs.get((key.high, key.low))
        if value is None:
            return storage.GetResponse(status=storage.Status.NOT_FOUND)
        return storage.GetResponse(value=value, generation=1, status=storage.Status.OK)

    def put(self, key, expected, value):
        self.blobs[(key.high, key.low)] = value
        return storage.PutResponse(generation=2, status=storage.Status.OK)


def test_storage_catalogue(monkeypatch):
    boot = {}
    monkeypatch.setattr(darq, "get_boot_value", boot.get)
    monkeypatch.setattr(darq, "set_boot_value", boot.__setitem__)
    client = FakeStorage()

    first = service_module.StorageCatalogue(client)  # type: ignore[arg-type]
    assert first.load() is None
    first.save({"version": 1, "n": 1})
    assert boot == {service_module.CATALOGUE_KEY: (1).to_bytes(8, "big") + (2).to_bytes(8, "big")}
    first.save({"version": 1, "n": 2})

    second = service_module.StorageCatalogue(client)  # type: ignore[arg-type]
    assert second.load() == {"version": 1, "n": 2}


################################################################
# Catalogue sync
################################################################

class FakeTypeClient:
    """Stands in for Type.Client, recording what's registered."""

    def __init__(self):
        self.types = {}
        self.lenses = []

    def register(self, type_id, description, implementation):
        if ":" in type_id:
            return RegisterResponse(status=Status.INVALID_ID)
        self.types[type_id] = (description, implementation)
        return RegisterResponse(status=Status.OK)

    def register_lens(self, lens, type_id, context):
        self.lenses.append((lens, type_id, context))
        return RegisterLensResponse(status=Status.OK)


def write_json(path: pathlib.Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_sync(tmp_path):
    root = tmp_path / "darq"
    base = tmp_path / "catalogue.json"
    write_json(base, {"types": [{"id": "public.text", "name": "Text"}]})
    write_json(root / "types" / "thing" / "type.json", {"types": [
        {"id": "test.thing", "name": "Thing", "conforms_to": ["public.text"], "icon": "thing.png",
         "implemented": True},
        {"id": "test.other"},
        {"id": "bad:id"},
    ]})
    write_json(root / "lenses" / "viewer" / "lens.json", {"types": ["test.thing", "test.other"]})
    (root / "lenses" / "broken").mkdir(parents=True)
    (root / "lenses" / "broken" / "lens.json").write_text("{")

    client = FakeTypeClient()
    assert sync_module.sync(client, root, base) == 1      # bad:id

    thing = root / "types" / "thing"
    description, implementation = client.types["test.thing"]
    assert implementation == str(thing)
    assert (description.name, description.conforms_to, description.icon) == \
        ("Thing", ["public.text"], str(thing / "thing.png"))
    assert client.types["test.other"][1] == ""
    assert client.types["public.text"] == (Description(name="Text"), "")

    viewer = str(root / "lenses" / "viewer")
    assert client.lenses == [(viewer, "test.thing", "terminal"), (viewer, "test.other", "terminal")]


def test_base_catalogue_consistent():
    types = json.loads(sync_module.BASE_CATALOGUE.read_text())["types"]
    ids = [t["id"] for t in types]
    assert len(ids) == len(set(ids))
    for t in types:
        assert darq.canonical_type_id(t["id"]) == t["id"]
        assert set(t.get("conforms_to", [])) <= set(ids), t["id"]
        assert not t.get("implemented")


################################################################
# The whole system
################################################################

def test_type_service(live_pkernel, tmp_path, monkeypatch):  # noqa: F811
    # Storage and the Type service are spawned by the p-kernel, the sync
    # registers an installed implementation, and a client activates it,
    # survives its exit, and activates it again.
    kernel = live_pkernel
    root = tmp_path / "darq"
    (root / "types").mkdir(parents=True)
    implementation = write_app(root / "types" / "thing", """
        import darq
        from darq.protocols.type import CreateResponse, DestroyResponse, Implementation, Ready, Status

        class Thing(Implementation.Handler):
            def create(self, request, source):
                return CreateResponse(item_id=f"{request.type_id}:1", status=Status.OK)

            def destroy(self, request, source):
                # Exit once the reply has gone.
                self.server.call_later(0, lambda: darq.exit(0))
                return DestroyResponse(status=Status.OK)

        def darq_main():
            port = darq.open_port()
            server = darq.Server()
            server.add(port, Thing())
            darq.send(darq.get_bootstrap_port(), Ready(implementation=port))
            server.run()
        """)
    write_json(root / "types" / "thing" / "type.json", {"types": [
        {"id": "test.thing", "name": "Thing", "conforms_to": ["public.text"], "implemented": True}]})
    write_json(root / "lenses" / "viewer" / "lens.json", {"types": ["public.text"]})
    monkeypatch.setenv("DARQ_ROOT", str(root))

    client_path = write_app(tmp_path / "client", """
        import time
        import darq
        from darq.protocols.type import Implementation, Status, Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)

            # Wait for the sync to register the thing.
            deadline = time.monotonic() + 20
            while types.describe(type_id="test.thing").status != Status.OK:
                if time.monotonic() > deadline:
                    return "test.thing never registered"
                time.sleep(0.05)

            if types.describe(type_id="public.png").description.name != "PNG Image":
                return "base catalogue not registered"
            lenses = types.find_lenses(type_id="Test.Thing", context="terminal")
            if lenses.status != Status.OK or not lenses.lenses[0].endswith("viewer"):
                return f"find_lenses: {lenses!r}"

            first = types.activate(type_id="test.thing")
            if first.status != Status.OK or first.implementation is None:
                return f"activate: {first!r}"
            if types.activate(type_id="test.thing").implementation != first.implementation:
                return "second activate gave another port"

            thing = Implementation.Client(first.implementation, timeout=10)
            if thing.create(type_id="test.thing").item_id != "test.thing:1":
                return "create failed"
            thing.destroy(item_id="test.thing:1")

            # Once the Type service hears it's gone, it starts another.
            while True:
                again = types.activate(type_id="test.thing")
                if again.status != Status.OK:
                    return f"reactivate: {again!r}"
                if again.implementation != first.implementation:
                    break
                if time.monotonic() > deadline:
                    return "never restarted"
                time.sleep(0.05)
            if Implementation.Client(again.implementation, timeout=10).create(type_id="test.thing").status != Status.OK:
                return "restarted implementation didn't answer"
            return 0
        """)

    # The boot starts Storage, the Type service, and the sync.
    kernel.do_boot()
    sync = next(p for p in kernel._process_table.values() if p._image_path == str(SYNC_PATH))
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie and sync.get_status() == zombie,
                     "the client and sync to exit")
    assert (sync.get_exit_status(), sync.get_exit_error()) == (0, None)
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)

    # The catalogue was saved, under a key in the boot vector.
    assert len(kernel._boot_vector.get(service_module.CATALOGUE_KEY)) == 16
    assert implementation == str(root / "types" / "thing")
