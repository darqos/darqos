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
concept.  Type identifiers are derived from IETF MIME types and Apple's
Uniform Type Identifiers, but do not conform to either of those
specifications directly.

A type identifier is

  * A UTF-8 string
  * Stored and compared in its Unicode NFKC_Casefold form, so type
    identifiers are case-insensitive, and canonically lower-case.  The
    rules below apply to that form.
  * The first character must be from the set:

    * Unicode UAX#31 core property XID_Start
  * Subsequent characters must be from the set:

    * Unicode UAX#31 core property XID_Continue
    * U+002D (hyphen)
    * U+002E (period)
    * U+002F (forward slash)
  * Never contains a colon (U+003A), so that an Item Identifier can be
    split into its parts at its first colon (see :doc:`item`)
  * Less than 128 bytes in length, after NFKC_Casefold (NOT including
    any terminating U+0000)
  * Typically have a dotted string format, eg. "public.item"

The ``darq`` module provides functions to check type identifiers, and
to convert them to their canonical, NFKC_Casefold form:
``is_valid_type_id()`` and ``canonical_type_id()``.  Programs should
canonicalize a type identifier before storing or comparing it.  The
functions for item identifiers are described in :doc:`item`.

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

The first two, and the lenses, are the *catalogue*: they persist across
restarts.  The running implementations' ports last only as long as the
implementations do, so they're kept in a separate table.

Its protocol, `Type` in `lib/didl/type.didl` (generated as
`darq.protocols.type`), has these methods:

* `Register(type_id, description, implementation)` adds or replaces a
  type's description, and its implementation (a directory path to
  spawn, or empty for none).  The service updates its in-memory tables,
  and saves the catalogue, before it answers.  A registration that
  changes nothing saves nothing.
* `Deregister(type_id)` removes both.
* `Describe(type_id)` returns them.
* `Activate(type_id)` returns the implementation's port, starting it if
  needed (see Activation, below).
* `RegisterLens(lens, type_id, context)`, `DeregisterLens(...)` and
  `FindLenses(type_id, context)` manage and look up lenses (see Lens
  Activation, below).

