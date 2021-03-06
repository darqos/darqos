﻿# Roadmap

Where to start?

There are many things to be done, falling into many different categories.
What’s important is actually making concrete progress, which means
actually implementing things, getting experience, and cycling.  The
Xerox Smalltalk team did 2 year iterations, which seems like a long time
these days.  I think continuous integration is probably a better
approach, perhaps reflecting the Bell Labs approach of doing occasional
snapshot “editions”.


## Deliverables
* A statement of philosophy and goals, being an implicit motivation
  for the project also
* A statement of non-goals, for clarity, reducing incorrect assumptions
* A working computer system: hardware + software, suitable for
  performing various tasks
* Enough documentation to enable a moderately sophisticated user to
  acquire, set up, and use the system

## Tentative Planning

There will be an initial series of milestone targets, providing a
platform for experimentation primarily at the UX level.  In
particular, these milstones will build upon existing kernels and
language runtimes.

These initial prototype milestones will have a "P" (for prototype)
prefix.  Subsequent releases removing dependencies on existing kernels
and library software will have an "M" prefix.


### P0
The goal of the first prototype milestone is to “boot” and get running
with an identifiably different system, minimal aesthetics, minimal
user functionality, but a viable target platform for further work.
The (vast) majority of M0 work should be done in Python, to ensure
it’s malleable.

Infrastructure
* Repository (DONE)
* Project structure
* CI
* Documentation
* Release process

System
* Host OS
  * Unix: macOS or Linux, really
* IPC
  * ZeroMQ with JSON marshalling.
* A collection of cooperating processes
  * Written in Python
  * Communicating using the ZMQ IPC
* GUI
  * Full-screen window
  * PyQt5 for macOS/X11
* Bootstrap
  * Basic shell script, starting up Unix processes
  * Storage backed by sqlite and Unix FS
  * Network shared with host OS

Services
* Security
  * Identity
  * Authentication
  * Permissions
* Storage
  * Key-value blob store
* History
  * System-wide
  * Activity timeline
* Terminal
  * Framebuffer(s)
    * Using Qt5 with a full-screen window, and Z-ordering of other
      windows.
    * Remove all the OS decoration
  * Keyboard
    * via Qt
  * Mouse, trackpad, etc.
    * via Qt
  * Supports login, etc, through interaction with the security
    service.
    * Login, logout, lock, reboot, shutdown
* HUD
  * If HUD is actually separate from Terminal?
  * Search
  * Factory
  * History
  * Clock

Runtime
* Object loader
* Some sort of abstraction for access to the GUI?

Types
* Text
  * CRUD
  * UTF8
  * Decent fixed-width font
  * Basically just using PyQt5's text widget
  * No BiDi or vertical support
  * The tricky stuff here will be the distinction between the type and
    the view(er).
    * The type implementation should mediate access to the object.
    * It should have an exposed API.
    * The viewer should use the type implementation, including
      providing whatever hooks are required for rendering.

Story
* Boot device.
* See login window.
  * No need to deal with initial account creation, etc, yet.
* Log in with username/password.
  * Should use TAB to move between entry widgets
  * Should default to username entry
  * Should allow shutdown / reboot
  * Enter should DTRT (move to password if in username, submit if in password)
* Get initial UI
  * Search bar, object factory, etc
* Create a new text document
* Close the text viewer
* Find document with search bar, and view it again
* Logout


### P1
Support web browsing, and begin work on metadata/indexer support to
make that experience better than on existing platforms.

Services
* URL fetcher
   * HTTP, HTTPS, FTP, SFTP, FTPS, etc
   * Not involved in WebSockets or WebRTC
   * Caching / archiving
   * Runtime object loader plugin
* Metadata
   * Tagging, typing, etc
   * Closely integrated with storage
* Indexer
   * Uses storage and metadata
   * Searching and completions
* Credentials
   * Secure storage of various secrets
   * 2FA token generation
   * Support for web browser, basically

