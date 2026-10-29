
Much of the DarqOS functionality is implemented as a set of system
*services* providing functionality via the system IPC mechanism.  In a
similar way to many micro-kernels, few if any user applications could
function using the *p-kernel* functionality alone.

In the First Edition, the following services exist:

* Terminal
* Security
* Storage
* History

..
   * Metadata
   * Index
   * Network
   * Configuration
