User Experience
===============

The fundamental principle underlying the DarqOS UX is that the system
operator is an *expert* (or will soon become one).

Some UX-relevant secondary principles include:

* Darq is a *single-user* system
* All software on the system is either part of the system or written
  by the operator.
  
These principles lead to general UX guidelines:

* The appearance of the system should maximise the content and
  minimise the infrastructure.

  * Backgrounds, transparency, most animations, skeuomorphism,
    excessive styling -- none of this foregrounds the content.  Make
    UI elements distinct, standardised, and simple.
  * Discoverability in the form of menus and visible preference
    settings should be largely eliminated.
  * Most actions should be triggered by keyboard commands.  In
    general, the mouse should be used only where its visual nature
    makes it superior.

* Always expect the operator to know what they are doing.  They don't
  need hand-holding or descriptions of their options.
* Make available the full possible power of the system.  Don't try to
  constrain the operator for their safety.
* Since the installed software is trusted, in general, it isn't
  necessary to defend against other parts of the system.

  * This is not universal.  In particular, the web lens executes
    untrusted software within an otherwise trusting environment.
    Consequently, it must be strongly isolated and controlled.

* Software should behave consistently.  There are no proprietary
  interests or barriers to implementing standard appearance, controls,
  and behaviour.

Scenarios
=========

First Boot
----------

The Terminal Service is responsible for managing a terminal device.  This
basically means a screen, keyboard, and mouse (for now).  So the bootstrap
process will kick off the terminal service along with the other system
services, and it is the component that you initially interact with.

On first boot, it will run through an account creation process: it
collects your Person details and a password, and creates an "account"
in the Security Service.  For subsequent logins, it displays a
simple password dialog, and if it is valid, it unlocks the terminal.

After creating your account and/or logging in, you'll arrive at the
home screen.  On first boot, this will be empty, so there will be
basically nothing visible.  The screen has no menubar, no start
button, no dock.


Standards
=========

Keyboard
--------

The system standardises shortcut keys well beyond what existing
operating systems do.  Two new modifier keys/roles are defined:

* System

  The system modifier introduces shortcuts that are implemented
  system-wide, beyond the scope of any individual application.

* Application

  The application modifier introduces shortcuts that are specific to
  the application with the UI focus.  The operating system will never
  use an application modifier.  Similarly, no application will ever
  use the System modifier.

At some point, the plan is to produce a Darq keyboard that adds these
modifier keys in hardware.

For First Edition, modifiers are mapped as follows:

* Shift, standard behaviour.
* CapsLock, maps to Control.
* LAlt, maps to Meta.
* LControl maps to System.
* Super/Windows maps to Application.
* RControl maps to Compose.
* RAlt maps to AltGr


Hotkeys
-------

+------------+----------------------+
| Sys-space  | Search               |
+------------+----------------------+
| Sys-b      | (Re)boot             |
+------------+----------------------+
| Sys-e      | Events               |
+------------+----------------------+
| Sys-h      | History              |
+------------+----------------------+
| Sys-i      | Item info            |
+------------+----------------------+
| Sys-l      | Lock screen toggle   |
+------------+----------------------+
| Sys-n      | New item             |
+------------+----------------------+
| Sys-w      | Move/resize window   |
+------------+----------------------+
| Sys-x      | Close focused window |
+------------+----------------------+


Windows
-------

For First Edition:

* Focus follows mouse.
* Click to raise.
* To move focused window: hold down Sys-w, and mouse drag to move.
* To move focused window: hold down Sys-w, and use the arrow keys to
  move in reasonable small increments.
* To resize focused window: hold down Sys-w, and then use: t = taller,
  s = shorter, b = broader, n = narrower to resize in reasonable small
  increments.

In future editions:
* It's possible that the resize and movement keys should be a
  two-stage combination, like Emacs, instead of a hold.  But hold
  proabably works better for repeated changes.
* Also possible: Sys-w will show a set of control decorations around
  the window.  Resize handles and a grab target for movement.

