# darqos
# Copyright (C) 2026 David Arnold

"""Python packages kept as items: darq.python items, each a zip of its
modules in a Storage blob (see doc/python-type.rst); and importing from
them, as if they were directories.

A package's zip has an ``__init__.py``, and its other modules, such as
``main.py``, which pyloader runs.  Its modules' paths are relative, and
its sources UTF-8.

Once install() has been called, as the REPL and pyloader do:

* ``darq-item:darq.python:<key>`` on ``sys.path``, or in a package's
  ``__path__``, is a package's blob, read as a directory: its modules
  are imported from it.
* ``import tidy`` imports the package whose item is named *tidy*, if
  nothing else is found by that name first: there must be only one.
* ``load(item_id)`` imports a package by its item.

What's imported is read from Storage once, and again when it's
reloaded, with ``importlib.reload``.  Its modules' ``__file__`` is
``darq-item:<item>/<path>``, and their source is there for tracebacks,
and ``inspect.getsource``."""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import keyword
import sys
import types
import typing
import zipfile

import darq.kb as model
from .errors import DarqError
from .identifier import split_item_id


# Control exports
__all__ = ["EXTRACTS", "HISTORY", "INIT", "MAIN", "PREFIX", "TYPE_ID", "PackageError", "check", "contents", "create",
           "install", "is_name", "load", "make", "modules", "name_of", "rename", "shadows", "unusable", "update"]

# The type of a package item.
TYPE_ID = "darq.python"

# What a package's path entries start with, then its item, and a
# directory in it, if it isn't the top.
PREFIX = "darq-item:"

# A package's own module, and the module pyloader runs, which is a REPL
# session's code; and a session's cells, as JSON, a list of their sources.
INIT = "__init__.py"
MAIN = "main.py"
HISTORY = "history.json"

# What a session's names were extracted to, as JSON: package items, by
# name (see darq.repl, extract).
EXTRACTS = "extracts.json"

# Seconds to wait for a package's type implementation.
TIMEOUT = 10.0


class PackageError(ValueError):
    """What's given isn't a package."""


def make(sources: typing.Mapping[str, str | bytes]) -> bytes:
    """Return a package, as a zip, of its files, by path, such as
    {"main.py": "..."}: modules' sources, and any other files, as text or
    bytes; with an empty __init__.py, if it hasn't one."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as package:
        for path, source in sorted({INIT: "", **sources}.items()):
            package.writestr(path, source.encode("utf-8") if isinstance(source, str) else source)
    return data.getvalue()


def contents(data: bytes) -> dict[str, bytes]:
    """Return all of a package's files, by path.

    :raises PackageError: if it isn't a package."""
    check(data)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return {name: package.read(name) for name in package.namelist() if not name.endswith("/")}


def modules(data: bytes) -> dict[str, str]:
    """Return a package's modules' sources, by path.

    :raises PackageError: if it isn't a package."""
    check(data)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return {name: package.read(name).decode("utf-8") for name in package.namelist() if name.endswith(".py")}


def check(data: bytes):
    """Check that a package is one: a zip, with an __init__.py, and
    relative paths, whose modules are UTF-8.

    :raises PackageError: if it isn't, saying why."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            names = package.namelist()
            if INIT not in names:
                raise PackageError(f"It has no {INIT}")
            for name in names:
                if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                    raise PackageError(f"{name} isn't a relative path")
                if name.endswith(".py"):
                    package.read(name).decode("utf-8")
    except zipfile.BadZipFile as e:
        raise PackageError(f"It isn't a zip: {e}") from None
    except UnicodeDecodeError as e:
        raise PackageError(f"A module isn't UTF-8: {e}") from None


def is_name(name: str) -> bool:
    """Return whether a name can be a package's: one that can be
    imported."""
    return name.isidentifier() and not keyword.iskeyword(name)


################################################################
# Reading packages from Storage
################################################################

def fetch(item_id: str) -> bytes:
    """Return a package's zip, from its type implementation.

    :raises ImportError: if it can't be read."""
    import darq.launch
    from .protocols.python import Python
    from .system import close_port
    try:
        port = darq.launch.open_item(item_id)
    except (DarqError, TimeoutError) as e:
        raise ImportError(f"Can't open {item_id}: {e}") from None
    try:
        return Python.Client(port, timeout=TIMEOUT).get().package
    except (DarqError, TimeoutError) as e:
        raise ImportError(f"Can't read {item_id}: {e}") from None
    finally:
        try:
            Python.Client(port, timeout=TIMEOUT).close()
        except (DarqError, TimeoutError):
            close_port(port)


