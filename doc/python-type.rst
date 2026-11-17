Python Package Type
===================

``darq.python`` is the type of Python code, as a package of modules,
kept as an item: so it's named, found by Search and History, and kept
in Storage, not in Linux's files.  It can be imported, as if it were a
directory of modules, and run, as a program.  It's what a REPL session
is kept as, and what's extracted from one (see :doc:`repl`), and run,
as a program (see Running, below).

Its lens is the REPL (``lenses/repl``): a package's a session, used in a
REPL of its own.  A session's code is its ``main.py``, and the cells it
ran are its ``history.json`` (see :doc:`repl`).

A package is a zip of its modules: ``__init__.py``, which it must have,
and the others, such as ``main.py``, which is what's run.  Their paths
are relative, and their sources UTF-8.  Its entity's label is its name,
as it's imported: ``import tidy`` imports the package named *tidy*.

Implementation
--------------

``types/python`` implements it, as ``types/data`` implements data: an
item is a Storage blob, identified by its key, in hex.  The blob is a
header, where the item's marked destroyed, and then the zip.

* ``Create(type_id)`` makes an empty package, with only an empty
  ``__init__.py``, and no name, so it can't be imported till it's
  named, in its entity.
* ``CreateFrom(type_id, data, name)`` keeps a package's zip, named
  *name*.  What isn't a package is refused, ``FAILED``.
* ``Export`` gives the zip back, as
  ``application/vnd.darq.python+zip``.
* Its item port serves the ``Python`` protocol
  (``lib/didl/python.didl``):

  * ``Get`` returns the zip, and its generation;
  * ``Put(expected, package, source)`` replaces it, if it's still the
    generation expected, recording it in History as ``edited``, from
    *source*.  If it's changed since, it's ``CONFLICT``, with the
    generation it is now: the client reads it again, and makes its
    change to that.  What isn't a package is ``INVALID``, with why.
  * ``Viewed``, for History, and ``Close``.

* Its entity is made with it, with its name as its label, and its kind,
  *Python Package*; renaming it publishes its new name to the Index.
* ``Destroy`` marks the blob destroyed, and its entity.

``darq.packages`` has the package format: ``make(files)``, a zip of
modules' sources, and other files, by path; ``modules(data)``, the
sources of one, and ``contents(data)``, all its files; and
``check(data)``, which says why what isn't a package isn't.  It makes
and changes package items: ``create(data, name, source)``;
``update(item_id, data, source, overwrite)``, which replaces a package,
if it's not been changed since, or ``overwrite`` says so;
``rename(item_id, name)``, and ``name_of(item_id)``; and
``unusable(name, item_id)`` says why a name can't be a package's: one
Python can't import, a module's already (``shadows``), or another
package's.

Importing
---------

``darq.packages.install()`` adds to Python's import system, so code in
a package is imported as code in a directory is (``importlib``):

* A path hook: ``darq-item:darq.python:<key>``, on ``sys.path``, or in a
  package's ``__path__``, is a package's zip, read as a directory, a
  sub-package's directory after a ``/``.  Its modules are imported from
  it, with ``PackagePathFinder`` and ``PackageLoader``.
* A finder, last on ``sys.meta_path``, so that the standard library,
  ``darq``, and files are found first: ``import tidy`` looks for the
  package items named *tidy*, by their entities' labels, in the
  Knowledge Base.  If there's one, it's imported, as a package, whose
  ``__path__`` is its zip; if there are several, it's an
  ``ImportError``, naming them.
* ``darq.packages.load(item_id, name)`` imports a package by its item,
  as *name*, or else as ``darq_python_<specifier>``.

A package is read from Storage once, through its type implementation,
and again when a module of it is reloaded, with ``importlib.reload``.
Each module's ``__file__`` is ``darq-item:<item>/<path>``, and its
``__darq_item__`` is its package's item.  Its loader gives its source,
so tracebacks show it, and ``inspect.getsource`` finds it.

Packages are imported by any process that installs the hooks: the
REPL does, and ``pyloader``, for a program kept as a package.

Running
-------

A package with a ``main.py`` that has a ``darq_main()`` is a program,
run by its item, as a directory's is: ``spawn_process`` takes
``darq.python:<key>`` as well as a directory.  The p-kernel can't read
Storage, so it passes the item to ``pyloader``, which installs the
import hooks, puts the item first on ``sys.path``, imports ``main``
from it, and calls ``darq_main()``.  If there's no such package, or no
``darq_main``, the process fails, saying why, as any program does.
``darq.launch.run_item(item_id)`` runs one, refusing what isn't a
package; the Terminal runs one with Ctrl+Enter in Search or History, as
its child (see :doc:`terminal`), and a script, or the REPL, with
``terminal.run_item``.
