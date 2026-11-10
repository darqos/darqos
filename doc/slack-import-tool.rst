Slack Import Tool
=================

This tool imports the history of a Slack workspace from an export on
the host: an export directory, with messages, users, conversations and
files in JSON, as ``experiments/slack/extract.py`` writes them.  It's
not a Service: it's a one-shot tool, that can be run again if
necessary, and it does *not* export anything from Darq to Slack.

It's in ``tools/slack-import``, started from the Terminal's Tools
panel (*Sys-t*): choose the export's directory, and name the
workspace.  The Message type and Conversation lens are in
:doc:`message-type`.

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
  types are implemented; importing again then picks them up.  So are
  files that their type won't take, such as binary data in a text file.
  Files larger than 64 MiB are left too, until Storage has ranged
  access.  A file attached to no message isn't imported.

If run multiple times, it will *not* create duplicate Messages: their
identifiers are made from what they are (see :doc:`message-type`), and
statements are restated, as an address book's are, so importing again
changes nothing, unless a file can now be an item.

A reply also sent to its channel is listed by Slack in both, and is
imported as one message.  Conversations with nothing said in them are
left out.  Every statement cites the workspace's item.

The raw JSON isn't kept in Darq: the export is the archive, and a
better conversion can be imported from it later.

Matching People
---------------

Slack's export has no email addresses, so a person can only be matched
to the KB by name, which is logged and listed in the window to be
confirmed, as the LinkedIn import proposes.  Otherwise, they're
created.  Their member identifier is kept, as *website account on*
(P553) the workspace, with *website username or ID* (P554) as its
qualifier, so a later import finds them exactly.  Bots are entities of
their own, of *software agent* (Q2297769), not people, as is anyone
Slack no longer lists, of whom there's only an identifier.

Converting Text
---------------

Slack's markup is converted to a body's subset of Markdown
(``slack_text.py``): mentions and channels become names, links their
text and URL, and bold and italics Markdown's.  What Slack wraps in
tokens is unwrapped in code too, whose text is otherwise left alone.  A
message's attachments' text is added to its body, since a bot's message
is often only that.  Standard emoji stay as ``:name:``, as custom ones
must.

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

It's built as ``tools/vcard-import`` is, with a window that lists what's
to be reviewed:

* ``slack_export.py`` reads an export.
* ``slack_text.py`` converts Slack's text.
* ``slack_importer.py`` imports it, as a client of the KB service, and
  of the KB type implementation, through which items are written.
* ``main.py`` is the window, and makes items of files, with
  ``darq.launch``.

An export of 66,137 messages and 459 attached files, through a real
p-kernel, takes about 3 minutes 20 seconds, and 43 seconds to import
again, changing nothing.  It became a KB of 738 MB, 335 MB in Storage,
and 30 MB in the Index.

The tests are in ``tests/test_slack_import.py``.
