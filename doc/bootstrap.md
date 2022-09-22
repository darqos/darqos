# Bootstrap

## P-Series

 1. Using the host OS, start the p-kernel process.
    * The p-kernel executable should be at a known, top-level
      location within the system image.
    * The system image should be a Unix directory tree, with various
      required files at known locations within the tree.
    * Despite being a file structure from the host OS viewpoint, it
      is considerd an opaque image from within DarqOS.
    * 
 1. Using either hard-wired, or possibly config-file, settings, the
    p-kernel should start the various system services required for
    operation, in the appropriate order.
    * Some p-kernel functions are implemented within the p-kernel 
      process itself, others exist as separate system service processes.
    * Services to be started include:
      * Storage
      * Metadata
      * Index
      * History
      * Security
      * Terminal
 1. The Terminal Service should be started last, and will allow the
    user to log in to the system.





 1. First prototype release
 2. Unix processes, communicated over TCP
 3. Python only
 4. Start order
    * Name server
    * Type server
    * Storage server
    * Others
 5. For start up, use a Python "agent"
    * Needs a TCP API
      * Reboot and shutdown actions
    * Shutdown should be a script too
      * Which just sends the right message to the agent
    * Has a list of services to start, in order
      * No dependencies (unless we really need them, static will do)
    * boot.py is parent process; all other spawn from it.
      * Could do something with control groups later, if required
    * Agent process should monitor services, and restart them on exit
 6. The build system should have a target that produces a
     pre-initialised storage service database.
     * This will avoid the need to come up with any other installation
       mechanism in the short term


## M0

In M0, there will be no actually DarqOS kernel (since it will use a host
operating system).

 1. Boot process is triggered.
    * By running a Unix bash script.
 1. Ssript starts the Kernel Service.
    * Kernel service is an implementation of the kernel's functionality,
      using the Unix API.  In particular, it implements the system IPC
      mechanism.
 1. The script starts the storage service.
    * Server is a Unix daemon process, written in Python (for now).
    * Pass it the "device" info, in this case a Unix filesystem path
    * The storage service registers itself with the kernel service, thus
      making itself available to its clients.
 1. Script starts Security Service.
    * Needs to access data from storage server
 1. Script starts index server
    * Needs to access data from the storage server
 1. Script starts metadata server.
    * Needs to access data from the storage server
 1. Script starts Terminal Service
    * Server is Unix daemon process
    * In this case, just create a large window.
    * Needs to have pointer & keyboard, in this case inherited from X11
      window, when in focus.
 
