# darqos
# Copyright (C) 2026 David Arnold

# Knowledge Base service: a graph of facts about people, organisations,
# places, and other things, registered as darq.kb.
#
# It serves the KB protocol (lib/didl/kb.didl), as described in
# doc/kb-service.rst.  Entities are kept by a KnowledgeBase (kb.py), in
# a SQLite database in the run directory (store.py).  The service is
# the database's only writer while it runs, so what it's read stays
# current.  It starts with whatever's in the database: type
# implementations find, or create, the entities they need.

import logging
import os
import typing

import darq
import darq.kb as model
from darq.protocols.kb import (
    KB,
    Changed,
    DestroyRequest,
    DestroyResponse,
    FindByLabelRequest,
    FindByLabelResponse,
    FindByValueRequest,
    FindByValueResponse,
    FindLinksRequest,
    FindLinksResponse,
    GetEntitiesRequest,
    GetEntitiesResponse,
    InstancesRequest,
    InstancesResponse,
    Label,
    Match,
    MergeRequest,
    MergeResponse,
    PutEntitiesRequest,
    PutEntitiesResponse,
    Status,
    UnwatchRequest,
    UnwatchResponse,
    WatchRequest,
    WatchResponse,
)

from kb import KnowledgeBase
from store import Store


# Database file, in the run directory.
DATABASE = "kb.sqlite"

# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"

# Most identifiers a lookup returns, and when it's asked for no limit.
MAX_IDS = 1000


class Failed(Exception):
    """A request failed, for an entity."""

    def __init__(self, status: Status, entity_id: str = ""):
        super().__init__(status.name)
        self.status = status
        self.entity_id = entity_id


def page(ids: typing.Iterable[str], max_ids: int, after: str) -> list[str]:
    """Return a batch of sorted identifiers: at most max_ids (0 for
    MAX_IDS), after the identifier after."""
    limit = MAX_IDS if max_ids == 0 else min(max_ids, MAX_IDS)
    return [i for i in sorted(ids) if i > after][:limit]


