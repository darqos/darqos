Processes
=========

The prototype milestones are implemented as an overlay layer above an
existing Unix system, using the Qt GUI toolkit to implement the user
interface.

The p-kernel is implemented as a Unix process, and other processes use a
TCP connection to communicate with the p-kernel.  "System calls" exist as
messages sent between application processes and the p-kernel.

Application processes are Unix processes, and at this point no work is
contemplated for a different process / threading model than is provided
but the host OS.  While it should be possible to write applications
using any Unix-hosted language, the only anticipated runtime for the
prototype series milestones is for Python3.

Classes of Applications
-----------------------

Four classes of applications are planned:

* Services
* Type implementations
* Lenses
* Tools

Services
~~~~~~~~

The DarqOS system is comprised of a small kernel and a suite of supporting
services that provide most of the functionality available to the user.
These services are implemented as application processes.

Services generally respond to requests from other application processes,
and so need an asynchronous, event-handling application model.  For the
prototype series, we'll use a traditional Unix-style event loop and
callbacks.

Type Implementations
~~~~~~~~~~~~~~~~~~~~

Type implementations provide the logic implementing MIME-style types, and
the actions that can be performed upon them.  They have no UI, only an
API exposed via the p-kernel IPC.  They are, in effect, object servers,
that in turn utilise system services for eg. storage.

Being essentially a network service, they will be implemented using an
asynchronous, callback-style application architecture.  On startup, they
will begin listening for IPC (network) requests, and servicing them.

For these applications, a traditional Unix server asynchronous model with
an event loop and callbacks will work.  It's possible that Python's
`asyncio` could be used too, but I'll leave that for a later experiment.

Lenses
~~~~~~

A *lens* is an application that exposes a type implementation to the user
via a UI context.  For example, a "plain text" type implementation might
have a lens for a full GUI context, and a different lens for a CLI/REPL
context, and another for a voice assistant context.

While it hasn't been worked out yet, it's likely that multiple lenses
might be observing the same type instance simultaneously.  Consequently,
the lens implementation model cannot assume that it is the initiator of
all events occurring for a type instance: it must be able to react to
asynchronous events.

So, again, the best model for a lens implementation is probably an event
loop system.

Tools
~~~~~

A *tool* is an implementation of _functionality_ rather than *data*
(like a type implementation).  A tool is used to perform a task that
might manipulate, create, or destroy objects, but doesn't have persistent
state of its own.

Tools can support different UI contexts: graphical, REPL, auditory, etc.
In some cases, the tool will never *respond* to external events: it will
direct the flow of work for its task, and be done.

So, it's possible that tools could use a synchronous application model,
and that in those cases where this is natural, using an asynchronous
model would have a high impedance.

If considering only the functionality, it might make sense to have tools,
like all other processes, adopt an asynchronous, reactive event model.
But conversely, if experimentation proves that a synchronous model is
more natural, that would inform future developments.

The cost of a synchronous model is a lot of dupliation in the runtime
library supporting both callback-driven and blocking styles of API use.

Implementation
--------------

All application processes of the four classes discussed should be
implemented using their respective base class.  The base class will
initialise the process, establish a connection with the p-kernel, and
hand off execution to the `main` function (method).  It's possible that
there will end up being only two base classes: asynchronous and
synchronous, but for now we'll try the four.
