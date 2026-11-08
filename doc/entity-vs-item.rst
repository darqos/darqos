Entities and Items
==================

*Decided, on 8 November 2026: everything is an entity (B, below).  The
review that led to it, and the plan for getting there, are at the end.*

DarqOS has two kinds of thing that can be named and linked:

* **Items** (see :doc:`item` and :doc:`types`): a text note, an image,
  and later documents, emails, PDFs and books.  Each has a type, and
  its type implementation gives it *behaviour*: it can be opened in a
  lens, edited, and exported.
* **Knowledge Base entities** (see :doc:`kb-service`): people,
  organisations, places, and the properties and classes that describe
  them.  They hold *knowledge*: labels, descriptions, and statements,
  with qualifiers, references and ranks, that can be followed and
  queried.

The line between them is behaviour.  An item has it; an entity has it
only if it has a *kind* (``darq.person``, ``darq.organisation``, and
the others), recorded by its DarqOS type statement, which makes it an
item too, with ``types/kb`` as its implementation.

So the question is how knowledge and behaviour are joined: whether
every item should be an entity, some should, or none.

What's Built Now
----------------

Items and entities are separate.  An entity with a kind is also an
item, whose identifier is its kind and its entity identifier.  Content
items (text and images, so far) aren't entities: they have no labels,
and nothing can be stated about them.

Other items refer to KB entities by their item identifiers, which are
just strings in the item's own data or metadata: an email's sender, a
photo's subject.  The KB can't refer to content items at all, yet.

The Item Identifier Datatype
----------------------------

The KB's design has an *Item identifier* datatype (see
:doc:`kb-service`), not implemented: a statement whose value is a
DarqOS item, such as

* Alice's photo is this image item (the nearest real case: the vCard
  import's PHOTO);
* Alice wrote this paper, a PDF item;
* this project's spec is this document;
* my notes about Bob are this text item.

As statements, they'd have what every statement has: qualifiers
(*photo, from 2019*), references (*stated in: the address book*),
ranks (*preferred photo*), and queries (*everyone with a photo*).
Indexed as links, they'd answer the reverse question too: *what refers
to this image?*, so an image lens could show "Alice's photo".

The two kinds of value would be:

* an **entity value**, pointing at knowledge: something to read,
  follow, and query; and
* an **item identifier value**, pointing at behaviour: something to
  open in its lens, edit, or export, through its type implementation.

An entity with a kind is both, but is still referred to as an entity:
its item identifier follows from its kind.  So an item identifier
value would refuse the KB kinds' identifiers, which would duplicate an
entity value.  And an item identifier can point at nothing: its type
implementation, not the KB, decides whether an item exists, so the KB
wouldn't check, and a lens would report an item that's gone when it
can't open it.

Facts about an item
~~~~~~~~~~~~~~~~~~~

Some links are facts about an item, rather than about an entity:
*this photo shows Alice and Bob* is about the photo.  Wikidata keeps
it on the file, as *depicts* (P180).  While a photo isn't an entity,
it can't hold statements, which leaves:

* storing it as metadata on the image item, in the Metadata service
  (see :doc:`metadata`), which the KB's design keeps for what isn't
  knowledge, such as tags and view state;
* turning it around, as a statement on Alice (*appears in: this
  photo*), which is awkward, and doesn't scale to a photo of a crowd;
  or
* giving the photograph an entity, with *depicts: Alice* and *image:
  the image item*, as Wikidata has an item for a painting, linked to
  its file: the knowledge on the entity, the behaviour on the item.

The last fits the line between knowledge and behaviour, and leads to
the question of which items should have entities.

Three Models
------------

A: Separate, as now
~~~~~~~~~~~~~~~~~~~

Items and entities stay separate.  Entities with kinds are items;
content items aren't entities.  The KB refers to content items with
the Item identifier datatype, and facts about an item go in its
metadata, or on an entity made for it by hand.

B: Everything is an entity
~~~~~~~~~~~~~~~~~~~~~~~~~~

Every item is a KB entity, and a type implementation is optional
behaviour on top.  An image would be an entity with *DarqOS type:
public.jpeg*, and ``types/image`` would keep its pixels in Storage.
This mostly generalises what's there: the KB kinds work this way now,
through the DarqOS type statement and ``types/kb``.

