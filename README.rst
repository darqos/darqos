darqos
======

DarqOS is an operating system in the broader sense that includes a user
interface and a collection of application and utility programs.

Its goal is to explore, by means of prototyping and daily use, an operating
system that moves beyond a 1970's model of a computer with some 1980's
GUI pasted on top.

Features
--------

* Single user
* Graphical
* For user terminal devices (not servers)
* For experts
* Willing to be incompatible
* Open source

Some things that might be surprising if you're used to other operating
systems:

* It has no files or filesystem
* It doesn't have applications, in the usual sense
* It is not POSIX-compatible
* It is not for everyone; it is not a general-purpose system
* It is incomplete

The goal of DarqOS is, somewhat grandly, to augment human abilities.  This
is not a new notion for computers, but it's also something that's been
more of a marketing ploy than an actual goal, at least for popular,
modern systems.  That said, it's not a "bicycle for the mind" either:
it's much more prosaic.

The system is (very) incomplete.  Its development process is
experimental, with ideas conceived, implemented, tested, and often
discarded or replaced.  There are many aspects of the design that are
prototyped using cobbled-together bits of other things.  It's not
currently a clean and efficient instantiation of its own design.

Target Platforms
----------------

The best way to try it is to use a secondhand Lenovo MiniPC with an
HDMI monitor, a USB mouse and USB keyboard.  Both wired and wireless
Ethernet are supported.  This is the primary development target.

It is also intended to work on tablets and mobile phones.  Some work
has been done on the original PinePhone platform; this will likely
not be an ongoing target platform, but it's a useful experimental
device for now.

At some point, it might begin to target a wider range of desktop and
laptop hardware.  From the DarqOS point of view, these are
more-or-less just different versions of the Lenovo MiniPC, with lots
of diversity in their I/O devices.  It's pretty likely that support
will target very specific models and configurations if and when it
happens.

Further Reading
---------------

* `Roadmap <doc/roadmap.rst>`__

Overview

* `Bootstrap <doc/bootstrap.rst>`__
* `Security <doc/security.rst>`__
* `IPC <doc/ipc.rst>`__
* `Runtime <doc/runtime.rst>`__
* `UX <doc/ux.rst>`__
* `REPL <doc/repl.rst>`__
* `Items <doc/item.rst>`__

Services

* `Storage <doc/storage.rst>`__
* `Metadata <doc/metadata.rst>`__
* `History <doc/history.rst>`__
* `Index <doc/search-service.rst>`__
* `Terminal <doc/terminal.rst>`__

Tools and Types

* `Types <doc/types.rst>`__
* `Tools <doc/tools.rst>`__
* `Bibliography <doc/bib.rst>`__
* `Books <doc/book.rst>`__
* `Audio <doc/audio.rst>`__
* `People <doc/pim.rst>`__
* `Calculations <doc/calculation.rst>`__

Details

* `UI Events <doc/input-events.rst>`__

Contact
-------

At some point, I'll set up something.  Until then, email
d+darq@0x1.org.

Copyright (C) 2022-2026, David Arnold
