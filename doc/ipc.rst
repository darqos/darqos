IPC
===

In DarqOS, inter-process communication uses a kernel-provided IPC
facility, similar in basic concept to many microkernels.  For clarity,
this is referred to as Darq Inter Process Communication (DIPC).

Influences include: Mach, QNX, L4, and Fuschia/Zircon.

Processes can open a _port_, and using it, send, and receive messages.  Because it
enables some significant performance optimisations, both asynchronous
(one-way) messages and synchronous (request-response, RPC) messaging
are supported.

For best performance of RPCs, the receiver should "take over" the
sender's scheduler quantum, and the message should be transferred in a
shared memory segment if it doesn't fit in processor registers.
Ideally, there's no need to spill registers, and no waiting for
scheduling: the request is made, fulfilled, and result returned all
within the active scheduler quantum across the two threads, and no
data copying is required.  This requires an API distinction between a
one-way message and an RPC.

During its prototype phase, the DarqOS kernel is implemented as a
pseudo-kernel service, with a matching runtime library that presents
the system call API to applications.  The runtime library communicates
with the p-kernel service.  All inter-process communication is
brokered by the p-kernel which internally maintains a collection of
abstract ports.

Communication between a process and the p-kernel uses TCP, in order to
have reliable delivery, and a well-known port, avoiding a need to
bootstrap the connection and resolve addresses between processes.

Messages
--------

Confusingly, there are two layers of messages in this design: the
prototype-specific p-kernel messages, and the application-layer
messages sent to and received from p-kernel-maintained ports.

p-kernel messages use a simple 32 bit framing header, with a
consequent maximum message size of 4GB (which seems ample, but is it
really?)

The p-kernel protocol consists of request and response messages for
the pKernel emulated system calls.  Those concerning ports and
messages are:

* OpenPortRequest
* OpenPortResponse
* ClosePortRequest
* ClosePortResponse
* SendMessageRequest
* SendMessageResponse
* DeliverMessage
* SendChunk
* DeliverChunk
* RegisterNameRequest
* RegisterNameResponse
* LookupNameRequest
* LookupNameResponse
* NotifyOnDeathRequest
* NotifyOnDeathResponse

A process can hold zero or more _ports_, which act as the source and
destination of messages (see Ports, below).  `OpenPortRequest` requests
the creation of a new port, and `OpenPortResponse` confirms it, and
returns the process' handle for the new port.  `ClosePortRequest`
requests that a handle be closed, and `ClosePortResponse` confirms it.

`SendMessageRequest` is used to send a message from a process to the
p-kernel, and `DeliverMessage` to deliver it from the p-kernel to a
process.  They contain a short header, identifying the source and
destination ports, followed by the payload, and a list of port handles
transferred with the message.  `SendMessageResponse` reports whether
the message was accepted for delivery.  `SendChunk` and `DeliverChunk`
are similar, but are used for streaming data, and include ordering and
positioning data within the stream.

`RegisterNameRequest` and `LookupNameRequest` use the p-kernel's name
registry (see Name Registry, below).

Both message and chunk payloads are structured binary data, managed by
generated functions derived from an IDL.

Ports
-----

Port rights follow the Mach model.  A port is a kernel object: a
destination for messages.  Processes never see ports directly, only
*rights* to them:

* A port has exactly one *receive right*, held by the process that
  created it.  Only that process receives the port's messages.
* A port can have any number of *send rights*, held by other
  processes, which may send messages to it.

A process refers to its rights using *handles*: 64 bit integers,
local to that process, like Unix file descriptors.  The p-kernel
translates handles into references to its port objects, and back into
the receiving process' handles on delivery.  A handle value means
nothing outside the process that holds it.

* Handles are allocated by a per-process counter, and never reused,
  so a stale handle can't silently refer to a different port.
* A process has at most one handle for each port.  Receiving another
  right to a port it already has a handle for returns the same handle,
  so comparing handles compares ports.  A receive right includes the
  right to send, so a send right to a port a process owns adds nothing.
* Handles aren't reference counted: a process holds a right, or
  doesn't.  Every delivered message grants a send right to its source
  port (see Sending), so with counting, a service would have to close a
  client's handle once for each message it received from that client.
  (Mach avoids this with *send-once* rights for replies.)

This is also the basis of security: a process can only send to ports
it holds a right to, and can only send from ports it owns, so it can
neither reach services it wasn't given, nor impersonate another port.

