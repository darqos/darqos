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

* Ideally, this would be a Storage-back UTF-8 string.
* It should have basically two operations:
  * Delete characters from index X to index Y
  * Insert a supplied sequence of characters at position Z
  * These could in fact be a single operation: insert sequence in
    place of X to Y, where the sequence can be empty, and X and Y can
    be the same.
* Ideally, it should use a CRDT to support multiple concurrent writers.
* Ideally, it should support infinite undo/redo.
* When the text is altered, the Index service should be updated
  * Does the index service just give a yes/no answer for a particular
    item, and leave the finer details up to the item/lens?  Or does it
    track every instance within the item?
  * Does the Type Implementation need to reference-count the tokens,
    so when a run of text is deleted, it can know that the only
    instance of a token is now removed?
* What granularity of history records should be recorded.  It could be
  every insert/delete operation, or it could just be "this text was
  edited today".  What's useful at the History service level?
* What Metadata is recorded?
  * an Item UUID should be allocated
  * the combination of type and item ids should be enough to request
    the type implementation to "open" the item via a Lens.
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
  * The help shortcut should show a usage "cheatsheet"
    * More detailed usage document should be launched from a button on
      the cheatsheet
* The text should be displayed with decent but not extravagant borders
* The Lens should support both read-only and read-write modes
  * Maybe with a toggle shortcut?
* Movement and editing controls should mirror emacs
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
