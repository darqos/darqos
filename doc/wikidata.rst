Wikidata
========

The Knowledge Base follows Wikidata's model, and uses Wikidata's
identifiers wherever they exist (see :doc:`kb-service`): a fact about
Australia is about Wikidata's Australia, Q408.  People, organisations,
places and languages made locally, by the address book, the LibraryThing
import, or a Book card, have identifiers of their own, as nothing's known
of them.  This is how they come to be Wikidata's, when they're there,
and gain a few basic facts from it.

Matching is always confirmed, and never guessed: two people can share a
name, and an entity matched wrongly is merged wrongly.  Nothing's sent to
Wikidata unless it's asked for.

Becoming Wikidata's Entity
--------------------------

A local entity that's matched is merged into Wikidata's (``Merge``):
what was said of it locally is kept, what links to it is relinked to
Wikidata's, and its identifier becomes a redirect, so its History still
finds it.  So
Peter Carey, made by an import, becomes Q313254, as a place from an
address book is Wikidata's place.  Its recorded type comes with it, so
he's still a person.  A property holding Wikidata's
identifier was considered, but it would keep two identities for one
person, and an entity matched twice would stay two.

Finding a Match
~~~~~~~~~~~~~~~

* **Find on Wikidata**, W in the entity lens, or its menu, on any
  entity's page: it searches Wikidata by the entity's label, shows the
  candidates, with their descriptions ("Australian novelist (born
  1943)"), and asks which, if any, it is.  On one that's matched, it
  refreshes its facts instead.
* **Match with Wikidata**, a tool (``tools/wikidata-match``), lists the
  people and organisations that aren't matched yet, to be worked through
  one at a time, with Link and Skip, so that what came from imports is
  reached.  A search can be typed again.

Two local entities that are one entity on Wikidata, as Bloomsbury and
Bloomsbury Publishing PLC are, are each found and linked, and so merged
into it, in turn.  M, in the entity lens, merges two local entities, or a
local one into Wikidata's, by name (see :doc:`kb-service`).

Open Library was to be asked first, but its author records don't have
Wikidata's identifiers: those looked at, and its author search, had none.

Publishers are tried too, to see how well they match against the data:
an imprint is often a child organisation of its publisher in Wikidata,
and the imprint field stays text either way.

Basic Facts
-----------

A matched entity is given a few of Wikidata's statements, a fixed list,
as its hundreds would make it unreadable.  Each is stated in Wikidata
(Q2013), with when it was retrieved (P813), and they're restated, as an
address book's are: a refresh, which is only on request, updates them,
and what's been edited by hand stays as it is.

=============  ============================================================
Kind           Statements
=============  ============================================================
Everything     Description, English aliases, English Wikipedia article,
               as described at URL (P973)
Person         Date of birth (P569) and death (P570), place of birth (P19)
               and death (P20), country of citizenship (P27), occupation
               (P106)
Organisation   Inception (P571), dissolved (P576), country (P17),
               headquarters location (P159), parent organisation (P749)
=============  ============================================================

* Of several values, Wikidata's best rank's taken: its preferred ones,
  or else its normal ones, and never its deprecated ones.
* Dates keep their precision, and their calendar.
* A place, country, or occupation is an entity too, so one that isn't
  here's made, minimally: its label, description, and what it's an
  instance of, from Wikidata, and nothing more, so that one person
  doesn't bring half of Wikidata with them.  Many will be here already,
  from address books.
* Sex or gender isn't taken: a library has no use for it.  Images (P18)
  wait for later: they're Commons files, which would be items, as covers
  are.

Wikipedia's articles are sitelinks in Wikidata, which the KB doesn't
have, as they're the Wikimedia Foundation's: described at URL (P973)
does for them.

Languages
---------

Every language with a two-letter ISO 639-1 code (P218), about 180, is
brought from Wikidata, with its codes, so that a book's language is
Wikidata's: English, Q1860.

* The Language fields match and complete against them, so English,
  english, and en are all Q1860, whichever's typed.
* B gives an e-book's language by its name, from ``darq.book``'s table of
  27 codes, which then matches Wikidata's: en-AU is English, Q1860.  The
  table stays, for before languages are brought, and for B's.
* The languages made locally so far, such as English and Latin, are
  merged into Wikidata's, by their names, when they're brought.
* They're brought by Match with Wikidata's Bring Wikidata's Languages,
  with one request to the Wikidata Query Service.  Before they are, a
  language's matched against those made here, as it was.

A language's naming and matching is a question for the whole KB, not
only for books, and this is a start on it.

The Person's Page
-----------------

The entity lens shows a matched entity's Wikidata page, and its Wikipedia
article, if it has one, as their URLs, in text.  DarqOS can't show a web
page until Third Edition's HTML type and web lens, so there's nowhere for
them to go: they're to become links then (see :doc:`roadmap`).

Talking to Wikidata
-------------------

``darq.wikidata`` does it, as ``darq.openlibrary`` does Open Library's:
it makes requests of Wikidata's API (``wbsearchentities`` and
``wbgetentities``), and its Query Service, for languages, and knows
nothing of how they're fetched.  Its ``Linker`` does what it says to a
KB, writing through the KB type implementation, so the Index is kept up
to date.  ``darq.ui.wikidata`` fetches through ``darq.http``.  One
entity's a request, with one more for what it refers to.  The properties
and items they need are in the vocabulary: Wikidata's own, where the KB
hasn't them already.

Wikimedia asks automated clients to say who they are, with a way to get
in touch, in their User-Agent, and refuses those that don't, with a 403.
``darq.http``'s own doesn't say who's using it, so these requests, and
only these, say::

    DarqOS/2.2 (David Arnold; d+wikidata@pobox.com)

The name and address are the operator's, so they aren't in the source:
they're the one line of ``wikidata-contact``, in the run directory.  With
no such file, Wikidata can't be asked, and that's said.

The tests are in ``tests/test_wikidata.py``, with Wikidata's answers in
the shapes it gives them.
