Items
=====

In DarqOS, *items* are things that, for instance, would usually be a
file in Unix-style operating systems.  They are not, typically, small
things like a programming language object (like, an integer or string),
but larger things like a document, a spreadsheet, an image, etc. They
are also used to represent real-world items, like people or places.

Items are identified using an *Item Identifier* (aka *iid*).
An item identifier is a string, with two parts: a type name and an
identifier specifier, separated by a colon character.  A type
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

Item state is often stored in the Storage Service, as a blob of bytes,
or in the Knowledge Service, as a graph of assertions and relationships.

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

Item Metadata
-------------

The :doc:`Metadata Service <metadata>` can be used to store additional
information about items.  This includes things like a canonical,
human-oriented name for the item, any tags the operator
might associate with it, etc.

Item Storage
------------

Different types can have different storage requirements.  Things
that are stored as files in a Unix-like operating system are usually
implemented to use the Storage Service to persist their state.  This
includes things like plain or formatted text documents, source code,
images, videos, sounds and songs, CAD diagrams, etc.

Other items, for instance person, group, or location items from an
address book application, are represented using a generic knowledge
graph service.

The *iid*'s type name provides the link between code that uses an
item and its stored state.  The system itself knows nothing about how
the item is stored.

Using Items
-----------

It's important to distinguish between the Item's state, the Type
Implementation that interprets it, and the *Lens* that presents
the item's properties to the system operator.  A typical Unix or
Windows application is broken into one or more lenses and type
implementations in DarqOS.

References
==========

* https://www.unicode.org/reports/tr31/ (UAX#31: Unicode Identifiers and Syntax)
* https://url.spec.whatwg.org/ (WHATWG URL Standard)
