
What is DarqOS?
========================================================================

*DarqOS* is an operating system, in the broader sense of the term,
that includes a user interface and a collection of application and
utility programs.

Its goal is to explore, by means of prototyping and daily use, an
operating system that moves beyond a 1970s model of a computer with a
1980s GUI pasted on top, running a collection of 1990s applications.

.. admonition:: The Name

   The name is a (poor) joke.

   Video terminals started out with dark backgrounds and coloured
   text.  To begin with, they were expensive, and the common people
   made do with DECwriters and their ilk, which printed dark text on a
   light paper.  Then, in the 1980s, graphical user interfaces
   switched to light backgrounds with dark text, echoing the printed
   output of their grandparents.  And currently, since some time in
   the 2000s, there's a trend for applications aimed at professional
   users to use an interface with light text on a dark background.
   
   As DarqOS is intended for expert users, and with bit of a nod to
   Quarterdeck's DESQview (and its precursor, Desq), it is called
   "Darq", and has light text on a dark background.

   
Features and Limitations
------------------------------------------------------------------------

The DarqOS system is:

* Single user
* Graphical
* For end-user terminal devices (not servers)
* For experts
* Willing to be incompatible
* Open source

Some immediate things the might be surprising if you're used to other
operating systems:

* It has no files or filesystem
* There is no support for third-party applications
* It doesn't even have applications, in the usual sense
* It is not POSIX (or Windows) compatible
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

DarqOS is firmly rooted in the tradition of crafting one's own tools.
It is *my* operating system, very opinionated, and is unlikely to work
well for other people.  But perhaps it will inspire others to write
their own operating systems that do.

This book is a combination of rationale, justification, documentation,
and history.


Implementation Strategy
------------------------------------------------------------------------

In keeping with the goal of being an experimental platform, the
implementation of the system leverages available hardware and software
as far as possible, without compromising its other goals.

Of particular note, the current implementatino is *not* an operating
system proper, but rather a collection of system and application
services running on a stripped-down Linux system.  This introduces
many unnecessary elements to the implementation, but at the same time
makes development convenient and fast.

A future release will rehost the system onto a lightweight dedicated
kernel.

Editions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

While the development process is driven in part by the results of
experimentation and use of the system to date, it is somewhat planned,
and organised into milestone releases, called *editions*.  Each
edition represents a snapshot of the system at a point in time where
significant new capability has been introduced compared to the
previous editions.

This book describes the *First Edition* system, together with some
plans for future editions, obviously subject to change.



Target Platforms
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The sole supported platform for the First Edition is a Lenovo mini PC
(AMD64, Intel i5 CPU), running Debian 13, with an HDMI monitor, a USB
mouse, and a USB keyboard.  Both wired and wireless Ethernet are
supported, as are an external stereo audio speaker and microphone.

Future editions are intended to work on laptops, tablets and mobile
phones.  Some early work has been done on the original PinePhone
platform; this will likely not be an ongoing target platform, but it's
a useful experimental device for now.

Some work was also done on a Raspberry Pi 400 device, but while a nice
form-factor, the system is significantly less capable (and
increasingly expensive) when compared to a refurbished enterprise PC
desktop.

At some point, it might begin to target other laptops and desktop PCs.
From the DarqOS point of view, these are more-or-less just variations
on the first edition's mini PC, with more diversity in their I/O
devices.  It's very likely that support will target very specific
models and configurations when it happens.
