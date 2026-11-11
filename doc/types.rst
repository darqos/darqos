*Items*, *Types* and *Lenses* form the basis of most Darq applications.

Types
=====

*Items* are the most basic elements of the use of Darq.  They most
closely correspond to files in traditional operating systems, but can
also be virtual placeholders for physical things.  Examples of Darq
Items could include, a person, a book, a Word document, or a GIF
image.

A *Type* describes an *Item*, classifying it by its properties and
behaviour.  Types can be both general and specific, with a rich
conformance model that allows generalisation of entire classes of
entities.

Strictly, a *type* is an abstract concept; the concrete artifacts
are a *type identifier*, a *type description*, and a *type
implementation*.


Type Identifiers
----------------

A *type identifier* is a globally-unique string identifier for a
*type* concept.  Type identifiers are derived from IETF MIME types and
Apple's Uniform Type Identifiers, but do not conform to either of
those specifications directly.

A type identifier

* is a UTF-8 string;
* stored and compared in its Unicode NFKC_Casefold form, so type
  identifiers are case-insensitive, and canonically lower-case.  The
  rules below apply to that form;
* must have its first character from the Unicode UAX#31 core property set `XID_Start`;
* must have its Subsequent characters from the set

  * Unicode UAX#31 core property `XID_Continue`,
  * U+002D (hyphen),
  * U+002E (period), or
  * U+002F (forward slash);
* cannot contain a U+003A (colon);
* be Less than 128 bytes in length, after NFKC_Casefold (NOT including
  any terminating U+0000); and,
* typically have a dotted string format, eg. "public.item".

The Python runtime module ``darq`` provides functions to check type
identifiers, and to convert them to their canonical, NFKC_Casefold
form: ``is_valid_type_id()`` and ``canonical_type_id()``.  Programs
should canonicalize a type identifier before storing or comparing it.

The set of basic types described by Apple's Uniform Type Identifier
system is used as the basis for DarqOS types.  That set is described
in Apple's `System-Declared Uniform Type Identifiers
<https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/UTIRef/Articles/System-DeclaredUniformTypeIdentifiers.html#//apple_ref/doc/uid/TP40009259-SW1>`_
document.


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
* `ListTypes(implemented)` returns the identifiers of the catalogue's
  types, sorted: all of them, or only those with an implementation.
* `FindTypes(mime_type, extension)` returns the implemented types for
  data with a MIME type, or a file name extension: those whose
  descriptions list it, and, for a type that isn't implemented, such
  as `public.plain-text` for a `.txt` file, the implemented types that
  conform to it.  Those matching the MIME type come first, and a MIME
  type matches with its parameters (`text/plain;charset=utf-8`), or
  without them; wildcards (`image/*`) aren't matched.  It's shared code
  for whatever brings data in: the filesystem type, a web browser, or an
  email client decides what its data is, by looking at it, too, and
  this is where it looks up the types.

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

The catalogue is filled by the *type sync*, `tools/type-sync`, spawned
by the p-kernel at boot, after the Type Service.  It registers:

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

* For First Edition, it's a path to the type implementation's directory,
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
running Type Implementation.  An implementation's directory can
implement several types (its `type.json` lists them): one process
serves all of them, so running implementations are kept by directory,
and activating any of its types returns the same port.  The Knowledge
Base's implementation, `types/kb`, serves all its kinds this way (see
:doc:`kb-service`).

An item's content is changed only through its type implementation:
other programs read it, if its store allows, but write it with the
implementation's protocols.  So the implementation sees every change
to its items, and can act on each one, as by publishing it to the
Index.  A text item's blob is written only by ``types/text``, and an
entity of one of the Knowledge Base's kinds only by ``types/kb``, with
its ``EntityWriter`` protocol (see :doc:`kb-service`).

Every item is a KB entity (see :doc:`entity-vs-item`), and everything
about it, its content and its entity, is written through its type
implementation, so that it keeps the item's Index entry up to date:

* every implementation inherits the entity type's: it serves the
  ``EntityItem`` protocol for each of its items, on a port from
  ``OpenEntity``, with ``darq.entity_type``'s ``EntityType`` as its
  base class, whose hooks it overrides to publish what's changed; and
* it makes and destroys its items' entities, and only it changes their
  kinds, their DarqOS type statements.

