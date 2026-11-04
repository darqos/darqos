p-Kernel
========

In its first edition, DarqOS uses a Linux kernel, a Linux Python
interpreter, a custom Wayland compositor, and the Qt6 GUI libraries to
host a collection of Linux processes that form the system
implementation.

These Linux processes make use of a *pseudo kernel* or *p-kernel*: a
Linux process that provides an API matching the eventual set of DarqOS
kernel system calls.  The *p-kernel* process emulates the eventual
DarqOS kernel, maintaining the state that would be managed by the real
kernel, and implementing its functionality using the equivalent
underlying Linux features.

Application processes communicate with the *p-kernel* using a private
network protocol.  It transmits messages identifying the system calls
to be invoked, passing their parameters, and returning their results.

For each programming language used for DarqOS components, a runtime
library exposes these virtual system calls as local functions that
call out to the network interface of the *p-kernel*.  In the first
edition, Python is the only supported language.

Python
------

A Darq Python application is launched by the *p-kernel* starting the
`pyloader` script as a new Linux process, with the *p-kernel*'s own
Python interpreter virtual environment, not whichever `python` is on
`PATH`.

The main thread of the `pyloader` process spawns a background thread
that runs an event loop handling messages to and from the *p-kernel*.
The main thread runs the application code, starting from the
`darq_main` function in the application's `main` module as its entry
point.

The runtime library API is comprised of functions that emulate kernel
system calls.  These pseudo system calls, or *p-calls* fall into two
general types: those requests that require a request to the *p-kernel*
for resources or functionality, and those that can operate solely on
local (process) resources.

There are three models for calling DarqOS runtime *p-call* functions
that require a request/response cycle from the *p-kernel*: blocking,
callback, or async.

The blocking model behaves most like traditional system calls.  The
calling thread is blocked until a response is received from the
*p-kernel*.  The background thread packages the call arguments, sends
the request, runs its event loop while waiting for the reply, which
then unblocks the calling thread.

The callback model requires the application thread to run an event
loop.  It can be well-suited to use with a GUI framework, ensuring
that the GUI thread is not blocked while system calls execute.  Each
call supplies a callback parameter which is invoked by the application
thread's event loop once the *p-kernel*'s reply is received.

The async model uses the Python `asyncio` module and event loop.
*p-calls* return a future, and must be `await`-ed in the application
code in the usual *async* style.

Communication between the application thread(s) and the background
*p-kernel* thread is via a queue, and an associated `socketpair` that
is used to interrupt the background thread's event loop.

*p-calls* that require access only to local resources might require
mutual exclusion while accessing local state, but can otherwise run to
conclusion and return to their caller.

The background thread can also handle unsolicited messages from the
*p-kernel*, and queue them for propagation to the application threads.

DarqOS processes communicate with each other using the DarqOS Inter
Process Communication (DIPC) system, implemented by the *p-kernel*.
Like other system calls, sending and receiving DIPC messages is routed
via the *p-kernel*.  The *p-call*s pass sent messages to the
background thread, which queues and then sends them to the *p-kernel*.
The *p-kernel* distributes these messages to their destination
processes, where they are delivered to the application thread.

Like Unix, delivered messages are queued.  A call to `recv()` will
either return an error or block if there are no messages pending for a
port.  It is also possible to wait for messages on multiple ports at
once.

Linux
-----

DarqOS applications use an unmodified Linux Python interpreter, and so
it is entirely possible for them to call Linux system calls directly.
In a future edition, when the system shifts to the DarqOS kernel, the
Python interpreter will no longer have access to these calls, and any
such usage will break.

In the first edition, the *p-kernel* provides the IPC system, a means
of spawning new processes, killing existing process, and some simple
calls for access to process state.  As part of the development
process, use of Linux features will be reviewed and appropriate
additions made to the *p-kernel* (and ultimately Darq kernel)
implementation.

The main areas of direct Linux kernel usage in first edition are:

* file access for persistence, often using Sqlite,
* GPU access via the DRM for the Terminal service, and
* the TCP/IP stack.
