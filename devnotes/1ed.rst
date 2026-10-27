Notes on the First Edition
==========================

The purpose of this document is to gather notes on the use
of the First Edition Darq system.  This will include basic
bug reports, but more importantly, larger issues identified
during use.  It will inform changes to the :doc:../roadmap.rst
and the overall design.

It might replace the TODO.org file.

All notes should be dated.  Revisions, fixes, etc, should not
replace the initial note, but be added to its description.

----

* I am concerned about the use of the flags byte in the IPC
  header to hack in error response and notification messages.
  This seems like a simple hack (2026-10-27)

* It's helpful to use Sqlite for things like the History
  service where a relational data model and queries are a
  good match to the requirements.  But by default, that
  means using the host filesystem (2026-10-27)

  * *What is needed to have eg. Sqlite run over the Storage
    service?*

    - Ranged Read and Write (methods 6 and 7 are reserved), so a page
      write doesn't rewrite the whole blob.
    - Truncate, or a ranged Write that sets the length.
    - Blobs found by a name the caller chooses, or a directory blob
      mapping names to keys: SQLite opens its files (db, -wal,
      -journal) by name.
    - A durability guarantee: a Put is on disk once it's answered, so
      it can serve as SQLite's sync barrier.
    - Maybe an atomic multi-blob Put, so the VFS can offer
      SQLITE_IOCAP_BATCH_ATOMIC and SQLite needs no journal.
    - Python's sqlite3 can't register a VFS: it needs C, or apsw.

* The History service is subject to data bloat if it records every
  single event in the system.  The design proposes that events are
  coalesced as they become older, with a count kept for a time
  interval, rather than every individual event time.

  But, for a given interval, it seems like just saying "Edited 10
  times this hour @ 10am" doesn't satisfy intuitively when you've
  edited at 10:59 (2026-10-28)

  * Would keeping both ends of the interval help?
  * This might be skewed because the initial implementation
    starts coalescing immediately at a fairly coarse interval?

* In the History service API, the `get_events` function allows
  filtering to a set of types.

  Is a direct match good enough here?  Or would sub-type matching
  be worth the extra effort? (2026-10-28)