It gains:

* names and descriptions for every item: labels, descriptions and
  aliases, in any language, which is the *item name* the Metadata
  service's notes ask for;
* facts about any item: *depicts: Alice*, *author*, *date taken*,
  *source*, each with qualifiers, references and ranks, and queries
  that span everything: photos of Alice from 2019, documents by people
  at Acme;
* one kind of link, entity values, everywhere: no Item identifier
  datatype, and no separate entity for a photograph; and
* one lookup: ``FindLinks``, search and History would cover
  everything.

It costs:

* the KB as a dependency of every item: creating a text note would be
  a KB write as well as a Storage blob, the KB would be on hot paths,
  and boot order would matter more;
* scale: every email and file would be an entity.  The KB keeps each
  entity as a JSON document, and every revision of it: fine for tens
  of thousands of entities, less clear for a few million emails;
* two stores to keep in step: an entity's description in the KB and
  its content in Storage, so creating or destroying an item touches
  both, where now each item lives in one;
* a change to what identifies an item: an item identifier now includes
  its type.  If identity were the entity's, converting a JPEG to a PNG
  could keep the same item, which might be better, but it changes the
  rules in :doc:`item`, and every type implementation; and
* short-lived items, such as a calculation or a scratch note, filling
  the KB, unless they could opt out.

C: An entity when there's something to say
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Any item *can* have an entity, made when there's first something to
say about it: a name, *depicts*, an author.  An unremarkable email or
file never gets one.

* It avoids B's millions of entities, while every item can have
  knowledge once it matters.
* The link from an entity to its item is the Item identifier datatype:
  *this entity is that item*.  So C needs the datatype, as A does.
* It's roughly how Wikidata and Wikimedia Commons treat files: a file
  is described when someone describes it.
* Finding an item's entity, from the item, is a reverse lookup of the
  link (``FindLinks`` on its identifier), which the design indexes.

What Decides It
---------------

* **How many items there will be.**  If emails and files run to
  millions, B gets expensive, and C or A fit better.
* **Whether creating an item may depend on the KB** being up, and
  fast.
* **Whether an item's identity should outlive its format,** so that
  converting a JPEG to a PNG keeps the same item.
* **Who writes an item.**  An item's content is changed only through
  its type implementation (see :doc:`types`), which is what lets it
  publish each change to the Index.  Under A and C, the KB is one
  store among others, written for each kind by its implementation;
  under B, it would be every type's store, and every implementation
  would have to write its items' entities through the KB.
* **Where names live.**  Under B and C, an item's name is its entity's
  label, in any language; under A, it's metadata, or nothing.

Consequences
------------

* The Item identifier datatype is needed by A and C, and not by B.
  It waits until this is decided.  Its design is in the plan doc,
  with the questions it raised: the value type, the index, refusing
  the KB kinds' identifiers, its first properties (*image*, and
  perhaps *related item*), and how an identifier is entered (copying
  it from a lens, and pasting it, or a picker of recent items).
* The Metadata service's role depends on it too.  It's deferred to
  Third Edition, and may not be needed: the KB could hold tags (*has
  tag*), as well as names and facts, under B or C, leaving only what
  isn't knowledge, such as view state, which each type implementation
  could keep.

The Decision
------------

B: every item is an entity.  Working on books, e-books, and the
people, organisations and languages they refer to showed what the KB
gives an item, and where its absence hurts:

* a Copy's content, an Edition's cover, a URL's snapshot, and a
  message's attachment are content items, so they're referred to by
  identifier strings (``CONTENT``, ``COVER``, ``SNAPSHOT``,
  ``ATTACHMENT``), not links: nothing can be followed back from a PDF
  to the Copy it is, or from an image to the Edition it's the cover of;
* a content item has no name of its own: it's found by what its type
  reads from its content, such as a PDF's title, and nothing more can be
  said of it, such as a name when its content has none, or who an image
  depicts; and
* merging, relinking, Wikidata, and the entity lens's page and editing
  work for entities, and would work for every item.

