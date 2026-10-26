Storage Service
===============

In place of a Unix-like hierarchical file system, ubiquitous in
contemporary operating systems, DarqOS provides a persistent key-value
storage service, with support for value sizes from a few bytes, to
many terabytes.

The keys are members of a flat namespace: there is no concept of
directories, or indeed meaningful key names.  This is purely a means
of ensuring the persistence of an identified sequence of bytes.

Role
----

Storage keeps *blobs*: sequences of bytes, each identified by a key.
It knows nothing about what they hold.

* Type implementations decide where their objects' state lives (see
  :doc:`object`).  Types whose objects would be files on a Unix-like
  system, such as text documents, images and source code, keep it in
  Storage.  Others use the knowledge graph instead.
* The link from an object to its blob, and the object's name, tags
  and dates, are kept by the Metadata service (see :doc:`metadata`).
  History, Index and Search make objects findable.  So Storage needs
  no names, and offers no listing.
* All system storage, aside from early bootstrap, should use this
  service.

DarqOS is a single-user system, without third-party applications.
Access control within Storage is therefore mostly a guard against
silly mistakes having major consequences, rather than a defence
against hostile processes.

Keys
----

Keys are allocated by the service, when a blob is created: clients
never choose them.

* A key is a 128-bit random integer, sent as a struct of two uint64s.
  Keys are never reused.
* Random keys can't collide, can't be squatted on, and are unique
  across devices, so blobs could later be copied between them without
  renaming.  They're also unguessable, which guards against a process
  reaching a blob by mistake.

A process that needs to find a blob again, across a restart, must
record its key somewhere: usually in Metadata, alongside the object.
Services that start before Metadata, such as Type, find their keys in
the boot vector (see Bootstrapping, below).

Generations
-----------

Each blob has a *generation*: a uint64 that starts at 1 when the blob
is created, and is incremented by every change to its value.  Every
response that describes a blob includes its generation.

A `Put` may name the generation it expects to replace: if the blob
has changed since, it's refused with `CONFLICT`, and nothing is
written.  An expected generation of 0 replaces the value
unconditionally.  This guards against a writer silently overwriting a
change it hasn't seen, such as a second copy of an object open by
mistake.

Semantics
---------

* `Create` allocates a key, and stores the blob's initial value.
* `Get` returns the whole value, and its generation.
* `Put` atomically replaces the whole value of an existing blob: it
  never creates one.
* `Delete` removes a blob.  Its key is never reused.
* `Stat` returns a blob's size and generation, without its value.
* Each change is committed before it's answered: once a client has
  the response, the change survives a crash or power loss.
* Requests are handled one at a time, in order of delivery.

Expected failures are reported in each response's `status`; a
malformed request, or a failure in the service, gets an error reply
(see :doc:`ipc`).

Values are sent whole, in a single message.  The IPC system doesn't
yet limit message sizes, and there's no stated maximum value size:
that's deferred until it's needed.  Ranged access, reading or writing
part of a value, is expected then too (see Ranged Access, below), and
method numbers are kept free for it.

Ranged Access
-------------

Not yet implemented: these are notes for when large values need it.
Method numbers 6 and 7 are kept free for them.

* `Read(Key key, uint64 offset, uint64 length)` returns up to `length`
  bytes of the value, starting at `offset`, with the blob's
  generation.  A read past the end of the value returns what there is.
* `Write(Key key, uint64 start, uint64 end, opaque content)` replaces
  the bytes from `start` up to (not including) `end` with `content`.
  The range and the content needn't be the same length, so a write
  can also insert and delete:

  * `start == end` inserts `content` at `start`;
  * empty `content` deletes the range;
  * a range of the content's length overwrites in place;
  * `start` and `end` at the value's length appends.

  Like `Put`, it would increment the generation, and probably take an
  expected generation too.

Protocol
--------

The protocol is defined in `lib/didl/storage.didl`::

    library darq.storage;

    enum Status : uint32 {
        OK = 0;
        NOT_FOUND = 1;
        CONFLICT = 2;
    }

    struct Key {
        uint64 high;
        uint64 low;
    }

    protocol Storage {
        1: Create(opaque value) -> (Key key, uint64 generation, Status status);
        2: Get(Key key) -> (opaque value, uint64 generation, Status status);
        3: Put(Key key, uint64 expected, opaque value) -> (uint64 generation, Status status);
        4: Delete(Key key) -> (Status status);
        5: Stat(Key key) -> (uint64 size, uint64 generation, Status status);
        // 6 and 7 are reserved for ranged Read and Write.
    }

The service registers the name `darq.storage`.  Clients use the
generated client::

    storage = Storage.Client(darq.connect("darq.storage"))
    created = storage.create(value=b"hello")
    current = storage.get(key=created.key)

Implementation
--------------

* The service is a Darq Python process, spawned by the p-kernel at
  boot.  It serves the protocol with `darq.serve()`.
* Blobs are kept in a SQLite database, `storage.sqlite`, in the run
  directory: `/darq/run` when installed (see :doc:`distribution`), or
  `./run` in a development tree.
* As a later optimisation, large values might be kept in plain files
  instead, or mapped into clients' memory.

Bootstrapping
-------------

With no boot volume, and no in-kernel storage drivers, a service
starting at boot has nowhere to find the keys of its own blobs.  The
p-kernel will provide a small key:value *boot vector* for this,
accessible only to processes it spawned itself (parent dpid 0): for
example, the Type service keeps the key of its registry blob there.

The build should also be able to produce a pre-initialised storage
database (see :doc:`bootstrap`).

Future
------

* Ranged reads and writes (see Ranged Access, above), and a maximum
  value size.
* Per-blob principals and rights, once there's a Security service
  (see :doc:`security`).
* An administrative listing, or a garbage-collection pass, to find
  blobs whose objects were deleted without deleting them.
