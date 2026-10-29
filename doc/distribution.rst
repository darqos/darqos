Installation
============

DarqOS supports two methods of distribution:

* tarball
* git clone

In either case, the host OS filesystem will contain the distributed image,
a typical Unix-style tree of files.

The host OS should be Debian 13 *trixie* AMD64 netinst
(https://www.debian.org/download).

1st Edition requires the Debian 13 packages: Python 3.13, and wlroots
0.18 for the compositor.

In all cases, the Darq distribution should be unpacked (or cloned)
into `/darq/dist`.  The `/darq/dist/darq/bin/install` script should then
be run (as `root`) to install or update the local installation.  This will
install any required host OS packages, configure the system to run
Darq, and then reboot.

Following the initial reboot, the *firstboot* tool will run, allowing
the system owner to configure the system ready for ongoing use.
Subsequent updates will boot directly into Darq.

Image Layout
------------

* `/darq`

  * Root of the installed files.
  * The only things installed or altered outside this directory are:

    * Required host OS packages, using the host system package manager
    * Host OS configuration files

* `/darq/dist`

  * Directory containing the distributed files.  This can be either a
    snapshot from a tarball, or a `git clone` of the source
    repository.  This area is not used at runtime, but instead must be
    explicitly *installed* to copy any updated components to their
    runtime locations.

* `/darq/kernel`

  * The p-kernel server implementation, and the Python loader
    (`pyloader`) it runs for each process
  * The p-kernel's systemd units, linked into systemd by the install
    script

* `/darq/services`

  * Contains sub-directories for each installed service, containing
    their executable files and any associated file storage.
  * Services are spawned by the p-kernel: they have no systemd units
    of their own.

* `/darq/types`

  * Contains sub-directories for each installed type implementation
    (described later).

* `/darq/lenses`

  * User interface modules for installed type implementations.

* `/darq/tools`

  * User applications

* `/darq/bin`

  * System scripts, or other Unix programs needed to support the
    operation of the system.  These items should be largely, if not
    totally, invisible from within Darq itself.
  * Includes the DIDL tools (`didlc` and `didlpygen`), and the
    compositor, which the Terminal service starts as a Linux child
    process.

* `/darq/lib/didl`

  * The DIDL sources of the system's protocols (`lib/didl/*.didl` in
    the repository).  Their generated Python modules are part of the
    darq package, in `darq.protocols`.

* `/darq/man`

  * Manual pages for the Unix-level programs, by section: view them
    with `man -M /darq/man <page>`.

* `/darq/env`

  * Python virtual environment
  * Includes all installed dependencies
  * Install process will update this using the requirements
    file from the distribution.

* `/darq/local`

  * Installed location of any required Unix software that is not
    provided by the host OS.

* `/darq/run`

  * Home for files that are modified at runtime by the installation.
    This will notably include the SQLite databases used by the various
    services, kernel configuration, the p-kernel's boot vector, etc.
  * In a development tree, the `DARQ_RUN` environment variable points
    the p-kernel at `./run` instead; spawned processes find it in
    `DARQ_RUN` too.
  * This directory will *not* be simply overwritten during an upgrade,
    but instead, will be processed by the install script to update all
    storage as required by the new version.

* Some items will be installed into the Linux environment:

  * The `darq` user, created by `dui`
  * systemd unit files, for the p-kernel only: it spawns the services.
    Its unit is also the `darq` user's login session on tty1, so the
    compositor gets its seat from logind, and it replaces the getty
    there.
  * PAM configuration for that session, if it uses its own PAM
    service rather than `login`
  * `/etc` configuration files (as altered by Darq)

* Some items are created by the host OS at runtime, not installed:

  * `/run/user/<uid>`, the `darq` user's runtime directory, created by
    logind for the session.  The compositor's Wayland sockets live
    here.
