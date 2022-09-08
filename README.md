# darqos

DarqOS is an operating system in the broader sense that includes a user 
interface and a collection of application and utility programs.

Its goal is to explore, by means of prototyping and daily use, an operating
system that moves beyond a 1970's model of a computer with some 1980's
GUI pasted on top.

## Features
* Single user
* Graphical
* For user terminal devices (not servers)
* For experts
* Willing to be incompatible
* Open source

Some things the might be surprising if you're used to other operating
systems:
* It has no files or filesystem
* It doesn't have applications, in the usual sense
* It is not POSIX-compatible
* it is not for everyone; it is not a general-purpose system
* It is incomplete

The goal of DarqOS is, somewhat grandly, augment human abilities.  This
is not a new notion for computers, but it's also something that's been
more of a marketing ploy than an actual goal, at least for popular,
modern systems.  That said, it's not a "bicycle for the mind" either:
it's much more prosaic.

The system is (very) incomplete.  Its development process is
experimental, with ideas conceived, implemented, tested, and often
discarded or replaced.  There are many aspects of the design that are
prototyped using cobbled-together bits of other things.  It's not
currently a clean and efficient instantiation of its own design.

## Target Platforms

The best way to try it is to use a Raspberry Pi with an HDMI monitor
and a USB mouse and keyboard.  Both wired and wireless Ethernet are
supported.  This is the primary development target.

It is also intended to work on tablets and mobile phones.  Some work
has been done on the original PinePhone platform; this will likely
not be an ongoing target platform, but it's a useful experimental
device for now.

At some point, it will begin to target laptops and desktop PCs.  From
the DarqOS point of view, these are more-or-less just bigger and
faster versions of a Raspberry Pi, with lots of diversity in their
I/O devices.  It's pretty likely that support will target very specific
models and configurations when it happens.

## Further Reading

* [Roadmap](doc/roadmap.md)

Overview
* [Bootstrap](doc/bootstrap.md)
* [Security](doc/security.md)
* [UX](doc/ux.md)
* [REPL](doc/repl.md)
* [Runtime](doc/runtime.md)
* [Ports](doc/ports.md)

Services
* [History](doc/history.md)
* [Metadata](doc/metadata.md)
* [Index](doc/index.md) 
* [BLOB](doc/blob.md)
* [Terminal](doc/terminal.md)

Tools and Types
* [Types](doc/types.mg)
* [Tools](doc/tools.md)
* [Bibliography](doc/bib.md)
* [Books](doc/book.md)
* [Music](doc/music-player.md)
* [People](doc/pim.md)

Details
* [UI Events](doc/input-events.md)

## Contact

At some point, I'll set up something.  Until then, email d+darq@0x1.org


Copyright (C) 2022, David Arnold
