#

from darq.os.base import Service
from darq.rt.history import Events

from datetime import datetime
import sqlite3
import sys


class HistoryService(Service):

    def __init__(self, file: str = None):
        super().__init__("tcp://*:11002")

        if file is None:
            file = "history.sqlite"

        self.db = sqlite3.connect(file)
        cursor = self.db.cursor()
        cursor.execute("create table if not exists history ("
                       "timestamp datetime not null,"
                       "subject text not null,"
                       "event text not null)")
        self.db.commit()

        self.context = None
        self.socket = None
        self.active = False
        return

    def add_event(self, timestamp: datetime, subject: str, event: Events):
        """Record an event."""
        cursor = self.db.cursor()
        cursor.execute("insert into history values (?, ?, ?)",
                       (timestamp, subject, event))
        self.db.commit()
        return

    def get_events_for_period(self, start_time: datetime, end_time: datetime):
        cursor = self.db.cursor()
        cursor.execute("select timestamp, subject, event "
                       "from history "
                       "where timestamp >= ? "
                       "  and timestamp < ?",
                       (start_time, end_time))
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def handle_request(self, request: dict):
        """Handle requests."""

        method = request.get("method")
        if method == "add_event":
            self.add_event(request["timestamp"],
                           request["subject"],
                           request["event"])
            self.send_reply(request, result=True)
            return

        elif method == "get_events_for_period":
            rows = self.get_events_for_period(request["start_time"],
                                              request["end_time"])
            self.send_reply(request, result=True, events=rows)

        else:
            super().handle_request(request)
        return


if __name__ == "__main__":
    service = HistoryService()
    result = service.run()
    sys.exit(result)