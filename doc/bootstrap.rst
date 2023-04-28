Bootstrap
=========

1st Edition
-----------

 * Raspberry Pi 4 / 400 platform

   * Nicely self-contained
   * Suitable platform for future OS-level work
   * Trivially available Linux environment for prototyping

 * Maintain distribution as Git repo, with an install script to run on
   the target device

   * Could fall back to a snapshot tarball too
   * Use *Raspberry Pi OS Lite* (64 bit) as the base image

     * Use Mobian for PinePhone
     * Use Debian Buster for development VMs

   * Don't bother with packaging Darq
   * Use `/darq` as the installed image root

     * `/datq/dist` should be the Git checkout or snapshot
     * Other stuff as required
     * Balance between using Storage service vs. host files TBD

   * Install script could also work for hosted (macOS / Linux)
     development too
   * Big things (eg. Qt) could be installed from binaries

 * Startup

   * p-Kernel

     * RPC-reachable pseudo-kernel
     * Prototype kernel API
     * Started from systemd unit file (?)
     * Once up and running, executes `/darq/sys/init` script

   * Storage

     * Server is a Unix daemon process, written in Python (for now).
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

     * Server is Unix daemon process, running PyQt5
     * Create full-screen background window on all attached screen
       devices (from Qt).
     * Needs to have pointer & keyboard

       * Can we get enough control via Qt?  Worried about the stupid
         Command/Control mapping Qt does ...
       * If not Qt, maybe need to wrangle directly with Linux

     * Also will need to provide audio I/O

       * Via ... ALSA?  Or PulseAudio?  Or Pipewire?

     * Either provides or facilitates the Factory, Find, and Events
       UIs.

   * If first boot, run firstboot

     * WiFi SSID and password
     * DHCP vs static network config
     * Country / timezone

       * Also used to set WiFi locale?

     * Add a single "user" user?

       * Could have firstrun set up a user account, but is there any
         benefit?

     * Password collection
     * This could all reasonably be a part of a "Settings" app?

 * Install systemd unit(s) for either

   * A single init replacement

     * A Python"agent"

       * Needs a TCP API

         * Reboot and shutdown actions

       * Shutdown should be a script too

         * Which just sends the right message to the agent

       * Has a list of services to start, in order

         * No dependencies (unless we really need them, static will
           do)

       * boot.py is parent process; all other spawn from it.

         * Could do something with control groups later, if required

       * Agent process should monitor services, and restart them on
         exit

   * Or just use systemd, with each long-running "daemon" process in
     Darq?

 * Python3.10?
 * Qt5 LTS
 * PyQt5


2nd Edition
-----------

 * Clean up install/distribution image

   * The build system should have a target that produces a
     pre-initialised storage service database.


nth Edition
-----------

 * Replace Linux with darq kernel

 * Replace X11/Wayland with direct GPU library
 * Replace Qt with custom UI

   * Look into O/mero from PlanB
