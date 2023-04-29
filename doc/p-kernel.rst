p-Kernel
========

In its first edition, DarqOS uses a Linux kernel, Python interpreter,
and Qt GUI libraries to host a collection of Linux applications that
form the system implementation.

There Linux processes make use of a *pseudo kernel* or *p-kernel*: a
Linux process that provides an API matching the eventual set of
required system calls via a network interface.

For each programming language used for DarqOS components, a runtime
library exposes these virtual system calls as local functions that
call out to the network interface of the *p-kernel*.

Implementation
--------------

The *p_kernel* interface is exposed via a TCP socket.  A custom binary
protocol is used to encode messages between the language runtime
libraries and the *p-kernel* Linux process.
