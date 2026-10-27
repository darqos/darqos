# darqos
# Copyright (C) 2020-2026 David Arnold

# History service: a record of events about items, by time, registered
# as darq.history.
#
# It serves the History protocol (lib/didl/history.didl), as described
# in doc/history.rst.  Events are kept in a SQLite database in the run
# directory.  Clients, such as lenses, record events: the service gives
# event and source names no meaning, beyond their form.

import logging
import os
import sqlite3
import time
import typing

import darq
from darq.protocols.history import (
    AddEvent,
    Event,
    GetEventsRequest,
    GetEventsResponse,
    History,
    Status,
)


# Database file, in the run directory.
DATABASE = "history.sqlite"

# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"

# Microseconds after an event is recorded that the same event, about the
# same item, from the same source, is counted rather than recorded again.
THROTTLE = 10 * 60 * 1_000_000

# Most events GetEvents returns, and when it's asked for no limit.
MAX_EVENTS = 1000


def now() -> int:
    """Return the time, in UTC microseconds since the epoch."""
    return time.time_ns() // 1000


class HistoryService(History.Handler):
    """A record of events about items, ordered by time.

    Each event's time is set by the service when it's recorded, and is
    unique: if the clock hasn't moved on, or has gone back, it's a
    microsecond after the latest.  So a time identifies an event, and
    can be used to page through them."""

    def __init__(self, path: str, clock: typing.Callable[[], int] = now):
        """Constructor.

        :param path: Database file, created if it doesn't exist.
        :param clock: Returns the time, in UTC microseconds."""
        self.clock = clock
        self.db = sqlite3.connect(path)

        # Losing the last few events in a crash is acceptable: syncing
        # each one isn't worth it.
        self.db.execute("pragma journal_mode = wal")
        self.db.execute("pragma synchronous = normal")
        with self.db:
            self.db.execute("create table if not exists events ("
                            "time integer not null primary key, "
                            "type_id text not null, "
                            "item_id text not null, "
                            "event text not null, "
                            "source text not null, "
                            "count integer not null)")
            self.db.execute("create index if not exists events_by_item "
                            "on events (item_id, event, source, time)")
        logging.info(f"History database {path}")
        return

    def add_event(self, request: AddEvent, source: int) -> None:
        try:
            type_id, specifier = darq.split_item_id(request.item_id)
            event = darq.canonical_name(request.event)
            event_source = darq.canonical_name(request.source)
        except darq.InvalidIdentifierError as e:
            logging.warning(f"Dropped event {request.event!r} from {request.source!r} "
                            f"about {request.item_id!r}: {e}")
            return
        item_id = f"{type_id}:{specifier}"

        when = self.clock()
        with self.db:
            row = self.db.execute("select time from events "
                                  "where item_id = ? and event = ? and source = ? and time > ? "
                                  "order by time desc limit 1",
                                  (item_id, event, event_source, when - THROTTLE)).fetchone()
            if row is not None:
                self.db.execute("update events set count = count + 1 where time = ?", row)
                return

            latest = self.db.execute("select max(time) from events").fetchone()[0]
            if latest is not None and when <= latest:
                when = latest + 1
            self.db.execute("insert into events values (?, ?, ?, ?, ?, 1)",
                            (when, type_id, item_id, event, event_source))
        return

    def get_events(self, request: GetEventsRequest, source: int) -> GetEventsResponse:
        try:
            filters = [
                ("type_id", [darq.canonical_type_id(t) for t in request.type_ids]),
                ("item_id", [darq.canonical_item_id(i) for i in request.item_ids]),
                ("event", [darq.canonical_name(e) for e in request.events]),
                ("source", [darq.canonical_name(s) for s in request.sources]),
            ]
        except darq.InvalidIdentifierError as e:
            logging.debug(f"get_events: {e}")
            return GetEventsResponse(status=Status.INVALID_ARGUMENT)

        where = ["time >= ?"]
        parameters: list[int | str] = [request.start]
        if request.end != 0:
            where.append("time < ?")
            parameters.append(request.end)
        for column, values in filters:
            if values:
                where.append(f"{column} in ({', '.join('?' * len(values))})")
                parameters.extend(values)
        limit = MAX_EVENTS if request.max == 0 else min(request.max, MAX_EVENTS)

        rows = self.db.execute("select time, item_id, event, source, count from events "
                               f"where {' and '.join(where)} order by time desc limit {limit}",
                               parameters).fetchall()
        return GetEventsResponse(events=[Event(*row) for row in rows], status=Status.OK)


def darq_main():
    run_dir = os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    darq.serve("darq.history", HistoryService(os.path.join(run_dir, DATABASE)))
