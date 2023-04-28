History Service
===============

The history service records _events_, their subject _objects_, and orders
the records by time.

Each record consists of:

* datetime timestamp, in UTC
* object identifier for the subject object
* event identifier

  * created
  * edited
  * viewed
  * deleted
  * printed
  * etc, etc

    * These events seem to be msotly verbs, representing some action
      that has occurred, so that's probably a decent guideline for
      choosing the available actions.

* maybe some extra text?

  * Not sure this is useful?

This volume would become overwhelming fairly quickly, so the records are
compressed as they become older:

* For the current week, all events are maintained
* For the last month, events within each wall-clock hour are coalesced,
  by removing duplicate event identifiers.  eg. a document saved multiple
  times is recorded only to have been saved within that hour.
* For periods over a month old, events are coalesced into days.

Objects are identified by a URL.  Local objects by their stored object
identifier, remote objects by their eg. HTTPS URL.

API
---

* add_event(timestamp, object_id, event)
* get_events_for_object(object_id) -> \[events\]
* get_events_for_period(start_time, end_time) -> \[events\]

Implementation
--------------

This data probably works ok in a relational database?  The table could
basically reflect the data: timestamp, object, event.

The coalescing should happen as a regular background task, perhaps
running daily?

Should there be a repeat-count on the events?  The compression strategy
would still lose the precision of timestamps, but popularity could still
be determined with very little size overhead?
