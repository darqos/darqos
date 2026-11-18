# darqos
# Copyright (C) 2026 David Arnold

# Tests for what the darq package itself has: its API, not its errors,
# which are darq.errors's.

import inspect

import darq
import darq.errors
import darq.loop
import darq.qtloop


def test_the_errors_are_darq_errors_only():
    errors = {name for name, value in vars(darq.errors).items()
              if inspect.isclass(value) and issubclass(value, BaseException)}
    assert "DarqError" in errors and "LaunchError" in errors
    assert not errors & set(vars(darq)), sorted(errors & set(vars(darq)))


def test_darq_errors_comes_with_darq():
    """import darq is enough to use darq.errors."""
    import pathlib
    import subprocess
    import sys
    root = pathlib.Path(__file__).parent.parent
    done = subprocess.run([sys.executable, "-c", "import darq; print(darq.errors.DarqError.__name__)"],
                          capture_output=True, text=True, cwd=root, env={"PYTHONPATH": str(root)})
    assert done.stdout.strip() == "DarqError", done.stderr



def test_the_loops_are_their_modules_only():
    exported = set(darq.loop.__all__) | set(darq.qtloop.__all__)
    assert exported == {"EventLoopInterface", "SelectEventLoop", "SocketListener", "TimerListener", "QtEventLoop"}
    assert not exported & set(vars(darq))
    assert darq.loop is vars(darq)["loop"]


def test_darq_doesnt_import_qt():
    """Only what has windows needs Qt: import darq doesn't bring it."""
    import pathlib
    import subprocess
    import sys
    root = pathlib.Path(__file__).parent.parent
    code = "import sys, darq; print(sorted(m for m in sys.modules if 'PyQt' in m))"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root,
                          env={"PYTHONPATH": str(root)})
    assert done.stdout.strip() == "[]", done.stdout + done.stderr


def test_process_and_version_are_their_modules_only():
    import darq._runtime.process
    import darq.version
    assert not (set(darq._runtime.process.__all__) | set(darq.version.__all__)) & set(vars(darq))
    assert "process" not in vars(darq) and vars(darq)["version"] is darq.version


def test_what_programs_use_is_darqs():
    """Item identifiers, and the operator's languages, are used directly."""
    for name in ("split_item_id", "canonical_type_id", "MAX_ITEM_ID_LENGTH", "languages", "language_chain",
                 "language_tag"):
        assert name in vars(darq), name


def test_systems_internals_arent_darqs():
    """What generated code, and the runtime, use: darq.system's, not darq's."""
    import darq.system
    for name in ("Client", "Handler", "call", "transact", "init", "init_cb", "SpawnResult"):
        assert name not in vars(darq) and hasattr(darq.system, name), name
    assert not hasattr(darq.system, "init_a") and not hasattr(darq.system, "get_loop")


def test_each_limit_is_one_thing():
    import darq.identifier
    assert darq.MAX_NAME_LENGTH == darq.identifier.MAX_NAME_LENGTH == 31
    assert darq.MAX_REGISTERED_NAME_LENGTH == 255


def test_darqs_exports_are_all_it_means_to_export():
    """darq's __all__ is what it imports for programs: no more, no less."""
    import types
    import darq.identifier
    import darq.system
    expected = set(darq.system.__all__) | set(darq.identifier.__all__) | {
        "language_chain", "language_tag", "languages", "errors", "loop", "version"}
    assert set(darq.__all__) == expected and len(darq.__all__) == len(expected)
    assert all(hasattr(darq, name) for name in darq.__all__)
    assert "send_message" not in vars(darq) and hasattr(darq.system, "send_message")
    incidental = {n for n, v in vars(darq).items() if isinstance(v, types.ModuleType)} - set(darq.__all__)
    assert not incidental & {"pcall", "process", "compositor", "vocabulary"}


def test_the_runtimes_modules_are_its():
    import subprocess
    import sys
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    code = ("import darq, darq._runtime.pcall, darq._runtime.process, darq._runtime.compositor, "
            "darq._runtime.vocabulary; print(sorted(n for n in ('pcall', 'process', 'compositor', 'vocabulary') "
            "if hasattr(darq, n)))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root,
                          env={"PYTHONPATH": str(root)})
    assert done.stdout.strip() == "[]", done.stdout + done.stderr



def test_darq_ui_is_only_whats_shared():
    """Importing a shared part of the UI doesn't bring the Terminal's, nor
    the Knowledge Base, the Index and launching, as its services did."""
    import pathlib
    import subprocess
    import sys
    root = pathlib.Path(__file__).parent.parent
    code = ("import sys, darq.ui; print(sorted(m for m in ('darq.kb', 'darq.index', 'darq.launch') "
            "if m in sys.modules), [n for n in vars(darq.ui) if not n.startswith('__')])")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root,
                          env={"PYTHONPATH": str(root)})
    assert done.stdout.strip() == "[] []", done.stdout + done.stderr
    for name in ("history", "search", "types", "tools"):
        assert not (root / "darq" / "ui" / f"{name}.py").exists(), name
    for module in (root / "darq" / "ui").glob("*.py"):
        if module.name != "__init__.py":
            assert "\n__all__ = [" in module.read_text(), module.name
