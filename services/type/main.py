# darqos
# Copyright (C) 2022-2026 David Arnold

# Type service: the catalogue of types and lenses, and the activator of
# type implementations, registered as darq.type.
#
# It serves the Type protocol (lib/didl/type.didl), as described in
# doc/types.rst.  It keeps three tables:
# - Descriptions, and implementations (directory paths to spawn), by
#   type identifier, and lenses by type and context.  These are the
#   catalogue: saved, as JSON, in a Storage blob whose key is in the
#   boot vector, so the p-kernel must spawn the service.
# - The ports of running implementations, by directory.  These last
#   only as long as the implementations do.  A directory can implement
#   several types: one process serves them all.
#
# An implementation is started by Activate: it's spawned with a
# bootstrap port, and reports its port there with Ready.  Activates
# wait (deferred) until then.  When an implementation's port dies, its
# entry is removed, and the next Activate starts it again.

import json
import logging
import typing

import darq
from darq.protocols import storage
from darq.protocols.type import (
    ActivateRequest,
    ActivateResponse,
    Bootstrap,
    DeregisterLensRequest,
    DeregisterLensResponse,
    DeregisterRequest,
    DeregisterResponse,
    DescribeRequest,
    DescribeResponse,
    Description,
    FindLensesRequest,
    FindLensesResponse,
    ListTypesRequest,
    ListTypesResponse,
    Ready,
    RegisterLensRequest,
    RegisterLensResponse,
    RegisterRequest,
    RegisterResponse,
    Status,
    Type,
)


# Boot vector name holding the catalogue blob's key.
CATALOGUE_KEY = "darq.type.catalogue"

# Seconds an implementation has to report Ready, once spawned.
ACTIVATION_TIMEOUT = 30.0

# Version of the catalogue's JSON format.
CATALOGUE_VERSION = 1

# Description fields, as saved in the catalogue.
DESCRIPTION_FIELDS = ("name", "description", "conforms_to", "mime_types", "extensions", "icon")


class Catalogue(typing.Protocol):
    """Where the catalogue is saved."""

    def load(self) -> dict | None:
        """Return the saved catalogue, or None if there isn't one."""
        ...

    def save(self, data: dict):
        """Save the catalogue, replacing what was saved."""
        ...


class StorageCatalogue:
    """The catalogue, saved in a Storage blob whose key is in the boot vector."""

    def __init__(self, client: storage.Storage.Client):
        self.client = client
        self.key: storage.Key | None = None

    def load(self) -> dict | None:
        value = darq.get_boot_value(CATALOGUE_KEY)
        if value is None:
            return None
        if len(value) != 16:
            raise ValueError(f"boot value {CATALOGUE_KEY} is {len(value)} bytes, not 16")
        self.key = storage.Key(int.from_bytes(value[:8], "big"), int.from_bytes(value[8:], "big"))

        response = self.client.get(key=self.key)
        if response.status != storage.Status.OK:
            raise RuntimeError(f"catalogue blob: {response.status.name}")
        return json.loads(response.value)

    def save(self, data: dict):
        value = json.dumps(data, sort_keys=True).encode("utf-8")
        if self.key is None:
            created = self.client.create(value=value)
            if created.status != storage.Status.OK:
                raise RuntimeError(f"can't create catalogue blob: {created.status.name}")
            self.key = created.key
            darq.set_boot_value(CATALOGUE_KEY, self.key.high.to_bytes(8, "big") + self.key.low.to_bytes(8, "big"))
            return

        # This service is the blob's only writer, so no generation check.
        response = self.client.put(key=self.key, expected=0, value=value)
        if response.status != storage.Status.OK:
            raise RuntimeError(f"can't save catalogue blob: {response.status.name}")
        return


class Starting:
    """An implementation that has been spawned, but hasn't reported Ready."""

    def __init__(self, bootstrap: int, timer: darq.Timer):
        # Port it reports Ready on.
        self.bootstrap = bootstrap

        # Activates waiting for it.
        self.waiting: list[darq.Deferred] = []

        # Fails the activation if it's not Ready in time.
        self.timer = timer


def description_to_dict(description: Description) -> dict:
    return {name: getattr(description, name) for name in DESCRIPTION_FIELDS}


def description_from_dict(d: dict) -> Description:
    return Description(**{name: d[name] for name in DESCRIPTION_FIELDS if name in d})


