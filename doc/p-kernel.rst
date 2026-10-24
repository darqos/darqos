p-Kernel
========

In its first edition, DarqOS uses a Linux kernel, Python interpreter,
and Qt5 GUI libraries to host a collection of Linux applications that
form the system implementation.

These Linux processes make use of a *pseudo kernel* or *p-kernel*: a
Linux process that provides an API matching the eventual set of
required system calls via a network interface, and maintains the shared
state that would be maintained by the real kernel.

For each programming language used for DarqOS components, a runtime
library exposes these virtual system calls as local functions that
call out to the network interface of the *p-kernel*.

Implementation
--------------

The *p_kernel* interface is exposed via a TCP socket.  A custom binary
protocol is used to encode messages between the language runtime
libraries and the *p-kernel* Linux process.

Python
~~~~~~

A Darq Python application is launched by the p-kernel starting the
`pyloader` Linux executable as a new Linux process.

The main thread of the `pyloader` process runs an event loop handling
messages to and from the p-kernel.  It also spawns a second thread for
the application code, starting the `darq_main` function in the
application's `main` module as its entry point.

The runtime library API is comprised of functions that emulate kernel
system calls.  These pseudo system calls, or *p-calls* fall into two
general types: those requests that require a request to the p-kernel
for resources or functionality, and those that can operate solely on
local (process) resources.

p-calls that require a request/response cycle from the p-kernel block
the calling thread until they're unblocked by the background thread
following the delivery of the response to their request.  In this way,
they behave similarly to a standard Unix system call.  Also supported
are an await-able async model, and a traditional Unix-style callback
model.

Communication between the application thread(s) and the background
p-kernel thread is via a _queue_, and an associated socketpair that
is used to interrupt the background thread's event loop.

p-calls that require access only to local resources might require
mutual exclusion while accessing local state, but can otherwise run to
conclusion and return to their caller.

The background thread can also handle unsolicited messages from the
p-kernel, and queue them for propagation to the application threads.

In the case of the IPC messaging system, messages are routed via the
p-kernel.  The p-calls pass sent messages to the background thread, which
queues and then sends them to the p-kernel.  The p-kernel distributes
these messages to their destination processes, where they are delivered
to the application thread.

Like Unix, delivered messages are queued.  A call to recv()
will either return an error or block if there are no messages pending
for a port.  Some equivalent of select/poll will be needed to enable
CPU-efficient waiting for messages to arrive from multiple ports.

At this point, TCP/IP networking is implemented locally by the wrapping
the host OS stack in the language runtime library, not via calls to the
p-kernel.
