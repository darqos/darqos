# darqos
# Copyright (C) 2026 David Arnold

# The predefined History event names (see doc/history.rst), for clients
# recording events with the History protocol (lib/didl/history.didl).
# The service gives them no meaning: any valid name can be recorded.
#
# Type implementations record the events their items' requests cause,
# described by their callers' contexts (see doc/history.rst, Who Records
# Events), with a Recorder.

import logging
import typing

import darq
from darq.protocols.history import History

CREATED = "created"
IMPORTED = "imported"
FETCHED = "fetched"
DELIVERED = "delivered"
OPENED = "opened"
EDITED = "edited"
VIEWED = "viewed"
DESTROYED = "destroyed"
MERGED = "merged"


class Context(typing.Protocol):
    """A caller's context, as the Implementation and KB protocols have
    it (darq.protocols.type.Context and darq.protocols.kb.Context)."""
    event: str
    source: str
    note: str
    quiet: bool


def connect() -> History.Client | None:
    """Return a client of History, or None if it isn't running."""
    handle = darq.lookup_name("darq.history", blocking=False)
    return History.Client(handle) if handle is not None else None


class Recorder:
    """Records events in History, as described by callers' contexts: best
    effort, as an item's changes don't wait for, or fail with, History.

    History's looked up when it's first needed, and again after it's
    failed, or wasn't running."""

    def __init__(self, source: str, connector: typing.Callable[[], typing.Any] | None = None):
        """Constructor.

        :param source: The source of events whose context gives none: the
                       implementation's name.
        :param connector: Returns a History client, or None: connect, as
                          it is when it's called, by default."""
        self.source = source
        self.connector = connector or (lambda: connect())
        self.client: typing.Any = None

    def record(self, item_id: str, event: str, context: Context | None = None, source: str = "", note: str = ""):
        """Record an event about an item: the context's event, if it gives
        one, from its source, or else source, or else the recorder's, with
        its note, or else note.  Nothing's recorded if it's quiet."""
        if context is not None:
            if context.quiet:
                return
            event = context.event or event
            source = context.source or source
            note = context.note or note
        if self.client is None:
            try:
                self.client = self.connector()
            except darq.DarqError as e:
                logging.debug(f"Can't find History: {e!r}")
            if self.client is None:
                return
        try:
            self.client.add_event(item_id=item_id, event=event, source=source or self.source, note=note)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")
            self.client = None
