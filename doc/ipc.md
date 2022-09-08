# IPC

During its prototype phase, DarqOS is implemented as a runtime library
and a collection of services, all communicating via message passing.

It is possible, even likely, that this model will persist beyond the
prototyping phase, following a similar path to systems such as CMU's
Mach.

The prototype IPC facility is implemented with some code in the runtime
library, and a single "message broker" process, sometimes referred to as
the pseudo-kernel, or p-kernel.  The use of the p-kernel enables 
experimentation with means of addressing, and hides the underlying
implementation from the service and application processes.

Communication between a process and the p-kernel uses TCP, in order to
have reliable delivery, and a well-known port, avoiding a need to
bootstrap the connection and resolve addresses between processes.

## Messages

Messages use a simple 32 bit framing header, with a consequent maximum
message size of 4GB (which seems ample, but is it really?)

The p-kernel protocol consists of four messages:
* OpenPort
* OpenedPort
* ClosePort
* ClosedPort
* Message
* Chunk

A process can register zero or more _ports_, which act as the source
and destination of messages.  `OpenPort` requests the allocation of a
new port, and `OpenedPort` confirms the allocation, and returns the new
port's identifier.  `ClosePort` requests destruction of a port, and
`ClosedPort` confirms it.

`Message` is used to both send a message from a process to the p-kernel,
and to deliver a message from the p-kernel to a process.  It contains
a short hader, identifying the source and destination ports, followed
by the payload.  `Chunk` is similar to `Message`, but is used for
streaming data, and includes ordering and positioning data within the
stream.

Both message and chunk payloads are structured binary data.  At this
point, they do not use an IDL, but rather are manually packed and
unpacked by the process code.  It's possible that the runtime library
will acquire some support for this encoding and decoding process.

## Ports

At this point, ports are identified by small integer values.  There is
not yet any provision to pass ports in messages, other than the source
port from which it was sent.

This limitation has implications for a Mach-style capabilities
architecture, and is subject to change in future.

## Application Architecture

Sending messages and chunks is a synchronous operation for the sending
process: control flow blocks until the runtime library has enqueued
the payload for transmission by the host operating system's TCP stack.

Receiving messages is more complicated.  The initial prototype releases
will adopt a single-threaded event-loop structure, with the ability to
poll for arriving messages, or to block control flow until one a
message is delivered (or the request times out).

Future releases might add better support for multiple threads, and/or
adopt a coroutine-style asynchronous model.  Depending on how that
evolves, and explicit RPC structure might also be provided.

On the service side, the blocking dispatch loop model will be directly
supported by the runtime.

## Establishing a Connection

There are two main scenarios in which the IPC mechanism is used:
service-to-service communication, and application-to-service
communication.

### Service to Service Communication

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

### Application to Service Communication

A related problem exists for application code.  Given a long-lived
object identifier, to _use_ that object, the appropriate type
implementation must be started, and must make itself available to the
process wanting access to the object.

This implies some sort of activation functionality that either locates
an existing type implementation instance or starts a new one, and
passes the service port identifier to the requestor.

This is related to eg. DBus activation, which uses reersed domain
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

----
Next: [Runtime](runtime.md) | Up: [Overview](../README.md)