``types/kb`` is the plain implementation, for the KB's own kinds, whose
content is all knowledge.  Other
entities, such as properties, classes, and plain ``darq.entity``
entities, can be written to the KB directly.  For now this is a
convention: Storage and the KB service will enforce it, once DIPC
tells them who sent a message.

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
  deferred, as is any other `Activate` for any of its directory's
  types that arrives meanwhile.
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

An `Activate` can arrive after the implementation has exited, but
before the notification that its port is dead.  The p-kernel then
refuses the reply carrying the port (`DEAD_TRANSFERRED_PORT`, see
:doc:`ipc`): the Type Service forgets the implementation, and starts
it again for that `Activate`.  (It replies with `Deferred.try_reply`,
which leaves the request unanswered when that happens.)  An
implementation that's gone by the time its `Ready` is answered fails
its waiting `Activate` requests, rather than being restarted.

Every implementation serves the `Implementation` protocol (also in
`lib/didl/type.didl`):

* `Create(type_id, context)` creates an item of a type, and returns its
  identifier, and a port for it.  A type the implementation doesn't
  implement is `NOT_FOUND`, and one with no empty items, such as a
  PDF, is `UNSUPPORTED`: they're only made with `CreateFrom`.
* `Open(item_id)` returns a port for an existing item.  An implementation
  must check that the identifier's type is its own: an item of another
  type, including a conforming subtype, is `NOT_FOUND`.
* `Destroy(item_id, context)` marks it destroyed (see Item Destruction,
  below).
* `CreateFrom(type_id, data, name, media_type, context)` creates an item of a
  type from data in the type's own format, such as a PNG file's bytes,
  and returns its
  identifier and a port, as `Create` does.  Data that isn't of the type
  is `INVALID_DATA`, and nothing is created: it isn't converted.  This
  is how items come in from outside: from a file, a web page, or an
  email's attachment.  Its name is the name of the file it came from,
  as its caller knows it, or empty: the item's entity is named from it,
  when the data has no title of its own (see :doc:`entity-vs-item`).
  Its media type is the one it came with, if it did, such as a server's
  `Content-Type`: a type that knows its own ignores it, but data's kept
  with it (see :doc:`data-type`).
* `Export(item_id)` returns an item's content as data in its type's
  format, with its MIME type: for attaching it to an email, saving it
  to a file, or uploading it.

  A type whose items have no form as data, such as the Knowledge Base's
  kinds (an address book's vCard file makes many entities, which is a
  tool's job), answers both `UNSUPPORTED`.  Data is sent whole, in one
  message, as Storage values are: large items, such as video, will want
  ranged access, or a Storage key, instead.
* `IndexEntry(item_id)` returns what's indexed for an item, as it's
  published to the Index: its names, such as labels and aliases, and
  its body (see :doc:`search-service`).  The implementation publishes
  each item as it saves it; this is for a reindex.  A destroyed item is
  `NOT_FOUND`, and a type that isn't indexed answers `UNSUPPORTED`,
  as `darq.entity` does.
* `OpenEntity(item_id)` returns a port for an item's entity, serving the
  `EntityItem` protocol (see :doc:`kb-service`), as every item is an
  entity (see :doc:`entity-vs-item`): it's inherited from
  `darq.entity_type`'s `EntityType`.  An item that has no entity yet,
  as one made before there were entities hasn't, is given one first, by
  its implementation (`EntityType.adopt`); an item that isn't there is
  `NOT_FOUND`.

The implementation records in History what `Create`, `CreateFrom` and
`Destroy` do, and the entity writes, as their `context` describes it:
what happened, from where, and a note, such as a file's path.  An
empty context is the request's own event, from the implementation.
Every item port has `Viewed(source, note)` too, after `Close`: a lens
calls it when it shows the operator the item, and the implementation
records it, and what's changed through that port, as from the lens (see
:doc:`history`, Who Records Events).

`darq.launch` has `create_item_from(type_id, data)`,
`export_item(item_id)` and `find_types(mime_type, extension)` for
clients.  Until there's a filesystem type, the Terminals' New panel
has *Import File...*, which brings in a file from the host: its type is
the first `FindTypes` finds for its extension whose `CreateFrom` takes
it, and its type records it in History as `imported`, noted with the
file's path.  A type implementation records what's done to its items,
as their callers describe it with a context: see :doc:`history`, Who
Records Events.

