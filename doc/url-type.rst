URL Type
========

A URL item, of type ``public.url``, stands for a place on the web: it
gives a URL an identity that can be opened, named, linked to, and found.
It's what *Alice's website*, or *where this PDF came from*, refer to.

A URL isn't its content, which can change, differ by who's asking, or
disappear.  So there are three things, kept apart:

* the **URL item**, which is the place;
* a **snapshot**, an ordinary item of its real type, such as a PDF or an
  image, which is what was there at a moment; and
* the **HTTP service** (see :doc:`http-service`), which does the fetching.

No Actions
----------

A URL item has no actions: no ``GET``, or any other method.  "Fetch this,
keep a copy, and note where it came from" is a job someone asks for, with
the URL as its argument, and not something a URL does.  So it's plain
data, as a Place is, and anything that fetches asks the HTTP service.

That means it needs no implementation of its own.  It's another kind in
``types/kb`` (see :doc:`kb-service`), of class URL (Q42253), which
gives it labels, statements, the groups of what links to it, publishing
to the Index, and the entity lens.  ``types/kb/type.json`` registers ``public.url`` itself, conforming
to ``darq.entity``, in place of the catalogue's ``public.data``, as an
entity isn't a byte stream.

Identity
--------

An item is a singleton: there's one for a URL.  Its entity identifier
is a UUID derived from its URL, in its serialized form
(``serialize_url``), so it's the same wherever it's made, and finding
one is working out its identifier.  So two spellings of a URL, that
serialize the same way, are the same item.

Using the URL itself as the item specifier, which item identifiers
allow, would be more readable, but a URL can be longer than a specifier's
895 bytes, and the item needs an entity for its snapshots regardless.

A resource that moves has two items: see Redirects in
:doc:`http-service`.  Each keeps its own snapshots, sharing an item if
the content's the same, and the old URL says where it went.

An entity's made when something needs one: when a URL's snapshotted, or
a statement refers to it.  URLs mentioned in documents or messages don't
each get one.

Statements
----------

================  ================================  ====================
Meaning           Identifier                        Value
================  ================================  ====================
The URL           URL (P2699)                       URL
A snapshot        local                             Item
When              retrieved (P813)                  Time, a qualifier
Its type          local *media type*                String, a qualifier
Its hash          local                             SHA-256, a qualifier
ETag              local                             String, a qualifier
Last-Modified     local                             String, a qualifier
Final URL         local                             String, a qualifier,
                                                    after a temporary
                                                    redirect
Moved to          local, with *retrieved*, and      The URL's item it
                  *HTTP status*                     moved to; end time
                                                    (P582) if it's over
================  ================================  ====================

A snapshot's a link to its item's entity, as a message's attachment is
(see :doc:`message-type`), with *snapshot of* as its inverse label: one
from before links is a String, its item's identifier, on *snapshot
(identifier, old)*, until Give Items Entities links it (see
:doc:`entity-vs-item`).  The latest is the last.  Wikidata's closest to *moved to*, replaced by (P1366), is for
people and offices, so it's local, with *moved from* as its inverse
label.  The item's label is its URL, so Search finds it by that: a
document's title would be better, once something can tell what it is.
``darq.url`` has this vocabulary.

Opening
-------

Its lens is the entity lens: its URL, and its snapshots, shown as links
that open in their own lenses.  A small lens could later open the latest snapshot
directly, without changing the item.

Fetching from Search
--------------------

A URL typed into the Terminal's Search (S-space) is fetched, stored, made
an item, recorded in History, indexed, and shown in its lens:

1. What's typed is a URL if it's all one word, starting ``http://`` or
   ``https://``: others are words to search for.  Enter asks the HTTP
   service for a ``Snapshot``, and the panel says it's fetching, without
   how far it's got.
2. It opens the snapshot that comes back, with its lens.  If it wasn't
   ``changed``, that's the one already kept.  If the server didn't
   return it, but an earlier snapshot was kept, that's opened, and the
   panel says so.

If it can't be fetched, or there's no type for it, the Terminal says
why, which includes web pages, until there's an HTML type (Third
Edition).  Fetching again is asking for another snapshot: there's no refresh
on the item.
