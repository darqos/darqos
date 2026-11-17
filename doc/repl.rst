REPL
====

I want all the application software to expose an API, which is used by
the Lenses (GUI, audio, etc) (if there is any) or can be driven by any
other application as well.

Those APIs will be the basis of automation as well.  I don't want a
shell.  Shells are bad programming languages, and rather than use
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

Purpose
-------

The goal of the REPL is to support scripting of repetitive tasks, to
combine and extend the functionality of Types, and to use details of
Types that might no be easily accessed by Lens.

Environment
-----------

The REPL is essentially like a Lens: launched by the Terminal service
in response to an operator hotkey.

It should have access to:
- the kernel's name registry, and so to all registered services
- the Type service, and thus all existing items in the system
- the Terminal service, and from there, all active Lenses
- the Factory, so new items can be created  

Functionality
-------------

The UI is a split pane: top for results, bottom for code.  Results are
assigned a local reference using a "$<number>" syntax, available to
subsequent commands.

Code in the REPL can be executed normally, or potentially debugged
with single-stepping and breakpoints.

In future editions, the REPL might implement a Python interpreter, to
allow full reverse debugging, implementing the evaluation and
exception stacks in a nested Python interpreter.

It might make sense to have REPL "sessions", each of which represents
a distinct interpreter environment, with its own process, namespaces,
and sequence of executed code.  Sessions are persistent Items, and
have an editable name, tags, and associated KB entity.

C-level state, such as sockets, and ``numpy`` arrays probably cannot
be persisted or reified.

So, it's not yet clear how persistent a Session can be: obviously, the
text of the executed code can be saved, at a minimum (like a shell
history).  It should be feasible to persist the session's namespace:
it could be updated after each execution step, if that's actually
valuable?  Similarly, the global namespace, especially imports?  It
will need some experimentation to see what's useful here.

See the PyPI ``dill`` module for one possible contribution here.

History and Indexing
--------------------

Sessions contribute to History: creation, editing, and execution
events.  Imports, class and function names, and comments are also
indexed.

.

The First Cut
-------------

Second Edition's REPL is a first cut, from the prototype: Python, a
cell at a time, in a window, as a DarqOS process, so its cells can use
the darq API, with a tree of what's bound, which can be browsed, and
edited (see The Namespace Tree, below).  *Sys-s* opens it, or focuses its window if it's open:
there's one session, which ends when its window's closed.  It doesn't
record History, or index anything; it can't debug, or save its
session.

* ``darq.repl`` is its engine, which uses no Qt: a namespace, and
  numbered cells run in it.  A cell's last expression is its result,
  which ``$3`` refers to later (rewritten to ``_out[3]``, but in strings
  and comments), as ``_`` is the last.  What it prints is captured, and
  a traceback shows only the cell's frames.  Completion is jedi's,
  against the live namespace.
* ``tools/repl`` is its window, a tool, *Python REPL*, so it's started as
  the Terminal's child, as tools are, and only one runs at a time.  Its
  manifest says it's ``quiet``: its finishing isn't said, unless it
  failed.  Its windows' Wayland app_id is ``darq.repl``, by which
  *Sys-s* finds it.
* A transcript, above, and an editor, below, highlighted by Pygments
  in the DarqOS palette.  Enter runs a complete cell, or else starts a
  new line, indented; Shift+Enter always starts a new line, and
  Ctrl+Enter always runs.  Tab completes, as does a ``.``; Up and Down,
  on the first or last line, step through what's been run.  Ctrl+A goes
  to the start of the line, as in Emacs, rather than selecting all.  Ctrl+L
  clears the transcript, and Ctrl+Q closes it.
* Its namespace starts with ``import darq``, and ``darq.terminal``, as
  ``terminal``, with its source ``repl``, so
  ``terminal.windows()`` and the rest are ready (see :doc:`terminal`,
  "Automation"), and what's made with them is recorded as the REPL's
  doing; and ``loop``, its Qt event loop, for ``terminal.watch``.
* Cells run on Qt's thread: a long one, or a slow call, holds the
  window till it's done, and can't be interrupted.

The Namespace Tree
~~~~~~~~~~~~~~~~~~

On the left is what's bound: the *Session*, the cells' namespace (the
REPL's own names left out), with ``darq`` and ``terminal`` in it; the
*Results*, ``$1`` and on; and the *Built-ins*.  A module expands to its
names, a class to its own members, and an object to its instance
attributes, each read only when it's expanded, without running any of
its code (``inspect.getattr_static``), so a property isn't computed by
being listed.  Private names, starting with ``_``, are shown only if
the context menu says so.  Each name has a badge for its kind, **M**
module, **C** class, **ƒ** function or method, **P** property, **v**
variable, and its type.  It's read again after each cell, or save,
keeping what's expanded.

Clicking a name edits it, in the editor, whose header says what it's
editing, with a × to go back to the cell: so does Escape.  Its source
is what was run, or saved, last, recorded by the engine as each cell
runs, as IPython does, by object: a function's ``def``, with its
decorators; a method's; a class's *header*, its decorators, its class
line, and its docstring, whose members are edited one by one; a
variable's ``name = value``, if its value is a literal, or else the
assignment that bound it.  A library's code, or a built-in, is shown,
read-only: only what's defined in a cell is edited.  An object's
attributes are edited wherever it came from, as they're state, not
code.

While it's edited, Enter starts a new line, and Ctrl+S saves it, as a
numbered cell, ``# edit Shape.area``, in the transcript:

* A variable's rebound to its new value.
* A function's changed in place: its code, defaults, and documentation,
  so what holds it, such as a callback, sees the change.  If its
  closure's changed, as when it's no longer wrapped by a decorator, it
  can't be, so it's bound anew, and the transcript says what holds the
  old one keeps it.
* A method's compiled in a stand-in class's body, so ``super()`` works,
  then pointed at its class, and changed in place, or set on the class,
  so every instance has it.
* A class's header changes its docstring, and its bases, in place.  A
  change of decorators, metaclass, or class keywords rebuilds it: its
  header, and its members' sources, are run again, and the new class is
  bound in its place, keeping what was set on the old otherwise.  What
  was made of the old class keeps it.

The context menu adds a member to a class defined in a cell, rebuilds
it, as after changing a dataclass's fields, which its decorator made
its ``__init__`` from, shows it whole, read-only, as it is now, its
header and members as they were last run or saved, with what has no
source here, as what a decorator made, listed as comments, or deletes
a name, as a cell too, ``del Shape.sides``.

What's edited and not saved is a *draft*, kept while the session lasts:
going elsewhere, and coming back, brings it back.  A name with a draft
is marked with a dot, and the drafts are listed under *Drafts*, at the
top of the tree, even if what they're of has gone.  A save ends it.

