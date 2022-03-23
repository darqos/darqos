# Terminal Service

The terminal service performs a role not unlike Unix getty or xdm:
it exposes an abstraction of a user's screen, keyboard, mouse,
microphone, speakers, camera, etc, to the system.

This abstraction can then be implemented using physical or logical
devices, based on availability and configuration.  A system without a
terminal is possible, but cannot directly interact with users: it can
provide other services, however.

In the initial milestones, the focus is on a simple terminal.
Dedicated (ie. not shared) physical devices, directly attached to the
host hardware.


While using an underlying Linux kernel, and prior to figuring out the
best abstraction for a graphical display ... I'm pretty tempted to use
SDL2 as the "driver" API.  It could run on Linux/VESA, Linux/X11, or
even macOS, all with the same basic interface.  It's not a great
abtraction, but ... it's a way to get moving.

An alternative might be OpenGLES3?  That'd need some scaffolding
around it, but again, should be fairly portable.


Then we need to handle login.  The terminal service can manage zero or
more configured terminals, and when "activated" it needs to
authenticate the propective user.  So, it should throw some sort of
username/password gathering UI, and collect the details.

It will then need to authenticate the user, and if successful, create
a terminal instance owned by that user, and perform whatever startup
is configured for a user session.

In X11, there's both a system startup and a per-user startup script.
In our case, it might be useful to have a per-configuration startup
option as well, because eg. an SSH session has different needs to a
GUI session.


## M0 Graphical Terminal

The initial graphical terminal is implemented using PyQt5.  The
primary benefit of this over eg. SDL2 is that it has a built-in widget
set, which means we don't need to implement our own.
