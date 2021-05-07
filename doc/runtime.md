# Runtime

The so-called "runtime" component is a library that's available to all
applications and, like the C runtime, provides both access to the
kernel's functionality, as well as various local supporting functions.


## API

### create_object
* The most basic representation of an object is a collection of
  metadata properties, including
  * type
  * name
  * creation datetime
  * last modified datetime
  * a content link, be it to local storage or a URL.

### activate_object
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

### deactivate_object
* Inform the type implementation that the object is no longer required
  to be active.
* Once deactivated, the type implementation may "forget" an object,
  unload its storage, etc.

### send_to_object
* Send an IPC message to an object
* I need to think about how I want receiving messages to work, but
  there should be a complementary call.

### create_process
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
