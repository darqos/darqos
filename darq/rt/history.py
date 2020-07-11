#

from datetime import datetime
import enum
import orjson
import zmq


@enum.unique
class Events(enum.Enum):
    CREATED = enum.auto()
    VIEWED = enum.auto()
    MODIFIED = enum.auto()
    PRINTED = enum.auto()


class History:
    """Interface to the History Service."""

    @staticmethod
    def api() -> "History":
        return History()

    def __init__(self):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect("tcp://localhost:11002")
        return

    def add_event(self, subject: str, event: Events):

        request = {"method": "add_event",
                   "xid": "xxx",
                   "timestamp": datetime.utcnow(),
                   "subject": subject,
                   "event": event}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        return reply["result"]

    def get_events_for_period(self, start_time: datetime, end_time: datetime):
        """Get list of events within a time range."""

        request = {"method": "get_events_for_period",
                   "xid": "xxx",
                   "start_time": start_time,
                   "end_time": end_time}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        return reply["events"]