Most of DarqOS works this way already.  People, places, organisations,
conversations, messages, URLs, and the book levels are entities with
kinds, served by ``types/kb``: 71,000 of them, of which 66,000 are
Slack messages.  What isn't is the four types that keep their content
in Storage: text, images, PDFs and e-books, a few hundred items.

Review
~~~~~~

The costs listed under B, above, as they look now:

* **Scale.**  ``bench/kb_scale.py`` grew a store to a million
  entities, mostly emails, shaped like B.  Getting an entity took
  0.04 ms, finding one by label 0.02 ms, a write about 0.9 ms, a
  two-word search 32 ms, and the first batch of a large group of
  links about 1 ms.  What's slow is a query of several links, emails
  from an organisation's staff, at 6 seconds, and listing hundreds of
  thousands of entities in full, at 3 to 9 seconds; and its size,
  8.5 KB an entity, of which documents and revisions are 40%.  The
  first is answered, should it matter, by a SPARQL engine alongside the
  KB, as a derived index for costly queries, such as QLever, which is
  built for Wikidata's model, as the KB is.  The second, by
  compressing or pruning revisions.  Neither is needed for B.
* **The KB as a dependency of every item.**  It's already one for
  every item that isn't text, an image, a PDF or an e-book, and for B,
  the Book card and Search.  The p-kernel spawns it early.  Creating a
  content item is a Storage write and a KB write, about a millisecond
  more.
* **Two stores in step.**  A content item's bytes stay in Storage, and
  its entity says what it is.  Its type implementation creates the blob
  first, and the entity second, so a failure leaves at worst a blob with
  no entity, which a tidy can find; and destroying marks the entity
  destroyed, as now.
* **Identity.**  An item identifier stays a type and a specifier (see
  :doc:`item`), and an item's specifier is its entity's identifier, as
  it is for the KB's kinds.  So a JPEG converted to a PNG is a new item,
  as now: changing that is a separate question (see Open Questions).
* **Short-lived items**, such as calculations, are entities too, and
  are destroyed like any other.  Their numbers are small.

Rules
~~~~~

* Every item has an entity.  Its item identifier is its kind and its
  entity's identifier: ``public.jpeg:03d1824d…``, as
  ``darq.person:0028db09-…`` is.
* A content item's entity's identifier is its Storage key, in hex, which
  is already its specifier.  So the identifiers of the items there are
  don't change, and nor do History's events, the Index's entries, or
  what refers to them.  It's how an item's bytes are found from its
  entity, and its entity from its bytes: no statement says it.  So every
  type that keeps its content in Storage does this, for its new items as
  well as its old: the blob's created first, so its key is there before
  the entity is.  Only a type whose content is all knowledge, as the
  KB's kinds' is, uses a UUID, as ``types/kb`` does.
* An item's entity records its kind with its DarqOS type statement, as
  the KB's kinds do, and has a label, its name, where it has one: a
  text's first line, an image's or PDF's file name, an e-book's title.
* Everything about an item, its content and its knowledge, is written
  through its type implementation, so it's what keeps the item's Index
  entry and History up to date, and nothing's done twice (see
  :doc:`types`).
* Every type implementation *inherits* the entity type's methods, as
  every type conforms to ``darq.entity``:

  * its interface: it serves the ``EntityItem`` protocol, on a second
    port for an item, from ``OpenEntity``, beside its own, from
    ``Open``.  A port speaks one protocol, and DIDL has no inheritance,
    so it's a second port, as COM's ``QueryInterface`` gives another of
    an object's interfaces.  DIDL may come to compose protocols, so that
    one port serves both, as every COM interface extends ``IUnknown``;
  * its implementation: ``darq.entity_type``'s ``EntityType``, a base
    class, with what was ``types/kb``'s: reading and writing an item's
    entity, the names of what it links to, its links, found a batch at
    a time, and searching for entities to link to.  It calls hooks after
    each change, which each implementation overrides, to publish its
    Index entry; and
  * ``types/kb`` is the plain implementation, for the kinds whose whole
    content is knowledge: its hooks publish their names and
    descriptions.  ``types/text``, ``types/image``, ``types/pdf`` and
    ``types/ebook`` add their content's protocols, and publish what's
    in it too.

  So the entity lens opens any item's entity with ``OpenEntity``, on
  the item's own implementation, which sees what's changed.
