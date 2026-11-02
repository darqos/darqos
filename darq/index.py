# darqos
# Copyright (C) 2026 David Arnold

"""Clients of the Index service, darq.index (see doc/search-service.rst).

Type implementations publish their items with a Publisher, each time
they save one: it's best effort, so an implementation works on without
the Index, which can be rebuilt (see tools/reindex).  Anything can
search."""

import logging
import typing

from .errors import DarqError
from .protocols.index import Field, FieldKind, Index, Result, Status
from .system import lookup_name

# The service's registered name.
NAME = "darq.index"


class Publisher:
    """Publishes items to the Index, if it's running.

    Publish and Withdraw are one-way, so nothing waits for the Index: a
    failure is logged, never raised, and the Index looked up again next
    time."""

    def __init__(self):
        self.client: Index.Client | None = None

    def connect(self) -> Index.Client | None:
        """Return a client of the Index, or None if it isn't running."""
        if self.client is None:
            handle = lookup_name(NAME, blocking=False)
            if handle is not None:
                self.client = Index.Client(handle)
        return self.client

    def publish(self, item_id: str, names: typing.Iterable[str] = (), body: str = ""):
        """Replace what's indexed for an item: its names, such as labels
        and aliases, and its body."""
        fields = [Field(FieldKind.NAMES, name) for name in names if name]
        if body:
            fields.append(Field(FieldKind.BODY, body))
        self.send("publish", item_id, lambda client: client.publish(item_id=item_id, fields=fields))

    def withdraw(self, item_id: str):
        """Forget an item."""
        self.send("withdraw", item_id, lambda client: client.withdraw(item_id=item_id))

    def send(self, what: str, item_id: str, message: typing.Callable[[Index.Client], None]):
        try:
            client = self.connect()
            if client is not None:
                message(client)
        except DarqError as e:
            logging.warning(f"Can't {what} {item_id} to the Index: {e!r}")
            self.client = None


def search(text: str,
           type_ids: typing.Iterable[str] = (),
           max: int = 0,
           offset: int = 0,
           timeout: float | None = 10) -> list[Result]:
    """Return the items found by the words of some text, best first.

    :raises DarqError: if the Index isn't running, or doesn't answer.
    :raises ValueError: if a type identifier isn't valid."""
    handle = lookup_name(NAME, blocking=False)
    if handle is None:
        raise DarqError("the Index isn't running")
    response = Index.Client(handle, timeout).search(text=text, type_ids=list(type_ids), max=max, offset=offset)
    if response.status != Status.OK:
        raise ValueError(f"search: {response.status.name.lower().replace('_', ' ')}")
    return response.results