Type Instances
--------------

An instance of a Type is called an *Item*.  Type Implementations
manage a collection of Items, and expose them to programs, and (via
Lenses) to operators.

An Item Identifier  (aka *iid*) consists of a Type Identifier, a
colon, and an *item specifier* for the specific Item, like

  ``net.daringfireball.markdown:c5f1ad2aea334248b1169c363cbade66``

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
linkage and annotation throughout the system: entities, index,
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
======

A Type Implementation provides the *behaviour* of items: basically,
the code.  It also determines how instances of the type are persisted
(for example, using the storage service, or the knowledge base
service).

Access to an item's behaviour is via the IPC system.  The type
implementation exports an API for items of the type, operating on
specific instance data loaded from and saved to a persistence backend.

But ... this is just the actual behaviour of the item: the "model" in
MVC terms.  In addition to type implementations, there is a closely
related second class of applications in DarqOS: *Lenses*.

A *lens* is a way to use an item of a type.  For example, Lenses
expose the item's data to its operators via the Terminal service.

As an example, consider a plain text document.  The type
implementation is responsible for storing the text data, and making it
available to lenses.  An example text document type lens is a text
editor: it provides an interface that enables a system operator to
view or alter the document's text.

This is similar to the "default application" configuration for a
filename extension in Windows: the file extension basically identifies
the file as being of a particular type, and the application is a lens
through which the operator can see the data.

Lenses share their look: ``darq.ui.style`` has their palette, and
stylesheets for the usual controls, the scroll bars, and the F1 help
panel, and ``darq.ui.lens`` has the text shown over a window for a
moment.  A lens adds rules only for its own widgets, so it needs no
colours of its own.


Contexts
--------

Lenses are activated in a *Context*.  There is initially only one context
defined: the graphical Terminal context.

The Terminal context is the full DarqOS graphical shell, with high
resolution bit-mapped graphics, a keyboard, a pointer, a set of
pointer-relative actions (clicks), mono audio input, and stereo
audio output.

The system compares the facilities available in the invoking *context*
before determining which *lenses* are able to be used for a specific
*type* of *item*.

For example, if you were to try to use an Amazon Alexa-like device to
use a recipe document, then a typical graphical recipe viewer would
not be appropriate, and you'd need to use a different, audio-only,
*lens*.

Additional contexts might be added in future, including things like
this Alexa-style voice interface, Jupyter Workbooks, a remote
text-only console, etc.


Lens Activation
---------------

Within a Terminal Service, the user has several means of obtaining
*item identifiers*, and requesting actions upon them.  These requests
combine:

* a *Context*
* an *Item Identifier* (which includes the item's *Type Identifier*), and,
* an action.

The Terminal must process that request and determine (with or without
help from the user) which of its available *lenses* should be used.

In First Edition, there's only one action, "use", so the action plays
no part in choosing a lens.

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

A lens is started for one item, with the protocols in
`lib/didl/lens.didl`:

* The Terminal spawns the lens's directory, with a bootstrap port, and
  the lens reports `Ready(lens)` there, with its own port.
* The Terminal sends `Use(item_id, item)`, with a port for the item
  from its implementation's `Create` or `Open`.  The lens takes over
  the item port, and closes the item when it's finished with it.  A
  lens uses one item: another `Use` is refused, `BUSY`.
* The lens exits once its window is closed, or it's sent `Close`.
* `Shown()` returns the item the lens is showing now, and whether that's
  the item's page, as the entity lens's is: for the Terminal's *Sys-i*
  (see :doc:`terminal`).  Every lens answers it alike, through
  `darq.ui.lens`'s `LensHandler`, and registers its port as
  `lens.<dpid>`, its windows' Wayland `app_id`, so it can be found from
  a window.
* The entity lens, which is any item's page, opens the item's entity
  itself, by its identifier, with its type implementation's
  `OpenEntity`, and closes the port it's given, which is its type's own.
  It's registered for `public.item`, as well as `darq.entity`, so an item
  of a type with no lens of its own, as data's, is shown by its page.

`darq.launch.use_item()` does all of this, from finding the lens to
sending `Use`.  A process per item is simple, and isolates items, but
each starts its own Qt: a lens could later serve several items from
one process instead, without changing the protocol.

References
----------

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
