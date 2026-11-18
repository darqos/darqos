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
edited (see The Namespace Tree, below).

A session is an item, a Python package (``darq.python``, see
:doc:`python-type`), and the REPL is its lens: each session's used by a
REPL of its own, in its own process, with its own namespace.  *Sys-s*
opens the default session, or, if its REPL's open, focuses its window;
others are opened from History or Search.  A session's saved when it's
asked to be, and opened again with what was saved (see Saving a
Session, below).  It doesn't index anything; it can't debug.

* ``darq.repl.Evaluator`` is what runs the session, which uses no Qt: a
  namespace, and numbered cells run in it, in a process of its own, the
  REPL's evaluator (see The Evaluator, below).  A cell's last expression is its result,
  which ``$3`` refers to later (rewritten to ``_out[3]``, but in strings
  and comments), as ``_`` is the last.  What it prints is captured, and
  a traceback shows only the cell's frames.  Completion is jedi's,
  against the live namespace, as is help (``Evaluator.help``): a name's
  signatures, and its docstring, or a value's type's.
* ``lenses/repl`` is its window, the lens for ``darq.python`` items (its
  ``lens.json``), started by the launcher, as lenses are, for a ``Use``:
  it's titled with its session's name, and says, ``Shown``, which session
  it's using, as lenses do.  It uses one: another ``Use`` is ``BUSY``.
  It records the session viewed, from ``repl``, as it's shown.
* The default session is recorded in the Knowledge Base
  (``darq.sessions``): the *REPL settings* entity's *default session* is
  its entity, so there's only ever one, and it's found again whatever
  it's renamed to.  If there's none, or it's gone, *Sys-s* makes one,
  named *session*, and makes it the default.  *Sys-s* asks each lens's
  window what it's showing, to find the session's, off the Terminal's
  thread, and while it's starting the REPL, it does nothing.
* A transcript, above, and an editor, below, highlighted by Pygments
  in the DarqOS palette.  Enter runs a complete cell, or else starts a
  new line, indented; Shift+Enter always starts a new line, and
  Ctrl+Enter always runs.  Tab completes, as does a ``.``.  Tab with
  nothing to complete, after a whole name, or in a call's arguments,
  explains it: its signatures, highlighted, and its docstring, in a popup
  below the cursor, which PgUp and PgDn scroll, and Tab, Escape, or
  editing, close.  Up and Down,
  on the first or last line, step through what's been run.  Ctrl+A goes
  to the start of the line, as in Emacs, rather than selecting all.  Ctrl+L
  clears the transcript, and Ctrl+Q closes it.
* Its namespace starts with ``import darq``, and ``darq.terminal``, as
  ``terminal``, with its source ``repl``, so
  ``terminal.windows()`` and the rest are ready (see :doc:`terminal`,
  "Automation"), and what's made with them is recorded as the REPL's
  doing.  There's no event loop given: a cell makes its own, as a
  program does, to serve, or to ``terminal.watch``.
* While a cell runs, its output's shown as it's printed, and the window
  works: what's typed next waits for it, as Enter says, in the status
  line.  Ctrl+G interrupts it, and Ctrl+Shift+G restarts the evaluator.

The Evaluator
~~~~~~~~~~~~~

A session's code runs in a process of its own, the REPL's evaluator
(``lenses/repl/evaluator``), not in the REPL's: so what's written in the
REPL runs as it would in a program.  A cell can make its own
``QApplication``, run a ``QtEventLoop``, or a ``SelectEventLoop``, serve
a protocol, and open windows, which are the evaluator's.  And what goes
wrong in it, as a crash, or a cell that never returns, costs the
evaluator, not the window.

* The REPL starts it, as its child, when it uses a session
  (``darq.repl.start_evaluator``), and asks it over its protocol,
  ``darq.repl.evaluator`` (``lib/didl/evaluator.didl``), with
  ``darq.repl.Remote``: the evaluator serves ``darq.repl.Served``, the
  ``Evaluator`` asked by ids, as ``darq.repl.Service``.  It's private to
  the REPL: it isn't registered, and ends when the REPL's gone.