def named(name: str) -> list[str]:
    """Return the packages' items named so, by their entities' labels.

    :raises ImportError: if the Knowledge Base can't be asked."""
    try:
        kb = model.KB.connect(timeout=TIMEOUT)
        ids = sorted(kb.find_by_label(name))
        found, _ = kb.get_entities(ids, follow=False) if ids else ([], None)
    except (DarqError, TimeoutError) as e:
        raise ImportError(f"Can't look for a package named {name}: {e}") from None
    return [model.item_id(e.id, TYPE_ID) for e in found
            if model.recorded_type(e) == TYPE_ID and e.redirect is None and not e.destroyed
            and e.labels.get("en") == name]


################################################################
# Importing
################################################################

# Packages' zips, by item, as last read; and how they're read, and how
# packages are found by name: tests replace them.
_zips: dict[str, zipfile.ZipFile] = {}
reader: typing.Callable[[str], bytes] = fetch
finder: typing.Callable[[str], list[str]] = named


def _zip(item_id: str, fresh: bool = False) -> zipfile.ZipFile:
    if fresh or item_id not in _zips:
        data = reader(item_id)
        try:
            check(data)
        except PackageError as e:
            raise ImportError(f"{item_id} isn't a package: {e}") from None
        _zips[item_id] = zipfile.ZipFile(io.BytesIO(data))
    return _zips[item_id]


def _entry(item_id: str, directory: str = "") -> str:
    return f"{PREFIX}{item_id}" + (f"/{directory}" if directory else "")


class PackageLoader(importlib.abc.InspectLoader, importlib.abc.Loader):
    """Loads a module from a package's zip.  Executed again, as by
    importlib.reload, it reads the package again first."""

    def __init__(self, item_id: str, path: str):
        self.item_id = item_id
        self.path = path

    def get_filename(self, fullname: str | None = None) -> str:
        return _entry(self.item_id, self.path)

    def get_source(self, fullname: str) -> str:
        return _zip(self.item_id).read(self.path).decode("utf-8")

    def is_package(self, fullname: str) -> bool:
        return self.path.rpartition("/")[2] == INIT

    def get_code(self, fullname: str) -> types.CodeType:
        return compile(self.get_source(fullname), self.get_filename(fullname), "exec", dont_inherit=True)

    def create_module(self, spec):
        return None

    def exec_module(self, module: types.ModuleType):
        if getattr(module, "__darq_loaded__", False):
            _zip(self.item_id, fresh=True)
        module.__dict__["__darq_item__"] = self.item_id
        exec(self.get_code(module.__name__), module.__dict__)
        module.__dict__["__darq_loaded__"] = True


class PackagePathFinder(importlib.abc.PathEntryFinder):
    """Finds modules in a package's zip, for a path entry,
    darq-item:<item>[/<directory>]: a path hook."""

    def __init__(self, entry: str):
        if not entry.startswith(PREFIX):
            raise ImportError("not a package's")
        item_id, _, directory = entry.removeprefix(PREFIX).partition("/")
        try:
            type_id, _ = split_item_id(item_id)
        except DarqError:
            raise ImportError(f"{item_id} isn't an item") from None
        if type_id != TYPE_ID:
            raise ImportError(f"{item_id} isn't a {TYPE_ID}")
        self.item_id = item_id
        self.directory = directory

    def find_spec(self, fullname: str, target: types.ModuleType | None = None):
        tail = fullname.rpartition(".")[2]
        prefix = f"{self.directory}/" if self.directory else ""
        names = set(_zip(self.item_id).namelist())
        package = f"{prefix}{tail}/{INIT}"
        if package in names:
            return _with_path(fullname, self.item_id, package, f"{prefix}{tail}")
        module = f"{prefix}{tail}.py"
        if module in names:
            spec = importlib.machinery.ModuleSpec(fullname, PackageLoader(self.item_id, module),
                                                  origin=_entry(self.item_id, module))
            spec.has_location = True
            return spec
        return None

    def invalidate_caches(self):
        _zips.pop(self.item_id, None)


def _with_path(fullname: str, item_id: str, path: str, directory: str) -> importlib.machinery.ModuleSpec:
    """A package's spec: its submodules are in its directory."""
    spec = importlib.machinery.ModuleSpec(fullname, PackageLoader(item_id, path), origin=_entry(item_id, path),
                                          is_package=True)
    spec.submodule_search_locations = [_entry(item_id, directory)]
    spec.has_location = True
    return spec


