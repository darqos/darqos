Email
=====

Email, and email applications, has evolved to become a classic example
of a "silo" application.  It includes both the viewing and composition
of emails, but also,

- a custom object-collection viewer
- a custom structured filing system
- a way of managing contacts, potentially both using a shared
  collection of contacts plus an internal-only collection of known
  correspondents without other details
- a spam filtering system
- some sort of alerting mechanism
- integrated viewers for a wide range of MIME types, embedded within
  the RFC-822 data type
- a vacation notification auto-responder

Many of these things could very reasonably be system-wide facilities.

The RFC-5322 message format describes header fields which are
essentially metadata for the content.  It does not address the
formatting of the message content.

In practice, most email content uses MIME (RFC-2046) encoding for
delivering bundled media and HTML as the primary content body.

So ... I think this suggests that "email" might not need a dedicated
viewer or composer, or perhaps if it does, it would just be
specialised metadata handling, but either way it's really just rich
text.  And MIME-handling is about the construction/encoding and
extraction/decoding of the embedded resources for that rich-text
document.

Workflow
--------

There are a few different workflows for email:

- Reading and processing a delivered message

  - Including storing important messages for easy future retrieval

- Composing and sending a message
- Forwarding a message, optionally with added content
- Resending a message
- Searching for previously-received messages

Reading
~~~~~~~

For an end-user system, emails are usually delivered via IMAP4
(RFC-9051), or possibly via POP3, using a pull model.  While connected
to the server, an IMAP client can use the IDLE (RFC-2177) extension to
indicate that it can handle asynchronous updates from the server.

The LEMONADE profile (RFC-5550) extends and codifies the use of IMAP,
SMTP, and Sieve for mobile or other restricted resource usages.

I think the right approach here is to have an Email Service that
maintains connections to configured account endpoints, and fetches
delivered mail using IDLE/NOTIFY with a fallback to a timed basis.

There's a question as to whether the email should remain on the
server, and be treated as a remote resource with an optional local
cache (either of metadata only, or both metadata and content), OR, if
it should be fetched for local storage.

  Which is actually a larger question: how should DarqOS deal with
  multiple devices in general?

Let's assume for now that all emails are downloaded to local storage,
and that's the canonical copy.

Having fetched the email (in its entirity? Or just the RFC5322 body?)
it will be possible to index, history, etc, it.  This should be a
standard pipeline for handling inbound objects, exposed as a
system-wide inbox.
