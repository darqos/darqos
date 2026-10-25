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
the pKernel emulated system calls.

* OpenPortRequest
* OpenPortResponse
* ClosePortRequest
* ClosePortResponse
* SendMessage
* DeliverMessage
* SendChunk
* DeliverChunk

A process can register zero or more _ports_, which act as the source
and destination of messages.  `OpenPortRequest` requests the
allocation of a new port, and `OpenPortResponse` confirms the
allocation, and returns the new port's identifier.  `ClosePortRequest`
requests destruction of a port, and `ClosePortResponse` confirms it.

`SendMessage` is used to send a message from a process to the
p-kernel, and `DeliverMessage` to deliver it from the p-kernel to a
process.  THey contain a short hader, identifying the source and
destination ports, followed by the payload.  `SendChunk`and
`DeliverChunk` are similar, but are used for streaming data, and
includes ordering and positioning data within the stream.

Both message and chunk payloads are structured binary data, managed by
generated functions derived from an IDL.

Ports
-----

Ports are identified by small integer values.  There is not yet any
provision to pass ports in messages, other than the source port from
which it was sent, but this will likely be needed soon.


Application Architecture
------------------------

Sending messages and chunks is a synchronous operation for the sending
process: control flow blocks until the runtime library has enqueued
the payload for transmission by the host operating system's TCP stack.

Receiving messages is more complicated.  The initial prototype releases
will adopt a single-threaded event-loop structure, with the ability to
poll for arriving messages, or to block control flow until one a
message is delivered (or the request times out).

Future releases might add better support for multiple threads, and/or
adopt a coroutine-style asynchronous model.  Depending on how that
evolves, an explicit RPC structure might also be provided.

On the service side, the blocking dispatch loop model will be directly
supported by the runtime.

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

This is primarily a security question: the services could use
well-known port numbers, or the p-kernel could implement a name
resolution service, but in either case, there is nothing to prevent
an imposter registering as the service.

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
from darq/common.py).

Syntax
~~~~~~

Example

```
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
```

Notes:

* Struct alignment and padding follow the C/C++ ABI: a struct is aligned to its most strictly aligned member, and its size is padded to a multiple of that alignment.
* The 'library' attribute is used to name the generated Python module.
* Specification of a return type(s) for method x implies two messages: xRequest and xResponse
* The 'port' type is a uint32, but identified for special handling within the IPC system.
  
