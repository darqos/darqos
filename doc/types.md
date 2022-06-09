# Types
Types, in the sense of MIME types and similar characterisations, are
a fundamental aspect of DarqOS.  A Type describes an object or entity,
classifying it by its properties and behaviour.  Types can be both
general and specific, with a rich conformance model that allows 
generalisation of entire classes of entities.

Example types are: text document, PNG image, ZIP file, etc.

## Type Implementations

Types are defined as part of the persistent state of the system.  This
state is owned and managed by the Type Service.  Programs access the
Type Service via an API in the usual way.

A lot of the terminology here is adopted from Apple's Uniform Type
system.

A _Type Implementation_ is a program that provides actions for a type.
Actions are things like _create_, _view_ or _print_.  Type implementations
are one of the major classes of application for the system.

Type descriptions consist of:
* The type identifier (derived from Apple's UTI)
* Zero or more other types to which this type conforms (to use the
  UTI terminology).  Note that this isn't a simple inheritance tree:
  types can conform to many other types.
* Zero or more MIME types that are equivalent
* Zero or more file extensions that indicate equivalence
* An icon to represent instances of this type
* A reference to the implementation for this type
* An optional endpoint for an active instance of the type implementation

Whn a Type Implementation is installed in the system, it registers itself
with the Type Service.  This process creates the description entries for
the type(s) that the program implements.  Uninstalling will remove the
description.  All type implementations should support these operations.

To create type instances, a program should invoke the Type Service,
passing it a UTI type name.  The Type Service consults its registry of
types.  If the type implementation is not already running, it is started.
The Type Service then requests a new instance via the Type Implementation's
API, and receives an object reference result that is passed back to the
initiating program.

## Lens Implementations

A type implementation provides the _behaviour_ of type instances:
basically, the code.  It also determines how instances of the type
are persisted (for example, using the storage service, or the knowledge
base service).

Access to objects' behaviour is via the IPC system.  The type implementation
essentially exports an API for instances of the type, operating on specific
instance data loaded from and saved to a persistence backend.

But ... this is just the actual behaviour of the object: the "model" in
MVC terms.  In addition to type implementations, there is a second class
of applications in DarqOS: Lenses.

A _lens_ is a way to view an object of a type.  Lenses expose the object's
data to its users via the Terminal service.

### Contexts

A Lens is activated in a context.  There are two initial contexts defined:
a CLI context and a GUI context.

The CLI context implements a traditional command-line interface, with 
textual input and output capability provided via the standard three
streams: stdin, stdout, and stderr.  The output stream implements the
VT-102 escape codes interface, allowing curses-style control of a 2D
grid of character cells.

The GUI context is the full DarqOS graphical shell, with high resolution
bit-mapped graphics.

This model will likely need refinement: it should cater for things like
Jupyter Workbooks, and Alexa-style voice interfaces, etc, as well as the
more traditional options.

In addition, the I/O channels might reasonably include sound options in
conjunction with visual choices: you could have a CLI visual display, but
use stereo or surround audio for a music player, for instance.

Similarly, what about visual functionality: there could be a difference
when selecting a lens for a basic frame buffer vs. a GPU-accelerated 4K
screen?

### Lens Activation

Within a Terminal Service, the user has a means of managing object
references (or identifiers), and requesting actions upon them.  These
requests combine:
* a context
* an object reference, including the object's type
* an action

The terminal must process that request and determine (with or without
help from the user) which of its available Lenses should be used.

The collection of Lenses, and their supported contexts, types, and action,
is maintained by the Type Service.



## Type Service
The set of types known to the system is managed by the Type Service.
A user can request the creation of a new type instance, or the use of an
existing type instance, using the type service to find the implementation
of the type.

### Creation
Using a shell, the user requests a new instance of a named type.  The
shell looks up the type name in the service registry, and obtains a 
reference to the type's implementation.  The shell uses the kernel 
to spawn a new process, running the type's implementation.

* Does it make sense for each type instance to be a new process?
  * Perhaps one process could manage many different type instances?
    eg. consider displayed text documents, or HTML documents, etc
  * Maybe it should be up to the type implementation to determine 
    what resources it requires?
  * So perhaps a type _factory_ should exist, and be asked to create
    a new instance?
    
### Use
For a pre-existing object, discovered through the object selector, 
its type should be available via metadata.  In this case, it's still
necessary to create a new type instance, but it should be
initialised from the storage server with the pre-existing content.

This is another variant of the type factory.

# Type Collection

One fundamental decision is to separate Text / Document and Code as 
different types.  Document should encompass everything from a README to 
FrameMaker, and the fundamental behaviour should be that it is displayed 
immediately as it is edited.

Code, on the other hand, undergoes a process of compilation or 
interpretation to generate some artifact.  The distinction is basically 
in the workflow, rather than the content (since a lot of the time, 
they’re both just an ordered collection of Unicode code points).

The interesting point is Markdown (and thus, TeX, roff, etc), where the 
Document is produced as an artifact of the Code.

One of the goals of M0 is to be able to create, show, and edit simple 
text documents.  That will require a simple text editor.  There’s some 
decisions to be made here:

* What, if any, boundaries are there between eg. a basic, unformatted text 
  document and a complex FrameMaker-style book?
* How does the system deal with:
  * ASCII/Unicode text files?
  * PDF documents?
  * Microsoft Word?
* In general, I think I’d like the approach to be one of importing via 
  translation to a native capability.  Which might, perhaps ideally, be 
  an implementation of an existing standard (de facto or de jure).

That said, I don’t really want to be writing a Word-compatible document 
editor: I’ll need to find a way to leverage eg. LibreOffice.

That aside, and for now, just considering a simple text editor, what is 
unique about this type implementation?

* Single tool that understands the type:
  * Creation
  * Editing
  * Diff and merge
* I’d like to have the GUI be a layer that simply drives the type API.
  * This implies that the API is available for programmatic access
  * Which should include scripting
  * So type implementations should be a library, with an API, and an 
    (optional?) GUI component that exposes the API to a terminal
* These APIs should be discoverable, self-documenting, and have decent 
  consistency between different types.
  * ie. something more like a Smalltalk class hierarchy than an existing 
    OS application.
* This is not entirely dissimilar to Windows’ COM, I guess
* This would mean that, eg.
  * Anything you can do in the GUI, you can do from a script, using the 
    same tool and the same commands
  * The GUI could reasonably have actions (menu items, etc) defined as 
    scripts/programs, and the user could easily alter or augment these 
    with their own action scripts/programs
  * Which means I’ll need to figure out how such programs should be 
    written
    * Which comes back to the unity of the ST80 experience
      * Albeit with awful performance and image-management issues
        * Both of which could probably be solved
          * Am I trying to talk myself into writing this in Smalltalk?
            Srsly?
* Smalltalk, Lilith, Oberon, LISP machines …
  * All had a unified language experience.
  * Unix immediately split that into shell and C: why?  Historical 
    accident?

# Sub-types
In some cases, it makes sense to have polymorphism for types.  The
motivating example is paper books and e-books: both have similar
metadata, but e-books have actual content in the storage service,
while paper books have a physical location property.

Depending on how collections end up being handled though, it might
make sense for both types of books to be handled by a single
collection, which kinda implies a base, parent type for both book
sub-types.

How is this to be handled within the type system?

I think it's the case that Apple's UTI system has sub-types as well.
They're not indicated in the naming, which suggests some additional
information is stored about types: this could end up make a Type an
object, which might get kinda meta.

# References

https://developer.android.com/guide/components/intents-common
https://developer.android.com/guide/components/intents-filters
https://developer.android.com/reference/android/content/Intent

https://developer.apple.com/documentation/uniformtypeidentifiers
https://developer.apple.com/documentation/uniformtypeidentifiers/defining_file_and_data_types_for_your_app
https://developer.apple.com/documentation/uniformtypeidentifiers/uttype