class KBService(KB.Handler):
    """Entities, and changes to them, for clients.

    Each change is a transaction: it's committed, or rolled back, before
    it's answered, and watchers are told of it once it's committed."""

    def __init__(self, path: str):
        """Constructor.

        :param path: Database file, created if it doesn't exist."""
        self.store = Store(path)
        self.kb = KnowledgeBase(self.store)

        # Watches, by identifier: the listener's port, and the entities
        # watched, or None for all.
        self.watches: dict[int, tuple[int, set[str] | None]] = {}
        self.next_watch = 1

        # The service's port, which notifications come to (see add()).
        self.port: int | None = None
        logging.info(f"Knowledge base {path}")

    def add(self, server: darq.Server, port: int):
        """Answer requests on a port, with a server."""
        self.port = port
        server.add(port, self)

    def close(self):
        self.store.close()

    # Entities

    def get_entities(self, request: GetEntitiesRequest, source: int) -> GetEntitiesResponse:
        if any(not i for i in request.ids):
            return GetEntitiesResponse(status=Status.INVALID_ARGUMENT)
        entities = [e for e in (self.kb.get(i, request.follow) for i in request.ids) if e is not None]

        labels = []
        if request.languages:
            ids = set().union(*(model.referenced(e) for e in entities))
            for entity_id in sorted(ids):
                entity = self.kb.get(entity_id)
                if entity is None:
                    continue
                for language in request.languages:
                    text = entity.get_label(language)
                    if text is not None:
                        labels.append(Label(entity_id, language, text))
                        break
        return GetEntitiesResponse(entities=[model.entity_to_wire(e) for e in entities], labels=labels,
                                   status=Status.OK)

    def put_entities(self, request: PutEntitiesRequest, source: int) -> PutEntitiesResponse:
        try:
            written = self.change(lambda: self.put(request))
        except Failed as e:
            return PutEntitiesResponse(failed_id=e.entity_id, status=e.status)
        return PutEntitiesResponse(entities=[model.entity_to_wire(e) for e in written], status=Status.OK)

    def put(self, request: PutEntitiesRequest) -> list[model.Entity]:
        """Write a request's entities, in the current transaction."""
        entities = []
        for e in request.entities:
            try:
                entity = model.entity_from_wire(e)
            except (KeyError, ValueError):
                raise Failed(Status.INVALID_ARGUMENT, e.id) from None
            entity.redirect, entity.destroyed = None, False
            try:
                if entity.revision == 0:
                    self.kb.register(entity)
                else:
                    self.kb.replace(entity)
            except model.ConflictError:
                raise Failed(Status.CONFLICT, entity.id) from None
            except LookupError:
                raise Failed(Status.NOT_FOUND, entity.id) from None
            except ValueError:
                # Registering an entity that exists.
                status = Status.CONFLICT if entity.revision == 0 else Status.INVALID_ARGUMENT
                raise Failed(status, entity.id) from None
            entities.append(entity)

        # Checked once they're all there, so they can refer to each other.
        for entity in entities:
            for s in entity.statements:
                try:
                    self.kb.check(s)
                except ValueError as error:
                    logging.debug(f"put_entities: {entity.id}: {error}")
                    raise Failed(Status.INVALID_STATEMENT, entity.id) from None
        return entities

    def merge(self, request: MergeRequest, source: int) -> MergeResponse:
        try:
            self.change(lambda: [self.kb.get(request.old_id, follow=False),
                                 self.kb.merge(request.old_id, request.new_id)])
        except Failed as e:
            return MergeResponse(status=e.status)
        return MergeResponse(status=Status.OK)

    def destroy(self, request: DestroyRequest, source: int) -> DestroyResponse:
        def destroy():
            self.kb.destroy(request.entity_id)
            return [self.kb.get(request.entity_id, follow=False)]

        try:
            self.change(destroy)
        except Failed as e:
            return DestroyResponse(status=e.status)
        return DestroyResponse(status=Status.OK)

    def change(self, make: typing.Callable[[], typing.Sequence[model.Entity | None]]) -> list[model.Entity]:
        """Make a change in a transaction, and tell the watchers of the
        entities it returns.

        :raises Failed: if it fails, and is rolled back."""
        try:
            with self.kb.transaction():
                entities = [e for e in make() if e is not None]
        except Failed:
            raise
        except model.ConflictError as e:
            raise Failed(Status.CONFLICT, e.entity_id) from None
        except LookupError:
            raise Failed(Status.NOT_FOUND) from None
        except ValueError:
            raise Failed(Status.INVALID_ARGUMENT) from None
        self.notify(entities)
        return entities

    # Lookups

    def find_by_label(self, request: FindByLabelRequest, source: int) -> FindByLabelResponse:
        if request.match == Match.WORDS:
            found = self.kb.search(request.text, request.language)
        else:
            found = self.kb.find_labelled(request.text, request.language)
        ids = {e.id for e in found}
        if request.class_id:
            ids &= {e.id for e in self.kb.instances(request.class_id)}
        return FindByLabelResponse(ids=page(ids, request.max, request.after), status=Status.OK)

    def find_by_value(self, request: FindByValueRequest, source: int) -> FindByValueResponse:
        found = self.kb.find_by_value(request.property_id, request.value)
        return FindByValueResponse(ids=page((e.id for e in found), request.max, request.after),
                                   status=Status.OK)

    def find_links(self, request: FindLinksRequest, source: int) -> FindLinksResponse:
        found = self.kb.find_links(request.target_id, request.property_id or None,
                                   model.FROM_ROLES[request.role])
        return FindLinksResponse(ids=page((e.id for e in found), request.max, request.after),
                                 status=Status.OK)

    def instances(self, request: InstancesRequest, source: int) -> InstancesResponse:
        found = self.kb.instances(request.class_id)
        return InstancesResponse(ids=page((e.id for e in found), request.max, request.after),
                                 status=Status.OK)

    # Notifications

    def watch(self, request: WatchRequest, source: int) -> WatchResponse:
        if request.listener is None:
            return WatchResponse(status=Status.INVALID_ARGUMENT)

        # Its watches are dropped when its port dies (see port_destroyed).
        assert self.port is not None, "KBService has no port for notifications"
        darq.notify_on_death(request.listener, self.port)
        watch_id = self.next_watch
        self.next_watch += 1
        self.watches[watch_id] = (request.listener, set(request.ids) if request.ids else None)
        return WatchResponse(watch_id=watch_id, status=Status.OK)

    def unwatch(self, request: UnwatchRequest, source: int) -> UnwatchResponse:
        watch = self.watches.pop(request.watch_id, None)
        if watch is None:
            return UnwatchResponse(status=Status.NOT_FOUND)
        self.forget(watch[0])
        return UnwatchResponse(status=Status.OK)

    def notify(self, entities: typing.Iterable[model.Entity]):
        """Tell the watchers of some entities that they've changed."""
        for entity in entities:
            for watch_id, (listener, ids) in list(self.watches.items()):
                if ids is not None and entity.id not in ids:
                    continue
                try:
                    darq.send(listener, Changed(entity_id=entity.id, revision=entity.revision))
                except darq.DarqError as e:
                    logging.warning(f"Dropped watch {watch_id}: {e}")
                    del self.watches[watch_id]
                    self.forget(listener)

    def forget(self, listener: int):
        """Close a listener's port, if it has no other watches."""
        if all(port != listener for port, _ in self.watches.values()):
            darq.close_port(listener)

    def port_destroyed(self, handle: int):
        for watch_id, (listener, _) in list(self.watches.items()):
            if listener == handle:
                logging.info(f"Dropped watch {watch_id}: its listener has gone")
                del self.watches[watch_id]
        darq.close_port(handle)


def darq_main():
    run_dir = os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    service = KBService(os.path.join(run_dir, DATABASE))

    port = darq.open_port()
    darq.register_name("darq.kb", port)
    server = darq.Server()
    service.add(server, port)
    logging.info(f"Serving darq.kb on port {port}")
    server.run()