class NamedPackageFinder(importlib.abc.MetaPathFinder):
    """Finds a top-level package by its item's name, after everything
    else has been looked in."""

    def find_spec(self, fullname: str, path=None, target=None):
        if path is not None or "." in fullname or not is_name(fullname):
            return None
        try:
            found = finder(fullname)
        except ImportError:
            return None
        if not found:
            return None
        if len(found) > 1:
            raise ImportError(f"More than one package is named {fullname}: {', '.join(found)}", name=fullname)
        return package_spec(fullname, found[0])

    def invalidate_caches(self):
        _zips.clear()


def package_spec(name: str, item_id: str) -> importlib.machinery.ModuleSpec:
    """A package's spec, as a top-level package named so: its zip's top
    is its directory."""
    spec = importlib.machinery.ModuleSpec(name, PackageLoader(item_id, INIT), origin=_entry(item_id, INIT),
                                          is_package=True)
    spec.submodule_search_locations = [_entry(item_id)]
    spec.has_location = True
    return spec


def install():
    """Import from packages: as a path hook, and, last, by name."""
    if PackagePathFinder not in sys.path_hooks:
        sys.path_hooks.insert(0, PackagePathFinder)
        sys.path_importer_cache.clear()
    if not any(isinstance(f, NamedPackageFinder) for f in sys.meta_path):
        sys.meta_path.append(NamedPackageFinder())


def load(item_id: str, name: str = "") -> types.ModuleType:
    """Import a package by its item, as name, or else as its item's
    specifier: it's kept in sys.modules, so its modules can import each
    other.

    :raises ImportError: if it can't be."""
    name = name or f"darq_python_{split_item_id(item_id)[1]}"
    if name in sys.modules and getattr(sys.modules[name], "__darq_item__", None) == item_id:
        return sys.modules[name]
    spec = package_spec(name, item_id)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module



################################################################
# Making and changing package items
################################################################

def shadows(name: str) -> bool:
    """Return whether a name's a module's already, which import would find
    before a package by that name: the standard library's, an installed
    one, or one on the path."""
    if name in sys.builtin_module_names:
        return True
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__darq_item__", None) is None:
        return True
    for found in sys.meta_path:
        if isinstance(found, NamedPackageFinder):
            continue
        find = getattr(found, "find_spec", None)
        try:
            if find is not None and find(name, None) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def unusable(name: str, item_id: str = "") -> str:
    """Return why a name can't be a package's, which it's to be imported
    by, or nothing if it can: it must be one Python can import, not a
    module's already, and no other package's.  Another package, but this
    item, has it."""
    if not is_name(name):
        return f"{name!r} isn't a name Python can import"
    if shadows(name):
        return f"{name} is a module already, which import would find first"
    others = [i for i in finder(name) if i != item_id]
    if others:
        return f"another package is named {name}: {', '.join(others)}"
    return ""


def create(data: bytes, name: str, source: str) -> str:
    """Make a package item, named, from its zip, as made by source.

    :raises DarqError, TimeoutError: if it can't be."""
    import darq.launch
    from .protocols.python import Python
    from .protocols.type import Context
    item_id, port = darq.launch.create_item_from(TYPE_ID, data, name, context=Context(source=source))
    Python.Client(port, timeout=TIMEOUT).close()
    return item_id


def update(item_id: str, data: bytes, source: str, overwrite: typing.Callable[[], bool] = lambda: False):
    """Replace a package item's zip, as changed by source.  If it's
    changed since it was read, it's replaced only if overwrite says so.

    :raises PackageError: if it isn't replaced, saying why.
    :raises DarqError, TimeoutError: if it can't be reached."""
    import darq.launch
    from .protocols.python import Python, Status
    item = Python.Client(darq.launch.open_item(item_id), timeout=TIMEOUT)
    try:
        response = item.put(expected=item.get().generation, package=data, source=source)
        if response.status == Status.CONFLICT and overwrite():
            response = item.put(expected=item.get().generation, package=data, source=source)
        if response.status != Status.OK:
            raise PackageError({Status.CONFLICT: "it was changed elsewhere"}.get(response.status, response.message)
                               or response.status.name)
    finally:
        item.close()


def name_of(item_id: str) -> str:
    """Return a package item's name, or nothing, if it has none, or it
    can't be read."""
    try:
        entity = model.KB.connect(timeout=TIMEOUT).get(split_item_id(item_id)[1])
    except (DarqError, TimeoutError):
        return ""
    return entity.labels.get("en", "") if entity is not None else ""


def rename(item_id: str, name: str):
    """Name a package item, as it's imported.

    :raises DarqError, TimeoutError: if its entity can't be changed."""
    kb = model.KB.connect(timeout=TIMEOUT)
    entity = kb.get(split_item_id(item_id)[1])
    if entity is not None and entity.labels.get("en") != name:
        entity.set_label(name)
        kb.put(entity)