Each method's result has a `Status`: `OK`, `NOT_FOUND`, `INVALID_ID`, or
`FAILED` (an implementation couldn't be started).  Type identifiers in
requests, including those in a description's conformance list, are
canonicalized with `canonical_type_id()`: any case or compatibility
form can be used, but the canonical form is what's stored and returned.

Catalogue
~~~~~~~~~

The catalogue is saved as JSON in a Storage blob.  The blob's key is
kept in the boot vector, as `darq.type.catalogue`, so the Type Service
must be spawned by the p-kernel, after the Storage service.  On a fresh
system there's no key: the blob is created on the first change.

The catalogue is filled by the *type sync*, `tools/type-sync`, run at
boot after the Type Service.  It registers:

* The base catalogue (`tools/type-sync/catalogue.json`): descriptions
  of the common types, from Apple's Uniform Type Identifiers, with their
  conformance, and no implementations.
* The types each installed type implementation describes in a
  `type.json` manifest in its directory (under `/darq/types`).  Those
  marked `"implemented": true` are registered with that directory as
  their implementation.
* The lenses each installed lens declares in a `lens.json` manifest in
  its directory (under `/darq/lenses`): the types it's for, and its
  context.

Registering replaces what was there, so a changed manifest takes
effect at the next boot.  Nothing is removed: a type or lens that's no
longer installed stays in the catalogue until it's deregistered.

Type Descriptions
-----------------

Type descriptions consist of:

* Zero or more other types to which this type conforms (to use the
  UTI terminology).  Note that this isn't a simple inheritance tree:
  types can conform to many other types.
* Zero or more MIME types that are equivalent
* Zero or more file extensions that indicate equivalence
* An icon to represent instances of this type
* A short name, and a longer description

These are stable, persistent properties of the type.  The type's
implementation is registered separately, since it can be replaced at
runtime:

* For 1ed, it's a path to the type implementation's directory,
  suitable for spawn()ing by the p-kernel.
* In future editions, it should be a storage key for the
  implementation's executable.

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
as a DarqOS process.  There can be at most one instance of a Type
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

Activation
~~~~~~~~~~

`Activate` starts a type's implementation if it isn't running:

* The Type Service opens a *bootstrap port*, and spawns the
  implementation with it (see :doc:`ipc`).  The `Activate` reply is
  deferred, as is any other `Activate` for the type that arrives
  meanwhile.
* The implementation opens the port it will serve the `Implementation`
  protocol on, and reports it with a one-way `Ready(implementation)`
  (protocol `Bootstrap`) on the port `darq.get_bootstrap_port()`
  returns.
* The Type Service then records the port, asks to be notified when it's
  destroyed (`darq.notify_on_death`), and answers the waiting
  `Activate` requests with it.
* If the implementation can't be spawned, or doesn't report `Ready`
  within 30 seconds, they're answered with `FAILED`.  A late `Ready`
  fails, since the bootstrap port is closed by then, and the
  implementation should exit.

When an implementation's port is destroyed, which happens when it exits
or crashes, the Type Service forgets it.  It isn't restarted until
something next asks for it with `Activate`.

Every implementation serves the `Implementation` protocol (also in
`lib/didl/type.didl`):

* `Create()` returns the new item's identifier, and a port for it.
* `Open(item_id)` returns a port for an existing item.  An implementation
  must check that the identifier's type is its own: an item of another
  type, including a conforming subtype, is `NOT_FOUND`.
* `Destroy(item_id)` marks it destroyed (see Item Destruction, below).

Type Instances
--------------

An instance of a Type is called an *Item*.  Type Implementations
manage a collection of Items, and expose them to programs, and (via
Lenses) to operators.

An Item Identifier  (aka *iid*) consists of a Type Identifier, a
colon, and an *item specifier* for the specific Item, like

  ``net.daringfireball.markdown:c5f1ad2a-ea33-4248-b116-9c363cbade66``

Items are described in more detail in :doc:`item`.

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

Every item port serves the methods of the `Item` protocol, numbered
from 1, alongside the type's own methods, numbered from 16: each type's
item protocol starts with them.  For now, that's just `Close()`.  A
client closes an item when it's done with it, so the implementation
knows it can save and forget the item, and close its port.

Closing is explicit because the p-kernel can't yet tell a port's owner
when the last send right to it has gone.  Mach calls this a *no-senders
notification*.  Once the p-kernel has one, an implementation can use
it to notice clients that crash, or forget to close.

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
* an Item Identifier (which includes the item's Type Identifier), and,
* an action.

The terminal must process that request and determine (with or without
help from the user) which of its available Lenses should be used.

In 1ed, there's only one action, "use", so the action plays no part in
choosing a lens.

The collection of Lenses, and their supported types and contexts,
is maintained by the Type Service.  A lens is identified by the path of
its directory, and registered (with `RegisterLens`) for a type and a
context: the context's name is "terminal" for the Terminal context.

`FindLenses(type_id, context)` returns the lenses registered for the
type in the context.  If there are none, it falls back to more general
lenses: those of the types it conforms to, and then the types they
conform to, and so on, returning the nearest that have any.  So a
`public.utf8-plain-text` item with no lens of its own is shown with a
`public.plain-text` lens if there is one, otherwise a `public.text`
lens, and so on.  Types that are equally near, such as `public.text`'s
`public.data` and `public.content`, are searched together.

References
==========

* https://www.unicode.org/reports/tr31/ (UAX#31: Unicode Identifiers and Syntax)
* https://en.wikipedia.org/wiki/Uniform_Type_Identifier
* https://developer.android.com/guide/components/intents-common
* https://developer.android.com/guide/components/intents-filters
* https://developer.android.com/reference/android/content/Intent
* https://developer.apple.com/documentation/uniformtypeidentifiers
* https://developer.apple.com/documentation/uniformtypeidentifiers/defining_file_and_data_types_for_your_app
* https://developer.apple.com/documentation/uniformtypeidentifiers/uttype
* https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/understanding_utis/understand_utis_intro/understand_utis_intro.html
* https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/UTIRef/Articles/System-DeclaredUniformTypeIdentifiers.html
