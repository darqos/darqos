MH Email Tool
=============

This tool will do a one-time import of email stored in a collection of
MH folders.  Each message is processed to identify Person,
Organisation, and Group entities from the headers, and the body is
extracted as an Email Type with associated attachments.  The folder
structure is maintained as tags on the Email.

It would be possible to have this operate on an ongoing basis,
synching against the folders to ingest new emails, but at this point,
I think that's unlikely to be useful: the IMAP sync service will take
care of that.

The email itself could exist as a USB storage device, an NFS or SMB
network filesystem, or a tar or ZIP archive.  For first or second
edition, a local filesystem on the host Linux machine might also be an
option.

Implementation
--------------

Python's standard `mailbox` and `email` modules provide the majority
of what is required to implement this functionality.  The tool code
should use the Email type implementation to create new Email items and
to populate their attributes.

The end result should be Storage-based items for the email body and
its attachments, with a KB-based entity maintaining the relationships
with Person, Organisation, and Group entities, the linkage with any
attachments, and a folder-derived categorisation property.

History entries should be created for the email.  It might be good to
retroactively install a "received" event, a "read", event (?), and
possibly "replied" event(s)?, together with a contemporary "imported"
event from the tool itself.

The Email item should also be indexed.  It's not currently decided
whether that should include all the headers, just the standard
headers, or none of the headers.  Given the standard header
information is reflected in the KB relationships, does it need to be
in the text index?  This depends on how the search function works:
does it include KB searching?