class TypeService(Type.Handler):
    """Answers the Type protocol, on the service's port."""

    def __init__(self, catalogue: Catalogue):
        """Constructor.

        :param catalogue: Where the catalogue is loaded from, and saved."""
        self.catalogue = catalogue

        # The catalogue.
        self.descriptions: dict[str, Description] = {}
        self.implementations: dict[str, str] = {}
        self.lenses: list[tuple[str, str, str]] = []

        # Implementations' ports, and those starting, by directory.
        self.running: dict[str, int] = {}
        self.starting: dict[str, Starting] = {}

        # The service's port, which notifications come to (see add()).
        self.port: int | None = None

        data = catalogue.load()
        if data is not None:
            self.load(data)
        return

    def add(self, server: darq.Server, port: int):
        """Answer requests on a port, with a server."""
        self.port = port
        server.add(port, self)

    # Catalogue

    def load(self, data: dict):
        """Set the catalogue from its saved form."""
        if data.get("version") != CATALOGUE_VERSION:
            raise ValueError(f"catalogue version {data.get('version')}, expected {CATALOGUE_VERSION}")
        self.descriptions = {t: description_from_dict(d) for t, d in data["types"].items()}
        self.implementations = dict(data["implementations"])
        self.lenses = [(lens, t, context) for lens, t, context in data["lenses"]]
        logging.info(f"Loaded {len(self.descriptions)} types, {len(self.implementations)} implementations, "
                     f"and {len(self.lenses)} lenses")

    def save(self):
        """Save the catalogue."""
        self.catalogue.save({
            "version": CATALOGUE_VERSION,
            "types": {t: description_to_dict(d) for t, d in self.descriptions.items()},
            "implementations": self.implementations,
            "lenses": [list(entry) for entry in self.lenses],
        })

    @staticmethod
    def canonical(type_id: str) -> str | None:
        """Return a type identifier's canonical form, or None if it's invalid."""
        try:
            return darq.canonical_type_id(type_id)
        except darq.InvalidIdentifierError:
            return None

    def conforming(self, type_id: str) -> typing.Iterator[list[str]]:
        """Yield a type, and then the types it conforms to, nearest first.

        Each is yielded once, as a list of the types at that distance."""
        seen = {type_id}
        level = [type_id]
        while level:
            yield level
            following = []
            for t in level:
                description = self.descriptions.get(t)
                for general in description.conforms_to if description else ():
                    if general not in seen:
                        seen.add(general)
                        following.append(general)
            level = following

    # Requests

    def register(self, request: RegisterRequest, source: int) -> RegisterResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return RegisterResponse(status=Status.INVALID_ID)

        description = request.description
        conforms_to = [self.canonical(t) for t in description.conforms_to]
        if None in conforms_to:
            return RegisterResponse(status=Status.INVALID_ID)
        description.conforms_to = list(dict.fromkeys(typing.cast(list[str], conforms_to)))

        # The sync registers everything at each boot: most is unchanged.
        if (self.descriptions.get(type_id) == description and
                self.implementations.get(type_id, "") == request.implementation):
            return RegisterResponse(status=Status.OK)

        self.descriptions[type_id] = description
        if request.implementation:
            self.implementations[type_id] = request.implementation
        else:
            self.implementations.pop(type_id, None)
        self.save()
        logging.info(f"Registered {type_id}" +
                     (f", implemented by {request.implementation}" if request.implementation else ""))
        return RegisterResponse(status=Status.OK)

    def deregister(self, request: DeregisterRequest, source: int) -> DeregisterResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return DeregisterResponse(status=Status.INVALID_ID)
        if type_id not in self.descriptions:
            return DeregisterResponse(status=Status.NOT_FOUND)

        del self.descriptions[type_id]
        self.implementations.pop(type_id, None)
        self.save()
        logging.info(f"Deregistered {type_id}")
        return DeregisterResponse(status=Status.OK)

    def describe(self, request: DescribeRequest, source: int) -> DescribeResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return DescribeResponse(status=Status.INVALID_ID)
        description = self.descriptions.get(type_id)
        if description is None:
            return DescribeResponse(status=Status.NOT_FOUND)
        return DescribeResponse(description=description, implementation=self.implementations.get(type_id, ""),
                                status=Status.OK)

    def list_types(self, request: ListTypesRequest, source: int) -> ListTypesResponse:
        types = self.implementations if request.implemented else self.descriptions
        return ListTypesResponse(type_ids=sorted(types))

    def register_lens(self, request: RegisterLensRequest, source: int) -> RegisterLensResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return RegisterLensResponse(status=Status.INVALID_ID)

        entry = (request.lens, type_id, request.context)
        if entry not in self.lenses:
            self.lenses.append(entry)
            self.save()
            logging.info(f"Registered lens {request.lens} for {type_id} in {request.context}")
        return RegisterLensResponse(status=Status.OK)

    def deregister_lens(self, request: DeregisterLensRequest, source: int) -> DeregisterLensResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return DeregisterLensResponse(status=Status.INVALID_ID)

        entry = (request.lens, type_id, request.context)
        if entry not in self.lenses:
            return DeregisterLensResponse(status=Status.NOT_FOUND)
        self.lenses.remove(entry)
        self.save()
        return DeregisterLensResponse(status=Status.OK)

    def find_lenses(self, request: FindLensesRequest, source: int) -> FindLensesResponse:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return FindLensesResponse(status=Status.INVALID_ID)

        for level in self.conforming(type_id):
            lenses = [lens for lens, t, context in self.lenses if t in level and context == request.context]
            if lenses:
                return FindLensesResponse(lenses=list(dict.fromkeys(lenses)), status=Status.OK)
        return FindLensesResponse(status=Status.NOT_FOUND)

    def activate(self, request: ActivateRequest, source: int) -> ActivateResponse | darq.Deferred:
        type_id = self.canonical(request.type_id)
        if type_id is None:
            return ActivateResponse(status=Status.INVALID_ID)

        path = self.implementations.get(type_id)
        if path is None:
            return ActivateResponse(status=Status.NOT_FOUND)

        port = self.running.get(path)
        if port is not None:
            return ActivateResponse(implementation=port, status=Status.OK)

        starting = self.starting.get(path)
        if starting is None:
            starting = self.start(path)
            if starting is None:
                return ActivateResponse(status=Status.FAILED)

        pending = self.defer()
        starting.waiting.append(pending)
        return pending

    # Activation

    def start(self, path: str) -> Starting | None:
        """Spawn an implementation, with a bootstrap port for its Ready.

        :returns: Its Starting entry, or None if it couldn't be spawned."""
        assert self.server is not None, "TypeService isn't being served"
        bootstrap = darq.open_port()
        try:
            dpid = darq.spawn_process(path, bootstrap=bootstrap)
        except darq.SpawnError as e:
            logging.error(f"Can't start implementation {path}: {e}")
            darq.close_port(bootstrap)
            return None

        logging.info(f"Started implementation {path}, dpid {dpid}")
        timer = self.server.call_later(ACTIVATION_TIMEOUT, lambda: self.not_ready(path))
        starting = Starting(bootstrap, timer)
        self.starting[path] = starting
        self.server.add(bootstrap, ReadyHandler(self, path))
        return starting

    def finish_start(self, path: str, port: int | None):
        """Answer the Activates waiting for an implementation, and stop
        waiting for its Ready.

        :param port: Its port, or None if it failed."""
        starting = self.starting.pop(path)
        starting.timer.cancel()
        assert self.server is not None
        self.server.remove(starting.bootstrap)
        darq.close_port(starting.bootstrap)

        if port is None:
            response = ActivateResponse(status=Status.FAILED)
        else:
            response = ActivateResponse(implementation=port, status=Status.OK)
        for pending in starting.waiting:
            pending.reply(response)

    def ready(self, path: str, port: int | None):
        """Record a started implementation's port, from its Ready."""
        if port is None:
            logging.error(f"Implementation {path} reported Ready without a port")
            self.finish_start(path, None)
            return

        # Its port dies with it: then it's removed (see port_destroyed).
        assert self.port is not None, "TypeService has no port for notifications"
        darq.notify_on_death(port, self.port)
        self.running[path] = port
        logging.info(f"Implementation {path} is ready, on port {port}")
        self.finish_start(path, port)

    def not_ready(self, path: str):
        """Fail an activation whose implementation didn't report Ready in time.

        It isn't killed: its Ready would now fail, since the bootstrap
        port is closed, and it should exit."""
        logging.error(f"Implementation {path} wasn't ready within {ACTIVATION_TIMEOUT}s")
        self.finish_start(path, None)

    def port_destroyed(self, handle: int):
        for path, port in list(self.running.items()):
            if port == handle:
                logging.warning(f"Implementation {path} has gone (port {handle})")
                del self.running[path]
        darq.close_port(handle)


class ReadyHandler(Bootstrap.Handler):
    """Answers an implementation's bootstrap port, while it starts."""

    def __init__(self, service: TypeService, path: str):
        self.service = service
        self.path = path

    def ready(self, request: Ready, source: int):
        self.service.ready(self.path, request.implementation)


def darq_main():
    catalogue = StorageCatalogue(storage.Storage.Client(darq.connect("darq.storage"), timeout=10))
    service = TypeService(catalogue)

    port = darq.open_port()
    darq.register_name("darq.type", port)
    server = darq.Server()
    service.add(server, port)
    logging.info(f"Serving darq.type on port {port}")
    server.run()

