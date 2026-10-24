Bootstrap
=========

1st Edition
-----------

 * Lenovo MiniPCR platform

   * Nicely self-contained
   * Suitable platform for future OS-level work
   * Trivially available Linux environment for prototyping

 * OS

   * Use *Debian* (64 bit) as the base image

     * Configure with Wayland server, but no desktop environment
       * Run Wayfire compositor, for now
     * auto-login into default user (`darq`)
       * Implemented via `getty@tty1` `systemd` service
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
       `/darq/dist/darq/sys/install` script to copy into `/darq` and
       activate
     * Clean `/darq/dist` once installed
     * Consider using eg. `rsync` to optimise this process later.

 * Startup

   * Standard Linux systemd boot to multi-user target
   * p-Kernel

     * p-kernel systemd unit is automatically started
     * p-kernel will spawn system services

   * Storage

     * Service is Darq Python app, and spawned as a p-process from the
       p-kernel as part of the bootstrap process.
     * Pass it the "device" info, in this case a Unix filesystem path
     * The storage service registers itself with the kernel service, thus
       making itself available to its clients.

       * How does this registration work?

         * Is there a generic name-to-port mapping service?
         * Is there a mapping in the p-Kernel?
         * Does it use a well-known port number?

           * There are issues of who's allowed to register names
             and/or ports here.

   * Security

     * User authentication

   * Metadata
   * Index
   * History
   * Terminal

     * Service is a Darq Python app, spawned as a p-process from the
       p-kernel as part of the bootstrap process.
     * Running PyQt5 (or 6?)
     * Spawns Wayfire compositor to manage windows
     * Needs to have pointer & keyboard

       * Can we get enough control via Qt?  Worried about the stupid
         Command/Control mapping Qt does ...
       * If not Qt, maybe need to wrangle directly with Linux evdev?

         * In the worst case, perhaps thd (trigger happy) will work

     * Also will need to provide audio I/O

       * Via ... PulseAudio?  Or Pipewire?

         * I guess ideally Pipewire, but it'll depend on what
           works for the MiniPC / Debian

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
 * Qt5 LTS + PyQt5 (or Qt6 + PyQt6 or PySide6?)


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
