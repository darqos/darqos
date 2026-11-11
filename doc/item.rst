Items
=====

*Items* are the things DarqOS holds: what would usually be a file in
other operating systems, a document, a spreadsheet, an image, and also
things in the world, people, places and organisations, and what's
known of them.  They are not, typically, small things like a
programming language's objects, an integer or a string.

Terms
-----

Item
  Anything in DarqOS, with an item identifier, a type, and an entity.
  History, the Index and Search, and the Terminal deal in items.

Entity
  An item's record in the Knowledge Base (see :doc:`kb-service`): its
  labels, descriptions and aliases, in any language, and its statements,
  which link it to other items.  Every item has one.  The entity lens
  shows and edits it.

Content item
  An item with content of its own, kept in Storage: a text, an image, a
  PDF, an e-book, or data.  Its type reads and writes the content, and
  its entity says what's known of it.

Kind
  An item's type, as its entity records it, its *DarqOS type*: the KB's
  kinds, ``darq.person``, ``darq.place`` and the others, and the content
  types, ``public.jpeg`` and the others.

Thing
  An item of the plain kind, ``darq.thing``: one with nothing more
  specific to be, such as a class, a given name, or a unit.  Its entity
  is all there is of it.

Item Identifiers
----------------

Items are identified using an *Item Identifier* (aka *iid*).
An item identifier is a string, with two parts: a type identifier and an
item specifier, separated by a colon character.  A type
identifier never contains a colon, but an item specifier may (so it
can hold a URL, for instance): an item identifier is parsed by
splitting it at its first colon.

The type identifier is defined in :doc:`types`.  The item
specifier constraints are:

* A non-empty UTF-8 string
* Normalized using Unicode NFKC normalization, but case-sensitive
* Characters must be from the set:

  * Unicode UAX#31 core property XID_Continue, excluding
    default-ignorable code points (which are invisible, like
    U+200D ZERO WIDTH JOINER)
  * Printable ASCII, U+0021 (``!``) to U+007E (``~``), which includes
    U+003A (colon)

  This rules out spaces, controls, and other invisible or
  formatting characters, so an item identifier can be safely logged
  or displayed.  Unlike a type identifier, there's no special rule
  for the first character.
* A URL used as an item specifier must be in its serialized form,
  as produced by the WHATWG URL Standard's serializer: non-ASCII host
  names are converted to Punycode, and other non-ASCII characters are
  percent-encoded.  The serialized form is printable ASCII.
* At most 895 bytes in length (NOT including any terminating U+0000)

  * Thus making the entire *iid* a maximum of 1023 bytes: at most 127
    for the type identifier, 1 for the colon, and 895 for the item
    specifier, so it fits in 1024 bytes with a terminating U+0000

The ``darq`` module provides functions to check and canonicalize
item identifiers, and to serialize URLs: ``is_valid_item_id()``,
``canonical_item_id()``, ``split_item_id()``, ``make_item_id()``, and
``serialize_url()``.  The functions for type identifiers are described
in :doc:`types`.

The type of the object determines its functionality.  Type
implementations are installed in the system, and enable the use of
items of their supported type(s).  The type implementation determines
where and how the state of an item is stored, and provides methods to
view and modify that state.

An item's content, if it has any, is usually stored in the Storage
Service, as a blob of bytes, and its entity in the Knowledge Base, as
statements and links.

Item Types
----------

The types of items are described using *Uniform Type Identifiers*. The
UTI system was defined by Apple as a way of unifying MIME types, MacOS
Classic Type and Creator codes, and file extensions into a consistent
hierarchy of common types.  In addition to basic identification,
UTIs describe sub-type conformance, representing, for example that a
PNG also conforms to the type of Image, which in turn conforms to the
Content type.

For more information on UTIs, see :doc:`types`.

Entities
--------

Every item has an entity, made with it by its type.

* Its item identifier is its kind and its entity's identifier:
  ``public.jpeg:03d1824d…``, as ``darq.person:0028db09-…`` is.
* A content item's entity's identifier is its Storage key, in hex, which
  is its specifier.  It's how an item's bytes are found from its entity,
  and its entity from its bytes: no statement says it.  So a type that
  keeps its content in Storage creates the blob first, so its key is
  there before the entity is.  A type whose content is all knowledge,
  as the KB's kinds' is, uses a UUID, as ``types/kb`` does.
* Its entity records its kind with its *DarqOS type* statement, and has
  a label, its name, where it has one: a text's first line, an image's
  or PDF's title or file name, an e-book's title.
* Everything about an item, its content and its entity, is written
  through its type implementation, so it's what keeps the item's Index
  entry and History up to date (see :doc:`types`).
