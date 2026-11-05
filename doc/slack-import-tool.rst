Slack Import Tool
=================

This tool imports the history of a Slack workspace from an export on
the host: an export directory, with messages, users, conversations and
files in JSON, as ``experiments/slack/extract.py`` writes them.  It's
not a Service: it's a one-shot tool, that can be run again if
necessary, and it does *not* export anything from Darq to Slack.

*Designed for Second Edition: not yet built.  It waits for the Message
type and the Conversation lens, in* :doc:`message-type`.

What It Imports
---------------

* Each channel, direct message and group chat becomes a Conversation
  entity.  (An earlier design had channels as Groups: see
  :doc:`message-type`.)
* Each message and thread reply becomes a Message entity.  System
  messages, such as channel joins and renames, are left out for now.
* Each person becomes, or matches, a Person entity, and each bot a
  ``darq.entity`` of its own.
* Each file of a type with an implementation (images, text, and PDFs)
  becomes an item, of its type, attached to its messages.  Others are
  recorded by name, type and size, and stay in the export until their
  types are implemented; importing again then picks them up.

If run multiple times, it will *not* create duplicate Messages: their
identifiers are made from what they are (see :doc:`message-type`).

The raw JSON isn't kept in Darq: the export is the archive, and a
better conversion can be imported from it later.

Matching People
---------------

Slack's export has no email addresses, so a person can only be matched
to the KB by name, which is logged and listed in the window to be
confirmed, as the LinkedIn import proposes.  Otherwise, they're
created.  Their Slack member identifier is kept, so a later import
finds them exactly.

Limits of the Export
--------------------

* Channel membership wasn't exported, so a conversation's participants
  are the people who posted in it.
* Custom emoji and link previews' images weren't downloaded, so custom
  emoji stay as ``:name:``.
* It's one person's view: other people's direct messages, and private
  channels they weren't in, aren't in it.

Implementation
--------------

It's built as ``tools/vcard-import`` is, with a window that takes the
export directory, and lists what's to be reviewed.
