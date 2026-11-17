# darqos
# Copyright (C) 2026 David Arnold

# Tests for importing from Python package items (darq.packages, see
# doc/python-type.rst): by a path entry, by a package's name, and by its
# item, with what reads them from Storage, and finds them by name, faked.

import importlib
import inspect
import sys
import traceback

import pytest

import darq.packages as packages

TIDY = "darq.python:" + "1" * 32
HELPERS = "darq.python:" + "2" * 32
OTHER = "darq.python:" + "3" * 32

PACKAGES = {
    TIDY: {"__init__.py": '"""Tidy windows."""\nVERSION = 1\n',
           "main.py": "from tidy import util\n\ndef darq_main():\n    return util.twice(21)\n",
           "util.py": "def twice(n):\n    return 2 * n\n\ndef fails():\n    raise ValueError('from util')\n",
           "sub/__init__.py": "", "sub/deep.py": "WHERE = 'deep'\n"},
    HELPERS: {"__init__.py": "", "main.py": "import tidy\n\ndef darq_main():\n    return tidy.VERSION\n"},
}


@pytest.fixture
def stored(monkeypatch):
    """Packages, as Storage has them, and their names: the import system
    put back as it was after."""
    blobs = {item_id: packages.make(sources) for item_id, sources in PACKAGES.items()}
    reads: list[str] = []

    def reader(item_id):
        reads.append(item_id)
        if item_id not in blobs:
            raise ImportError(f"Can't open {item_id}")
        return blobs[item_id]

    names = {"tidy": [TIDY], "helpers": [HELPERS], "twins": [TIDY, OTHER]}
    monkeypatch.setattr(packages, "reader", reader)
    monkeypatch.setattr(packages, "finder", lambda name: names.get(name, []))
    monkeypatch.setattr(packages, "_zips", {})
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "path_hooks", list(sys.path_hooks))
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    monkeypatch.setattr(sys, "path_importer_cache", {})
    before = set(sys.modules)
    packages.install()
    yield blobs, reads
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_a_path_entry_is_a_directory(stored):
    """As pyloader runs a package: its main, from its item, first on the
    path."""
    sys.path.insert(0, packages.PREFIX + HELPERS)
    sys.path.insert(0, packages.PREFIX + TIDY)
    main = importlib.import_module("main")
    assert main.__file__ == f"darq-item:{TIDY}/main.py"
    assert main.darq_main() == 42
    assert importlib.import_module("sub.deep").WHERE == "deep"


def test_a_package_is_imported_by_its_name(stored):
    import tidy  # type: ignore[import-not-found]
    assert tidy.__doc__ == "Tidy windows." and tidy.__file__ == f"darq-item:{TIDY}/__init__.py"
    assert tidy.__path__ == [f"darq-item:{TIDY}"]
    from tidy import main  # type: ignore[import-not-found]
    assert main.darq_main() == 42
    assert importlib.import_module("tidy.sub.deep").WHERE == "deep"
    # And by another, which imports it.
    assert importlib.import_module("helpers.main").darq_main() == 1


def test_what_isnt_a_package_isnt_found(stored):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nothing_by_this_name")
    with pytest.raises(ImportError, match="More than one package is named twins"):
        importlib.import_module("twins")


def test_files_come_first(stored, monkeypatch):
    monkeypatch.setattr(packages, "finder", lambda name: [TIDY])
    assert str(importlib.import_module("json").__file__).endswith("json/__init__.py")


def test_by_its_item(stored):
    tidy = packages.load(TIDY, "tidy")
    assert sys.modules["tidy"] is tidy and tidy.VERSION == 1
    assert packages.load(TIDY, "tidy") is tidy
    unnamed = packages.load(HELPERS)
    assert unnamed.__name__ == "darq_python_" + "2" * 32
    with pytest.raises(ImportError, match="Can't open"):
        packages.load(OTHER, "other")
    assert "other" not in sys.modules


def test_its_source_is_shown(stored):
    import tidy.util  # type: ignore[import-not-found]
    assert inspect.getsource(tidy.util.twice) == "def twice(n):\n    return 2 * n\n"
    with pytest.raises(ValueError) as raised:
        tidy.util.fails()
    text = "".join(traceback.format_exception(raised.value))
    assert f'File "darq-item:{TIDY}/util.py", line 5, in fails' in text
    assert "raise ValueError('from util')" in text


def test_its_read_once_and_again_when_reloaded(stored):
    blobs, reads = stored
    import tidy.util  # type: ignore[import-not-found]
    import tidy.main  # type: ignore[import-not-found]
    assert reads == [TIDY]
    blobs[TIDY] = packages.make({**PACKAGES[TIDY], "util.py": "def twice(n):\n    return 3 * n\n"})
    importlib.reload(tidy.util)
    assert reads == [TIDY, TIDY] and tidy.util.twice(2) == 6


################################################################
# At boot: the type, Storage, and the KB
################################################################

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: E402, F401


