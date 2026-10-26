Items
=====

In DarqOS, *items* are things that, for instance, would usually be a
file in Unix-style operating systems.  They are not, typically, small
things like a programming language object (like, an integer or string),
but larger things like a document, a spreadsheet, an image, etc. They
are also used to represent real-world items, like people or places.

Items are identified using an *Item Identifier* (aka *iid*).
An item identifier is a string, with two parts: a type name and an
identifier, separated by a colon character.

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

For more information on UTIs, see `Types <types.rst>`_.

Item Metadata
---------------

The `Metadata Service <metadata.md`_ can be used to store additional
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
