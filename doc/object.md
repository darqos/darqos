# Objects

In DarqOS, _objects_ are things that, for instance, would usually be a
file in Unix-style operating systems.  They are not, typically, small
things like a programming language object (like, an integer or string),
but larger things like a document, a spreadsheet, an image, etc. THey
are also used to represent real-world objects, like people or places.

Objects are identified using an _object reference_ (aka _objref_).
An object reference is a string, with two parts: a type name and an
identifier, separated by a colon character.

The type of the object determines its functionality.  Type
implementations are installed in the system, and enable the use of
objects of their supported type(s).  The type implementation determines
where and how the state of an object is stored, and provides methods to
view and modify that state.

Object state is often stored in the Storage Service, as a blob of bytes,
or in the Knowledge Service, as a graph of assertions and relationships.

## Object Types

The types of objects are described using _Uniform Type Identifiers_. The
UTI system was defined by Apple as a way of unifying MIME types, MacOS
Classic Type and Creator codes, and file extensions into a consistent
hierarchy of common object types.  In addition to basic identification,
UTIs describe sub-type conformance, representing, for example that a
PNG also conforms to the type of Image, which in turn conforms to the
Content type.

For more information on UTIs, see:
 * https://en.wikipedia.org/wiki/Uniform_Type_Identifier
 * https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/understanding_utis/understand_utis_intro/understand_utis_intro.html
 * https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/UTIRef/Articles/System-DeclaredUniformTypeIdentifiers.html

## Object Metadata

The [Metadata Service](metadata.md) is used to storage additional
information about objects.  This includes things like a canonical,
human-oriented name for the object, any tags the user
might associate with it, etc.

## Object Storage

Different object types can have different storage requirements.  Things
that are stored as files in a Unix-like operating system are usually
implemented to use the Storage Service to persist their state.  This
includes things like plain or formatted text documents, source code,
images, videos, sounds and songs, CAD diagrams, etc.

Other objects, for instance person, group, or location objects from an
address book application, are represented using a generic knowledge
graph service.

The _objref_'s type name provides the link between code that uses an
object and its stored state.  The system itself knows nothing about how
the object is stored.

## Using Objects

It's important to distinguish between the object's state, the type
implementation that interprets it, and _lens_ that presents the object's
properties to the user.  A typical Unix or Windows application is
broken into one or more lenses and type implementations in DarqOS.