def test_a_package_item_is_imported(live_pkernel, tmp_path):  # noqa: F811
    """A client makes a package item, imports it by its name, and by its
    item, changes it, and reloads it.  It's straight after booting, with
    nothing else started: its entity, its name, is made, as the KB's
    vocabulary is set up as it starts."""
    kernel = live_pkernel
    client_path = write_app(tmp_path / "client", """
        import importlib
        import time
        import darq
        import darq.launch
        import darq.packages as packages
        from darq.protocols.python import Python, Status
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="darq.python").implementation:
                if time.monotonic() > deadline:
                    return "darq.python never registered"
                time.sleep(0.05)

            made = packages.make({"main.py": "def darq_main():\\n    return 42\\n"})
            item_id, port = darq.launch.create_item_from("darq.python", made, "tidylive")
            packages.install()
            import tidylive.main
            if tidylive.main.darq_main() != 42 or tidylive.__darq_item__ != item_id:
                return "imported wrong"
            loaded = packages.load(item_id)
            if importlib.import_module(loaded.__name__ + ".main").darq_main() != 42:
                return "loaded wrong"

            item = Python.Client(port, timeout=30)
            got = item.get()
            changed = packages.make({"main.py": "def darq_main():\\n    return 43\\n"})
            if item.put(expected=got.generation, package=changed, source="test").status != Status.OK:
                return "not put"
            if item.put(expected=got.generation, package=made, source="test").status != Status.CONFLICT:
                return "a stale put was taken"
            importlib.reload(tidylive.main)
            if tidylive.main.darq_main() != 43:
                return "not reloaded"
            item.close()
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


def test_an_extracted_program_is_imported(live_pkernel, tmp_path):  # noqa: F811
    """A function extracted from a session, with what it needs, is made a
    package item, imported by its name, and run; changed, and renamed."""
    kernel = live_pkernel
    client_path = write_app(tmp_path / "client", """
        import importlib
        import time
        import darq
        import darq.packages as packages
        from darq.protocols.type import Type
        from darq.repl import Engine

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="darq.python").implementation:
                if time.monotonic() > deadline:
                    return "darq.python never registered"
                time.sleep(0.05)

            engine = Engine()
            engine.start()
            for cell in ["BASE = 40", "def helper():\\n    return BASE + 2",
                         "def darq_main():\\n    return helper()"]:
                if engine.run(cell).error:
                    return "a cell failed"
            written = engine.extract("darq_main")
            if written.left_out:
                return f"not extracted: {written.left_out}"
            item_id = packages.create(packages.make({"main.py": written.text}), "tidyx", "test")
            packages.install()
            import tidyx.main
            if tidyx.main.darq_main() != 42:
                return "it didn't run"
            if not packages.unusable("tidyx").startswith("another package is named tidyx"):
                return "its name wasn't taken"

            engine.run("BASE = 41")
            packages.update(item_id, packages.make({"main.py": engine.extract("darq_main").text}), "test")
            importlib.reload(tidyx.main)
            if tidyx.main.darq_main() != 43:
                return "not updated"
            packages.rename(item_id, "tidyy")
            if packages.name_of(item_id) != "tidyy":
                return "not renamed"
            if importlib.import_module("tidyy.main").darq_main() != 43:
                return "not imported by its new name"
            return 0
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


def test_a_package_is_run(live_pkernel, tmp_path):  # noqa: F811
    """A package item's spawned, as a program: its main.darq_main runs.
    One with none, or that's gone, fails, saying why."""
    kernel = live_pkernel
    ran = tmp_path / "ran"
    code = f"import darq\ndef darq_main():\n    open({str(ran)!r}, 'w').write(str(darq.getpid()))\n    return 0\n"
    client_path = write_app(tmp_path / "client", f"""
        import time
        import darq
        import darq.launch
        import darq.packages as packages
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="darq.python").implementation:
                if time.monotonic() > deadline:
                    return "darq.python never registered"
                time.sleep(0.05)

            program = packages.create(packages.make({{"main.py": {code!r}}}), "runme", "test")
            dpid = darq.launch.run_item(program)
            finished = darq.wait(dpid)
            if (finished.status, finished.error) != (0, None):
                return f"it finished {{finished!r}}"
            if open({str(ran)!r}).read() != str(dpid):
                return "it didn't run as itself"

            nothing = packages.create(packages.make({{"main.py": "x = 1"}}), "nomain", "test")
            finished = darq.wait(darq.launch.run_item(nothing))
            if finished.status == 0 or "darq_main" not in (finished.error or ""):
                return f"with no darq_main, it finished {{finished!r}}"

            gone = "darq.python:" + "f" * 32
            finished = darq.wait(darq.launch.run_item(gone))
            error = finished.error or ""
            if finished.status == 0 or "Error" not in error:
                return f"gone, it finished {{finished!r}}"
            try:
                darq.launch.run_item("public.utf8-plain-text:" + "1" * 32)
                return "text was run"
            except darq.LaunchError:
                pass
            return 0
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
