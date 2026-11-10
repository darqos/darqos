History Service
===============

The history service records *events*, their subject *items*, and orders
the records by time.

It's for the operator: a timeline, as another way of finding an item,
beside searching for it with the Index, and browsing to it through the
Knowledge Base.  There's only ever one operator, so it doesn't say who:
it says when, and what happened, and, through its source and note, how.

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
    * imported (created from data, such as a file)
    * fetched (a URL's item, each time it's fetched from the server,
      whether or not it's changed)
    * opened (no longer to be recorded: see Who Records Events, below)
    * edited
    * destroyed
    * merged (an entity, into another, or another into it)
    * viewed and delivered (designed, not built: see below)

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
compressed as they become older (deferred: it may be that this is only
how the History panel shows older events, and every event is kept):

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
events: they can't tell who caused one.  That's to change: see Who
Records Events, below.

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

Who Records Events
------------------

*A design for Second Edition: not built yet.*

Recording events in the type implementations, and recording them in
the programs nearer the operator, each have something the other lacks:

* A type implementation is a single point, always involved: every
  item's created, changed and destroyed through it, whoever asks, so
  nothing's missed, and every event's described in the same way.  But
  it doesn't know why: that a file was imported from the operator's
  Downloads, or a message delivered by Slack.
* The programs nearer the operator, a lens, the Terminal, an importer,
  know why, and so can say what happened in the operator's terms, which
  is what makes the timeline useful for finding things again.  But
  there are many of them, so recording's implemented in many places,
  and what's recorded, and how it's described, drifts.

So the two are split: *the type implementation records every event,
and its caller describes it*.

Events
~~~~~~

The type implementation records these, from a fixed set:

=============  ========================================================
Event          When
=============  ========================================================
``created``    An item's made, by the operator, or a program.
``imported``   An item's made from data from outside: a file.
``fetched``    A URL's item is fetched from its server.
``delivered``  An item arrives from outside on its own: a message, or
               an email.
``edited``     An item's content, or its entity, is changed and saved:
               each save is recorded, and counted together if it's
               within 10 minutes, so an hour's writing is about six.
``viewed``     The operator's looking at an item, in a lens (see
               Viewing, below).
``destroyed``  An item's destroyed.
``merged``     An entity's merged into another: recorded on both.
=============  ========================================================

``opened`` isn't recorded any more: an item's opened far more often
than the operator looks at it, by the book card, drawing a page from a
dozen entities, by tools, and by the type implementations themselves.
What the operator cares about is reading and editing.  Events recorded
as ``opened`` are kept, and shown.

Context
~~~~~~~

A caller describes what it's asking for with a *context*, passed with
the request that causes the event: ``Create``, ``CreateFrom`` and
``Destroy``, in the ``Implementation`` protocol, and the writes of the
``EntityItem`` and ``EntityWriter`` protocols.  It has:

* the event, one of those above, such as ``imported``, when it's not
  the one the request would otherwise record;
* the source, what it came through, such as ``terminal``,
  ``slack-import`` or ``http``: with one operator, that says where from,
  not who; and
* a note, for the operator: a line of text, such as the file's path, the
  URL, or the conversation, *#general, Acme Slack*.

The type implementation records one event, with its caller's context.
A caller doesn't record events itself, so it can't record them
differently: it describes them, through a parameter it fills in once.
Without a context, the type implementation records the event the
request would, ``created``, ``edited`` or ``destroyed``, with itself as
its source.

Events gain the note, as a field: free text, of at most a few hundred
bytes, not normalized, and not part of what makes an event the same as
another, to be counted with it.  An event that's counted keeps its
latest note: for ``viewed``, where the operator had got to.  The
History panel shows it.

Viewing
~~~~~~~

The ``Item`` protocol, which every type's item protocol starts with,
gains ``Viewed(note)``, beside ``Close``, as does ``EntityItem``: so
every item, of every type, and its entity, has it.  A lens calls it
when it shows the operator an item, and again while they go on using
it: when they turn a page, or every few minutes while it has the focus.
The type implementation records it as ``viewed``, with the lens as its
source, and the note the lens gives, such as *page 45 of 296*, or
*chapter 3*: counted within 10 minutes, so an evening with a book is a
few events, with where the reader had got to.

So only what's been put in front of the operator is recorded: the book
card opening entities to draw its page, a tool sweeping thousands of
items, and type implementations reading their own items, never call it.

Many at Once
~~~~~~~~~~~~

An import that makes many items, such as 66,000 messages from a Slack
workspace, would record an event for each, all different items, so
not counted together.  So a context can also say not to record, and a
program that's made many items records one event of its own, for the
whole: ``delivered``, on each conversation, with a note of how many
messages.  It's the one exception to callers not recording, and is
deliberate, for the operator's sake.

What Changes
~~~~~~~~~~~~

* History's protocol: events gain the note, and ``GetEvents`` returns
  it.
* The type protocols: ``Implementation``'s ``Create``, ``CreateFrom``
  and ``Destroy``, and ``EntityItem``'s and ``EntityWriter``'s writes,
  take a context; ``Item`` and ``EntityItem`` gain ``Viewed``.
* The type implementations record events, through
  ``darq.entity_type``'s ``EntityType``, which every implementation
  inherits (see :doc:`entity-vs-item`), so it's written once.
* The launcher, the lenses, ``darq.ui.book_store``, the importers, and
  the HTTP service stop calling ``AddEvent``, but for an import's
  summary, and pass a context instead; the lenses call ``Viewed``.
* The History panel shows notes.

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
