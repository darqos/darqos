Installation
============

DarqOS is distributed as a tarball, made by ``mkdist``, and installed
on a Debian 13 *trixie* AMD64 machine (from the netinst image,
https://www.debian.org/download), with no desktop: DarqOS is the
machine's session.

1st Edition uses the Debian 13 packages: Python 3.13, and wlroots 0.18
for the compositor.

Making a Distribution
---------------------

``mkdist``, at the top of the tree, makes
``dist/darqos-2ed-snapYYYYMMDD-b17.tar.gz`` (or in the directory given):
the files git tracks in the directories that are installed (``bin``,
``darq``, ``kernel``, ``lib``, ``services``, ``types``, ``lenses``,
``tools``, ``compositor``, ``man``, and ``requirements.txt``), as they
are in the working tree, under ``darq/``, owned by root.  Its
``VERSION`` file gives its version, the commit, and the date.

Files git doesn't track are left out, with a warning, and uncommitted
changes to what's distributed make a ``-dirty`` version: a release is
made from a commit.

Versions
--------

A version is an edition and a build, written ``2.17``.  ``RELEASE``, at
the top of the tree, has the edition, and the last build.  Each tarball
``mkdist`` makes is a new build, so a number identifies one tarball:
it's added to ``RELEASE`` once the tarball's been made, and committed
with everything else.  Builds aren't started again for an edition, so a
higher build is always later, whatever its edition.

================  ====================================================
Version           What it is
================  ====================================================
``2.17``          An installed tarball.
``2.17-dirty``    One made with uncommitted changes.
``2.17-dev``      A source tree, since build 17.
``unknown``       Neither ``VERSION`` nor ``RELEASE`` can be read.
================  ====================================================

The p-kernel reads the version when it starts, and logs it.
``darq.get_version()`` returns it, as a ``darq.Version``: the text, for
showing, and the edition and build, to compare.  Any process can ask.

Installing
----------

As root, ``dui`` (see ``dui(8)``) installs from a tarball, given as a
path, or downloaded:

.. code-block:: sh

   bin/dui darqos-1ed-snap20261103.tar.gz

It unpacks it into ``/darq/dist``, runs its ``bin/install`` (see
``install(8)``), and removes it.  The same command installs and
updates.  Install:

1. stops DarqOS, if it's running;
2. installs the Debian packages it needs: to build the compositor, the
   libraries Qt needs, polkit (``polkitd``: without it, logind lets
   only root power the machine off or reboot it), ``sqlite3`` and
   ``rsync``;
3. creates the ``darq`` user;
4. copies the code into ``/darq``, as source, owned by root and
   writable only by root (whatever its modes in the distribution), with
   ``rsync --delete``, so what's gone from the distribution goes; and
   the manual pages, by section;
5. builds the compositor, in ``/darq/compositor/build``;
6. makes, or updates, the Python environment, ``/darq/env``, from
   ``requirements.txt`` (PyQt6 included);
7. creates the run directory, ``/darq/run``, owned by ``darq``, mode
   0700;
8. installs the systemd units, and the backup configuration, if there
   isn't one;
9. enables the daily backup; and
10. starts DarqOS again, if it was running.

It logs to ``/var/log/darqos``.

Installing doesn't make DarqOS start at boot: once it's been tried
(``systemctl start darqos-pkernel``), ``dui --boot`` (or
``/darq/bin/install --boot``, which, run from the installation itself,
does everything but copying the code) enables its unit and makes
``darqos.target`` the default boot target.  The p-kernel's unit is the ``darq`` user's login
session on tty1 (see :doc:`terminal`, "Session and Login").

Backups
-------

``darqos-backup.timer`` runs ``/darq/bin/darqos-backup`` daily (see
``darqos-backup(8)``), and at the next boot after a missed run.  It
takes a snapshot of the run directory into ``/var/backups/darqos``,
named by its time: the databases with SQLite's online backup, so
DarqOS can go on running, and the other files as they are.  It keeps
the last seven, and then copies them, with ``rsync --delete``, to the
backup destination, if there is one.

``/etc/darqos/backup.conf`` sets the destination, ``BACKUP_DEST`` (a
directory, or ``host:directory`` over SSH, with root's key), and how
many to keep, ``KEEP``.

Restoring is copying a snapshot's files back into ``/darq/run``, with
DarqOS stopped, owned by ``darq``, having removed the databases'
write-ahead logs (``*.sqlite-wal`` and ``*.sqlite-shm``): see
:doc:`guide`.

Image Layout
------------

* `/darq`

  * Root of the installed files.
  * The only things installed or altered outside this directory are:

    * Required host OS packages, using the host system package manager
    * Host OS configuration files

* `/darq/dist`

  * The unpacked distribution, while it's being installed.  It isn't
    used at runtime: it's *installed*, which copies its files to their
    runtime locations, and then removed.

* `/darq/VERSION`

  * The installed distribution's version, commit and date.

* `/darq/darq`

  * The DarqOS Python package, as source: the p-kernel's launcher puts
    `/darq` on `PYTHONPATH`.

* `/darq/kernel`

  * The p-kernel server implementation, its launcher
    (`darqos-pkernel`), and the Python loader (`pyloader`) it runs for
    each process
  * The systemd units, which install copies into
    `/etc/systemd/system`

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

* `/darq/compositor`

  * The compositor's source, and its build, in `build/`: the Terminal
    service starts it as a Linux child process.

* `/darq/bin`

  * System scripts, or other Unix programs needed to support the
    operation of the system.  These items should be largely, if not
    totally, invisible from within Darq itself.
  * Includes the DIDL tools (`didlc` and `didlpygen`), `dui`,
    `install`, and the backup, `darqos-backup`.

* `/darq/lib/didl`

  * The DIDL sources of the system's protocols (`lib/didl/*.didl` in
    the repository).  Their generated Python modules are part of the
    darq package, in `darq.protocols`.

* `/darq/man`

  * Manual pages for the Unix-level programs, by section: view them
    with `man -M /darq/man <page>`.

* `/darq/env`

  * Python virtual environment
  * Includes all installed dependencies, from the distribution's
    `requirements.txt`

* `/darq/local`

  * Installed location of any required Unix software that is not
    provided by the host OS.

* `/darq/run`

  * Home for files that are modified at runtime by the installation.
    This will notably include the SQLite databases used by the various
    services, kernel configuration, the p-kernel's boot vector, etc.
  * Owned by the `darq` user, and only it may read it.
  * In a development tree, the `DARQ_RUN` environment variable points
    the p-kernel at `./run` instead; spawned processes find it in
    `DARQ_RUN` too.
  * This directory isn't changed by an update.  Later, the install
    script will migrate its storage as required by the new version.

* Some items will be installed into the Linux environment:

  * The `darq` user, created by install
  * systemd unit files, in `/etc/systemd/system`: the p-kernel's,
    `darqos-pkernel.service`, which spawns the services, and
    `darqos.target`; and the backup's, `darqos-backup.service` and
    `darqos-backup.timer`.  The p-kernel's unit is also the `darq`
    user's login session on tty1, so the compositor gets its seat from
    logind, and it replaces the getty there.
  * Later, PAM configuration for that session, if it uses its own PAM
    service rather than `login`
  * The backup configuration, `/etc/darqos/backup.conf`
  * The backups, in `/var/backups/darqos`, and install's logs, in
    `/var/log/darqos`

* Some items are created by the host OS at runtime, not installed:

  * `/run/user/<uid>`, the `darq` user's runtime directory, created by
    logind for the session.  The compositor's Wayland sockets live
    here.
