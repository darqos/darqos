# Types
Types, in the sense of MIME types and similar characterisations, are
a fundamental aspect of the system.  Most of what would usually be
considered applications are installed in the system as type implementations.

Users access the system via an implementation of the Terminal Service.
The terminal service provides a shell-like facility for interacting with
the system.  One feature of this shell is the ability to create new
instances of types.

## Type Registry
The set of types known to the system is managed by the Type Registry.
A user can request the creation of a new type instance, or the use of an
existing type instance, using the type registry to find the implementation
of the type.

### Creation
Using a shell, the user requests a new instance of a named type.  The
shell looks up the type name in the registry, and obtains a reference
to the type's implementation.  The shell uses the kernel to spawn a new
process, running the type's implementation.

* Does it make sense for each type instance to be a new process?
  * Perhaps one process could manage many different types (eg. consider
    displayed text documents, or HTML documents, or ...)
  * Maybe it should be up to the type implementation to determine what
    resources it requires?
  * So perhaps a type _factory_ should exist, and be asked to create
    a new instance?
    
### Use
For a pre-existing object, discovered through the object selector, the
type should be available via metadata.  In this case, it's still
necessary to create a new type implementation, but it should be
initialised from the storage server with the pre-existing content.

* So perhaps this should be another function on the type factory?

## Implementation

So, in fact, perhaps the Type Registry could be implemented using the
metadata service?  The type name, maybe some other tags, etc, could all
be stored in the metadata, and the shell could simply look it up there?

So ...
* On startup, the shell will query the metadata service, looking for
  types.  
* Those types will populate a cached set of the available types.
  * New types can be added at runtime: how is the cache updated?
    * Polled regularly?  (urgh)
    * Persistent queries in the metadata service?  (hrm)
    * An explicit API, which requires an explicit target service, to
      register a new type? (easiest)

Perhaps that's the best argument for it being a separate service.  In
which case, the type registry at least could just use the metadata
service as storage, but provides a point of coordination for registering
types and maintaining the cache.

What access to this service is required outside the shell?  Does there
need to be an API for this in the standard runtime>

 
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
* Smalltalk, Lillith, Oberon, LISP machines …
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

