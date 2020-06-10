# Bootstrap

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
   
   
  