* An entity refers to an item with an entity value, a link, so it's
  followed, and found backwards, as any link is.  The Item identifier
  datatype (see above) isn't needed.
* What isn't knowledge, such as a reader's position, stays with the
  content, in its blob's header, kept by its type implementation.

The Plan
--------

In steps, each of which leaves the system working, and is tested,
measured, and documented as it's built.

1. **Foundations** (built).  A module, ``darq.items``, for type
   implementations:
   making a content item's entity, with its kind, label and source,
   alongside its blob; reading it; renaming it; and destroying it.
   The KB vocabulary for content items: a local *created* time, and
   *file name*, as imports had it, and Wikidata's classes of files.
   The kinds of the content types (``public.utf8-plain-text``,
   ``public.jpeg`` and the others, ``com.adobe.pdf``,
   ``darq.book.epub`` and the others) known to the KB, so ``kind_of``
   and the entity lens know them (see :doc:`kb-service`), and the write
   rule recorded in :doc:`types`.
2. **The entity type, inherited.**  ``darq.entity_type``, the lower
   layer, taken from ``types/kb``, which becomes a layer above it;
   ``OpenEntity`` in the ``Implementation`` protocol; and the entity
   lens opening entities with it.  Then ``types/text``, ``types/image``,
   ``types/pdf`` and ``types/ebook`` inherit it, make an entity for each
   item they create, and name it.  Their items' pages, in the entity lens, show
   it, and what links to it.  Imports, from the filesystem, by URL, and
   from Slack, give it its file name.
3. **Existing items are entities.**  A migration, run once at start, as
   relinking is, makes an entity for each content item there is,
   identified by its Storage key, and named from what's known of it:
   its file name, from History's ``imported`` events or what imported
   it; an e-book's title, from its own metadata; a text's first line.
   It's a tool too, to be run again, and reports what it couldn't
   name.
4. **Links, not strings.**  ``CONTENT``, ``COVER``, ``SNAPSHOT`` and
   ``ATTACHMENT`` become item properties, and their values links,
   rewritten by a migration, as merges are relinked.  So an image's
   page shows it's the cover of an Edition, a PDF's that it's a Copy,
   and a file's the message it was attached to, and B's catalogue
   finds a Copy by a link, not a string.
5. **Names everywhere.**  History, Search and the switcher show an
   item's label, not its identifier, and the Index publishes labels as
   names.  A lens shows its item's name, and renames it: the image lens,
   the PDF and e-book readers, and the text lens.  A key in each opens
   the item's page, its entity, in the entity lens, to say more of it.
6. **Facts about items.**  *Depicts* (P180) on an image's entity, and
   the vCard import's PHOTO as image items, linked from their people, as
   *image* (P18).  Tags as statements, *has tag*, brought forward from
   Third Edition's Metadata question.
7. **What it replaces.**  The Item identifier datatype's design, in the
   plan doc, and the Metadata service, in Third Edition (see
   :doc:`metadata`): names and tags are statements, and view state is
   each type's.  :doc:`item`, :doc:`types`, :doc:`kb-service`,
   :doc:`metadata` and :doc:`roadmap` say so.

Steps 1 and 2 are the bulk of it; 3 and 4 are migrations of the kind
already done for merges; 5 and 6 are per lens and per import; and 7 is
documentation.

Open Questions
~~~~~~~~~~~~~~

* **Identity across formats.**  Whether a converted item keeps its
  identity: the entity could, with a new kind, if an item identifier
  didn't include its type, but History, the Index, and the Type
  service's activation all use the type in it.  Left as it is, for now.
  Books already answer the commoner case, one book as a PDF, an EPUB
  and a paper copy: they're three items, each a Copy of an Edition of
  the same Work (see :doc:`book`).
* **Which properties a content item has.**  Beyond its name, kind and
  created time: size, dimensions, page count, and an image's EXIF, or
  only what's said of it by a person, or a source such as Wikidata?
  Each type's, decided as it's built.
* **Events and the REPL**, still to be designed, should assume B: an
  event, and a REPL's object, can be about any item, as an entity.
