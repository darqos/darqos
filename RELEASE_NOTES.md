# DarqOS Release Notes

Summary notes on newly added features for each release.

## Unreleased

- **The darq API's exceptions are `darq.errors`'s.**  They're no longer
  imported into `darq` itself: `darq.DarqError` is now
  `darq.errors.DarqError`, `darq.LaunchError` `darq.errors.LaunchError`,
  and so on, as in scripts and the REPL.
- **The event loops are `darq.loop`'s, and Qt's `darq.qtloop`'s.**
  `QtEventLoop` is now imported from `darq.qtloop`, and the select loop,
  and the loops' interface, are `darq.loop`'s, not `darq`'s.  So `import
  darq` no longer loads Qt: the p-kernel and the services don't.
- **`darq.process` and `darq.version` are only modules in `darq`:** their
  names, such as `PortState` and `Version`, are theirs, not `darq`'s.  The
  operator's languages stay in `darq`: `languages()`, and
  `language_chain()` and `language_tag()`, which were `darq.language`'s
  `chain()` and `tag()`.
- **`darq.system`'s internals aren't `darq`'s:** `Client`, `Handler`,
  `call`, `transact`, `init`, `init_cb` and `SpawnResult` are reached as
  `darq.system.…`; `init_a` and `get_loop`, unused, are gone.
  `darq.MAX_NAME_LENGTH` is a canonical name's limit, 31 bytes, as
  `darq.identifier`'s: a registered name's, 255, is
  `darq.MAX_REGISTERED_NAME_LENGTH` (it was the 255 before, by mistake).
- **What programs don't use is `darq._runtime`'s:** `pcall`, `process`,
  `compositor` and `vocabulary` moved there, from `darq`.  `darq` has an
  `__all__`: the system API, identifiers, languages, and the modules
  `errors`, `loop` and `version`.  `send_message`, the raw p-call, is
  `darq.system.send_message`: programs use `send`.

- **Items are named from the start.**  The Knowledge Base now sets up its
  vocabulary when it starts.  Until something used a person, a book, or
  the like, a new install's items, such as a text file made with New, got
  no entity, and so no name, and a new release's vocabulary, and its
  migrations, waited till then too.

- **Python packages as items.**  A new item type, *Python Package*
  (`darq.python`), keeps Python code as a package of modules, a zip, in
  Storage, named as it's imported.  `darq.packages` imports from them: a
  package's item on `sys.path` is read as a directory, and
  `import tidy` finds the package named *tidy*.  It's the first step to
  keeping REPL sessions, extracting programs from them, and running them.

- **The REPL's namespace tree.**  On the left of the REPL is what's bound:
  the session's names, its results, and the built-ins, with a badge for
  each kind of thing, and modules, classes and objects to expand.
  Clicking one edits it: a function's, method's or variable's source, or a
  class's header.  Ctrl+S saves it, changing what exists in place, so
  instances and what holds a function see the change.  Library code is
  shown, but not changed.  What isn't saved is kept as a draft, marked in
  the tree.

- **A Python REPL**, with *Sys-s*: Python, a cell at a time, with the darq
  API ready (`darq`, and `terminal`, to drive the Terminal), syntax
  highlighting, completion with Tab, and `$3` for cell 3's result.  A
  session is an item, a Python package, and the REPL is its lens: *Sys-s*
  opens the default session, or focuses its REPL, and others open from
  History or Search, each in a REPL of its own.  Ctrl+Shift+S saves a
  session, its code and the cells run, and it's opened again with them;
  what can't be saved is said.  *Extract package…* takes a function, such
  as a program's `darq_main`, and everything of the session's it needs,
  into a package of its own, to be imported by its name, and run:
  Ctrl+Enter in Search or History runs a package as a program, or
  `terminal.run_item` does.

- **Windows have shadows.**  A soft shadow sets each window, and each of
  the Terminal's panels, off from what's under it, and a darker one shows
  which has the keyboard.  `DARQ_SHADOW_FOCUSED` and
  `DARQ_SHADOW_UNFOCUSED` change them, as *blur,opacity,offset*: the
  defaults are `24,0.55,0` and `24,0.3,0`.

- **Scripts can drive the Terminal.**  The Terminal serves a protocol,
  `darq.terminal`, so a script, and later the REPL, can do what the
  operator does: list the windows, with the items their lenses show, and
  focus, move, resize and close them; open, create and import items, and
  open URLs and entities, each answered with the new lens's window; run
  tools; lock the screen; show a notice; and watch what happens.  The
  `darq.terminal` module is its client (`man darq.terminal`).  What a
  script creates is recorded in History as the script's doing.  While the
  screen's locked, it's refused.
- **The compositor reports where windows are**, and the screens, and can
  place and size an application window, for the Terminal.

- **Related names find people by nickname and maiden name.**  The vCard
  import linked a related name, such as a sister's, only to a card with
  exactly that full name, and otherwise made a stub person.  Now, failing
  that, it links to the one person known by it: by given name or nickname
  and family name (*Libby Arnold* for Elizabeth Marianne Arnold, nicknamed
  Libby), by all their given names, or with their maiden name.  Each such
  link is listed in the window, to check.  Re-importing removes the stubs
  this makes unnecessary, and a person's nicknamed and maiden names become
  aliases, so a search finds them by those.

- **Merging finds imported people again.**  Merge into (M, in the entity
  lens) found nothing on a system whose locale isn't plain English, such
  as `en_AU`, as it searched only that locale's language, and imports
  label in English.  It now searches all of the operator's languages.
- **The operator's languages** are a chain, best first, from the host's
  locale: `LANGUAGE`'s list, such as `en_AU:de_CH`, or else `LANG`'s.
  That's en-AU, en, de-CH, de, then English and `mul`, Wikidata's code for
  every language, which it keeps many people's names in.  An entity from
  Wikidata with only a `mul` name is now labelled with it, rather than
  with its Q-number.
- **Two cards merged stay merged.**  When two address book cards' people
  are merged, re-importing the address book keeps both cards' statements
  in the one kept, rather than each replacing the other's, which lost a
  card's UID and made it again on the next import.

- **A server that doesn't answer in HTTP** is said to: such as
  `python3 -m http.server -p 8000`, whose `-p` is the protocol, not the
  port.  It was reported as "only http and https".

## 2.9, build 9 (2026-11-13)

- **Updating from within DarqOS.**  *Update DarqOS*, on the Tools panel,
  shows what's installed, the newest stable release, and the newest
  pre-release, and updates to one, or reinstalls what's installed.  It
  asks a root updater to install it, which checks it's signed with the
  release key, backs up first, and restarts DarqOS; the Terminal then says
  how it went.  `dui` installs the stable release from GitHub by default,
  or the newest pre-release.
- **Releases** are signed, and published on GitHub: each build as a
  pre-release, tagged with its version, until one's declared stable.
  Builds are counted within an edition, from 0: 3.0 follows 2.125.

- **Release notes**, this file, are installed with each release, as
  `/darq/RELEASE_NOTES.md`.

- **Importing from items.**  The vCard, LibraryThing and Slack imports read
  an item, so a file fetched from a URL can be imported: fetch a URL, choose
  an item of the right kind, or bring in a local file.  Slack's can also be
  a local directory, read as it is, and its item is a zip of one.  The
  import remembers what it was imported from (*imported from*), and offers
  it again, with *Fetch again* for one that came from a URL.
- **Book fields.**  A Work has Dewey and LC classification numbers, an
  edition its LCCN, and a copy when, where from, and for what it was
  acquired, and notes, such as whose it is.  A book's numbers, its ISBNs,
  LCCN, ASIN, Open Library and LibraryThing IDs, and classification
  numbers, are searchable: run Reindex to include books already there.

## 2.8, build 8 (2026-11-12)

- **Terms.**  An *item* is anything in DarqOS; its *entity* is its record
  in the Knowledge Base; a *thing* is an item of the plain kind.
  `darq.entity` is now `darq.thing`, migrated when the system starts.
  Sys-i opens the *Item's entity*.
- **No Metadata service.**  Names, tags and dates are statements on items'
  entities, changes are in History, and view state is kept in items.
- **Adding books.**  A book matched by title is offered as an edition of
  one of its texts, or a new text of the same Work, such as another
  translation, in the add loop and in B's catalogue.  Adding a copy says
  how many copies its edition has, and how many books have been added.
- **Merged entities** show their own item identifier, not the merged one's.
- **Dialogs** in the lenses are DarqOS's own, dark, with a list to choose
  from, in place of Qt's stock ones.
- **Touchpads** scroll naturally, and tap to click.
- **The lock screen** shows the machine's name and network addresses.

## 2.7 (2026-11-11)

- **History, rebuilt.**  Type implementations record every event, as
  their callers describe it, with a note, such as a file's path, or the
  page a reader got to.  Lenses say what's *viewed*, rather than
  *opened*, as you read, and the History panel shows notes.
- **Facts about items.**  Tags are items of their own (*has tag*), made by
  name in the entity lens; pictures say what they *depict*; and the vCard
  import brings in contacts' photos, as their *image*, and categories, as
  tags.
- **The installer** removes what's gone from the distribution, caches and
  all.

## 2.6 (2026-11-11)

- **Names everywhere.**  Every item has a name, its label, or its kind
  and when it was made, shown in History, Search and window titles.
- **Sys-i** opens the entity of the item in the focused window, where
  it's renamed, and anything else is said of it.
- **The Tools panel** (Sys-t) runs the imports and other tools, from the
  Terminal.  The development Terminal is gone.

## 2.5 (2026-11-10)

- **Every item has an entity**, in the Knowledge Base, including texts,
  images, PDFs and e-books: items made before are given theirs by the
  *Give Items Entities* tool.
- **Links between items.**  A copy's content, an edition's cover, a URL's
  snapshot and a message's attachment link to their items, and can be
  followed back.
- **Data** keeps any file no other type takes, as it came, so nothing
  that comes in is refused.
- **Types** are entities in the Knowledge Base too.

## 2.4 (2026-11-09)

- **Wikidata.**  People, organisations and places can be matched with
  Wikidata's, and given its facts: from the entity lens, or the *Match
  with Wikidata* tool.  Languages come from Wikidata too.
- **Merging.**  Two entities that are one, such as two spellings of a
  publisher, are merged in the entity lens (M), and what linked to either
  links to the one that's left.
- **Qt 6.12**, with fixes to PDF search and e-book page turns.

## Earlier Second Edition snapshots (2026-11-04 to 2026-11-08)

- **Books.**  Works, texts, editions and copies, with a book card, an add
  loop for scanning ISBNs, Open Library lookups, and a LibraryThing
  import.  E-books (EPUB, MOBI and AZW) are read in their own lens, and
  catalogued alongside paper books.
- **PDFs**, read in their own lens, and searchable by their text.
- **Messages.**  A Slack import, with conversations and their messages,
  and a conversation lens.
- **Fetching from the web.**  The HTTP service keeps what's at a URL as an
  item, of its own type, from Search.
- **Builds and editions.**  Each dist now has an edition and build
  number, which the kernel reports.

## First Edition (2026-11-03)

The first experimental release.  This release has a very limited
scope, but within that, serves as a learning platform for future
development.

Components include:

- the p-kernel
  - Based on a minimal Linux platform
  - Provides processes and IPC
  - First draft IDL and stub generators for C and Python
- Storage Service
- History Service
- Index Service
- Terminal Service
  - Custom Wayland compositor
  - Search overlay, using Index Service data
  - Factory overlay, to create new Items
  - History overlay, to browse past system events
  - Login, plus lock, reboot, and shutdown controls.
- Security Service
  - Very basic functionality
- Knowledge Base Service
  - Derived from Wikidata's Wikibase model
- Text Type and Lens
- Image Type and Lens
  - Supports a range of popular image formats
- Person, Group, Organisation, Organisational Unit, and Place Types
- Entity Browser Lens
  - For Knowledge Base-based Types
- vCard import tool
- Install and upgrade scripts
- Daily backups
- Documentation and manual pages