* What runs code, a cell, a save, a rebuild, a delete, or loading a
  session, runs on its main thread, one at a time, as a program's code
  does.  A cell's run returns at once: the REPL's told, on a port it
  listens on, when the cell starts, and its number, what it prints, as
  it prints it (held for 50ms, to send what's printed together, together),
  and its result.  A save, or the rest, while a cell runs, is ``BUSY``.
* Everything else, completion, help, the tree, sources, writing the
  session, and extracting, is answered at once, on another thread, even
  while a cell runs, from the namespace as it is then.
* The tree's targets are named by refs: ids the evaluator keeps each
  target by, as a target holds what it's bound in, which only it has.
  It forgets them when the tree's read again, but for the drafts'.
  What can be done to one, on its context menu, is the evaluator's to
  say (``Actions``).
* Ctrl+G interrupts what's running, with a ``SIGINT`` to the main
  thread: it's a ``KeyboardInterrupt``, in the cell, which stops a
  sleep, or a wait, too.  It can't stop C code that doesn't return to
  Python, as Qt's ``exec()``: Ctrl+Shift+G restarts the evaluator,
  killing it, and starting another.
* If the evaluator dies, the REPL's told (it watches its port), and
  restarts it, as Ctrl+Shift+G does.  A restart loads the session's
  code, as it was last saved: what's been run since is lost, but its
  cells are still in the history, to be run again.

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
is what was run, or saved, last, recorded by the evaluator as each cell
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

Saving a Session
~~~~~~~~~~~~~~~~

Ctrl+Shift+S, or *Save session* on the tree's context menu, saves the
session to its item: its code, as a module, ``main.py``, and the cells
that were run, as ``history.json``, a list of their sources.  The
package's other files are kept.  It's saved with the generation it was
read, or last saved, as: if it's been saved elsewhere since, as by
another REPL using it, it asks before saving over it.

The module is what each of the session's names is bound to, as it can
be written (``darq.repl.Evaluator.write_session``):

* a function's, or a class's, source, as it's been run, or saved, since:
  a class's header and its members as they are now;
* another name for one, as ``alias = helper``;
* an import, as the cell wrote it, one for each name it bound, or, for a
  module, or a library's function or class, that it's imported back by;
* a variable whose value's a literal, as its value is now: so a list a
  cell appended to is saved with what was appended;
* any other variable, by the assignment that last bound it, run again:
  an object's re-made as it was, not as it's been changed since.

What can't be written, as a value with no assignment, nor literal, is
left out, and so is what needs it to be defined; the transcript says
which, and why.  What the REPL gives the session, ``darq`` and
``terminal``, isn't saved, unless it's been bound to something else.  It's written in an order it can be run in: imports
first, then each name once what it needs as it's run is (as a class's
body, a decorator, a default value, or an assignment's value), found by
``symtable``; what's only needed once a function's called doesn't
order it.

Opened again, the session's ``main.py`` is run into its namespace, a
statement at a time, so one that fails, said in the transcript,
doesn't stop the rest, and what it defines is recorded, to be edited,
as a cell's is; its history's what Up and Down step through.  Closing
a session with what's been run since it was saved asks first.

Extracting a Package
~~~~~~~~~~~~~~~~~~~~

*Extract package…*, on a function's, or class's, context menu in the
tree, takes it out of the session, with everything of the session's it
needs, and nothing else, into a package item of its own
(``darq.repl.Evaluator.extract``): usually a program's ``darq_main``, to be
imported, or run, by itself.

What it needs is found from its source, and what's needed by that, and
so on, whether it's needed as it's defined, or only once it's called,
and written as a session is (see Saving a Session, above), as the
package's ``main.py``, in an order it can be run in.  The REPL's
``darq`` and ``terminal`` are imports, as they'd be anywhere.  Unlike
saving, nothing's left out: if anything it needs can't be written, or
isn't defined, or is a result, ``$3``, it's not extracted, and the
transcript says what, and why.
What's found by name as the code runs, as with ``getattr`` and a
string, or ``globals()``, isn't seen.

It asks for the package's name, as it's to be imported, which must be
one Python can import, not a module's already, as ``json``, and no
other package's.  What a function, or class, was last extracted to is
kept with the session (``extracts.json``): extracting it again asks
whether to update that one, which is renamed, if it's given another
name, or to make another.  An update replaces the package, asking first
if it's been changed since.

