Types
=====

Types are a fundamental aspect of DarqOS.  A *Type* describes an
*Item*, classifying it by its properties and behaviour.  Types can
be both general and specific, with a rich conformance model that
allows generalisation of entire classes of entities.

Example types are: person, book, text document, PNG image, etc.

Strictly, a *type* is an abstract concept; the concrete artifacts
are a *type identifier*, a *type description*, and a *type
implementation*.

Type Identifiers
----------------

A *type identifier* is a globally-unique, string identifier for a *type*
concept.  Type identifiers are derived from IETF MIME types, and Apple's
Uniform Type Identifiers, but do not conform to either of those
specifications directly.

A type identifier is

  * A UTF-8 string
  * May contain ASCII characters A-Za-z0-9, hyphen, dot, and any
    Unicode codepoint over U+007f.
    * Of note, that means it cannot contain a colon or a forward-slash.
  * Must be less than 128 characters in length
  * Typically have a dotted string format, eg. "public.item"

The set of basic types described by Apple's Uniform Type Identifier system
is used as the basis for DarqOS types.  That set is described in Apple's
`System-Declared Uniform Type Identifiers <https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/UTIRef/Articles/System-DeclaredUniformTypeIdentifiers.html#//apple_ref/doc/uid/TP40009259-SW1>`_ document.

Type Service
------------

Types are defined as part of the persistent state of the system.  This
state is owned and managed by the Type Service.  Programs access the
Type Service via its port, registered with the p-kernel as "darq.type".

The Type Service maintains four mappings:

* Type identifier to type description
* Type identifier to type implementation
* Type identifier to active type implementation instance
* Type identifier + context to zero or more lenses

Type Descriptions
-----------------

Type descriptions consist of:

* Zero or more other types to which this type conforms (to use the
  UTI terminology).  Note that this isn't a simple inheritance tree:
  types can conform to many other types.
* Zero or more MIME types that are equivalent
* Zero or more file extensions that indicate equivalence
* An icon to represent instances of this type
* A reference to the implementation for this type

  * For 1ed, this will be a path to the type implementation's
    directory, suitable for spawn()ing by the p-kernel.
  * In future editions, it should be a storage key for the
    implementation's executable.

These are stable, persistent properties of the type.

Type Implementations
--------------------

A *Type Implementation* is a program that implements the behaviour for
items of a type as represented within DarqOS.  The type implementation's
API can be used by programs to manipulate the item's properties, and by
*Lenses* to present the item's properties to the system operator via the
Terminal Service.  Type implementations are one of the major classes of
application for the system.

The Type Service maintains a dynamic mapping of type identifiers to
running Type Implementations.  A Type Implementation program is started
as a DarqOS process.  The can be at most one instance of a Type
Implementation active at any time; multiple Items of a Type are
represented by a single Type Implementation.

The Type Service maps the Type Identifier to the Darq *port* for each
running Type Implementation.

Note that a Type Implementation may use multiple processes in its
implementation of the type's behaviour.  The constraint is just that
there is a single port and process that acts as the reference point,
and distributes the per-Item ports.

New Type Implementations may be registered at runtime, and existing
Type Implementations may be removed or replaced.  The registrations
are persistent across system restarts.

Type Instances
--------------

An instance of a Type is called an *Item*.  Type Implementations
manage a collection of Items, and expose them to programs, and (via
Lenses) to operators.

An Item Identifier consists of a Type Identifier, a colon, and a
type-specific identifier for the specific Item, like

  `net.daringfireball.markdown:c5f1ad2a-ea33-4248-b116-9c363cbade66`

Items are described in more detail in `Items <item.rst>`_.

A Type Implementation supports the creation, use, and destruction
of items.

Item Creation
~~~~~~~~~~~~~

To create type instances, or *Items*, a program should first obtain
the port for the Type Implementation by requesting it from the Type
Service using the Type Identifier.  The Type Service consults its
registry of types, and if the Type Implementation is not already
running, it is started, and either way, its port is returned.

The program can then request, via that port, that the Type
Implementation create a new instance.  The implementation will
return an Item Identifier for the newly created instance, and a
port through which the program (possibly a Lens implementation) can
manipulate the item.

Item Use
~~~~~~~~

An existing Item can be opened similarly: get the port for the
Type Implementation from the Type Service, and then ask it to
open the Item using its Item Identifier, and get a port that
addresses the Type Implementation and the specific Item.

Item Destruction
~~~~~~~~~~~~~~~~

A program may request that a Type Implementation destroy an Item.

A fundamental characteristic of DarqOS is that items have lots of
linkage and annotation throughout the system: metadata, index,
history, etc, all enmesh items.  As a result, literal destruction
of an item would cause either a rewriting of history, update of
indexes, etc, or an acceptance of broken linkage.

Consequently, Darq does not support destruction in the literal
sense.  Instead, items may be marked as destroyed, but all the
web of their existence remains.

In some cases, where the storage requirements are large, two
different approaches might be taken:

* The item's content might be compressed, reducing storage
  requirements, or off-loaded to long-term storage media.  Such
  items may not be immediately accessible.
* In extreme cases, the item's content might be replaced by
  a "tombstone".  Then item can still be found, but attempts
  to access it will return an error indicating that the
  content is no longer maintained.

This choice is made and implemented by the Type Implementation,
and is not expected to be implemented in early editions.

Lenses
------

A Type Implementation provides the *behaviour* of type instances:
basically, the code.  It also determines how instances of the type
are persisted (for example, using the storage service, or the knowledge
base service).

Access to an item's behaviour is via the IPC system.  The type implementation
essentially exports an API for instances of the type, operating on specific
instance data loaded from and saved to a persistence backend.

But ... this is just the actual behaviour of the item: the "model" in
MVC terms.  In addition to type implementations, there is a second class
of applications in DarqOS: *Lenses*.

A *lens* is a way to use an item of a type.  For example, Lenses expose
the item's data to its operators via the Terminal service.

Contexts
--------

A Lens is activated in a *Context*.  There is initially only one context
defined: the Terminal context.

The Terminal context is the full DarqOS graphical shell, with high
resolution bit-mapped graphics, a keyboard, a pointer, a set of
pointer-relative actions (clicks), mono audio input, and stereo
audio output.

Additional contexts might be added in future, including things like
Jupyter Workbooks, Alexa-style voice interfaces, etc.

Lens Activation
---------------

Within a Terminal Service, the user has several means of managing
Item Identifiers, and requesting actions upon them.  These requests
combine:

* a Context
* an Item Identifier (which includes the item's Typa Identifier), and,
* an action.

The terminal must process that request and determine (with or without
help from the user) which of its available Lenses should be used.

The collection of Lenses, and their supported types and contexts,
is maintained by the Type Service.

References
==========

* https://en.wikipedia.org/wiki/Uniform_Type_Identifier
* https://developer.android.com/guide/components/intents-common
* https://developer.android.com/guide/components/intents-filters
* https://developer.android.com/reference/android/content/Intent
* https://developer.apple.com/documentation/uniformtypeidentifiers
* https://developer.apple.com/documentation/uniformtypeidentifiers/defining_file_and_data_types_for_your_app
* https://developer.apple.com/documentation/uniformtypeidentifiers/uttype
* https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/understanding_utis/understand_utis_intro/understand_utis_intro.html
* https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/UTIRef/Articles/System-DeclaredUniformTypeIdentifiers.html
