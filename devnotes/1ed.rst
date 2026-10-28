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
  This seems like a simple hack

  (2026-10-27)

* It's helpful to use Sqlite for things like the History
  service where a relational data model and queries are a
  good match to the requirements.  But by default, that
  means using the host filesystem

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

  (2026-10-27)      
      
* The History service is subject to data bloat if it records every
  single event in the system.  The design proposes that events are
  coalesced as they become older, with a count kept for a time
  interval, rather than every individual event time.

  But, for a given interval, it seems like just saying "Edited 10
  times this hour @ 10am" doesn't satisfy intuitively when you've
  edited at 10:59.

  * Would keeping both ends of the interval help?
  * This might be skewed because the initial implementation
    starts coalescing immediately at a fairly coarse interval?

  (2026-10-28)

* In the History service API, the `get_events` function allows
  filtering to a set of types.

  Is a direct match good enough here?  Or would sub-type matching
  be worth the extra effort?

  (2026-10-28)

* It'd be useful to have some visibility into the kernel, for
  equivalents of things like `ps`, `netstat`,  or the `/proc`
  filesystem.

  To support tools like `top`, it'd be helpful to have a subscription
  interface, whereas `ps` just wants a snapshot.

  Should the resulting "tables" ever be Items?

  (2026-10-28)

* What controls and shortcuts are needed for the compositor/window
  manager?

  * Some sort of window carousel like Alt-Tab?
  * Some sort of zoom-to-grid like macOS?
  * Raise/lower
  * Full-screen/restore

  * It'd be nice to be able to select a group of windows, and then
    perform some action on them all.  Selection being distinct from
    focus.

    * Like, select a bunch of stuff, and drag it together to
      reposition it.
    * Or drag a marching ants box around a bunch of windows and close
      them all.

  * Ideally, everything is doable without a mouse too.

  (2026-10-28)

* I think Metadata names and/or tags for items will be quite
  important.  When selecting stuff via the history browser (currently
  the only way), not being able to see a name or something other than
  the 32-byte identifier is not great.

  This could be populated into a tooltip or something, fetched on
  demand as the item stays in focus, but ... it'd be nicer to just
  visually scan down the list and see a meaningful name.

  That's a lot of work on the Metadata service though.  I guess maybe
  the Terminal could cache them?

  (2026-10-28)

* Types should have a "operator-friendly" name.

  ``public.utf8-plain-text`` is all well and good, but "UTF-8 Text" or
  just "Text" would be better in the UI, I think.  Especially as the
  UTI names move out of the ``public`` namespace and use reversed DNS
  prefixes.

  (2026-10-28)

* For me to start actually using this, there will need to be a backup
  mechanism.  I'm going to want snapshots, at least, of the variable
  bits of the filesystem (/darq/run/* ?) on a pretty regular basis.

  Also, there might need to be a process for running migration scripts
  as part of the install.  That kinda implies some discipline around
  build numbers, so that restored backups get a way to re-run the
  necessary scripts several migrations down the line.

  So ... maybe just a cron job in the host Linux that snapshots the
  volume and rsyncs the backup to my nas?

  (2026-10-28)

* The need for build numbers for migration scripts kinda implies a
  ``uname(2)`` equivalent API.  And ``mkdist`` will need to populate
  it somehow.

  (2026-10-28)
  