Kernel State
~~~~~~~~~~~~

The p-kernel keeps:

* A table of live ports, each a `KernelPortState`: an internal
  identifier (used only for logging, never sent to processes), the
  process holding its receive right, whether it's alive, and its
  registered name, if any, and the processes to notify when it's
  destroyed, with their notify ports.
* For each process (its `KernelProcessState`), a handle table mapping
  each handle to its port and right (RECEIVE or SEND), the reverse
  mapping from port to handle, and the next handle number.
* The name registry, mapping names to ports.

Sending
~~~~~~~

A sender names a source handle, a destination handle, the payload,
and a list of handles to transfer.  The p-kernel:

1. Checks the source handle holds a RECEIVE right in the sender's
   table: the source is where replies go, so it must be the sender's
   own port.
2. Checks the destination handle holds a SEND right, or a RECEIVE
   right (a process may send to its own ports), to a live port.
3. Translates the message for the receiver, which holds the
   destination port's receive right:

   * The destination becomes the receiver's handle for the port.
   * The source becomes a SEND right in the receiver's table, created
     if needed: every delivered message carries a right to reply.
   * Each transferred handle becomes a SEND right in the receiver's
     table, in the same way.

4. Delivers the translated `DeliverMessage`, and answers the sender
   with a `SendMessageResponse`.

A failed check, or a dead destination, is reported in the
`SendMessageResponse`, and nothing is delivered.  Sending is
synchronous: `send_message()` returns once the p-kernel has answered,
and raises an exception for a failure.  The result codes (`SendResult`)
are:

* `OK`: delivered.
* `BAD_HANDLE`: the source or destination isn't an open handle
  (`NonExistentPortError`).
* `NOT_RECEIVE_RIGHT`: the source is only a send right
  (`PortRightError`).
* `DEAD_PORT`: the destination port has been destroyed
  (`DeadPortError`).

The p-kernel answers after delivering, so a message sent to one of the
sender's own ports is already queued for `receive_message()` when
`send_message()` returns.

Ports in Messages
~~~~~~~~~~~~~~~~~

Payloads are opaque to the p-kernel, so it can't find ports written
into them.  Instead, ports are transferred in a list alongside the
payload, and a DIDL `port` field holds an index into that list.  The
generated code builds the list when encoding, and maps indexes back
to handles when decoding.  The p-kernel translates the list (step 3,
above).

* A `port` field is a uint16 index; the value 0xFFFF means "no port",
  for optional ports (such as an optional reply port).
* A message can transfer at most 64 handles.  The handle count in
  `SendMessageRequest` and `DeliverMessage` is a uint16, and the
  p-kernel refuses a `SendMessageRequest` with more than 64 handles, or
  a handle it can't translate, in its `SendMessageResponse`.

Most systems limit ports per message to a small number: Zircon allows
64 handles per channel message, Linux 253 file descriptors per
`SCM_RIGHTS` message, and seL4 2 or 3 extra capabilities.  The limit
is what bounds the work for the p-kernel and receivers; the uint16
index leaves room to raise it without changing the wire format.

In 1ed, a transferred handle grants a SEND right.  Moving a RECEIVE
right to another process can be added later.

Port Lifetimes
~~~~~~~~~~~~~~

* Closing a RECEIVE handle destroys the port: it's marked dead, and
  its registered name (if any) is removed.
* Closing a SEND handle gives up that right.
* When a process exits, or its p-call session is lost, all the ports
  it holds receive rights for are destroyed, and all its handles are
  released.
* Other processes' handles to a destroyed port remain until they're
  closed, as *dead* handles: sending to one fails.

Dead Port Notifications
~~~~~~~~~~~~~~~~~~~~~~~

A process can ask to be told when a port it holds a handle for is
destroyed, with `darq.notify_on_death(handle, notify_port)`:

* The notify port must be one of the caller's receive rights.  When
  the port is destroyed, the p-kernel delivers a notification to it:
  a `darq.dipc.PortDestroyed` message, from source handle 0, with txid
  0, the header's NOTIFICATION flag set, method 1 (PORT_DESTROYED),
  and a body of the caller's (now dead) handle, as a uint64.
* No process can send from handle 0, so a message from it is always
  from the p-kernel.
* If the port is already dead, the notification is queued before the
  call returns.
* Each notification is sent once.  Asking again for the same notify
  port changes nothing; asking for another one adds a notification
  there too.
* Closing the watched handle cancels its notifications, as does
  closing the notify port, or the caller finishing.
