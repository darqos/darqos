# Terminal Service

The terminal service performs a role not unlike Unix getty: it exposes
an abstraction of a user's screen, keyboard, mouse, microphone,
speakers, camera, etc, to the system.

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
