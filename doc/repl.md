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
