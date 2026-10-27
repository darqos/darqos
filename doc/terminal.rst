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
PyQt6.

Wayland provides a display server: a process that controls access to
the display hardware and allows multiple other processes to share
it.  DarqOS uses its own small compositor, rather than an existing one
(see Compositor, below).

PyQt6 offers one primary benefit: a
pre-existing widget set, which avoids us having to implement our own.
Longer term, a simpler widget set should be implemented, avoiding Qt's
breadth and cross-platform support.

The Terminal Service will manage: 1 or 2 HDMI screens, a USB
keyboard, a USB mouse, stereo audio out, mono audio in, and a USB
camera.  The camera is the lowest priority.

On startup, the Terminal Service will use PyQt6 to identify all
available screen, and display a full-screen, most-distant Z-order root
window on each of them.  There will be no support for multi-seat
configuration: multiple screens will all be used by the one
"terminal".

For starters, only the default keymap provided by the Linux layer will
be supported.

The Terminal Service runs as a DarqOS process, spawned by the p-kernel.
It starts the compositor, and decides what it does: the compositor
provides the mechanism, and the Terminal the policy.

Once initialised, the terminal will display a lock screen with an
authentication field, controls to sleep/reboot/shutdown the machine,
and an output volume control with muting.

Once authenticated, the Terminal will hide the lock screen.  At this
point the main "desktop" functionality will be available.  If
*firstrun* mode is active, the manpage reader application should be
started to show the *intro* page, giving the user an overview of the
system controls.

Compositor
~~~~~~~~~~

Several of the Terminal's requirements can only be met inside the
compositor:

- Global hotkeys: a Wayland client only sees keys sent to its own
  windows, so only the compositor can catch *System-N* and friends.
- The lock screen: a client can't stop input reaching other clients,
  so only the compositor can block access to the machine.
- Window layering: Wayland has no "keep this window at the bottom (or
  top)" hint.  Desktop surfaces normally use the layer-shell protocol,
  but ``layer-shell-qt`` has no Python bindings.  Our compositor
  doesn't need it: it identifies the Terminal's connection, and places
  its surfaces by policy.
- No decorations, with windows moved by dragging their margin, and
  resized by dragging their border.

Existing compositors are opinionated about window management and
decorations, and those that could be extended far enough (eg. Wayfire,
with plugins) would mean writing compositor code anyway, while working
around their defaults.  So, the 1st Edition compositor is forked from
``tinywl``, the small reference compositor in the wlroots source tree,
built against wlroots 0.18 (as packaged in Debian 13).  ``tinywl``
already handles windows (xdg-shell), keyboard focus, the cursor, and
interactive move and resize.

The compositor adds:

- Four layers: background, applications, overlay, and lock.  The
  Terminal's windows are placed in the background, overlay, or lock
  layers; all other clients' windows go in the applications layer.
- No decorations: it tells clients that it draws decorations
  (xdg-decoration, server-side mode), and then draws nothing.
- System-key combinations are not delivered to clients, but passed to
  the Terminal, which decides what they do.
- While locked, input goes only to the lock layer.

It's written in C, and kept to mechanism: what a hotkey does, which
panel to show, and when to lock, are all decided by the Terminal.

Starting the Compositor
~~~~~~~~~~~~~~~~~~~~~~~

The Terminal is the session: if it dies, the display is useless.  So
the Terminal starts the compositor, as an ordinary Linux child process
(not a DarqOS process), and the compositor dies with it.  In
``darq_main``, before creating the ``QApplication``, the Terminal:

1. Creates a socket pair, and starts the compositor with one end,
   setting ``PR_SET_PDEATHSIG`` in the child so it exits if the
   Terminal does.
2. Sets ``WAYLAND_SOCKET`` to the other end.  ``wl_display_connect()``
   uses ``WAYLAND_SOCKET`` in preference to ``WAYLAND_DISPLAY``, so Qt
   connects over the socket pair without further code.
3. Waits for the compositor to report that it's ready.

The Wayland connection over the socket pair identifies the Terminal to
the compositor: no other client can connect over it, so no other proof
is needed.  The policy channel between them (hotkeys, locking, and
layer placement) uses the same pair, or a second one.

The compositor also listens on a public socket, ``darq-0``.  The
p-kernel sets ``WAYLAND_DISPLAY=darq-0`` and
``QT_WAYLAND_DISABLE_WINDOWDECORATION=1`` in the environment of the
processes it spawns, so other GUI applications connect there, and Qt
doesn't draw its own decorations if the compositor's request isn't
honoured.

Session and Login
~~~~~~~~~~~~~~~~~

The compositor needs a *seat*: access to the display and input
devices, which is revoked when switching virtual terminals.  This is
provided by logind (``systemd-logind``), through libseat, which
wlroots uses.

logind gives devices to processes in an active login session, so the
p-kernel's systemd unit is a login session: it runs as the ``darq``
user, with ``PAMName=``, ``TTYPath=/dev/tty1``, and
``Conflicts=getty@tty1.service``.  The p-kernel, the Terminal it
spawns, and the compositor the Terminal starts, all inherit that
session.  logind also provides:

- The runtime directory, ``/run/user/<uid>``, created by
  ``pam_systemd``, where the Wayland sockets live.
- Access to audio and camera devices for the active session.
- Power button and lid handling, and inhibitor locks for sleep and
  idle.

The machine auto-logs-in to this one Linux session: DarqOS users exist
only within it, and the Terminal manages DarqOS login itself, using its
lock screen.  A display manager like greetd isn't used, because it
would start the p-kernel itself, rather than via its systemd unit.

seatd is the fallback, if a PAM session turns out to be awkward: the
``darq`` user would be a member of seatd's group, and the p-kernel's
unit would create the runtime directory itself (``RuntimeDirectory=``).
Switching only changes units and groups, not code.

Until the session is set up, the p-kernel runs as root, and so does
the compositor, using libseat's built-in backend.

Keyboard
~~~~~~~~

The default keymap will more-or-less replicate the PC105/Win95
standard keyboard.  A standard PC keyboard will map:

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
windows: the equivalent of Alt-Tab.  The compositor passes the
switching hotkey to the Terminal, which shows the switcher, and asks
the compositor to raise and focus the chosen window.

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
using PyQt6 to provide the GUI, on a Qt event loop (as lenses are).

It will communicate using the system IPC with the p-kernel.  It will
interact with other services to populate the New, Search, and Event
overlays.
