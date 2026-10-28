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


Development Terminal
--------------------

`tools/devterm` is a development tool: an ordinary window for
creating items, browsing History, and shutting down or rebooting.  It
was written to try types and lenses before the compositor existed,
and is kept alongside the real Terminal, sharing its New and History
parts (``darq.ui``).  It's started at boot, last, when
`DARQ_DEV_TERMINAL=1` is set.

The real Terminal is started at boot, unless `DARQ_TERMINAL=0` is set.
Run the p-kernel from the desktop session: with the Terminal, the
compositor is then a window on the host's desktop, and devterm and
the lenses are windows in it; without it, they're windows on the
host's desktop.  Build the compositor first (see Building and Trying
It, below).  From the top of the tree, with the virtual environment's
Python first on the path (the p-kernel and its loaders run `python`),
and the tree on `PYTHONPATH` (for the p-kernel's own `import
darq`)::

    . venv313/bin/activate
    PYTHONPATH=$PWD DARQ_RUN=./run DARQ_DEV_TERMINAL=1 kernel/pkernel

Add `DARQ_TERMINAL=0` for devterm alone, on the host's desktop.  With
the Terminal, devterm waits (up to 10 seconds) for the compositor's
socket before it starts: it may not be listening yet.

It has:

* New: the implemented types (`ListTypes`), greyed out if they have no
  Terminal lens.  Create makes an item, records `created` (source
  `devterm`), and starts its lens (`darq.launch`).
* History: events between two times, filtered by type, event, and
  source, newest first, a page at a time.  Double-clicking one starts
  a lens for its item.
* System: Shutdown and Reboot (it's a system process, so it may), and
  Quit, which closes just this window.

It creates and uses items with the Terminal's launcher, so it doesn't
freeze while a lens starts (see Architecture, below).


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
available screens, and display a full-screen background window on
each of them, in the compositor's background layer.  There will be no
support for multi-seat configuration: multiple screens will all be
used by the one "terminal".

The Terminal Service runs as a DarqOS process, spawned by the p-kernel
at boot.  It starts the compositor, and decides what it does: the
compositor provides the mechanism, and the Terminal the policy.  The
Terminal is the session: if it exits or crashes, or can't be started,
the p-kernel reboots.  If it fails within 30 seconds of starting, three
boots in a row, it's taken to be broken, and the p-kernel shuts down
instead, with status 1 (the count is kept across the reboot, which is
an ``exec``, in ``DARQ_TERMINAL_FAILURES``).

Once initialised, the terminal displays the lock screen: a password
field, and nothing else (sleep, and the volume control, come later).
On the first boot, it asks for a new password instead, and sets it
with the Security service.  1st Edition has only a password: there are
no user names.

Once the password is accepted, the Terminal hides the lock screen, and
the hotkeys (see :doc:`ux`) are available.  If *firstrun* mode is
active, the manpage reader application should be started to show the
*intro* page, giving the user an overview of the system controls.

Compositor
~~~~~~~~~~

Several of the Terminal's requirements can only be met inside the
compositor:

- Global hotkeys: a Wayland client only sees keys sent to its own
  windows, so only the compositor can catch *Sys-n* and friends.
- The lock screen: a client can't stop input reaching other clients,
  so only the compositor can block access to the machine.
- Window layering: Wayland has no "keep this window at the bottom (or
  top)" hint.  Desktop surfaces normally use the layer-shell protocol,
  but ``layer-shell-qt`` has no Python bindings.  Our compositor
  doesn't need it: it identifies the Terminal's connection, and places
  its surfaces by policy.
- No decorations, with windows moved and resized from the keyboard
  and mouse (see :doc:`ux`).

Existing compositors are opinionated about window management and
decorations, and those that could be extended far enough (eg. Wayfire,
with plugins) would mean writing compositor code anyway, while working
around their defaults.  So, the 1st Edition compositor is forked from
``tinywl``, the small reference compositor in the wlroots source tree,
built against wlroots 0.18 (as packaged in Debian 13).  ``tinywl``
already handles windows (xdg-shell), keyboard focus, the cursor, and
interactive move and resize.  It runs on libwayland-server's event
loop (``wl_display_run``), which the wlroots backends use too: the
policy channel is one more file descriptor on it, so the compositor
stays single-threaded.

The compositor adds:

- Four layers: background, applications, overlay, and lock.  The
  Terminal's windows are placed in the background, overlay, or lock
  layers; all other clients' windows go in the applications layer.
  The Terminal names a window's layer in its title (``darq:background``,
  ``darq:overlay``, or ``darq:lock``), which is honoured only on the
  Terminal's own connection; its windows with any other title go in
  the overlay layer.  The layer is decided by the title at the
  window's first commit (Qt sets it before then), and a later title
  doesn't move it.
- Background and lock windows fill an output: the one the client asks
  for with ``set_fullscreen`` (as Qt's ``showFullScreen`` does), or else
  the cursor's, and they're refitted when outputs change.  Other
  windows pick their own size, and are centred on the cursor's output
  when they're mapped.
- No decorations: it tells clients that it draws decorations
  (xdg-decoration, server-side mode), and then draws nothing.
- Focus follows the mouse, sloppily: into application and overlay
  windows, but not into the background, or out of an overlay window
  (one of the Terminal's panels keeps the focus until it's gone).  A
  click focuses and raises the window, within its layer, background
  included.  A newly mapped window takes the focus, except a
  background, unless nothing has it; when the focused window goes, the
  frontmost window in the highest layer that can have it does.
- While *Sys-w* is held, the focused application window moves with
  the mouse (no button needed), or 16 pixels at a time with the arrow
  keys, and is resized 16 pixels at a time with t, s, b, and n
  (taller, shorter, broader, narrower), within the client's limits.
  The keys repeat while they're held.  It ends when *w* or System is
  released.  This is all done in the compositor: the Terminal isn't
  involved, and while *w* is held, those keys aren't hotkeys.
- Keys pressed with the System modifier are never delivered to
  clients: those the Terminal has bound are passed to it, and the
  rest are dropped.
- A list of windows, reported to the Terminal, with commands to focus,
  raise, and close them, for the switcher and *Sys-x*.
- It starts locked.  While locked, the applications and overlay layers
  aren't shown, and input goes only to the lock layer, so nothing is
  reachable until the Terminal unlocks it.  Locking takes the focus
  from anything not in the lock layer; unlocking gives it back to the
  frontmost window in the highest layer, so the Terminal hides its
  lock window when it unlocks.

It's written in C, and kept to mechanism: what a hotkey does, which
panel to show, and when to lock, are all decided by the Terminal.

Starting the Compositor
~~~~~~~~~~~~~~~~~~~~~~~

The Terminal starts the compositor, as an ordinary Linux child process
(not a DarqOS process), and the compositor dies with it.  In
``darq_main``, before creating the ``QApplication``, the Terminal:

1. Creates two socket pairs, and starts the compositor with one end of
   each: one for the Terminal's Wayland connection, and one for the
   policy channel.
2. Sets ``WAYLAND_SOCKET`` to its end of the Wayland pair.
   ``wl_display_connect()`` uses ``WAYLAND_SOCKET`` in preference to
   ``WAYLAND_DISPLAY`` (and then unsets it), so Qt connects over the
   socket pair without further code.
3. Waits for the compositor's Ready message on the policy channel.

The Terminal must not use Qt's GTK platform theme, which is Qt's
default on a GNOME host: it calls ``gtk_init``, which finds no display
(libwayland has unset ``WAYLAND_SOCKET`` by then), and exits the
process.  ``QT_QPA_PLATFORMTHEME=generic`` avoids it.

The Wayland connection over the socket pair identifies the Terminal to
the compositor: no other client can connect over it, so no other proof
is needed.

The compositor exits when the policy channel is closed: that's how it
dies with the Terminal, and on ``SIGTERM``, removing its socket.  In
case the Terminal's stuck, it also sets ``PR_SET_PDEATHSIG`` (to
``SIGTERM``) itself, and exits if its parent has already gone.

The compositor also listens on a public socket, ``darq-0``.  The
p-kernel sets ``WAYLAND_DISPLAY=darq-0`` and
``QT_WAYLAND_DISABLE_WINDOWDECORATION=1`` in the environment of the
processes it spawns, so other GUI applications connect there, and Qt
doesn't draw its own decorations if the compositor's request isn't
honoured (Qt 6 does honour it: without it, Qt draws a frame of its
own).  It saves the host's ``WAYLAND_DISPLAY``, if any, as
``DARQ_HOST_WAYLAND_DISPLAY``, and the Terminal passes that on to the
compositor, as its ``WAYLAND_DISPLAY`` (``Compositor.start`` does, by
default).  With it set, wlroots uses its Wayland backend, and the
whole display is a window on the host's desktop: that's how it's
developed.  Without it, wlroots uses the display hardware (DRM and
libinput).  Without the Terminal, the p-kernel leaves
``WAYLAND_DISPLAY`` alone.

Building and Trying It
~~~~~~~~~~~~~~~~~~~~~~

The compositor is in ``compositor/``, built with meson::

    meson setup compositor/build compositor
    meson compile -C compositor/build

Run from the host's desktop, it's a window there, listening on
``darq-0``: ``-s`` starts a command once it's running.  Headless, for
tests, it runs with ``WLR_BACKENDS=headless`` and
``WLR_RENDERER=pixman``, and ``-k`` adds a test keyboard and pointer,
driven by lines on its standard input: ``key <evdev keycode> <1|0>``,
``move <x> <y>`` (in layout coordinates), and ``button <evdev button>
<1|0>``; and, as when nested (see Keyboard), ``hostkey <evdev keycode>
<1|0>``, which doesn't update the keyboard's state, and ``hostmods
<depressed> <latched> <locked>``, the host's modifiers.  The runtime directory must have a short path: a Unix socket's
path is limited to 108 bytes.

The Terminal starts it with ``darq.compositor.Compositor.start``,
which gives it ``-t``, the Terminal's Wayland connection, and ``-p``,
the policy channel.  Without ``-p``, *Sys-Escape* exits.

``tests/test_compositor.py`` checks the policy channel's C against the
generated Python, through ``compositor/policy-check.c``, which it
builds itself; and, if the compositor's built, runs it headless, with
Qt clients: the policy channel, layers, locking, focus, *Sys-w*, and
dying with the Terminal.

Policy Channel
~~~~~~~~~~~~~~

The policy channel is a ``SOCK_SEQPACKET`` socket pair, so each packet
is one message.  Its messages are defined in DIDL
(``lib/didl/compositor.didl``), in the usual DIPC format: the Terminal
uses the classes generated by ``didlpygen``, through
``darq.compositor.PolicyChannel``, and the compositor's C
(``compositor/policy.c``) encodes and decodes the same layouts by hand,
checked against the generated classes by a test.  Every message is
one-way, and none has a port.

From the compositor to the Terminal (protocol ``Display``):

- ``Ready``, once it's listening on ``darq-0``.
- ``HotkeyPressed``, for a bound hotkey: its keysym, as on the key's
  first level (``Tab``, not ``ISO_Left_Tab``, with Shift), and the
  modifiers held besides System.
- ``SystemReleased``, when System is released after a hotkey: the end
  of a chord, like *Sys-Tab*, *Tab*, for the switcher.
- ``WindowAdded``, ``WindowRemoved`` and ``WindowRetitled``: the window
  list.  A window is a toplevel from any client but the Terminal,
  numbered from 1, and never reused; it's added when it's mapped, with
  its title and app_id.
- ``WindowFocused``: the keyboard focus moved to a window, or to none
  (0), which includes the Terminal's own.

From the Terminal to the compositor (protocol ``Policy``):

- ``BindHotkey`` and ``UnbindHotkey``: a keysym and exact modifiers,
  with System.  System keys that aren't bound, or the compositor's own
  (*Sys-w*), are dropped.
- ``Lock`` and ``Unlock``.
- ``FocusWindow``, ``RaiseWindow`` and ``CloseWindow`` (which asks the
  window's client to close it).  An unknown window, such as one just
  removed, is ignored.

The compositor never blocks on the channel: what the socket has no
room for is queued, and if the queue reaches 4096 messages, the
Terminal is assumed stuck, and the compositor exits.  It also exits
when the channel is closed.  A message it can't decode is logged, and
dropped.

Nothing else talks to the compositor: anything wanting a window raised
or the screen locked asks the Terminal.

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

The modifiers are mapped as in :doc:`ux`.  The compositor sets the
keymap, from xkb options:

- System, on Left Control, is a modifier of its own, not Control, so
  the compositor can tell it apart, and clients never see it.  Our
  option ``darq:system`` (in ``compositor/xkb``, whose rules include
  the system's) makes Left Control ``Hyper_L``, on ``Mod3``, which no
  other key uses.  The compositor masks ``Mod3`` out of the modifiers
  it sends to clients.
- Caps Lock is Control (``ctrl:nocaps``): there's no Caps Lock.
- Left Alt stays Alt.  It's Meta to Emacs, and to our lenses, which
  bind Qt's ``AltModifier``: an xkb ``Meta_L`` would reach Qt as
  ``MetaModifier``, which is Super.
- Super/Windows stays Super: it's Application, which Qt reports as
  ``MetaModifier``.
- Right Control is Compose (``compose:rctrl``), and Right Alt is
  AltGr (``lv3:ralt_switch``).

Nested in another compositor, as on the desktop, the host's keymap
doesn't apply: wlroots passes on its key codes, but takes the
modifiers from the host, in its keymap, where Left Control is Control.
So the compositor keeps each keyboard's state itself, from its keys
alone, in its own keymap, and replaces the host's modifiers with it.
(Tools on the host, like ``xev``, show the host's keymap, not the
compositor's.)

For starters, only a US layout will be supported.

Functions
---------

The Terminal Service is responsible for providing several crucial
functions of the user interface, each invoked by a hotkey (see
:doc:`ux`).  In 1st Edition, *Sys-space* (Search), *Sys-e* (Events),
and *Sys-i* (Item info) show a "TBD" panel: they need the Index and
Metadata services, and an events source.

New
~~~

The new panel is the interface used to create new objects.

The new panel is invoked with *Sys-n*.

Objects are created by selecting a *type* of object to create.  Object
types are implemented by installed code, more-or-less like
applications in a traditional operating system.  Examples of object
types might be "image", "document", "movie", etc.

It lists the implemented types, greying out those without a Terminal
lens: typing filters them, by name or type, the arrow keys move
through them, and Enter creates an item of the selected one, records
`created` (source `terminal`), and starts its lens.

History
~~~~~~~

The history panel, invoked with *Sys-h*, lists History events between
two times (the last hour, when it's opened), filtered by type, event,
and source, newest first.  Choosing one (Enter, or double-click)
starts a lens for its item.

Search
~~~~~~

The search panel is the central interface for locating existing
objects of interest to the user.  It integrates access to all the
searching abilities of DarqOS, plus access to Internet search.

The search panel is invoked with *Sys-space*.

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

Lock
~~~~

*Sys-l* locks the screen: the Terminal shows its lock window, and has
the compositor lock.  While it's locked, *Sys-l* does nothing:
unlocking always needs the password.  Of the other hotkeys, only
*Sys-b* works, on the lock screen itself: the overlay layer isn't
shown while it's locked.

Until the Security service is ported, there's no password: the lock
screen says "Press Enter to unlock", and Enter does.

Shutdown and Reboot
~~~~~~~~~~~~~~~~~~~

*Sys-b* shows a confirmation panel: *s* shuts down, *b* reboots, and
any other key cancels.

Close
~~~~~

*Sys-x* closes the focused window: the compositor asks its client to
close it (``xdg_toplevel.close``).

Switcher
~~~~~~~~

The Terminal service provides a means of switching between open
windows: the equivalent of Alt-Tab, invoked with *Sys-Tab*.  The
compositor passes the hotkey to the Terminal, which shows the
switcher, and asks the compositor to raise and focus the chosen
window.

It lists the windows most recently focused first, starting at the
previous one.  While System is held, *Tab* moves on, and *Shift-Tab*
back (*Sys-Shift-Tab* starts at the least recent); releasing System
chooses.  So a single *Sys-Tab* goes back to the previous window.

Item Properties
~~~~~~~~~~~~~~~

There should be a way to easily access the system information about an
item.  This would include metadata, indexing, and history.  You should
be able to edit the metadata -- eg. add a tag, view the history,
and .. not sure about indexing?

It's invoked with *Sys-i*.


Future Editions
---------------

Future editions will move towards using a dedicated base OS, and
consequently, most likely no longer using Qt.  It's likely that EGL
will continue to be used as the abstraction for the underlying GPU,
with a display manager overlying that.  Type Lenses and Tools will use
a standard widget set to provide UI elements.  One option would be to
use a widget server model, perhaps similar to O/mero from PlanB.  Or
it might be a more traditional library-based approach.




Architecture
------------

The Terminal service is implemented as a Darq Python process, using
PyQt6 to provide the GUI, on a Qt event loop (as lenses are), with the
policy channel watched by a ``QSocketNotifier``.  It's in
``services/terminal``: ``main.py`` starts the compositor, and handles
its messages and the hotkeys; ``screens.py`` has the background and
lock windows, one of each on every screen; and ``panels.py`` the
panels.  The panels are overlay windows, one shown at a time: Escape
closes one, and so does losing the focus, but for the switcher's.
New and History are shared with devterm, in ``darq.ui``.

If the compositor isn't built, or isn't ready within 10 seconds, or
closes the policy channel, the Terminal exits with status 1: the
p-kernel then reboots.

Its calls to services are synchronous, on Qt's thread, but for
creating and using items, which take a second or more: activating the
type's implementation, creating or opening the item, and starting a
lens, which waits for its Ready.  Those are done by
``darq.ui.launcher.Launcher``, each on a thread of its own, where its
calls can wait (each thread has its own reply port, closed when it's
done), with its result posted back to Qt's thread.  Meanwhile, the
panel stays open, showing that it's starting, and closes once the
lens is using the item; Escape closes it anyway.  While an item is
being created, New won't create another, so a second Enter doesn't
make a second item.  If it fails, the error is shown in the panel, or,
if it's been closed, in a notice, which any key closes.

Asynchronous generated clients, with replies posted to the event loop,
would do without the threads: see ``TODO.org``.

It communicates using the system IPC with the p-kernel and the other
services, to populate the New and History panels, and to check the
password.  It talks to the compositor over the policy channel.
