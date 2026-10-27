Text Type
=========

A key first edition goal is to support a Text type.  The point is less
the actual type functionality, and more the mechanisms of types,
items, type implementations, and lenses.

A text item should be a sequence of UTF-8 characters.  It is
essentially a Unix-style text file.  The text should be persisted
using the Storage Service.

In the first edition story, a new text item is created via the
Terminal's factory UI.  It is then edited, and the Lens closed.

The requirements to implement this are:

* the Text type implementation must be registered
* the factory must have a way to obtain the set of available type implementations
* the factory must have a way to request the type implementation create a new item
* the factory(?) must be able to choose a Lens to interact with the new item
* the operator should be able to discover the functions of the Lens

Text Type Implementation
------------------------

The implementation, `types/text`, implements `public.utf8-plain-text`.
It's activated by the Type Service like any other (see :doc:`types`),
and serves the `Implementation` protocol.  Each client of an item gets
its own port, which serves the `Text` protocol (`lib/didl/text.didl`):

* `Close()`, as for every item port.
* `Get()` returns the whole text, and its version.
* `Replace(start, end, text, expected)` replaces the text from `start`
  to `end` with another string.  This is the one editing operation: an
  empty string deletes, and `start` equal to `end` inserts.
* `Stat()` returns the text's length, and its version.

Positions and lengths count Unicode code points.  They aren't bytes,
so a client can't split a character's encoding, and they aren't
user-perceived characters (grapheme clusters): where a cursor can stop,
and how far a word or line is, is up to the lens.  A Qt lens converts
from Qt's UTF-16 positions.

Each change gives the text a new version, counting from 1 while the
item is open.  A `Replace` with a non-zero `expected` is made only if
the text is at that version, else it fails with `CONFLICT`: this is
how a client notices that another has changed the text, in place of a
CRDT for concurrent editing.

Storage
~~~~~~~

An item's text is kept in a Storage blob, and its item specifier is
the blob's key, as 32 lowercase hex digits, so the implementation needs
no table of its items.  The blob is an 8-byte header (`DTXT`, a 16-bit
format version, and 16 bits of flags), then the text in UTF-8.

An open item's text is kept in memory, shared by its clients.  It's
saved 2 seconds after its last change, or at most 30 seconds after its
first unsaved change, and when its last client closes it.  Saves are
made over the blob's generation as last loaded or saved, so if
something else has changed the blob, the save fails, and is logged,
rather than overwriting it.

`Destroy` sets the header's destroyed flag, keeping the text.  A
destroyed item can't be opened, but clients that already have it open
can go on using it.

Not Yet
~~~~~~~

* Undo and redo, and a cursor and mark, are the lens's.  A saved
  cursor position belongs in the Metadata service.
* Clients aren't told of others' changes: they find out from a
  `CONFLICT`, or by asking for the version.
* Once started, the implementation keeps running.  It could exit once
  it has no open items, to be started again by the next `Activate`.
* If it's killed, changes not yet saved, up to 2 seconds' worth, are
  lost: it isn't told it's being killed.
* When the text is altered, the Index service should be updated:

  * Does the index service just give a yes/no answer for a particular
    item, and leave the finer details up to the item/lens?  Or does it
    track every instance within the item?
  * Does the Type Implementation need to reference-count the tokens,
    so when a run of text is deleted, it can know that the only
    instance of a token is now removed?

* History events are recorded by the lens, not the implementation,
  which can't tell who caused a change (see :doc:`history`).  The
  History service counts repeated events, so the lens can record each
  edit.
* What Metadata is recorded?

  * Things like the current cursor position within the text, the
    screen position of the Lens, perhaps the creation and modification
    timestamps? could all be recorded using the Metadata service.
  * The real question is how and by what would they be used?

    * They don't belong in the content: that should just be the
      characters of the text item themselves.
    * But any usage means that something has to know they're there.
    * What about user tags?  Sure, they're easy to store, but to make
      them useful, they need to build a searchable index.  Should that
      be here?

Text Lens
---------

* This should be a plain, unadorned window.

  * No titlebar
  * No menubar
  * No toolbar
  * Subtle scrollbar
  * The help shortcut should show a usage "cheatsheet"

    * More detailed usage document should be launched from a button on
      the cheatsheet

* The text should be displayed with decent but not extravagant borders
* The Lens should support both read-only and read-write modes

  * Maybe with a toggle shortcut?

* Movement and editing controls should mirror emacs

  * Character, word, line, and paragraph movements
  * Line start / line end
  * Doc start / doc end
  * Delete character, delete word, kill rest of line
  * Mark
  * Yank region
  * Kill ring
  * Auto save

* Traditional clipboard cut, copy, paste of UTF-8 strings should be supported

  * I'd like an ability to select an area (rectangular?) as a
    graphical source too (copy only, obvs)
  * It might make sense to copy the item identifier too?
  * The "clipboard" should have history, probably an emacs-style kill-ring?

* I think all of the emacs-style commands should be implemented in the
  Lens, rather than the Type.  The type can do storage and basic data
  access.  I think I'm happy with this split for now.

  * The downside of this is that the emacs-style editing operations
    are not available for scripting.


Implementation
~~~~~~~~~~~~~~

The lens, `lenses/text`, is registered for `public.utf8-plain-text` in
the `terminal` context by its `lens.json`.  It's started for one item
(see Lenses, in :doc:`types`): it reports `Ready` on its bootstrap
port, is asked to `Use` the item, with the item's port, and exits when
its window is closed, or it's asked to `Close`.

* The window is frameless, with a 50 pixel margin: dragging the margin
  moves it (the compositor does the move), and its context menu has
  Full screen, Read only, Help, and Close.  A status line under the
  text says why it's read only, and the like.
* Its widget is a `QPlainTextEdit`.  Each change is sent to the item
  as it's made, as one `Replace` of the part that differs from the
  item's text, which the lens keeps a copy of, so positions are in
  code points, as the Text protocol counts.  The lens holds nothing
  unsaved: the implementation saves the text.
* If another client has changed the text, the `Replace` is refused
  with `CONFLICT`: the lens reloads the text, keeping the cursor near
  where it was, and says so.  If the item stops answering, the lens
  becomes read only.
* Text that Qt can't hold exactly, such as a U+2029 paragraph
  separator, is shown read only, so editing can't change what isn't
  shown.
* It records `opened` when it starts using the item, and `edited` for
  each change, with source `text-lens` (see :doc:`history`): the
  service counts repeats.
* Keys follow Emacs, with Alt as Meta: C-f, C-b, C-n, C-p, C-a, C-e,
  M-f, M-b, M-<, M->, C-v, M-v to move; C-d, M-d and C-k to delete and
  kill; C-SPC to set the mark, C-g to cancel it, C-w and M-w to kill
  and copy the region, C-y to yank; C-/ to undo; C-x C-q to toggle read
  only, C-x C-c to close; F1 for help, F11 for full screen.  The kill
  buffer is the clipboard.

`darq.launch` has what the Terminal needs to use items:
`create_item(type_id)`, `open_item(item_id)`, `find_lens(type_id)`,
`start_lens(path)`, and `use_item(item_id, item)`, which does the rest.

Not yet:

* A kill ring, and consecutive kills appending to the last.
* Paragraph movement, and a subtle scrollbar.
* A rectangular, graphical selection, and copying the item identifier.
* A usage document, from the help.
* Saving the cursor and window position, in the Metadata service.
* Telling the lens of others' changes as they're made, rather than at
  its next change.
* Each keystroke is a synchronous `Replace`, so a slow item stalls
  typing.  Async clients would let the lens send changes without
  waiting.
