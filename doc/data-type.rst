Data Type
=========

``public.data`` is the type of a file DarqOS has no implementation for:
a spreadsheet, an archive, a video.  Its bytes are kept as they came,
with their media type, so nothing that comes in is refused, and every
file is an item, and so an entity (see :doc:`entity-vs-item`), that can
be found, named, and linked to, as a Slack message's attachment is.

It's the type of what no other takes: ``darq.launch.import_data``, which
the HTTP service and the Slack import use, and the launcher's file
import, try the types for a file's media type, and then its extension,
and then this, which takes anything.

Implementation
--------------

``types/data`` implements it, as ``types/pdf`` implements PDFs: an item
is a Storage blob, read-only, identified by its key, in hex.  The blob
is a header, where the item's marked destroyed, its media type, and
then the bytes.

* ``CreateFrom(type_id, data, name, media_type)`` keeps any data.  Its
  media type is the one it came with, such as Slack's, or a server's
  ``Content-Type``, or else the one its name's extension suggests, or
  else ``application/octet-stream``.  ``Create`` is ``UNSUPPORTED``:
  there's no empty file.
* ``Export`` gives the bytes back, with their media type.
* Its item port serves the ``Data`` protocol (``lib/didl/data.didl``):
  ``Get``, the bytes and their media type, and ``Stat``, their size and
  media type.
* Its entity's named from the file it came from, without its
  extension, and has its *file name*, *media type* (P1163), and *file
  extension* (P1195), Wikidata's properties for a file.  Its names are
  published to the Index: there's nothing in it to read.

There's no lens for it yet: an item of it is shown by its entity's
page.  A file's limited to what Storage can hold in one value, as every
blob is, and the Slack import doesn't keep files over 64 MiB.

The tests are in ``tests/test_data_type.py``.
