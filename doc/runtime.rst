
The so-called "runtime" component is a library that's available to all
applications and, like the C runtime, provides both access to the
kernel's functionality, as well as various local supporting functions.


Architecture
============

Python
------

A Python process is started by the p-kernel using a "loader"
executable.  This process establishes a socket connection to the
p-kernel, and sends a message containing its DPID allowing the
p-kernel to associate the DPID with the accepted socket's file
descriptor.

It then starts a second, I/O, thread, running an event loop that
services the p-kernel connection.  Once the p-kernel accepts the
process, the main thread imports the application, and runs its entry
point.  The application owns the main thread, so it can use toolkits
(like Qt) that require it, and it receives signals.

The runtime API functions often need to interact with the p-kernel to
implement their functionality.  For synchronous functions, this
interaction uses a queue, and posts a call to the I/O thread's event
loop to send the request.  The application waits on a threading.Event,
which is then signaled when the p-kernel's response is received,
unblocking it.  Callback-style functions instead have their callback
posted to the application's event loop (see the event loop's post()
method), as are messages delivered to the application's listener, or
to a port's own listener (see `set_port_listener`, in :doc:`ipc`).
Async code can await the asynchronous versions of the system calls,
which use loop.call_soon_threadsafe() to update the future safely.

The application's entry point is executed in a try:finally: block,
which captures its exit and passes it to the I/O thread for
communication back to the p-kernel.  An abnormal exit (ie. interpreter
crash, host OS process kill, etc) is detected by closure of the TCP
connection to the p-kernel.

API
===

The runtime environment for DarqOS applications consists of:

* Simple system calls

  * Things that would be provided by the real kernel, but require no
    state in the p-kernel.
  * These are implemented within the application's thread, where they
    have access to the process' static state.
  * Examples are: getpid() (which uses local state), and gettime()
    (which directly accesses the host OS)

* Complex system calls

  * Things that need to access "kernel" state, held in the p-kernel
    executable
  * Examples are

    * the IPC system
    * creating or destroying processes

* System service wrappers

  * System services are extensively used to implement DarqOS tools,
    types, lenses, and other services
  * Rather than sending raw messages, wrapper APIs are provided to
    make application code simpler

* General programming model helper functions

  * Base classes for the application code framework used by DarqOS
    tools, types, lenses, and services.

    
create_object
-------------

* The most basic representation of an object is a collection of
  metadata properties, including

  * type
  * name
  * creation datetime
  * last modified datetime
  * a content link, be it to local storage or a URL.

activate_object
---------------
* An object "at rest" is an entry in the metadata database.  In order
  to "use" the object, it must be activated.  This involves:

  * Fetch the object's metadata from the Metadata Service.
  * Get the type from the metadata, and ask the Type Service to
    activate it.

    * The Type Service will spawn the type implementation, if it's not
      already running.
    * The type implementation will create the instance, using the
      metadata to load any required content from storage or remote
      URL.
    * The activation request must include a context for the instance:

      * User identity
      * A terminal session
      * Anything else?

    * Given this context, and the persistent content, the type
      implementation will be able to render the object.

* Once activated, an "object" is really an IPC handle for the type
  implementation, and an identifier for the specific instance that the
  type implementation supplies.

  * There should be at most one type implementation process for each
    type active in the system.

    * It may use multiple threads to support its pool of instances.
    * Type implementations might support multiple types as well.
    * All interaction with type instances occurs via IPC to the type
      implementation

* If you choose to "view" the type instance (aka object), you will
  need to provide a session context within which it should be
  displayed.

  * The default context will refer to your terminal session, so the
    object can be displayed using its framebuffer.

* The type implementation is responsible for updating the history
  service, recording significant events in the object's lifecycle.

deactivate_object
-----------------

* Inform the type implementation that the object is no longer required
  to be active.
* Once deactivated, the type implementation may "forget" an object,
  unload its storage, etc.

send_to_object
--------------

* Send an IPC message to an object
* I need to think about how I want receiving messages to work, but
  there should be a complementary call.

create_process
--------------

* Execute a program in a new process.
* I'm not sure what form the program will take:

  * If it was C, a basic executable would usually work.
  * If it's Python, perhaps I should PyInstaller things?

* Regardless, it'd be nice if it was a darq object, with metadata and
  content as usual ...
* This should end up being used to run services.
* The Type Service should use this to run type implementations.
* I need to figure out what kinds of processes there are:

  * Type implementations
  * Services
  * What else?