Types
* PDF
  * Display only
  * Possible vectors:
    * https://github.com/Belval/pdf2image
    * https://github.com/Zain-Bin-Arshad/PDF-Viewer
    * https://github.com/pymupdf/PyMuPDF
  * This might require some refactoring of the type/viewer design.

* HTML
  * Display-only
  * HTML5/CSS3/ES7/SVG2/etc
    * CEF / cefpython (https://github.com/cztomczak/cefpython)
  * Use URL fetcher
    * So we get history, metadata, caching and archiving control
  * This might require some refactoring of the type/viewer design.


### P2
Programming, to the point of becoming self-hosting.

Infrastructure

System
* Some sort of conceptual support for USB storage / SD cards / etc in
  the storage system.

Services
* Diff / Merge
  * Add support to existing types for diff and merge operations
  * Three-way merge UI element for objects, including collections
* Conversion
  * Compression and archive formats
    * Eg. tar, zip, 7z, bzip, rar, etc, etc, etc
    * ISO / Joliet read/write, eg.CD/DVDs
  * Perhaps part of some sort of general translation service?
    * With plugin abilities to add bilateral capabilities

Types
* Collection
   * CRUD+DM (Create, Read, Update, Delete + Diff, Merge)
   * Generic set/group type
* Project
   * CRUD+DM
   * Specialised Collection
   * Hierarchical structure, compatible with a POSIX filesystem
* Repository
   * CRUD+DM
   * Specialised collection
   * Trees, branches, tags
   * Commit log viewer, etc
   * Should there be derivative objects for different SCMs?  Or
     plugins to specialize a single implementation?
     * Must support Git
     * It'd be nice to support RCS and CVS as well.
     * Maybe Subversion?
* Code
   * Sub-type of Text
   * CRUD+DM
   * Moderately decent source code editor
     * Emacs itself doesn't make sense here, but something with
       usefully similar keybindings would be good?
     * https://github.com/mradultiw/pyropes
   * Line numbers
   * Highlighting
   * Sublime-style scrolling
   * Intellisense support
     * via language server protocol?
   * Debugger support
   * Blame support
   * Must support Python, C, HTML, CSS, JavaScript, Bash, any any other
     system languages (others out of scope for this milestone)

Story
* At this point, the system should be useful for programming.
* The _system_ should provide equivalent functionality to an IDE,
  without being a monolithic application
  * So, projects manage the constituent objects
  * Some sort of LSP (?) will extract the semantic elements from the
    text, and expose that to the Repository/Project?
  * How is debugging integrated?
  * How is compilation integrated?
  * Integration with system search
    * Both for code and documenation
  * What events get added to history?


### P3
PIM support: email, calendar, contacts, messaging, world clock.  This
should be enough for daily driving with the exception of office and
graphical work.

Infrastructure

System

Services
* Notifications
   * System-to-user communication
* People
   * Repository of data about people
   * Able to sync with external services (Exchange, Google, CardDAV,
     vCard, LinkedIn, etc)
   * Index and History providers
   * Needs better name, since it can be businesses or groups etc also
* Calendar
   * Repository of calendar events
   * Able to sync with external services (Exchange, Google, CalDAV,
     vCalendar)
   * Index and History providers
* Mail
   * IMAP, SMTP, JMAP, Exchange, etc, service
   * Index, Metadata, Notification, and History providers
      * Maybe Storage as well?
   * Send action on Mail type should use mail service
   * Could split SMTP and IMAP/POP into different services?
   * How should IMAP repositories be modelled?
      * Some sort of object provider?
      * How’s this related to, eg. the Storage service?
* Messages
   * Interface to external services: GChat, Slack, Discord, WhatsApp,
     Signal, etc
   * Index, Metadata, Notification, and History providers
      * Maybe Storage as well?
   * Sync local archive with remote where supported (eg. Slack)?
   * What’s the relationship with People?
      * Especially wrt creation or lookup?
* Music
   * Gateway to streaming or externally-hosted storage services
   * Metadata for music extends that of general sound files

Types
* Mail
   * CRUD
      * Reply / Reply-all / Forward are specialisations of standard
        create action
      * Delete is just delete, Update is just edit
      * Send is a type-specific action, I guess
      * Headers might translate nicely into metadata?
   * MIME
      * Consider archive support for eg. HTML email, which can change
        given externally-hosted content
   * Mail objects aren’t special
      * They have Index, Metadata, and History (like all other objects),
        and so you don’t need folders
      * Inbox is really just part of the Notification service
      * So there’s no special collection required for mail: just
        Notification and the type actions?
* Message
   * CRUD
   * Unified interface to multiple providers
* Event
   * CRUD
   * Events, alarms, to-dos
   * Repeating events, encompassing CalDAV content standards
* Sound
   * CRUD+DM
   * Audio player/editor
   * Might want different presentation facets for podcast vs. song vs.
     sample vs. etc
   * Music, and Album, as possibly derived types?
   * Music plugin for selector/dashboard/HUD?

### P4
Office: word processor, spreadsheet, slides, vector drawing, pixel
drawing

Infrastructure

System

Services
* Additional type conversions

Types
* Document
   * CRUD+DM
   * Libre Office?
      * Microsoft Word interop
* Spreadsheet
   * CRUD+DM
   * Libre Office?
      * Microsoft Excel interop
* Presentation
   * CRUD+DM
   * Libre Office?
      * Microsoft Powerpoint interop
* Vector Drawing
   * CRUD+DM
   * Inkscape?
* Pixel Drawing
   * CRUD+DM
   * Should scale from simple image viewer for eg. Messages, to
     Photoshop-style editing capability
   * Start with GIMP?
* Diagrams
   * CRUD+DM
   * Might be part of vector drawing?
   * Microsoft Visio interop; OmniGraffle interop
   * Again, needs to scale from simple viewer to full editor
* Things
   * CRUD+DM
   * Should support CAD formats for 3D printing

### P5
Fill out features for full daily-driver usage.

Infrastructure

System

Services
* Various type converters
   * eBook formats
* Communicate
   * AV P2P, P2MP
   * SIP, WhatsApp, Signal, Zoom, Skype, etc

Types
* Icons
   * CRUD+DM
   * Specialised tool for icons, vs. pixel/vector images
      * How to determine which one to use?
      * Can this be a scalable feature of an overall “Images”
        application?
* PCBs
   * Schematic capture
   * Board design
   * Simulation
   * Etc
* System
   * Configuration
   * Preferences
   * AppStore
* Book
   * Could be an eBook, or an avatar for a physical book
   * Bunch of metadata
   * Some similarities to music and video: there can be physical
     entities that are cataloged with their metadata, but don’t have a
     stored object underneath them.  They then have a collective
     presentation that facilitates browsing in a type-appropriate way.
   * Some metadata lookup/collection functionality here too (ie. ISBN
     scan, and then lookup/fetch)
   * Eg. Delicious Monster, Bookpedia, etc.
* eBook
   * RD+DM
   * How is this related to the PDF viewer?  Or even the Document
     viewer?
      * Is there a different UI for “books” vs. “papers”?
      * Is that difference something that should really just be
        presentational affordances, driven by metadata?
      * Does this end up implying that Document and Book are different
        facets of the same thing, with a bunch of underlying converters
        to port the content over?
* Bibliography
   * Again, a kind of specialised metadata collection, for mostly
     externally stored objects.
   * Eg. BibDesk
* Game
   * Quite a big category of stuff.
   * Specialisation of executable
      * Save files should be type instances
      * ROMs are really just an executable with a different “VM”

Throughout the P-series of milestones, we can take advantage of the
underlying Unix operating system to wrap existing applications into
the Darq model.  This will facilitate experimentation with the model
while not requiring the effort to rewrite massive amounts of
functionality onto a new OS/GUI.

After the P-series, it is conceivable that we choose not to proceed to
rewriting the OS, or that we adopt a cut-down form of an existing
kernel and runtime instead.



### M0

System
* Interim base OS
   * Processes / threads
   * Memory
   * Block storage drivers
   * Keyboard / mouse
   * Display and GPU
   * Network devices and TCP/IP stack
   * Linux?  FreeBSD?  Zircon/Fuschia?  Minix3?
* Some sort of IPC
   * Kernel mediated
   * Not DBus
   * Mach + MIG
   * Protobuf / CapNProto / Thrift / Avro / etc
   * Is there a role for Elvin here?
   * In-memory local transport option + network transport option
* Some sort of low-level graphics API
   * Not X, not Wayland
   * Not Qt or Gtk or other existing UI toolkit either, unless I come
     across something well suited or as a great starting point for
     forking
   * OpenGL ES 2 or 3?  As a base API to the GPU.  How does this work
     with the Linux framebuffer?  SDL?  DirectFB?  OpenVG?  Etc.
* Language runtime
   * C?  Go?  Rust?   Something that can be compiled, with decent
     performance, and not too difficult to retarget to a non-POSIX
     runtime.


## Possible Technology Elements
Cario (cairographics.org) is a 2D graphics library with backends for
various things, including PNG and (experimentally) DirectFB.

Pango is a proper text API that integrates with Cairo.

DirectFb is an abstraction over the Linux framebuffer that appears to
be dead, but otherwise sounds quite nice.

Replacing DirectFB with writing directly to the Linux framebuffer
device (/dev/fb0) might be an option?  Or perhaps it’d be necessary to
get into DRI/DRM with libdrm and /dev/dri/cardX or /dev/dri/renderDX?

Or, use OpenGL ES as the base layer?

OpenVG is possibly an alternative to Cairo?

libinput is the FDO input device abstraction.

libevdev is a wrapper for the basic kernel evdev facility (and is used
by libinput).

Where does SDL2 fit into this picture?

I *think* there’s a few categories here:
* SDL2, DirectFB, /dev/fb0, OpenGL, libdrm(?)
* Cario (+ Pango), OpenVG
* evdev, libinput

Note that SDL2 includes graphics, sound and input device support in a
single layer.

* https://www.freedesktop.org/wiki/Software/glitz/
* https://gitlab.gnome.org/GNOME/mutter
* https://pypi.org/project/glfw/
* https://github.com/oasislinux/oasis

Example of bare-metal OpenGL application
* https://gitlab.freedesktop.org/mesa/kmscube/

Notes on running on RPi4
* https://www.raspberrypi.org/forums/viewtopic.php?p=1490438
* https://github.com/matusnovak/rpi-opengl-without-x

L4, LittleKernel, Fuschia/Zircon, Minix3, -- some existing micro-kernel
might be a good start for the OS?

* Cut-down RPi Linux: https://dietpi.com/

## Notes
* How does a Calculator app fit in?
   * It has no object, unless you get pretty obscure
      * Although typing arithmetic into the search bar should probably
        a) use the Calculator service, and
        b) offer a means of bringing up a UI based on what you’ve typed
           so far
   * It could be a “tool panel” type thing?
      * Either invoked directly off the dashboard, or perhaps “tools”
        as a category can be found via search/index?’
   * If there’s to be a “New …” button, aimed at creating objects,
     perhaps “calculation” could be in there?  Pretty obscure though …
* Types need to be a combination of:
   * Executable APIs exposed to the system
   * UI presentations, also exposed to the system
      * These could be graphical or scripted
      * There might be multiple variants here, selectable somehow?
         * Sometimes perhaps automagic, based on metadata
         * But probably switchable manually also?
      * Does an audio-driven UI fit in here too?  Alexa/Siri?
   * The GUI presentation should be able to be embedded within other
     GUI elements, so that eg. the Document UI can display Images.
* How are Services embodied?
   * Are they just an available API?
   * Can a service have a UI component?
      * I think yes here?
   * How are services started?
      * IPC-based activation?
