Terminal Service
================

The terminal service performs a role not unlike Unix `getty` or `xdm`:
it exposes an abstraction of a user's screen, keyboard, mouse,
microphone, speakers, camera, etc, to the system.

This abstraction can then be implemented using physical or logical
devices, based on availability and configuration.  A system without a
terminal is possible, but cannot directly interact with users: it can
provide other services, however.

In the initial editions, the focus is on a simple terminal.
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


First Edition Graphical Terminal
--------------------------------

The initial graphical terminal is implemented using Wayland and
PyQt5.

Wayland provides a display server: a process that controls access to
the display hardware and allows multiple other processes to share
it.  It's likely that we'll use the Weston compositor, although that
will depend on experimentation: in general, we require few services
beyond basic compositing.

PyQt5 (and/or PyQt6 or PySide6) offer one primary benefit: a
pre-existing widget set, which avoids us having to implement our own.
Longer term, a simpler widget set should be implemented, avoiding Qt's
breadth and cross-platform support.

For the RPi4 target, the Terminal Service will manage: 1 or 2 HDMI
screens, a USB keyboard, a USB mouse, stereo audio out, mono audio in,
and a USB camera.  If using the RPi400, audio will need a USB audio
dongle.

For the PC target, we'll support basically the same set of
peripherals; in both cases, the camera is the lowest priority.

On startup, the Terminal Service will use PyQt5 to identify all
available screen, and display a full-screen, most-distant Z-order root
window on each of them.  There will be no support for multi-seat
configuration: multiple screens will all be used by the one
"terminal".

For starters, only the default keymap provided by the Linux layer will
be supported.

The Terminal Service will run as a Linux process.  It will use Wayland
to direct user input (mouse/keyboard) to different processes running
on the display server: it won't have any direct control over this
process -- this is the domain of the Wayland compositor.

Once initialised, the terminal will display a lock screen with an
authentication field, controls to sleep/reboot/shutdown the machine,
and an output volume control with muting.

Once authenticated, the Terminal will hide the lock screen.  At this
point the main "desktop" functionality will be available.  If
*firstrun* mode is active, the manpage reader application should be
started to show the *intro* page, giving the user an overview of the
system controls.

Keyboard
~~~~~~~~

The default keymap will more-or-less replicate the PC105/Win95
standard keyboard.  The RPi400 built-in keyboard will support the
following mappings:

- System : the Raspberry Pi key
- Application : Alt_L
- Control_L : Caps Lock
- AltGr : Alt_R

A standard PC keyboard will map:

- System : the Windows key
- Application : Alt_L
- Control_L : Caps Lock
- AltGr : Alt_R

A standard Mac keyboard will map:

- System : Option
- Application : Command
- Control_L : Caps Lock
- AltGr : Option_R

Functions
---------

The Terminal Service is responsible for providing several crucial
functions of the user interface.  In 1st Edition, these will be:

New
~~~

The new panel is the interface used to create new objects.

The new panel is invoked with a *System-N* shortcut.

Objects are created by selecting a *type* of object to create.  Object
types are implemented by installed code, more-or-less like
applications in a traditional operating system.  Examples of object
types might be "image", "document", "movie", etc.

Search
~~~~~~

The search panel is the central interface for locating existing
objects of interest to the user.  It integrates access to all the
searching abilities of DarqOS, plus access to Internet search.

The search panel is invoked with a *System-S* shortcut.

Searching is driven via a seach-box style input field with slash
commands.

Events
~~~~~~

The events panel is the central location for exposing to the user
changes to the system state that occur asynchronously, without direct
user control.  It encompasses the functionality of the system
notification pane, email inbox, and messaging.

The events panel is invoked with a *System-E* shortcut.  When not
displayed, ephemeral event notifications can also be displayed
overlaying the primary screen, alerting the user to their event's
occurence. These notification should be largely unobtrusive: able to
be noted, and ignored, not stealing focus, and disappearing after a
short time.

The events panel itself shows a time-ordered list of event summaries.
This might take the form of an email sender and subject, or a chat
message nick and first few words.  All events are shown with a
timestamp.

Each event in the list has two visible *states*: unacknowledged, and
incomplete.  *Unacknowledged* events are like unread emails:
highlighted as being as yet "unseen".  *Incomplete* events have been
seen or read, but have not been dealt with.  The user can alter an
event's state manually, or perform an action that caused it to be
altered implicitly.

The list of events can be searched and/or filtered by the user.  A
search-box style input allows the user to enter a full-text search
string, plus slash command filtering parameters, like:

  `/type:email /sender:user@domain keyword keyword`

The set of slash-commands will be augmented over time.  1st Edition
will provide a few basic operations.

Menu
~~~~

A system menu will be available at any time, accessed perhaps using
the combination of the System key and a context-click (?) providing an
alternative method of invoking the New, Search, or Event overlays,
plus the ability to Lock, Logout, Reboot, or Shutdown the system.

Switcher
~~~~~~~~

The Terminal service should provide a means of switching between open
windows: the equivalent of Alt-Tab.  This might end up being
implemented by Wayfire, initially.

Object Properties
~~~~~~~~~~~~~~~~~

There should be a way to easily access the system information about an
object.  This would include metadata, indexing, and history.  You
should be able to edit the metadata -- eg. add a tag, view the
history, and .. not sure about indexing?

This might be another system-wide hotkey?


Future Editions
---------------

Future editions will move towards using a dedicated base OS, and
consequently, most likely no longer using Qt.  It's likely that EGL
will continue to be used as the abstraction for the underlying GPU,
with a display manager overlying that.  Type Lenses and Tools will use
a standard widget set to provide UI elements.  One option would be to
use a widget server model, perhaps similar to O/mero from PlanB.  Or
it might be a more traditional library-based approach.

It's also possible that we'll move away from Wayland and/or Wayfire to
a dedicated compositor.


Architecture
------------

The Terminal service will be implemented as a Linux Python process,
using PyQt5 to provide the GUI, and a callback-based Qt event loop (as
is used by various Qt-based type lenses).

It will communicate using the system IPC with the p-kernel.  It will
interact with other services to populate the New, Search, and Event
overlays.
