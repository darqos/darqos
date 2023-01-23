# Terminal Service

The terminal service performs a role not unlike Unix getty or xdm:
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


## 1st Edition Graphical Terminal

The initial graphical terminal is implemented using PyQt5.  The
primary benefit of this over eg. SDL2 is that it has a built-in widget
set, which means we don't need to implement our own.

For the RPi4 target, it uses the Qt EGLFS build, avoiding both X11 and
Wayland but instead talking more-or-less directly to the GPU.  This
will avoid some overhead, at the cost of introducing some constraints
from the Qt EGL backend.  It seems like a reasonable starting point
though.

For the RPi4, the Terminal Service will manage: 1 or 2 HDMI screens, a
USB keyboard, a USB mouse, stereo audio out, mono audio in, and a USB
camera.  If using the RPi400, audio will need a USB audio dongle.

On startup, the Terminal Service will use PyQt5 to identify all
available screen, and display a full-screen root window on each of
them.  There will be no support for multi-desk configuration: multiple
screens will all be used by the one "terminal".

Via PyQt5, libinput will be used to gather input from an attached USB
keyboard and mouse, and either the RPi4's built-in audio I/O or a USB
audio dongle will be used for the RPi400.  For starters, only a
default keymap will be supported.

It would be good to have camera support, but ... maybe not for 1st
Edition.

Once all devices have been identified and initialised, the first
screen will show both a sleep/power-off panel and a login panel.

The login panel will accept a password in the usual way, and once
authenticated, will remove the control and login panels, and enable
the rest of the workspace.

If _firstrun_ mode is active, the manpage reader application should be
displayed, with the _intro_ page showing.  This will give the user an
overview of the system controls.

## Keyboard

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
- AltFr : Option_R

## Functions

The Terminal Service is repsonsible for providing several crucial
functions of the user interface.  In 1st Edition, these will be:

### New

The new panel is the interface used to create new objects.

The new panel is invoked with a _System-N_ shortcut.

Objects are created by selecting a _type_ of object to create.  Object
types are implemented by installed code, more-or-less like
applications in a traditional operating system.  Examples of object
types might be "image", "document", "movie", etc.


### Search

The search panel is the central interface for locating objects of
interest to the user.  It integrates access to all the searching
abilities of DarqOS, plus access to Internet search.

The search panel is invoked with a _System-S_ shortcut.

Searching is driven via a seach-box style input field with slash
commands.


### Events

The events panel is the central location for exposing to the user
changes to the system state that occur asynchronously, without direct
user control.  It encompasses the functionality of the system
notification pane, email inbox, and messaging.

The events panel is invoked with a _System-E_ shortcut.  When not
displayed, ephemeral event notifications can also be displayed
overlaying the primary screen, alerting the user to their event's
occurence. These notification should be largely unobtrusive: able to
be noted, and ignored, not stealing focus, and disappearing after a
short time.

The events panel itself shows a time-ordered list of event summaries.
This might take the form of an email sender and subject, or a chat
message nick and first few words.  All events are shown with a
timestamp.

Each event in the list has two visible _states_: unacknowledged, and
incomplete.  _Unacknowledged_ events are like unread emails:
highlighted as being as yet "unseen".  _Incomplete_ events have been
seen or read, but have not been dealt with.  The user can alter an
event's state manually, or perform an action that caused it to be
altered implicitly.

The list of events can be searched and/or filtered by the user.  A
search-box style input allows the user to enter a full-text search
string, plus slash command filtering parameters, like:

  `/type:email /sender:user@domain keyword keyword`

The set of slash-commands will be augmented over time.  1st Edition
will provide a few basic operations.


## Future Editions

Future editions will move towards using a dedicated base OS, and
consequently, most likely no longer using Qt.  It's likely that EGL
will continue to be used as the abstraction for the underlying GPU,
with a graphical toolkit overlying that.  On option would be to use a
widget server model, perhaps similar to O/mero from PlanB.  Or it
might be a more traditional library-based approach.
