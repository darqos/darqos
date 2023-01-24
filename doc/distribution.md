# Distribution

DarqOS will initially support two methods of distribution:
- tarball
- git clone

In either case, the host OS filesystem will contain the distributed image,
a typical Unix-style tree of files.

The host OS should be one of:
* Debian 11 _bullseye_ AMD64 netinst (https://www.debian.org/download)
* Raspberry Pi OS (aka Raspbian) (https://www.raspberrypi.com/software/)
* Mobian (https://mobian-project.org/)

IN all cases, the Darq distribution should be unpacked (or cloned)
into `/darq/dist`.  The `/darq/dist/install` script should then be
run to install (or update) the local installation.  This will install
any required host OS packages, configure the system to run Darq, and
then reboot.  

Following the initial reboot, the _firstboot_ application will run,
allowing the system owner to configure the system ready for ongoing
use.

## Image Layout

* `/draq`
  * Root of the installed files.  The only stuff installed outside
    this directory is configuration files to integrate with the host
    OS.
  * `/darq/dist`
    * Distribution files.  This can be either a snapshot from a 
      tarball, or a `git clone` of the source repository.  This area 
      is not used at runtime, but instead must be explicitly
      installed to activate any updated components.
  * `/darq/kernel`
    * The p-kernel server and related configuration databases.
  * `/darq/services`
    * Contains sub-directories for each installed service, containing
      their executable files and any associated file storage.
  * `/darq/types`
    * Contains sub-directories for each installed type implementation
  * `/darq/lenses`
  * `/darq/tools`
  * `/darq/sys`
    * System scripts, or other Unix programs needed to support the
      operation of the system.  These items should be largely, if
      not totally, invisible from within Darq itself.
  * `/darq/local`
    * Installed location of any required Unix software, not 
      provided by the host OS.
* Some items will be installed into the Linux environment:
  * systemd unit files
  * `/etc` configuration files (as altered by Darq)