* Every type implementation inherits ``ItemType``, from
  ``darq.item_type``: it serves the ``EntityItem`` protocol for an
  item's entity, on a second port, from ``OpenEntity``, beside the port
  from ``Open`` that serves its type's own protocol, since a port speaks
  one protocol, and DIDL has no inheritance.  It reads and writes the
  entity, gives the names of what it links to, finds its links a batch
  at a time, searches for entities to link to, and records in History
  what's done (see :doc:`history`).  Each implementation publishes its
  items to the Index when they change: ``types/kb``, for its kinds,
  their names and descriptions, and a content type what's in the
  content too.
* A statement refers to an item with an entity value, a link, so it's
  followed, and found backwards, as any link is.
* What isn't knowledge, such as where a reader's got to, is view state,
  kept by its type in the item: for a content item, in its blob's
  header.
* Short-lived items, such as calculations, have entities too, and are
  destroyed like any other.

Facts About an Item
-------------------

What's known of an item is said on its entity:

* **Its name**, a label, in any language.  An item with none is named
  by its kind and when it was made, *Text File, 8 Nov 2026*
  (``darq.ui.names``), and History, Search, and the lenses' window
  titles, for the switcher, show items by name.
* **Its kind**, and **when it was made**, a local *created* property,
  and, for what came from a file, its *file name*.
* **Its tags**, with *has tag*, linking to tags, items of their own
  kind, ``darq.tag``, whose entities list what has them, as *tag of*
  (``darq.items.tag``).
* **What a picture depicts**, with Wikidata's *depicts* (P180), and
  **the picture of something**, with *image* (P18): a person's entity
  lists the pictures they're in, as *depicted in*.
* **What it's part of**, as links: a Copy's *content* is its PDF or
  e-book, an Edition's *cover* is an image, a URL's *snapshot* is what
  was fetched, and a message's *attachment* is a file, each read
  backwards too, as *content of*, *cover of* and so on.

An item's entity is opened with *Sys-i*, from the focused window's
item: the Terminal asks the window's lens what it's showing, through
the Lens protocol's ``Shown`` (see :doc:`terminal`), and opens that
item's entity in the entity lens, where it's renamed, tagged, and
anything else is said of it.  What happened to it is in History.

Item Storage
------------

Different types can have different storage requirements.  Things
that are stored as files in a Unix-like operating system are usually
implemented to use the Storage Service to persist their state.  This
includes things like plain or formatted text documents, source code,
images, videos, sounds and songs, CAD diagrams, etc.

Other items, for instance people, groups, or places from an address
book, are all knowledge: their entities in the Knowledge Base are all
there is of them.

The *iid*'s type identifier provides the link between code that uses an
item and its stored state.  The system itself knows nothing about how
the item is stored.

Whatever the store, an item's state is changed only through its type
implementation, never by writing the store directly (see
:doc:`types`): so the implementation sees every change, to index it,
say.

Using Items
-----------

It's important to distinguish between the item's state, the Type
Implementation that interprets it, and the *Lens* that presents it to
the operator.  A typical Unix or Windows application is broken into one
or more lenses and type implementations in DarqOS.

An item opens in its type's own lens, such as the text lens for a text,
or the book lens for a book.  The entity lens is the lens for
``public.item``, which every type conforms to, so it's the lens of a
thing, and of any item whose type has no lens of its own, as data's
hasn't; and it's where *Sys-i* shows any item's entity.

Older Items
-----------

An item made before every item had an entity is given one when it's
opened, or its entity is, by its type (``adopt``), named by its
content's title, if it has one.  The Give Items Entities tool
(``tools/item-entities``), from the Terminal's Tools panel (*Sys-t*),
does that for every content item History has events for, and every one
the KB refers to, names those still unnamed from what referred to them,
and turns references to them, as identifiers in text, into links.
References that are still text, to items that are gone, or files that
aren't items, are kept in the old properties, labelled *(identifier,
old)*, or *attached file*, and shown as links to the items.

Open Questions
--------------

* **Identity across formats.**  Whether a converted item keeps its
  identity: its entity could, with a new kind, if an item identifier
  didn't include its type, but History, the Index, and the Type
  service's activation all use the type in it.  Books answer the
  commoner case, one book as a PDF, an EPUB and a paper copy: they're
  three items, each a Copy of an Edition of the same Work (see
  :doc:`book`).
* **Which facts a content item has.**  Beyond its name, kind and
  created time: size, dimensions, page count, and an image's EXIF, or
  only what's said of it by a person, or a source such as Wikidata?
  Each type's, decided as it's built.

References
==========

* https://www.unicode.org/reports/tr31/ (UAX#31: Unicode Identifiers and Syntax)
* https://url.spec.whatwg.org/ (WHATWG URL Standard)
