Index Service
=============

The *Index Service* provides both search (query) and index (publish)
functions for a system-wide full-text search facility.  All objects
can, and should, register their content with the index service,
enabling the common search UI to discover them.  This is especially
important given the deliberate absence of a system-wide hierarchical
storage system.

Publishing
----------

An item's entry is published by its type implementation, the only
thing that changes the item's content (see :doc:`types`): so each
change is published, whoever made it, and the Index never interprets
an item itself.

* ``Publish(item_id, fields)`` replaces everything indexed for the
  item.  Each field is ``NAMES``, such as a person's labels and
  aliases, or ``BODY``, the item's content.
* ``Withdraw(item_id)`` forgets the item, when it's destroyed.

Both are one-way, so a publisher never waits for the Index, and works
on if it isn't running.  An invalid item identifier is logged, and
dropped.

An implementation publishes an item each time it saves it to its
store, at the same rate: the text type, a couple of seconds after
typing stops.  The whole entry is replaced each time, rather than the
words added and removed: a lost publish is put right by the next, and
the words are found from the text in one place, the Index.  It's
cheap: replacing a 1 MiB entry takes about 10 ms.

Items have no titles yet: they'll be published as names once items
have them, from metadata or a KB property.

The publishers are type implementations only:

* the text type publishes an item's text as its body (see
  :doc:`text`);
* the image type, its title, artist and copyright as names, and its
  comment, date taken and format as its body (see :doc:`image-type`);
* the PDF type, its title and author as names, and the text of its
  pages as its body (see :doc:`pdf-type`); and
* ``types/kb``, the entities of its kinds (people, organisations,
  organisational units, places, groups, conversations and messages),
  with their labels and aliases, in every language, as names, and
  their descriptions, and a message's text, as body, each time it
  writes one: an entity destroyed, merged into
  another, or no longer of its kind is withdrawn.  The Knowledge Base
  service doesn't index, and entities that aren't of a kind with its
  own implementation, such as properties, classes and ``darq.entity``
  items, aren't indexed.

Any process can publish, for now: the Index can't yet tell whether a
publisher is the item's implementation, since DIPC doesn't say who
sent a message.

``darq.index.Publisher`` is the client for implementations: its
``publish(item_id, names, body)`` and ``withdraw(item_id)`` look the
Index up when they need it, and log a failure rather than raise it.

Searching
---------

``Search(text, type_ids, max, offset)`` returns the items with words
starting with each of the text's words, ignoring case and accents:
``cof sho`` finds *Coffee shop*.  Phrases, tags and filters such as
``type:`` may come later.

Results are best first: ranked by how well they match (FTS5's bm25),
with a match in an item's names counting ten times one in its body,
then by when the item was last published.  Each has a snippet of the
item's text, around what matched.  If ``type_ids`` isn't empty, only
items of those types, exactly, are found.  At most ``max`` results are
returned, 100 at most, after skipping ``offset``.

``darq.index.search(text, type_ids, max, offset)`` is the client.

Implementation
--------------

The service, ``services/index``, is started at boot, after History,
and registers ``darq.index``.  It serves the ``Index`` protocol
(``lib/didl/index.didl``).

Entries are kept in ``index.sqlite``, in the run directory: an
``items`` table of each item's identifier, type and when it was
published, and an FTS5 table of its names and body, tokenized as the
KB's labels are (``unicode61``, removing diacritics).  It holds the
text of every item, so only its owner can read it.  An entry's names
and body are indexed up to 1 MiB, together: the rest is left out.

Rebuilding
----------

An index can be lost, or miss publishes sent while the Index wasn't
running.  ``tools/reindex`` publishes every item's entry again:

* the items are those History says were created or imported (History
  keeps every event), and the Knowledge Base's entities of its kinds,
  which an import writes without recording them: those with a recorded
  kind, or an instance of a class with a kind as its instance type,
  each once;
* each is asked for its entry by its type's implementation, with the
  ``Implementation`` protocol's ``IndexEntry(item_id)``, which gives
  what the implementation publishes when it saves the item, and it's
  published;
* one that's been destroyed, ``NOT_FOUND``, is withdrawn; and
* one whose type isn't indexed (``UNSUPPORTED``), or whose
  implementation can't be started or doesn't answer, is skipped.

It logs how many it published, withdrew and skipped, and exits.  It's
a tool, in the development Terminal's Tools menu, and the Index starts
it itself when it starts with a new database (on its own thread, which
collects it when it's finished): so deleting ``index.sqlite`` and
rebooting rebuilds the index.

The Terminal's Search panel, on *Sys-space*, searches it (see
:doc:`terminal`).
