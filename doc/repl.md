# REPL

I want all the application software to expose an API, which is used by
the GUI (if there is one) or can be driven by any other application
as well.

Those APIs will be the basis of automation as well.  I don't want a
shell.  Shells are shitty programming languages, and rather than use
that, I want to have a real programming language.  OTOH, you need a
REPL for doing simple stuff: you don't want to have to write a 
"program" to do something simple.

So, there needs to be a REPL, possibly many REPLs.  There's no good
reason to actually specify a _single_ language.  But I'd like to stay
away from the Unix-style shell.

OTOH, I would like to have the general composition of functionality
that is exposed by a Unix shell.  Like, wc/sort/grep/etc.  It's not
clear whether those should be provided by the language runtime, or
provided as services in the OS but exposed in the language via the
services' APIs.

So, the question is: how should this be exposed in the first-run
UI scenario?  And beyond that: should there be a default REPL built
into the UI?

If I want to do something, I can:
* Create a new object (vis System-n)
* Do something, via a task (how are tasks launched?)
* Search for something (via System-space)

Where does a REPL fit in here?
* Should it be a popup panel, like the object selector (System-space)?
* How does the REPL integrate with eg. history?
* Does the REPL incorporate an output device, like a terminal does?
* Is there even a `print` function?
* Does the object system deal with instances of stuff like string or int?
* What are the actual use-cases for the REPL?
* What's the boundary between services, and in-built functionality in 
  a programming language?


PowerShell
* Good: move away from just lines of text as the only interface
* Bad: awful syntax for interactive usage
* Replace lines of text with XML.
  * Good: much more rich data model
  * Bad: awful for interactive use
  * Bad: verbose
  * Bad: ugly
  * Bad: inaccurate for eg. floats

Bash
* Good: basic interactive syntax is easy
* Bad: quoting, esp. nested, sucks
* Bad: segregated name spaces: PIDs, sockets, scalars, arrays, etc
* Bad: crappy language
* Bad: no debugger
* Bad: no live editing: change file, and restart
* Bad: stdin/stdout/stderr API is too limiting
* Bad: only signals for interaction after process start
* Bad: UI is a mess, with both TTY-style and curses-style together
* Bad: X11 is tacked on
* Bad: no way to convert interactive history to saved script
* Bad: basically no interaction with GUI apps once launched (signals)


A user can only _focus_ on one target for input at a time.  So, it's
probably feasible to have a single point of interaction with the REPL.

There might reasonably be many, even many thousands, of tasks running
simultaneously in the REPL.  These tasks need to be first-class
entities, beyond bash job control, more like threads in a debugger.

At any point, it should be possible to persist the code of a REPL
thread such that it can be reused in future.  And consequently, a
previously-persisted REPL thread must be able to be re-launched.
Open questions as to whether the entire state of the thread should be
captured, or just the code?

Full introspection of REPL contexts should be feasible: see the value
of all variables, browse APIs, set breakpoints, watchpoints, etc.

When interacting with types and tasks, the introspection will be 
limited to their API layer: it likely won't be feasible to see into
their implementation.  Although ... with a debugger protocol?  Maybe
the could be like the marketing for the .NET debugger that could
step all the way from front-end to device driver?

When a REPL program is manipulating a type instance, it should be
feasible to attach a Lens to the type so it can be viewed.

