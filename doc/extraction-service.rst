Extraction Service
==================

*Proposed for Fourth Edition: not designed in detail, or implemented.*

The *Extraction Service* turns unstructured text into structured data,
using a language model.  An importer sends it some text and a
description of what it wants, and gets back data in that shape, which
it can check, match against the Knowledge Base, and show to the
operator for review.

It exists so that importers don't each have to parse their source's
layout.  A parser written for one web page's markup breaks when the
page changes; a model reads the text for what it says, so a moved
heading or a renamed label doesn't matter, and the same importer can
read a CV, a biography, or an email signature as well as a profile
page.

It's the first use of a language model in DarqOS.

Requests
--------

A request carries:

* the text, as plain text or HTML;
* a JSON Schema for the result, such as a list of positions, each
  with an organisation, a title, a start and an end; and
* which of the result's strings are *quoted*: values that must appear
  in the text, such as names and titles.

The reply is a result that conforms to the schema, or an error.  It
isn't trusted because the schema was met: the service checks it as
well (see Grounding, below).

Values are returned as the text gives them.  Dates, in particular,
come back as written ("Jan 2019 - Present"), and the caller turns them
into KB times, so the rules for precision and ranges are in one place,
and testable without a model.

Grounding
---------

A model can invent things that look right.  The service refuses a
result whose quoted strings don't appear in the text, after folding
case and whitespace, so a name, a title or an organisation can't be
made up.

That doesn't catch everything: a model can attach a real title to the
wrong organisation, or swap two dates, with every word still in the
text.  So an extraction is a proposal: the caller shows it to the
operator, next to what's already known, before anything is written.

What It Doesn't Do
------------------

* It doesn't decide which entity something is.  Matching an extracted
  organisation to a KB entity is the importer's job, with the same
  normalisation and review log as its other matches.
* It doesn't write to the Knowledge Base, or to any store.
* It doesn't fetch anything.  The text comes from the caller: pasted
  by the operator, read from a file, or fetched by the URL fetcher.

Models
------

Models are pluggable.  Each backend wraps one way of running a model:

* a *local* backend, such as llama.cpp or Ollama, with the model on
  the machine, so the text never leaves it; and
* a *remote* backend, a hosted model reached over the network, which
  is faster and more capable, but sends the text to someone else.

Which backends there are, and which is the default, is configuration.
A request can require a local backend: the text an importer sends
may be about other people, such as a contact's profile, and it
shouldn't leave the machine unless the operator has said it may.

A remote backend needs an API key.  Until there's a Credentials
service (Third Edition), it would be kept as the Security service
keeps its password: a file in the run directory, readable only by
DarqOS.

Callers
-------

* The LinkedIn import, reading a person's experience and education
  pages, pasted by the operator: its second phase, which waits for
  this service.
* The URL fetcher, for a fetched document's title and author, later.
* BookRecord, for a book's metadata.
* The email client, for the details in a signature.

Open Questions
--------------

* The protocol, in DIDL: is a schema sent as JSON text, or does DIDL
  need a way to describe one?
* Limits: how long a text can be, and how long a request may take,
  since a local model may need tens of seconds.  Should requests be
  asynchronous, with the result sent back later?
* Whether results are cached, keyed by the text and schema, so that a
  re-import doesn't ask again.
* How a backend is chosen when there are several: by the request, by
  configuration, or both.
