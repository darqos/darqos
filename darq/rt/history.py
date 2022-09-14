# darqos
# Copyright (C) 2020-2022 David Arnold

import enum

from datetime import datetime

from darq.os.service import ServiceAPI

@enum.unique
class Event(enum.Enum):
    """Events recorded by the history service."""

    # Object was created.
    CREATED = "created"

    # Object was read, but not modified.
    READ = "read"

    # Object was altered.
    MODIFIED = "modified"

    # Object was printed (?)
    PRINTED = "printed"


class History(ServiceAPI):
    """Interface to the History Service."""

    @staticmethod
    def api() -> "History":
        return History()

    def __init__(self):
        super().__init__(11002)
        return

    def add_event(self, subject: str, event: Event):
        """Record an event."""

        request = {"method": "add_event",
                   "xid": "xxx",
                   "timestamp": datetime.utcnow(),
                   "subject": subject,
                   "event": event}
        reply = self.rpc(request)
        return reply["result"]

    def get_events_for_period(self, start_time: datetime, end_time: datetime):
        """Get list of events within a time range."""

        request = {"method": "get_events_for_period",
                   "xid": "xxx",
                   "start_time": start_time,
                   "end_time": end_time}
        reply = self.rpc(request)
        return reply["events"]

    def get_events(self, start_time: datetime, count: int, older: bool):
        """Get events from a starting time.

        :param start_time: Starting time for events.
        :param count: Number of events to fetch.
        :param older: Get events older than start_time if True, else younger.

        This request is intended for pagination of large requests."""

        request = {"method": "get_events",
                   "xid": "xxx",
                   "start_time": start_time,
                   "count": count,
                   "older": older}
        reply = self.rpc(request)
        return reply["events"]


def test():
    api = History.api()

    start_time = datetime.utcnow()

    api.add_event("foo", Event.CREATED)
    api.add_event("foo", Event.MODIFIED)
    api.add_event("foo", Event.READ)

    later_time = datetime.utcnow()

    api.add_event("foo", Event.READ)
    api.add_event("foo", Event.PRINTED)

    all = api.get_events(start_time, 100, False)
    old = api.get_events(later_time, 100, True)
    new = api.get_events(later_time, 100, False)

    assert len(all) == 5
    assert len(old) == 3
    assert len(new) == 2
    return


if __name__ == "__main__":
    test()
