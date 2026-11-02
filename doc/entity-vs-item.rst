Entities and Items
==================

*An open design question: not yet decided.*

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
