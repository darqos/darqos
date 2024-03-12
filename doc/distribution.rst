Installation
============

DarqOS supports two methods of distribution:

* tarball
* git clone

In either case, the host OS filesystem will contain the distributed image,
a typical Unix-style tree of files.

The host OS should be one of:

* Raspberry Pi OS Lite (64 bit) (https://www.raspberrypi.com/software/)
* Mobian (https://mobian-project.org/)
* Debian 12 *bookworm* AMD64 netinst (https://www.debian.org/download)

In all cases, the Darq distribution should be unpacked (or cloned)
into `/darq/dist`.  The `/darq/dist/install` script should then be run
(as `root`) to install or update the local installation.  This will
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

    * Directory containing the distributed files.  This can be either
      a snapshot from a tarball, or a `git clone` of the source
      repository.  This area is not used at runtime, but instead must
      be explicitly *installed* to copy any updated components to
      their runtime locations.

  * `/darq/kernel`

    * The p-kernel server implementation

  * `/darq/services`

    * Contains sub-directories for each installed service, containing
      their executable files and any associated file storage.

  * `/darq/types`

    * Contains sub-directories for each installed type implementation
      (described later).

  * `/darq/lenses`

    * User interface modules for installed type implementations.

  * `/darq/tools`

    * User applications

  * `/darq/sys`

    * System scripts, or other Unix programs needed to support the
      operation of the system.  These items should be largely, if
      not totally, invisible from within Darq itself.

  * `/darq/sys/init`

      * The system bootstrap program; the point of handover from the
        underlying Linux kernel to DarqOS.

  * `/darq/sys/venv`

    * Python virtual environment
    * Includes all installed dependencies
    * Install process will update this using the requirements
      file from the distribution.

  * `/darq/local`

    * Installed location of any required Unix software that is not
      provided by the host OS.

  * `/darq/run`

    * Home for files that are modified at runtime by the installation.
      This will notably include the SQLite databases used by the
      various services, kernel configuration, etc.
    * This directory will *not* be simply overwritten during an
      upgrade, but instead, will be processed by the install script to
      update all storage as required by the new version.

* Some items will be installed into the Linux environment:

  * systemd unit files
  * `/etc` configuration files (as altered by Darq)