* A process' ports are destroyed when it finishes, so watching a port
  also notices its owner crashing.

The p-call fails with `NonExistentPortError` if the handle isn't open,
and `PortRightError` if the notify port isn't a receive right.

Clients use this to notice a service dying mid-call (see Requests and
Responses), and services to notice the processes they depend on
dying: a `Handler`'s `port_destroyed(handle)` method is called for a
notification on its port.

Name Registry
~~~~~~~~~~~~~

Every handle comes from somewhere: from opening a port, receiving a
message, or a transfer.  To bootstrap, a process needs a way to obtain
rights to the system services.  The p-kernel provides a name
registry:

* `register_name(name, handle)` associates a name with a port: the
  handle must hold a RECEIVE right.  A name is 1 to 255 bytes of UTF-8,
  and a port can have one name.  The name is removed when the port is
  destroyed, so it can then be registered again.
* `lookup_name(name, blocking)` returns a new SEND handle for the
  named port (or the caller's existing handle, if it has one).
  Services start concurrently at boot, so a blocking lookup waits
  until the name is registered, rather than failing; a non-blocking
  lookup of an unregistered name returns nothing.  A waiting lookup is
  abandoned if its process disconnects.

Both have `_cb` variants for the callback model.  Failures are reported
with a `NameResult` code, which `register_name` raises as an exception.

Names `darq`, and beginning with `darq.`, are reserved for system services: they
may only be registered by processes the p-kernel spawned itself (during
boot), which have a parent dpid of 0.  No other process can create one:
a `spawn_process` p-call always records its caller as the parent.
Other names are registered on a first come, first served basis.

This trusts every boot-spawned service with the whole `darq.`
namespace: it keeps impostors out, but doesn't stop one system service
registering another's name.  If that's needed, each name could be bound
to the boot table entry that spawned its service, without changing the
API.

For 1ed, the registry is a table inside the p-kernel, reached by
p-calls.  The alternative is a separate name service process, as in
Mach (and macOS' launchd), reached through every process' bootstrap
port (see below).  That also needs the registry process' own
authority to check registrations.  The `register_name` and
`lookup_name` API would be the same either way, so the registry can
move into a process later.

This replaces the earlier scheme of well-known port numbers.

Bootstrap Ports
~~~~~~~~~~~~~~~

A parent can give a new process a port, as it's spawned: its
*bootstrap port*.  The two can talk over it before either knows
anything else of the other.  The Type service, for instance, gives
each type implementation it starts a port of its own, and the
implementation sends its service port back over it, once it's ready.

* `spawn_process(path, bootstrap=handle)` names the port: the caller
  must hold a right to it, as for a transferred handle.  If it
  doesn't, or the port is dead, the spawn fails at once, with
  `SpawnResult.BAD_BOOTSTRAP` (raised as `SpawnError`), and nothing is
  started.
* When the new process runs, the p-kernel gives it a SEND right to the
  port, and the handle in its `RunResponse`.  It's found with
  `darq.get_bootstrap_port()`, which returns None if the parent named
  none, or the port was destroyed before the process ran.
* What the two say over the port is up to them: it's an ordinary
  port.  A parent that opens a fresh port for each child knows that
  messages on it come from that child (or from a process the child
  gave it to).
* Processes the p-kernel spawns at boot have no bootstrap port: they
  use the name registry.


Application Architecture
------------------------

Sending messages and chunks is a synchronous operation for the sending
process: control flow blocks until the p-kernel has accepted (or
refused) the message.

Receiving messages is more complicated.  The initial prototype releases
will adopt a single-threaded event-loop structure, with the ability to
poll for arriving messages, or to block control flow until one a
message is delivered (or the request times out).

The p-kernel delivers every process' messages, for all its ports, over
its one p-call connection, and the runtime queues them by port.
`receive_message(port)` takes the next message for one port;
`receive_any(ports)`, or a `PortSet`, takes the next for any of
several, in the order they were delivered across them.  Port sets are
the process' own grouping: the p-kernel knows nothing of them, and
ports can be added and removed at any time.

Future releases might add better support for multiple threads, and/or
adopt a coroutine-style asynchronous model.

Requests and Responses
~~~~~~~~~~~~~~~~~~~~~~

`darq.system` provides requests and responses over ports, for DIDL
protocols.  For each protocol, didlpygen generates a `Client` and a
`Handler` class, with a method for each protocol method, named in
snake case::

    storage = Storage.Client(darq.connect("darq.storage"))
    response = storage.get(key=b"foo")

    class StorageService(Storage.Handler):
        def get(self, request, source):
            return GetResponse(value=..., status=0)

    def darq_main():
        darq.serve("darq.storage", StorageService())

* A client's requests are sent from its thread's *reply port*, opened
  on first use, so the service's replies come back to it.
* Each request gets the next transaction identifier (txid), skipping
  0, which marks one-way messages.  The reply is the message on the
  reply port from the service's handle (the same handle, since handles
  coalesce), with the request's txid.  Anything else is a late reply
  to an abandoned call, and is dropped.
* The client waits for the reply indefinitely, or for the Client's
  `timeout`, raising `TimeoutError`.
* Before a thread first calls a service handle, it asks for a dead
  port notification for it, on its reply port.  If the service's port
  is destroyed before it replies (the service crashed, say), the call
  raises `DeadPortError`, rather than waiting.  A reply sent before
  the port died still arrives first, and is returned.  Notifications
  for other services are dropped.
* `darq.serve()` opens a port, registers its name, and then handles
  requests one at a time, in order of delivery, until the process is
  killed.  Each reply is sent to the request's source.
* It does so with a `darq.Server`, which can answer requests on
  several ports, each with its own Handler: a handler can add ports
  (`self.server.add(port, handler)`) and remove them while it runs, and
  schedule callbacks (`self.server.call_later(delay, callback)`).
  Requests are handled in order of delivery across all its ports.
* A Server is single-threaded: only the serving thread may use it, or
  the Deferreds of its handlers.  Another thread can ask it to do
  something by sending a message to one of its ports.  Timers run
  between messages, so a handler that blocks (in `spawn_process`, for
  instance) delays them.
* A handler that can't answer yet *defers* the reply: it calls
  `self.defer()`, keeps the `Deferred`, and returns it, so nothing is
  sent.  Later, typically while handling a message on another port, or
  in a timer, it answers with `deferred.reply(response)` or
  `deferred.error(code, description)`, sent from the port the request
  arrived on.  Each is answered exactly once; one that's never
  answered is logged when it's discarded, and its client waits until
  it times out.  If the handler raises an exception, or returns a
  response, after deferring, that answers the request instead.
  One-way messages can't be deferred.

  `self.defer(timeout=seconds)` sets a deadline: if the request still
  hasn't been answered then, the Server sends an error reply with code
  `TIMEOUT` instead.  This needs the handler to have been added to a
  Server.

  This lets a service wait for something without blocking: for
  example, the Type service defers an `Activate` until the
  implementation it started reports, over its bootstrap port, that
  it's ready::

      def activate(self, request, source):
          pending = self.defer(timeout=30)
          bootstrap = darq.open_port()
          self.server.add(bootstrap, Ready(self, request.type_id, bootstrap))
          darq.spawn_process(path, bootstrap=bootstrap)
          self.waiting[request.type_id].append(pending)
          return pending
* A request the service can't handle gets an *error reply*, in place
  of its response: the request's txid and method, with the header's
  ERROR flag set, and a body of a uint32 code and an opaque UTF-8
  description (`darq.dipc.ErrorReply`).  The codes are
  `UNKNOWN_METHOD`, `BAD_MESSAGE` (it can't be decoded),
  `NOT_IMPLEMENTED` (the Handler doesn't override the method),
  `INTERNAL_ERROR` (the handler raised an exception, or returned the
  wrong response), and `TIMEOUT` (a deferred request wasn't answered
  by its deadline).  The client raises it as `RemoteError`.  Failures a
  protocol expects, such as "not found", belong in its response fields
  instead.
* One-way messages are never answered, even with an error reply.

Establishing a Connection
-------------------------

There are two main scenarios in which the IPC mechanism is used:
service-to-service communication, and application-to-service
communication.

Service to Service Communication
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

During the prototype phase, services are host operating system
processes.  They are started by the system boot process, or potentially
by a user action and their runtime library establishes TCP connectivity
with the p-kernel, using a well-known TCP port number on the loopback
address.

They may then register their service port(s) with the IPC system, but
their client processes are unaware of these registered port numbers.
How do the clients and their server rendezvous?

This is primarily a security question: there must be nothing to
prevent an imposter registering as the service.  DarqOS uses the
p-kernel's name registry, which reserves the system services' names
for the processes it spawned to run them (see Name Registry, above).

Application to Service Communication
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A related problem exists for application code.  Given a long-lived
object identifier, to _use_ that object, the appropriate type
implementation must be started, and must make itself available to the
process wanting access to the object.

This implies some sort of activation functionality that either locates
an existing type implementation instance or starts a new one, and
passes the service port identifier to the requestor.

This is related to eg. DBus activation, which uses reversed domain
name strings as the rendezvous and an application-specific
configuration file to specify how to start it.  In that case though,
the activation name and the port name are the same: there's no need
for an aditional resolution process.

This suggests that perhaps port identifiers should have more
semantic content than a simple integer: if they were a string
identifier, which matched a registration from the type implementation,
the p-kernel could be responsible for activating the process on
demand.

We still have the problem of imposters, which is quite important,
even if we do avoid answering it for now.

The best bet here is probably:

* Embed the type description in the object identifier.

  * Also, embed the location in the object identifier.

    * So, a three-part id?  `type:location:id`?

* Have type implementations register themselves with the p-kernel
  so the runtime can look them up there, and be able to spawn them.
* When resolving an object id:

  * Contact the p-kernel at the location
  * Activate the type implementation (subject to context)
  * Ask the type implementation for the specific object

    * Does this end up being an _ephemeral_ port?

  * Talk to the type implementation + object as required.


IDL
---

DIPC messages are defined using a typical IDL syntax, called Darq
Interface Definition Language (DIDL).  A short header will identify
the message to the receiver: this is an integer, valid only across a
single system where the register of message types uses a common index.



IDL Compiler
~~~~~~~~~~~~

The IDL compiler takes a description of one or more messages, and
generates suitable code in a specified programming language to read
and write messages using that structure.

The compiler is not a type implementation.  It could perhaps be seen
as representing the type of the interface definition, but that's
stretching the metaphor a bit (I think?)  It's not a view (once could
argue that the generated code is a view of the IDL, perhaps, but
again, I think that's pushing things a bit far).  So, it's probably
best as a service, with an API that allows you to request the
generation of stubs in language X.  Perhaps a syntax check on the IDL
without generation?  Does it make sense to have it be an LSP for the
IDL too?

Python
~~~~~~

For 1ed, the IDL compiler (didlc) should be a Linux command line
utility.  It should generate one Python file/module for each DIDL file
processed, and each message should be a class, with encode() and
decode() functions (in a similar style to the p-call message classes
from darq/pcall.py).

Syntax
~~~~~~

Example::

    library filesystem;

    struct FileAttributes {
        uint64 size;
        uint32 permissions;
        uint32 owner_id;
    }

    protocol FileServer {
        1: GetAttributes(uint64 fd) -> (FileAttributes attr, uint32 status);
        2: Foo(uint8 a, uint16 b, int8 c, real32 d, real64 e, bool f, opaque g, port h);
    }

Notes:

* An enum names a set of integer values: `enum Status : uint32 { OK = 0; NOT_FOUND = 1; }`.  It's based on an integer type, which sets its layout, and is generated as an IntEnum.  Decoding a value that isn't a member fails.
* Struct alignment and padding follow the C/C++ ABI: a struct is aligned to its most strictly aligned member, and its size is padded to a multiple of that alignment.
* The 'library' attribute is used to name the generated Python module.
* Specification of a return type(s) for method x implies two messages: xRequest and xResponse
* The 'port' type is a uint16 index into the list of handles transferred with the message, or 0xFFFF for no port (see Ports in Messages): the generated code converts between indexes and handles.  A message can transfer at most 64 handles.
* The 'opaque' type is a sequence of bytes.  It's encoded as a uint32 offset (from the start of the message) and a uint32 length, and the bytes are appended to the end of the message, 8-byte aligned.
* The 'string' type is UTF-8 text, encoded as an opaque.  Decoding invalid UTF-8 fails.
* The 'sequence<T>' type is a sequence of elements of any type T, including other sequences: `sequence<string>`, `sequence<sequence<Key>>`.  It's encoded as a uint32 offset and a uint32 count of elements, and the elements are appended to the end of the message as an array, at a stride of T's size.  The elements' own strings, opaques and sequences follow the array.  T can't be an empty struct, and a struct can't contain itself through a sequence.
* Out-of-line values (opaques, strings and sequences) are appended in the order they're encoded: fields in order, and a sequence's array before its elements' values.  Decoding requires exactly that order, so one value can't be referred to many times.
  
