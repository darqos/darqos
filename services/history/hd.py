#! /usr/bin/env python

# Dump history list (exercise API via CLI).

from datetime import datetime
from darq.rt.history import History


def main():

    api = History.api()
    now = datetime.utcnow()
    events = api.get_events(now, 100, True)

    for event in events:
        print(f"{event[0]}  {event[1]}  {event[2]}")

    return


if __name__ == "__main__":
    main()