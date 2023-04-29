======
DarqOS
======

Introduction
============

DarqOS is an operating system in the broader sense that includes a user
interface and a collection of application and utility programs.

Its goal is to explore, by means of prototyping and daily use, an operating
system that moves beyond a 1970's model of a computer with some 1980's
GUI pasted on top.

Features and Limitations
------------------------

The system is:

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

Implementation Strategy
-----------------------

In keeping with the goal of being an experimental platform, the
implementation of the system leverages available hardware and software
as far as possible, without compromising its other goals.

Of particular note, the current implementatino is *not* an operating
system proper, but rather a collection of system and application
services running on a stripped-down Linux system.  This introduces
many unnecessary elements to the implementation, but at the same time
makes development convenient and fast.

A future edition will rehost the system onto a lightweight dedicated
kernel.

Target Platforms
----------------

The sole supported platform for the first edition is the Raspberry Pi
400 with an HDMI monitor, a USB mouse, USB keyboard, and USB
"soundcard".  Both wired and wireless Ethernet are supported.  While
not tested, it's very likely that a Raspberry Pi 4 will also work, if
you can get one.

Future editions are intended to work on tablets and mobile phones.
Some work has been done on the original PinePhone platform; this will
likely not be an ongoing target platform, but it's a useful
experimental device for now.

At some point, it will begin to target laptops and desktop PCs.  From
the DarqOS point of view, these are more-or-less just bigger and
faster versions of a Raspberry Pi, with lots of diversity in their
I/O devices.  It's pretty likely that support will target very specific
models and configurations when it happens.
