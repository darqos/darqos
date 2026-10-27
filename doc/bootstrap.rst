Bootstrap
=========

1st Edition
-----------

 * Lenovo MiniPC platform

   * Nicely self-contained
   * Suitable platform for future OS-level work
   * Trivially available Linux environment for prototyping

 * OS

   * Use *Debian* (64 bit) as the base image

     * Configure with Wayland, but no desktop environment

       * DarqOS has its own compositor, forked from wlroots' `tinywl`,
         started by the Terminal service (see :doc:`terminal`)

     * auto-login into default user (`darq`)

       * Implemented by the p-kernel's `systemd` unit, which is a
         logind session for `darq` on tty1 (`PAMName=`, `TTYPath=`)
       * `getty@tty1` is not used: the unit conflicts with it
       * DarqOS users exist only within this session: the Terminal
         manages DarqOS login

     * Install single `dui` script that can fetch/install Darq when
       run as root.

   * Distribute Darq as tarballs, containing an install script to run on
     the target device

   * Install Darq as a file tree, rooted at `/darq`

     * Use `dui` script to ensure the Linux configuration matches what's
       required: installing appropriate packages, updating
       configuration files, etc.
     * Fetch tarball for darq
     * Use `/darq/dist` as staging area for updates, and then
       `/darq/dist/darq/bin/install` script to copy into `/darq` and
       activate
     * Clean `/darq/dist` once installed
     * Consider using eg. `rsync` to optimise this process later.

 * Startup

   * Standard Linux systemd boot to multi-user target
   * p-Kernel

     * p-kernel systemd unit is automatically started, as the `darq`
       user's login session; it's the only DarqOS systemd unit
     * p-kernel spawns the system services, in order: Storage, History, Type,
       then the type sync (`tools/type-sync`), which registers the
       installed types and lenses and exits.  They're started at
       once: each waits, with a blocking name lookup, for the services
       it uses.
     * Until the session is set up, the p-kernel runs as root

   * Storage

     * Service is Darq Python app, and spawned as a p-process from the
       p-kernel as part of the bootstrap process.
     * It keeps its database in the run directory, which the p-kernel
       passes in `DARQ_RUN`: `/darq/run`, or `./run` in a development
       tree.
     * The storage service registers `darq.storage` in the p-kernel's
       name registry, making itself available to its clients.  Names
       under `darq.` are reserved for processes the p-kernel spawned
       (see :doc:`ipc`).

   * Boot vector

     * There's no boot volume, and no in-kernel storage drivers, so a
       service starting at boot has nowhere to find its own state,
       such as the keys of its storage blobs.
     * The p-kernel keeps a small, persistent name:value *boot vector*
       for this, in the run directory, accessible only to processes it
       spawned itself (parent dpid 0), with the `get_boot_value` and
       `set_boot_value` p-calls.  For example, the Type service keeps
       the key of its catalogue blob there.
     * It holds at most 64 names, with values of at most 1 KiB, so it
       stays small: it's for finding state, not holding it.

   * Security

     * User authentication

   * History

     * Spawned as part of the bootstrap process, after Storage.
     * Like Storage, it keeps its database in the run directory, and
       registers `darq.history` (see :doc:`history`).

   * Metadata
   * Index
   * Terminal

     * Service is a Darq Python app, spawned as a p-process from the
       p-kernel as part of the bootstrap process.
     * Running PyQt6
     * Starts the DarqOS compositor, as a Linux child process, before
       creating its `QApplication`
     * The compositor provides the mechanism, and the Terminal sets the
       policy: what hotkeys do, which panels to show, and when to lock
     * The compositor gets its seat (display and input devices) from
       the logind session, through libseat
     * Needs to have pointer & keyboard

       * The compositor catches System-key combinations before clients
         see them, and passes them to the Terminal, so global hotkeys
         don't depend on Qt
       * Worried about the stupid Command/Control mapping Qt does ...

     * Also will need to provide audio I/O

       * Via ... PulseAudio?  Or Pipewire?

         * I guess ideally Pipewire, but it'll depend on what
           works for the MiniPC / Debian

       * Device access comes from the logind session

     * Either provides or facilitates the Factory, Find, and Events
       UIs.

   * If first boot, run firstboot

     * WiFi SSID and password

       * Also used to set WiFi locale?

     * DHCP vs static network config
     * Country / timezone
     * Add a single "user" user?

       * Could have firstrun set up a user account, but is there any
         benefit?

     * Password collection
     * This could all reasonably be a part of a "Settings" tool?

 * Python 3.13 (default on _Trixie_)
 * Qt 6 + PyQt6


2nd Edition
-----------

 * Clean up install/distribution image

   * The build system should have a target that produces a
     pre-initialised storage service database.


nth Edition
-----------

 * Replace Linux with darq kernel

 * Replace Wayland with direct GPU library
 * Replace Qt with custom UI

   * Look into O/mero from PlanB
   * Learn a lot more about what can be done with the GPU
