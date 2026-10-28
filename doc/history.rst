History Service
===============

The history service records *events*, their subject *items*, and orders
the records by time.

Each record consists of:

* datetime timestamp
  * In UTC.
  * Must have at least microsecond precision.

* Item identifier for the subject item
* An event identifier
  
  * A UTF-8 string
  * NFKC_CaseFold normalized to lower case
  * At most, 31 bytes long (allowing the 32nd byte to be a trailing U+0000)
  * No whitespace or control characters
  * Predefined values:

    * created
    * opened
    * edited
    * destroyed

  * These events seem to be mostly verbs, representing some action
    that has occurred, so that's probably a decent guideline for
    choosing other actions.

* A string name for the source of the event

  * A UTF-8 string
  * NFKC_CaseFold normalized to lower case
  * At most, 31 bytes long (allowing the 32nd byte to be a trailing U+0000)
  * No whitespace or control characters
  * Examples might be "lens" or "indexer".  Some way to filter events
    to a specific component.

This volume would become overwhelming fairly quickly, so the records are
compressed as they become older:

* For the last hour, all events are maintained
* For the previous 24 hours, events are coalesced to 10 minute intervals.
* For the previous week, all events are coalesced to hourly intervals
* For the previous four weeks, events are coalesced to daily intervals
* For periods over 36 days old, events are coalesced to weekly intervals


API
---

 * ``add_event(item_id, event, source)``

   * The timestamp is recorded within the History service.
   * The `source` is a string that describes what instigated the
     event.  Possible values are like "type_lens" or "indexer".

 * ``get_events(start_time, end_time, type_ids=[], item_ids=[], events=[], sources=[], max=0) -> [events]``

   * Return all events within the time bounds that match the provided
     parameters.
   * If one or more type_ids are specified, only return events
     matching those types.
     
     * It'd be *ideal* if this supported the UTI subtyping, but not
       for early editions.

   * If one or more item_ids are specified, only return events for
     those items.
   * If one or more events are specified, only return those types of
     events.
   * If one or more sources are specified, only return events that
     match one of those sources.
   * All parameters are ANDed together: if more than one is set, event
     records must match each parameter set.
   * max is the maximum number of events to return.  If there's more,
     the time bounds can be used to get the next batch.


Implementation
--------------

The service, `services/history`, is started at boot, and registers
`darq.history`.  It serves the `History` protocol
(`lib/didl/history.didl`), whose `AddEvent` and `GetEvents` are the
API above.

Recording
~~~~~~~~~

Events are recorded by the History service's clients: lenses, and in
future, other things that use items, like the indexer.  Each says what
it is with the event's source.  Type implementations don't record
events: they can't tell who caused one.

`AddEvent` is one-way, so recording an event never waits for the
service.  An invalid event is logged, and dropped.  The predefined
event names are in `darq.history`, but the service gives event and
source names no meaning: it just checks and normalizes them, with
`darq.canonical_name()`.

The service sets an event's time, in UTC microseconds since the epoch.
Times are unique: an event recorded in the same microsecond as the
latest, or after the clock has gone back, is given the next
microsecond.  So a time identifies an event, and paging by time bounds
never skips or repeats one.

Each event has a repeat count.  If the same event, about the same item,
from the same source, was recorded in the last 10 minutes, its count
is incremented, rather than recording it again: a lens can record each
edit, or each save, without flooding the history.  The time is that of
the first.

Queries
~~~~~~~

`GetEvents` returns events from `start`, up to but not including `end`
(0 for now), newest first.  It returns at most `max` events, and never
more than 1000, which is also the limit when `max` is 0.  The next
batch is fetched with `end` set to the time of the oldest returned.  Filters are normalized like the
values they match, and type identifiers match exactly, not by
conformance.

Storage
~~~~~~~

Events are kept in a SQLite database, `history.sqlite`, in the run
directory, rather than in the Storage service: SQLite needs files it
can read and write in pages, and Storage doesn't yet have the features
an SQLite VFS would need (see the TODO list).  The table has a column
for each field, and the item's type identifier, taken from its item
identifier, for filtering.  The database uses SQLite's write-ahead
log, and isn't synced on each event: a crash can lose the last few.

Not Yet
~~~~~~~

* Implement the coalescing strategy described above.  It could run as
  a daily background task.  With the 10 minute throttle, 1ed's volumes
  are small.
* Filtering by type conformance, rather than exact type identifiers.
* A query for the most recently used items, each once, for the
  Terminal's selector.  Its order would want the latest time of a
  repeated event, not just the first.
* Telling clients of new events, for an Events HUD.
* Forgetting an item's history.  A destroyed item's history is kept.
* Any process can record any event, about any item, from any source.
