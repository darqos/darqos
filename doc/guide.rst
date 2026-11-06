Getting Started
===============

This is a guide to setting up First Edition, and using it: for someone
at home with installing Debian, and a shell, but new to DarqOS.  The
details are in the chapters it points to.

What You Need
-------------

* A PC (an AMD64 MiniPC, for First Edition) with a screen, a USB
  keyboard and a mouse, which will run DarqOS and nothing else: DarqOS
  is its only session, and there's no other desktop.
* A Debian 13 *trixie* AMD64 netinst image, on a USB stick
  (https://www.debian.org/download).
* A DarqOS distribution tarball, ``darqos-2ed-snapYYYYMMDD-b17.tar.gz``,
  made by ``mkdist`` (see :doc:`distribution`).
* Another computer, to reach it over SSH if DarqOS doesn't start.

Installing
----------

1. Install Debian from the netinst image.  In *Software selection*,
   choose only *SSH server* and *standard system utilities*: no
   desktop environment.  Set a root password, or make an account
   that can use ``sudo``.

2. Copy the tarball over, and install it, as root:

   .. code-block:: sh

      scp darqos-1ed-snap20261103.tar.gz root@minipc:
      ssh root@minipc
      tar -xzOf darqos-1ed-snap20261103.tar.gz darq/bin/dui > dui
      bash dui darqos-1ed-snap20261103.tar.gz

   ``dui`` unpacks it, and runs its install (see ``dui(8)`` and
   ``install(8)``), which installs the Debian packages DarqOS needs,
   creates the ``darq`` user, copies DarqOS into ``/darq``, builds the
   compositor, and makes its Python environment.  It takes a few
   minutes, and logs to ``/var/log/darqos``.

3. Try it, on the machine's screen:

   .. code-block:: sh

      systemctl start darqos-pkernel

   DarqOS takes over the screen, on the first virtual terminal.  If
   it doesn't come up, see `Troubleshooting`_.

4. Once it works, make it start at boot:

   .. code-block:: sh

      /darq/bin/install --boot

   That makes ``darqos.target`` the default boot target.

Updating is the same command as installing: ``dui`` with the new
tarball.  DarqOS is stopped, updated, and started again if it was
running; your items, in ``/darq/run``, are kept.

First Boot
----------

DarqOS starts with its lock screen: a password field, and nothing
else.  The first time, it asks for a new password, twice.  There are
no user names: First Edition has one user.

Once the password's accepted, the screen is empty: there's no menu
bar, dock or clock.  Everything starts from a hotkey.

The Keyboard
------------

DarqOS has two modifiers of its own: *System*, for the system's
shortcuts, and *Application*, for the focused window's.  On a PC
keyboard (see :doc:`ux`):

* Left Control is System, written *S-*;
* Caps Lock is Control, written *C-*;
* Left Alt is Meta, written *M-*;
* the Windows key is Application, written *A-*;
* Right Control is *Compose*, and Right Alt is *AltGr*.

Only a US layout is supported, for now.

The system's hotkeys:

+---------------+---------------------------------------------------+
| S-n           | New: create an item of a type                     |
+---------------+---------------------------------------------------+
| S-space       | Search, with the time and date                    |
+---------------+---------------------------------------------------+
| S-h           | History: what's been created, opened and changed  |
+---------------+---------------------------------------------------+
| S-Tab         | Switch windows (hold Sys, Tab to move on)         |
+---------------+---------------------------------------------------+
| S-x           | Close the focused window                          |
+---------------+---------------------------------------------------+
| S-w           | Hold, and drag or use the arrow keys, to move the |
|               | focused window; t, s, b and n to make it taller,  |
|               | shorter, broader and narrower                     |
+---------------+---------------------------------------------------+
| S-l           | Lock the screen                                   |
+---------------+---------------------------------------------------+
| S-b           | Shut down (s) or reboot (b): any other key        |
|               | cancels.  It works on the lock screen too         |
+---------------+---------------------------------------------------+
| S-t           | Import an address book (vCards), for now          |
+---------------+---------------------------------------------------+

*S-e* (Events) and *S-i* (Item info) show placeholders in First
Edition.  Escape closes a panel.  Focus follows the mouse, and a click
raises a window.

Using It
--------

DarqOS has no files or folders: it has *items*, each of a *type*,
shown by a *lens* (see :doc:`types`).  You create them with New, and
find them again with Search or History.

*S-n* and Book starts a book for the catalogue: see :doc:`book`.

A walk through it:

1. *S-n*, type ``text`` to find the Text type, and press Enter: a
   new, empty text document opens in its lens.
2. Type something.  It's saved as you go, a couple of seconds after
   you stop, and indexed for searching.  The keys follow Emacs (C-f,
   C-b, C-k, C-y, C-/ to undo, and the others: F1 lists them); C-x
   C-q makes it read only, or editable again.
3. Close it: *S-x*, or C-x C-c.
4. *S-space*, and type a word or two from it: words are matched by
   their beginnings, so ``cof sho`` finds *coffee shop*.  The arrow
   keys move through the results; Enter opens one in its lens again.
5. *S-l* to lock the screen, and the password to unlock it.
6. *S-b*, then *s*, to shut down: the machine powers off.

Other things to try:

* New's *Import File...* makes an item from a file on the machine,
  such as a photo: the image lens shows it, and +, - and F zoom, [ and
  ] turn it, and C crops it (see :doc:`image-type`).  A PDF opens in
  the PDF lens: Ctrl+F finds text, and O shows its outline (see
  :doc:`pdf-type`).
* Typing a URL into Search, such as a PDF's, and pressing Enter fetches
  it, keeps it as an item, and opens it (see :doc:`url-type`).  Web
  pages can't be kept yet: PDFs, images and text can.
* *S-t* imports an address book, a ``.vcf`` file of vCards: the
  people and organisations in it are found by Search, by their names,
  and open in the entity lens (see :doc:`kb-service`).
* *S-h* lists what's happened recently, and opens an item from
  there too.

Your Data and Backups
---------------------

Everything DarqOS keeps is in its run directory, ``/darq/run``, which
only the ``darq`` user can read:

* ``storage.sqlite``, the items' contents;
* ``kb.sqlite``, the Knowledge Base: people, organisations, places,
  and what's known about them;
* ``history.sqlite``, the History of what's happened to each item;
* ``index.sqlite``, the search index, which can be rebuilt from the
  others (see :doc:`search-service`);
* ``security.json``, the password's hash; and
* ``bootvec.json``, the boot vector, which the services need to find
  their state.

Those are what to back up, and the install already does: a snapshot
each day, in ``/var/backups/darqos``, keeping the last seven (see
``darqos-backup(8)``).  To keep them off the machine too, set
``BACKUP_DEST`` in ``/etc/darqos/backup.conf``, to a directory or
``host:directory`` that root can reach with ``rsync`` over SSH.  To
take one now:

.. code-block:: sh

   systemctl start darqos-backup

To restore one, stop DarqOS, remove the databases' write-ahead logs
(the snapshot's databases don't need them, and the old ones would be
replayed over them), copy the snapshot's files into ``/darq/run``,
owned by ``darq``, and start it again:

.. code-block:: sh

   systemctl stop darqos-pkernel
   rm -f /darq/run/*.sqlite-wal /darq/run/*.sqlite-shm
   cp /var/backups/darqos/20261103-031500/* /darq/run/
   chown darq:darq /darq/run/*
   systemctl start darqos-pkernel

Troubleshooting
---------------

Log in over SSH.  Then:

* ``journalctl -u darqos-pkernel`` has DarqOS's log: the p-kernel's,
  and every process it started.
* ``loginctl`` should show the ``darq`` user's session, on seat0 and
  tty1.
* ``systemctl stop darqos-pkernel`` stops it; ``systemctl start
  darqos-pkernel`` starts it again.  If it keeps failing, it's left
  stopped after five tries a minute.
* To boot without DarqOS, ``systemctl set-default multi-user.target``
  (and ``systemctl set-default darqos.target`` to go back).  Without
  SSH, boot to single-user mode from the boot menu, and do the same.
* A forgotten password is reset by stopping DarqOS and deleting
  ``/darq/run/security.json``: the lock screen then asks for a new one
  (see :doc:`security`).
* Install's own logs are in ``/var/log/darqos``.

For trying DarqOS on a desktop, from its source tree, without
installing it, see :doc:`terminal` (Development Terminal).
