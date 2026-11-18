# darqos
# Copyright (C) 2026 David Arnold

"""The REPL's evaluator: Python, run a cell at a time, in one namespace,
so its cells can use the darq API.  See doc/repl.rst.

It runs in a process of its own, the evaluator (lenses/repl/evaluator),
which serves it to the REPL's window (lenses/repl) over its protocol
(lib/didl/evaluator.didl): Served is the Evaluator asked by ids and plain
records, Service serves it, Remote asks it, and Local is Served asked as
Remote is, in-process, for tests.

A cell's last expression is its result, numbered: ``$3`` is cell 3's.
Completion is jedi's, against the live namespace, as is help: a name's
signatures and docstring.

What's in the namespace can be browsed, as targets: a name, where it's
bound, in the session's namespace, a module, a class, or an object.
Each has a kind, and a source, which can be edited and saved, as a
numbered cell, changing what exists in place, as far as it can: a
function's code, a class's members and bases.  Only what was defined in
a cell is editable: library code is shown, but not changed.  The source
of what a cell defines is recorded as it runs, by object, as IPython
does.

It uses no Qt."""

import ast
import builtins
import codeop
import importlib
import inspect
import io
import linecache
import logging
import os
import pathlib
import pprint
import queue
import re
import signal
import symtable
import textwrap
import threading
import time
import tokenize
import traceback
import types
import typing
from contextlib import redirect_stderr, redirect_stdout
import dataclasses
from dataclasses import dataclass

import jedi

import darq
import darq.dipc
import darq.loop
from darq.protocols import evaluator as wire


# Control exports
__all__ = ["Actions", "Binding", "CLASS", "Evaluator", "FUNCTION", "Help", "MODULE", "Member", "PRELUDE",
           "Local", "PROPERTY", "Ref", "Remote", "Result", "Results", "SOURCE", "Served", "Service", "Source", "Target",
           "VARIABLE", "Written", "kind_of", "needs", "rewrite", "shown", "start_evaluator"]

# What the REPL is, as the source of what it makes, in History.
SOURCE = "repl"

# What's run in a new namespace: the darq API, and the Terminal's, as
# the REPL.
PRELUDE = f"""\
import darq
import darq.terminal as terminal
terminal.source = {SOURCE!r}
"""

# A cell's result, by number.
_DOLLAR_RE = re.compile(r"\$(\d+)")

# A saved session's next cell's number, in its header.
NEXT_CELL = re.compile(r"^# Its next cell is (\d+)\.$", re.MULTILINE)


class Results(dict[int, typing.Any]):
    """Cell results, by cell number: ``$n`` is ``_results[n]``."""

    def __missing__(self, key: int) -> typing.Any:
        raise NameError(f"${key} is not defined")


def rewrite(source: str) -> str:
    """Replace each ``$n`` with ``_results[n]``.

    Tokenizing leaves ``$n`` in strings and comments alone.  Source
    that won't tokenize (it's incomplete, or broken) falls back to a
    plain substitution."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return _DOLLAR_RE.sub(r"_results[\1]", source)

    # (row, start column, end column, replacement), rows 1-based.
    edits: list[tuple[int, int, int, str]] = []
    for tok, nxt in zip(tokens, tokens[1:]):
        # The number's digits only: ``$1.real`` tokenizes as ``$``,
        # ``1.``, ``real``.
        digits = re.match(r"\d+", nxt.string)
        if (tok.type == tokenize.OP and tok.string == "$"
                and nxt.type == tokenize.NUMBER and digits
                and nxt.start == tok.end):
            edits.append((tok.start[0], tok.start[1],
                          nxt.start[1] + digits.end(),
                          f"_results[{int(digits.group())}]"))
    if not edits:
        return source

    lines = source.splitlines(keepends=True)
    for row, start, end, text in reversed(edits):
        line = lines[row - 1]
        lines[row - 1] = line[:start] + text + line[end:]
    return "".join(lines)


@dataclass
class Result:
    """What a cell did: its output, its value, if it has one, or its
    error.  A save says what it edited, and anything to know of how."""
    number: int
    stdout: str = ""
    stderr: str = ""
    value: typing.Any = None
    has_value: bool = False
    error: str | None = None
    edited: str = ""
    note: str = ""

    # Its value, as the transcript shows it: pretty-printed, here, where
    # the value is.
    shown: str = ""


# What a name's bound to.
MODULE = "module"
CLASS = "class"
FUNCTION = "function"
PROPERTY = "property"
VARIABLE = "variable"


def unwrapped(value: typing.Any) -> typing.Any:
    """Return a static or class method's function, or anything else."""
    return value.__func__ if isinstance(value, (staticmethod, classmethod)) else value


def kind_of(value: typing.Any) -> str:
    """Return what kind of thing a value is, as it's bound: a static or
    class method's a function."""
    value = unwrapped(value)
    if inspect.ismodule(value):
        return MODULE
    if inspect.isclass(value):
        return CLASS
    if isinstance(value, property):
        return PROPERTY
    if inspect.isroutine(value):
        return FUNCTION
    return VARIABLE


@dataclass(frozen=True)
class Target:
    """A name, where it's bound: in a container, which is the session's
    namespace, the built-ins, a module, a class, an object, or the
    results; and its path, for showing, such as ("Window",
    "on_submitted").  A new member of a class has no name, till it's
    saved."""
    container: typing.Any
    name: str
    path: tuple[str, ...]

    @property
    def label(self) -> str:
        return ".".join(self.path)

    def key(self) -> tuple[int, str]:
        """Identifies it, as long as its container lives."""
        return id(self.container), self.name


@dataclass(frozen=True)
class Help:
    """What a name is: as its signatures say, and its docstring, without
    them.  For a value that's neither a function nor a class, it's its
    type's."""
    name: str
    signatures: tuple[str, ...]
    docstring: str


@dataclass
class Written:
    """A session's code, as a module; what's left out of it, by name, and
    why; the names written, in order; and the results, as $8."""
    text: str
    left_out: list[tuple[str, str]]
    names: list[str]
    results: list[str] = dataclasses.field(default_factory=list)


@dataclass
class Binding:
    """A name in a container, as the tree shows it."""
    target: Target
    kind: str
    type_name: str
    expandable: bool


@dataclass
class Source:
    """What a target's editor shows: its source, whether it can be saved,
    and why not, if it can't."""
    text: str
    editable: bool
    why: str = ""


# The REPL's own names, which aren't shown.
HIDDEN = {"__name__", "__builtins__", "_results", "_", "__doc__", "__loader__", "__spec__", "__package__"}

# What every class has, made by Python, not its source.
STANDARD_MEMBERS = {"__module__", "__dict__", "__weakref__", "__doc__", "__qualname__", "__firstlineno__",
                    "__static_attributes__", "__annotations__", "__annotate__", "__annotate_func__",
                    "__annotations_cache__", "__orig_bases__", "__type_params__"}

# Types whose values are shown as a variable, not expanded.
PLAIN = (int, float, complex, str, bytes, bool, type(None), list, tuple, dict, set, frozenset)


def statement_source(lines: list[str], node: ast.stmt) -> str:
    """Return a statement's source, from the lines of a cell, with its
    decorators, dedented."""
    first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    last = node.end_lineno or node.lineno
    return textwrap.dedent("".join(lines[first - 1:last])).rstrip() + "\n"


def class_header(lines: list[str], node: ast.ClassDef) -> str:
    """Return a class statement's header: its decorators, its class line,
    and its docstring, if it has one."""
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    body = node.body
    docstring = body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
        and isinstance(body[0].value.value, str)
    rest = body[1:] if docstring else body
    if not rest or all(isinstance(s, ast.Pass) for s in rest) and rest[0].lineno > node.lineno:
        # It's all header.
        kept = [line for line in lines[first - 1:node.end_lineno or node.lineno] if line.strip() != "pass"]
        return textwrap.dedent("".join(kept)).rstrip() + "\n"
    if rest[0].lineno > node.lineno:
        last = rest[0].lineno - 1
        while last > first and not lines[last - 1].strip():
            last -= 1
        return textwrap.dedent("".join(lines[first - 1:last])).rstrip() + "\n"
    # It's on one line, as class A: pass: made again.
    header = ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords, body=[ast.Pass()],
                          decorator_list=node.decorator_list, type_params=getattr(node, "type_params", []))
    text = ast.unparse(header).rsplit("\n", 1)[0] + "\n"
    if docstring:
        text += textwrap.indent(repr(ast.get_docstring(node, clean=False)), "    ") + "\n"
    return text


def is_literal(value: typing.Any) -> bool:
    """Return whether a value's repr is it, as a literal."""
    try:
        return ast.literal_eval(repr(value)) == value
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False


def _cell_frames(tb):
    """Skip the REPL's own frames, leaving the cell's."""
    while tb is not None and not tb.tb_frame.f_code.co_filename.startswith("<cell "):
        tb = tb.tb_next
    return tb


class Evaluator:
    """The interpreter: a namespace, and numbered cells run in it."""

    def __init__(self) -> None:
        self.out = Results()
        self.namespace: dict[str, typing.Any] = {
            "__name__": "__main__",
            "__builtins__": __builtins__,
            "_results": self.out,
        }
        self.count = 0

        # Each result's expression, by its number, as its cell's last
        # expression was, or as a session's code assigned it: a result's
        # saved as it, as an assignment is, if its value isn't a literal.
        self.result_sources: dict[int, str] = {}

        # The source of what cells defined: functions, properties and
        # classes, by object, kept alive with it, so ids aren't reused;
        # classes' headers, and assignments, by container and name.
        self.sources: dict[int, tuple[typing.Any, str]] = {}
        self.headers: dict[int, tuple[typing.Any, str]] = {}
        self.assignments: dict[tuple[int, str], str] = {}

        # The session's imports, the statements for each name they bound, as
        # "from os.path import join as j"; what the REPL provides, which
        # isn't the session's own, by name; and the cells run, in order.
        self.imports: dict[str, list[str]] = {}
        self.provided: dict[str, typing.Any] = {}
        self.history: list[str] = []

    def start(self, prelude: str = PRELUDE) -> str:
        """Run the prelude in the namespace: the darq API.  Returns what
        to say of it."""
        try:
            exec(prelude, self.namespace)
        except Exception:
            return "The darq API couldn't be imported:\n" + traceback.format_exc()
        self.provided.update((name, value) for name, value in self.namespace.items() if name not in HIDDEN)
        try:
            version = self.namespace["darq"].get_version().version
        except Exception as e:
            version = f"(its version can't be had: {e!r})"
        return f"DarqOS {version}: darq, and darq.terminal as terminal"

    def is_complete(self, source: str) -> bool:
        """True if ``source`` is a complete cell, or is broken.

        Broken source counts as complete, so running it shows the
        error."""
        try:
            code = codeop.compile_command(rewrite(source), "<input>", "exec")
        except (SyntaxError, ValueError, OverflowError):
            return True
        return code is not None

    def provide(self, name: str, value: typing.Any):
        """Give the session a name of the REPL's, such as its event loop,
        which isn't saved with it."""
        self.namespace[name] = value
        self.provided[name] = value

    def run(self, source: str, write: typing.Callable[[str, str], None] | None = None) -> Result:
        """Run a cell.  A final expression's value becomes its result.

        :param write: Told what it prints, as it prints it, with the
                      stream's name, "stdout" or "stderr": the result's
                      own stdout and stderr are then empty."""
        self.history.append(source)
        self.count += 1
        n = self.count
        filename = f"<cell {n}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        result = Result(n)
        stdout: io.TextIOBase
        stderr: io.TextIOBase
        if write is None:
            stdout, stderr = io.StringIO(), io.StringIO()
        else:
            stdout, stderr = _Stream("stdout", write), _Stream("stderr", write)
        statements: list[ast.stmt] = []
        rewritten = source
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                rewritten = rewrite(source)
                tree = ast.parse(rewritten, filename, "exec")
                statements = list(tree.body)
                last = None
                expression = None
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    expression = ast.get_source_segment(rewritten, tree.body[-1].value)
                    last = ast.Expression(tree.body[-1].value)
                    del tree.body[-1]
                exec(compile(tree, filename, "exec"), self.namespace)
                if last is not None:
                    value = eval(compile(last, filename, "eval"), self.namespace)
                    if value is not None:
                        result.value = value
                        result.has_value = True
                        result.shown = shown(value)
                        self.out[n] = value
                        self.namespace["_"] = value
                        if expression is not None:
                            self.result_sources[n] = expression
            except BaseException as e:
                result.error = "".join(traceback.format_exception(type(e), e, _cell_frames(e.__traceback__)))
            else:
                self.record(self.namespace, statements, rewritten, filename, assignments=True)
            if result.error is not None:
                # What it defined before it failed.
                self.record(self.namespace, statements, rewritten, filename, assignments=False)
        if write is None:
            result.stdout = typing.cast(io.StringIO, stdout).getvalue()
            result.stderr = typing.cast(io.StringIO, stderr).getvalue()
        return result

    # The sources of what's defined

    def record(self, container: typing.Any, statements: list[ast.stmt], text: str, filename: str,
               assignments: bool):
        """Record the sources of what a cell's statements defined, in a
        container: definitions whose code is the cell's, and, if it all
        ran, assignments to names."""
        lines = text.splitlines(keepends=True)
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                value = self.lookup(container, node.name)
                if isinstance(node, ast.ClassDef):
                    # A class has no file: it's the cell's if it's new,
                    # and the session's.
                    found = self.headers.get(id(value))
                    if not inspect.isclass(value) or value.__module__ != self.namespace["__name__"] \
                            or (found is not None and found[0] is value):
                        continue
                elif value is None or not from_file(value, filename):
                    continue
                self.remember(value, statement_source(lines, node))
                if isinstance(node, ast.ClassDef):
                    self.headers[id(value)] = (value, class_header(lines, node))
                    self.record(value, node.body, text, filename, assignments)
            elif assignments and isinstance(node, (ast.Import, ast.ImportFrom)) and container is self.namespace:
                for name, line in import_lines(node):
                    kept = self.imports.setdefault(name, [])
                    if line not in kept:
                        kept.append(line)
            elif assignments and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.assignments[(id(container), target.id)] = statement_source(lines, node)
                    elif (container is self.namespace and isinstance(target, ast.Subscript)
                          and isinstance(target.value, ast.Name) and target.value.id == "_results"
                          and isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, int)
                          and node.value is not None):
                        found = ast.get_source_segment(text, node.value)
                        if found is not None:
                            self.result_sources[target.slice.value] = found

    def remember(self, value: typing.Any, text: str):
        value = unwrapped(value)
        self.sources[id(value)] = (value, text)

    def recorded(self, value: typing.Any) -> str | None:
        found = self.sources.get(id(unwrapped(value)))
        return found[1] if found is not None and found[0] is unwrapped(value) else None

    def lookup(self, container: typing.Any, name: str) -> typing.Any:
        """Return what a name's bound to, in a container, without running
        any of its code, or None."""
        if isinstance(container, dict):
            return container.get(name)
        try:
            return inspect.getattr_static(container, name)
        except AttributeError:
            return None

    # Browsing

    def session(self) -> Target:
        return Target(self.namespace, "", ())

    def is_session(self, container: typing.Any) -> bool:
        return container is self.namespace

    def members(self, parent: Target, private: bool = False) -> list[Binding]:
        """Return the names in what a target's bound to, or, for the
        session, in its namespace: a module's, or a class's own, or an
        object's instance attributes.  Private ones, starting with _,
        only if asked."""
        if parent.name == "" and isinstance(parent.container, dict):
            found = parent.container
        elif parent.name == "":
            found = vars(parent.container)
        else:
            value = self.lookup(parent.container, parent.name)
            if isinstance(value, Results):
                found = {f"${n}": v for n, v in sorted(value.items())}
            elif inspect.ismodule(value) or inspect.isclass(value):
                found = vars(value)
            else:
                found = instance_attributes(value)
        result = []
        for name, value in found.items():
            if parent.container is self.namespace and parent.name == "" and name in HIDDEN:
                continue
            if name.startswith("_") and not private and not name.startswith("$"):
                continue
            target = Target(found if found is self.namespace or isinstance(parent.container, Results)
                            else self.container_of(parent), name, (*parent.path, name))
            result.append(Binding(target, kind_of(value), type(unwrapped(value)).__name__, expandable(value)))
        return sorted(result, key=lambda b: (b.target.name.startswith("$") and int(b.target.name[1:]) or 0,
                                             b.target.name.casefold()))

    def container_of(self, parent: Target) -> typing.Any:
        """Return what a target's members are bound in: what it's bound
        to."""
        if parent.name == "":
            return parent.container
        return self.lookup(parent.container, parent.name)

    def builtins(self) -> Target:
        return Target(builtins, "", ("builtins",))

    # Sources

    def source_of(self, target: Target) -> Source:
        """Return what a target's editor shows: its source, if it's
        defined in a cell, which can be edited; a library's, which can't;
        or, for a variable, what it is."""
        value = self.lookup(target.container, target.name)
        if isinstance(target.container, Results):
            return Source(f"{target.name} = {pprint.pformat(target.container[int(target.name[1:])], width=100)}\n",
                          False, "It's a result: assign it to a name to change it")
        kind = kind_of(value)
        if kind in (FUNCTION, PROPERTY):
            text = self.recorded(value)
            if text is not None:
                return Source(text, True)
            return library_source(value)
        if kind == CLASS:
            found = self.headers.get(id(value))
            if found is not None and found[0] is value:
                return Source(found[1], True)
            return library_source(value)
        if kind == MODULE:
            return Source(f"# module {value.__name__}\n# {getattr(value, '__file__', None) or 'built in'}\n",
                          False, "A module's names are edited one by one")
        if target.container is builtins:
            return Source(f"{target.name} = {value!r}\n", False, "It's a built-in")
        if is_literal(value):
            return Source(f"{target.name} = {pprint.pformat(value, width=100)}\n", True)
        assigned = self.assignments.get((id(target.container), target.name))
        if assigned is not None:
            return Source(assigned, True)
        return Source(f"# {value!r}\n{target.name} = \n", True)

    def whole_class(self, target: Target) -> str:
        """Return a class's source, as it is now: its header and members,
        as they were last run or saved, and, as comments, the members
        with none here; or, for a library's class, its source."""
        value = self.lookup(target.container, target.name)
        found = self.headers.get(id(value))
        if found is None or found[0] is not value:
            return library_source(value).text
        source, unknown = self.class_source(value, found[1])
        if unknown:
            source += "\n" + "".join(f"    # {name}: no source here (made by a decorator, or set elsewhere)\n"
                                     for name in unknown)
        return source

    def class_source(self, cls: typing.Any, header: str) -> tuple[str, list[str]]:
        """Return a class's source, from a header and its members' sources,
        in the order they're in the class; and the names of its members
        with no source here, but for those every class has."""
        members, unknown = [], []
        for name, value in vars(cls).items():
            text = self.recorded(value) if kind_of(value) in (FUNCTION, PROPERTY, CLASS) else None
            text = text or self.assignments.get((id(cls), name))
            if text is not None:
                members.append(text)
            elif name not in STANDARD_MEMBERS:
                unknown.append(name)
        body = "".join(textwrap.indent(m.rstrip() + "\n", "    ") for m in members)
        return header.rstrip() + "\n" + (body or "    pass\n"), unknown

    # Saving

    def save(self, target: Target, text: str) -> Result:
        """Save a target's edited source, as a numbered cell: a variable's
        rebound; a function's, or a method's, code is changed in place, so
        what holds it sees it, or, if its closure's changed, it's bound
        anew; a class's header changes its docstring and bases in place,
        or else rebuilds it.  A class's new member has no name: its
        source's says it."""
        return self.as_cell(target.label or "new member", text, lambda filename: self.apply(target, text, filename))

    def rebuild(self, target: Target) -> Result:
        """Make a class again, from its header and its members' sources, as
        after changing a decorated class's fields."""
        value = self.lookup(target.container, target.name)
        found = self.headers.get(id(value))
        header = found[1] if found is not None and found[0] is value else ""
        return self.as_cell(target.label, f"# rebuild {target.label}\n",
                            lambda filename: self.rebuild_class(target, value, header, filename))

    def delete(self, target: Target) -> Result:
        """Unbind a name: in the session, a class defined in a cell, or an
        object."""
        def unbind(filename: str) -> str:
            self.check_editable_container(target.container)
            if isinstance(target.container, dict):
                del target.container[target.name]
            else:
                delattr(target.container, target.name)
            self.assignments.pop((id(target.container), target.name), None)
            return ""

        return self.as_cell(target.label, f"del {target.label}\n", unbind)

    def as_cell(self, label: str, text: str, action: typing.Callable[[str], str]) -> Result:
        self.count += 1
        n = self.count
        filename = f"<cell {n}>"
        linecache.cache[filename] = (len(text), None, text.splitlines(keepends=True), filename)
        result = Result(n, edited=label)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                result.note = action(filename)
            except BaseException as e:
                result.error = "".join(traceback.format_exception(type(e), e, _cell_frames(e.__traceback__)))
        result.stdout = stdout.getvalue()
        result.stderr = stderr.getvalue()
        return result

    def check_editable_container(self, container: typing.Any):
        """Refuse to change the built-ins, the results, or a library's
        class."""
        if container is builtins or isinstance(container, Results):
            raise PermissionError("The built-ins, and the results, aren't changed here")
        if inspect.isclass(container) and id(container) not in self.headers:
            raise PermissionError("It's a library's class: only what's defined in a cell is edited")

    def apply(self, target: Target, text: str, filename: str) -> str:
        source = textwrap.dedent(text)
        container = target.container
        current = self.lookup(container, target.name) if target.name else None
        if kind_of(current) == CLASS and target.name:
            source = source.rstrip() + "\n"
        tree = ast.parse(source + ("    pass\n" if kind_of(current) == CLASS and target.name else ""),
                         filename, "exec")
        if len(tree.body) != 1:
            raise SyntaxError("Save one definition, or one assignment")
        node = tree.body[0]
        self.check_editable_container(container)
        kind = kind_of(current) if target.name else None
        if kind in (FUNCTION, PROPERTY) and self.recorded(current) is None or \
                kind == CLASS and id(current) not in self.headers:
            raise PermissionError("It's a library's: only what's defined in a cell is edited")
        if kind == CLASS:
            if not isinstance(node, ast.ClassDef):
                raise SyntaxError("A class's header is its decorators, its class line, and its docstring")
            return self.save_header(target, current, node, source, filename)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if target.name and node.name != target.name:
                raise NameError(f"It must define {target.name}")
            return self.save_definition(container, current, node, source, filename)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(names) != 1 or not isinstance(names[0], ast.Name) or node.value is None:
                raise SyntaxError("Assign one name a value")
            if target.name and names[0].id != target.name:
                raise NameError(f"It must assign {target.name}")
            return self.save_assignment(container, names[0].id, node.value, source, filename)
        if isinstance(node, ast.Expr) and target.name:
            return self.save_assignment(container, target.name, node.value, source, filename)
        raise SyntaxError("Save a definition, or an assignment")

    def bind(self, container: typing.Any, name: str, value: typing.Any):
        if isinstance(container, dict):
            container[name] = value
        else:
            setattr(container, name, value)

    def save_assignment(self, container: typing.Any, name: str, expression: ast.expr, source: str,
                        filename: str) -> str:
        value = eval(compile(ast.Expression(expression), filename, "eval"), self.namespace)
        self.bind(container, name, value)
        self.assignments[(id(container), name)] = source
        return ""

    def save_definition(self, container: typing.Any, current: typing.Any, node: ast.stmt, source: str,
                        filename: str) -> str:
        """Run a definition, and change what it defines in place, or bind
        it anew."""
        name = typing.cast(ast.FunctionDef | ast.ClassDef, node).name
        scratch: dict[str, typing.Any] = {}
        if inspect.isclass(container):
            # In a class body, as a stand-in's, so super() works, then
            # pointed at the class itself.
            wrapper = ast.parse("class __darq_edit__:\n" + textwrap.indent(source, "    "), filename, "exec")
            ast.increment_lineno(wrapper, -1)
            exec(compile(wrapper, filename, "exec"), self.namespace, scratch)
            new = vars(scratch["__darq_edit__"])[name]
            for function in functions_of(new):
                if "__class__" in function.__code__.co_freevars and function.__closure__ is not None:
                    function.__closure__[function.__code__.co_freevars.index("__class__")].cell_contents = container
        else:
            exec(compile(source, filename, "exec"), self.namespace, scratch)
            new = scratch[name]
        note = ""
        old, fresh = unwrapped(current), unwrapped(new)
        if (isinstance(old, types.FunctionType) and isinstance(fresh, types.FunctionType)
                and type(current) is type(new) and old.__code__.co_freevars == fresh.__code__.co_freevars):
            update_function(old, fresh)
        else:
            if isinstance(old, types.FunctionType):
                note = "It's bound anew, as its closure's changed: what holds the old one keeps it"
            self.bind(container, name, new)
        self.record(container, [node], source, filename, assignments=False)
        return note

    def save_header(self, target: Target, cls: typing.Any, node: ast.ClassDef, source: str, filename: str) -> str:
        """Change a class's docstring and bases in place, or, if its
        decorators or keywords changed, rebuild it."""
        rest = node.body[1:] if ast.get_docstring(node, clean=False) is not None else node.body
        if any(not (isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                                 and s.value.value is Ellipsis)) for s in rest):
            raise SyntaxError("A class's header is its decorators, its class line, and its docstring: "
                              "edit its members in the tree")
        if node.name != target.name:
            raise NameError(f"It must define {target.name}")
        old = ast.parse(self.headers[id(cls)][1] + "    pass\n").body[0]
        assert isinstance(old, ast.ClassDef)
        same = ([ast.unparse(d) for d in old.decorator_list] == [ast.unparse(d) for d in node.decorator_list]
                and [ast.unparse(k) for k in old.keywords] == [ast.unparse(k) for k in node.keywords])
        if not same:
            return self.rebuild_class(target, cls, source, filename)
        bases = tuple(eval(compile(ast.Expression(b), filename, "eval"), self.namespace) for b in node.bases)
        bases = bases or (object,)
        if bases != cls.__bases__:
            try:
                cls.__bases__ = bases
            except TypeError as e:
                raise TypeError(f"{target.label}'s bases can't be changed in place ({e}): Rebuild it") from None
        cls.__doc__ = ast.get_docstring(node, clean=False)
        self.headers[id(cls)] = (cls, class_header(source.splitlines(keepends=True), node))
        return ""

    def rebuild_class(self, target: Target, cls: typing.Any, header: str, filename: str) -> str:
        """Make a class again, from a header and its members' sources, and
        bind it in place of the old: what's set on it otherwise is kept."""
        if not inspect.isclass(cls) or not header:
            raise PermissionError("Only a class defined in a cell can be rebuilt")
        source, _ = self.class_source(cls, header)
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        tree = ast.parse(source, filename, "exec")
        scratch: dict[str, typing.Any] = {}
        exec(compile(tree, filename, "exec"), self.namespace, scratch)
        new = scratch[target.name]
        for name, value in vars(cls).items():
            if not name.startswith("__") and name not in vars(new):
                setattr(new, name, value)
        for name in list(self.assignments):
            if name[0] == id(cls):
                self.assignments[(id(new), name[1])] = self.assignments[name]
        self.bind(target.container, target.name, new)
        self.record(target.container, tree.body, source, filename, assignments=False)
        return "Rebuilt: what was made of the old class keeps it"

    # Saving and loading a session

    def write_session(self) -> "Written":
        """Return the session's code, as a module: what each of its names
        is bound to, as it can be written, in an order it can be run in,
        imports first; and what's left out, and why: what can't be
        written, and what needs it to be defined.  What the REPL gives
        the session isn't written, unless it's been bound otherwise."""
        names = [n for n in self.namespace if n not in HIDDEN and not (n.startswith("__") and n.endswith("__"))]
        given = [n for n in names if n in self.provided and self.provided[n] is self.namespace[n]]
        names = [n for n in names if n not in given]

        # What's imported of what the REPL gives, as "import darq.kb", is
        # written, though what it binds, darq, isn't.
        imported = [line for n in given for line in self.imports.get(n, [])]
        return self.write(names, f"# A DarqOS REPL session.\n# Its next cell is {self.count + 1}.\n\n", imported)

    def extract(self, root: str) -> "Written":
        """Return a module of a name, a function or class, usually, and of
        everything of the session's it needs, whether as it's defined, or
        once it's called, imports included: or, as what's left out, the
        problems, which stop it being extracted, as what can't be written,
        or isn't defined."""
        problems: dict[str, str] = {}
        closure: list[str] = []
        queue = [root]
        while queue:
            name = queue.pop(0)
            if name in closure or name in problems:
                continue
            if name == "_results":
                problems["$n"] = "a result's used: assign it to a name"
                continue
            if name not in self.namespace:
                if not hasattr(builtins, name):
                    problems[name] = "it's used, but isn't defined"
                continue
            if name in self.provided and self.provided[name] is self.namespace[name] \
                    and not inspect.ismodule(self.namespace[name]):
                problems[name] = "it's the REPL's: it isn't the session's to extract"
                continue
            text, why = self.statement_for(name, self.namespace[name])
            if text is None:
                problems[name] = why
                continue
            closure.append(name)
            try:
                now, later = needs(text)
            except SyntaxError:
                now, later = set(), set()
            queue.extend(sorted((now | later) - {name}))
        if problems:
            return Written("", sorted(problems.items()), [])
        return self.write(closure, f"# {root}, extracted from a DarqOS REPL session.\n\n")

    def write(self, names: list[str], head: str, imported: list[str] | None = None) -> "Written":
        """Return a module of some names, as statement_for writes them, in
        an order they can be run in, and what's left out of it; and, first,
        some imports of names that aren't written."""
        texts: dict[str, str] = {}
        left_out: dict[str, str] = {}
        for name in names:
            text, why = self.statement_for(name, self.namespace[name])
            if text is None:
                left_out[name] = why
            else:
                texts[name] = text

        # The results it uses, as $8, written as names are, and the
        # results they use: only those needed.
        wanted = [r for text in texts.values() for r in results_used(text)]
        while wanted:
            key = wanted.pop(0)
            if key in texts or key in left_out:
                continue
            text, why = self.result_statement(int(key[1:]))
            if text is None:
                left_out[key] = why
            else:
                texts[key] = text
                wanted.extend(results_used(text))

        # What each needs as it's run, of what's written.
        now: dict[str, set[str]] = {}
        for name, text in texts.items():
            try:
                needed, _ = needs(text)
            except SyntaxError:
                needed = set()
            now[name] = (needed - {name, "_results"}) | set(results_used(text)) - {name}
        while True:
            dropped = {n: f"it needs {', '.join(sorted(now[n] & set(left_out)))}, which is left out"
                       for n in texts if now[n] & set(left_out)}
            if not dropped:
                break
            left_out.update(dropped)
            for n in dropped:
                del texts[n]

        # Imports first, then each once what it needs is: in the order
        # they were bound, otherwise.
        order = sorted((n for n in texts if n.startswith("$")), key=lambda r: int(r[1:]))
        order += [n for n in self.namespace if n in texts]
        position = {n: i for i, n in enumerate(order)}
        imports = [n for n in order if texts[n].startswith(("import ", "from "))]
        ordered: list[str] = list(imports)
        done = set(ordered)
        waiting = [n for n in order if n not in done]
        while waiting:
            ready = next((n for n in waiting if not (now[n] & set(texts)) - done), waiting[0])
            ordered.append(ready)
            done.add(ready)
            waiting.remove(ready)
        assert len(ordered) == len(position)
        seen: set[str] = set()
        statements = [line + "\n" for line in dict.fromkeys(imported or [])]
        seen.update(imported or [])
        for name in ordered:
            if texts[name] not in seen:
                seen.add(texts[name])
                statements.append(texts[name].rstrip() + "\n")
        body = "".join(s for s in statements if s.startswith(("import ", "from ")))
        others = "\n\n".join(s for s in statements if not s.startswith(("import ", "from ")))
        return Written(head + body + ("\n\n" if body and others else "") + others, sorted(left_out.items()),
                       [n for n in ordered if not n.startswith("$")],
                       sorted((n for n in ordered if n.startswith("$")), key=lambda r: int(r[1:])))

    def statement_for(self, name: str, value: typing.Any) -> tuple[str | None, str]:
        """Return a statement that binds a name to its value, or why
        there's none."""
        defined = self.definition(value)
        if defined is not None:
            own = getattr(unwrapped(value), "__name__", name)
            if own == name:
                return defined, ""
            if self.namespace.get(own) is value:
                return f"{name} = {own}", ""
            return None, f"it's {own}, which isn't bound by that name"
        if name in self.imports:
            return "\n".join(self.imports[name]), ""
        if inspect.ismodule(value):
            module = value.__name__
            return (f"import {module}" if module == name else f"import {module} as {name}"), ""
        if is_literal(value):
            return f"{name} = {pprint.pformat(value, width=100)}", ""
        assigned = self.assignments.get((id(self.namespace), name))
        if assigned is not None:
            return assigned, ""
        imported = library_import(value)
        if imported is not None:
            qualified = getattr(value, "__qualname__", "")
            return imported + (f" as {name}" if qualified != name else ""), ""
        return None, f"its value, a {type(value).__name__}, can't be written"

    def result_statement(self, n: int) -> tuple[str | None, str]:
        """Return a statement that binds a result to its value, as $8 is
        _results[8], or why there's none: by its value, if it's a
        literal, or else its expression, as a name's assignment is."""
        if n not in self.out:
            return None, "there's no such result"
        value = self.out[n]
        if is_literal(value):
            return f"_results[{n}] = {pprint.pformat(value, width=100)}", ""
        found = self.result_sources.get(n)
        if found is not None:
            return f"_results[{n}] = {found}", ""
        return None, f"its value, a {type(value).__name__}, can't be written"

    def definition(self, value: typing.Any) -> str | None:
        """Return the source of a function or class defined in a cell, as
        it is now, or None."""
        kind = kind_of(value)
        if kind == CLASS:
            found = self.headers.get(id(value))
            if found is not None and found[0] is value:
                return self.class_source(value, found[1])[0]
            return None
        if kind in (FUNCTION, PROPERTY):
            return self.recorded(value)
        return None

    def load_session(self, text: str) -> Result:
        """Run a session's code into the namespace, a statement at a time,
        so one that fails doesn't stop the rest, recording what it
        defines, as a cell's.  Its failures are its error."""
        self.count += 1
        n = self.count
        filename = f"<cell {n}>"
        linecache.cache[filename] = (len(text), None, text.splitlines(keepends=True), filename)
        result = Result(n, edited="session")
        stdout, stderr = io.StringIO(), io.StringIO()
        failures = []
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                tree = ast.parse(text, filename, "exec")
            except SyntaxError as e:
                result.error = "".join(traceback.format_exception_only(type(e), e))
                return result
            done = []
            for node in tree.body:
                try:
                    exec(compile(ast.Module([node], []), filename, "exec"), self.namespace)
                except BaseException as e:
                    failures.append(f"line {node.lineno}: {type(e).__name__}: {e}")
                else:
                    done.append(node)
            self.record(self.namespace, done, text, filename, assignments=True)
        result.stdout = stdout.getvalue()
        result.stderr = stderr.getvalue()
        result.note = f"Loaded {len(done)} of {len(tree.body)} statements"

        # Cells are numbered on from the session's, so a result's number,
        # as $8, isn't used again.
        found = NEXT_CELL.search(text)
        if found is not None:
            self.count = max(self.count, int(found.group(1)) - 1)
        if failures:
            result.error = "\n".join(failures) + "\n"
        return result

    def _jedi(self, before: str) -> tuple[jedi.Interpreter, int, int]:
        """jedi, on ``before``, against the namespace, with ``$n`` as a
        name bound to its result; and the line and column of its end."""
        extra: dict[str, typing.Any] = {}

        def sub(m: re.Match[str]) -> str:
            n = int(m.group(1))
            if n not in self.out:
                return m.group(0)
            extra[f"_dollar_{n}"] = self.out[n]
            return f"_dollar_{n}"

        code = _DOLLAR_RE.sub(sub, before)
        lines = code.split("\n")
        return jedi.Interpreter(code, [self.namespace, extra]), len(lines), len(lines[-1])

    def complete(self, before: str) -> tuple[list[str], int]:
        """Complete at the end of ``before``, the text before the cursor.

        Returns the candidate names, and the length of the partial name
        they replace."""
        try:
            interpreter, line, column = self._jedi(before)
            completions = interpreter.complete(line, column)
        except Exception:
            return [], 0
        if not completions:
            return [], 0
        c = completions[0]
        prefix = len(c.name) - len(c.complete or "")
        return [c.name for c in completions], prefix

    def help(self, before: str) -> Help | None:
        """Return the help for the end of ``before``, the text before the
        cursor: the name it ends with, if it ends with one, or else the
        call it's in the arguments of; or None, if there's none."""
        if not before.strip():
            return None
        try:
            interpreter, line, column = self._jedi(before)
            if before[-1].isidentifier() or before[-1].isdigit():
                for name in interpreter.help(line, column):
                    if name.type == "keyword":
                        continue
                    signatures = tuple(s.to_string() for s in name.get_signatures())
                    shown = re.sub(r"^_dollar_(\d+)$", r"$\1", name.name)
                    return Help(shown, signatures, name.docstring(raw=True))
                return None
            signatures = interpreter.get_signatures(line, column)
        except Exception:
            return None
        if not signatures:
            return None
        return Help(signatures[0].name, tuple(dict.fromkeys(s.to_string() for s in signatures)),
                    signatures[0].docstring(raw=True))


def results_used(source: str) -> list[str]:
    """The results source uses, as $8: written so, or, as recorded,
    rewritten to _results[8]; but in strings and comments."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(rewrite(source)).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []
    used = set()
    for i, token in enumerate(tokens[:-2]):
        if (token.type == tokenize.NAME and token.string == "_results" and tokens[i + 1].string == "["
                and tokens[i + 2].type == tokenize.NUMBER):
            used.add(int(tokens[i + 2].string))
    return [f"${n}" for n in sorted(used)]


class _Stream(io.TextIOBase):
    """A cell's stdout, or stderr, told to a function as it's written."""

    def __init__(self, name: str, write: typing.Callable[[str, str], None]):
        self._name = name
        self._write = write

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if text:
            self._write(self._name, text)
        return len(text)


def shown(value: typing.Any) -> str:
    """A value as the transcript shows it."""
    try:
        return pprint.pformat(value, width=100)
    except Exception as e:
        return f"<repr failed: {e!r}>"


################################################################
# Served: an Evaluator, as it's asked over its protocol

@dataclass(frozen=True)
class Ref:
    """A target, as it's named outside the evaluator: by an id, which
    Served keeps it by.  Its container's only identified, by its id
    there, so that drafts can be kept by key(), as targets are."""
    id: int
    container: int
    name: str
    path: tuple[str, ...]

    @property
    def label(self) -> str:
        return ".".join(self.path)

    def key(self) -> tuple[int, str]:
        return self.container, self.name


@dataclass(frozen=True)
class Member:
    """A binding, as the tree shows it, by Ref."""
    ref: Ref
    kind: str
    type_name: str
    expandable: bool


@dataclass(frozen=True)
class Actions:
    """What can be done to a target, from the tree: a class defined in a
    cell can have a member added, and be rebuilt; any class can be read
    whole; a session's function, or class, can be extracted; and what's
    the session's, or a cell's class's, or an object's, deleted."""
    add_member: bool = False
    rebuild: bool = False
    whole_class: bool = False
    extract: bool = False
    delete: bool = False


class Served:
    """An Evaluator, as its protocol asks of it (lib/didl/evaluator.didl):
    by Refs, and plain records, not live objects.  The REPL's evaluator
    process serves it; tests use it directly.

    It keeps each Target it's given out a Ref for till tops() is asked
    again, but for those it's told to keep, such as drafts'."""

    def __init__(self, evaluator: "Evaluator | None" = None):
        self.evaluator = evaluator if evaluator is not None else Evaluator()
        self.targets: dict[int, Target] = {}
        self.count = 0

    def ref(self, target: Target) -> Ref:
        for n, known in self.targets.items():
            if known.key() == target.key() and known.path == target.path:
                return Ref(n, id(target.container), target.name, target.path)
        self.count += 1
        self.targets[self.count] = target
        return Ref(self.count, id(target.container), target.name, target.path)

    def target(self, ref: "Ref | int") -> Target:
        """The target a Ref names.

        :raises KeyError: if it's not known, or no longer."""
        return self.targets[ref if isinstance(ref, int) else ref.id]

    # Running

    def start(self, prelude: str = PRELUDE) -> str:
        return self.evaluator.start(prelude)

    def run(self, source: str, write: typing.Callable[[str, str], None] | None = None) -> Result:
        result = self.evaluator.run(source, write)
        result.value = None
        return result

    def is_complete(self, source: str) -> bool:
        return self.evaluator.is_complete(source)

    def complete(self, before: str) -> tuple[list[str], int]:
        return self.evaluator.complete(before)

    def help(self, before: str) -> "Help | None":
        return self.evaluator.help(before)

    @property
    def history(self) -> list[str]:
        return self.evaluator.history

    @history.setter
    def history(self, cells: list[str]):
        self.evaluator.history = list(cells)

    # Browsing

    def tops(self, keep: typing.Iterable[int] = ()) -> list[tuple[str, Ref]]:
        """The tree's top level: the session, the results, and the
        built-ins.  Refs given out before are forgotten, but those to
        keep."""
        kept = set(keep)
        self.targets = {n: t for n, t in self.targets.items() if n in kept}
        e = self.evaluator
        return [("Session", self.ref(e.session())),
                ("Results", self.ref(Target(e.namespace, "_results", ("Results",)))),
                ("Built-ins", self.ref(e.builtins()))]

    def members(self, ref: "Ref | int", private: bool = False) -> list[Member]:
        return [Member(self.ref(b.target), b.kind, b.type_name, b.expandable)
                for b in self.evaluator.members(self.target(ref), private)]

    def source_of(self, ref: "Ref | int") -> "Source":
        return self.evaluator.source_of(self.target(ref))

    def whole_class(self, ref: "Ref | int") -> str:
        return self.evaluator.whole_class(self.target(ref))

    def actions(self, ref: "Ref | int") -> Actions:
        target = self.target(ref)
        e = self.evaluator
        value = e.lookup(target.container, target.name)
        is_class = kind_of(value) == CLASS and bool(target.name)
        cells = is_class and id(value) in e.headers
        container = target.container
        deletable = (e.is_session(container) or id(container) in e.headers
                     or not (inspect.isclass(container) or inspect.ismodule(container)
                             or isinstance(container, (dict, Results))))
        return Actions(add_member=cells, rebuild=cells, whole_class=is_class,
                       extract=e.is_session(container) and kind_of(value) in (FUNCTION, CLASS) and bool(target.name),
                       delete=bool(target.name) and deletable and not target.name.startswith("$"))

    def new_member(self, ref: "Ref | int") -> Ref:
        """A new member of a cell's class: a target with no name, till
        it's saved."""
        target = self.target(ref)
        value = self.evaluator.lookup(target.container, target.name)
        return self.ref(Target(value, "", (*target.path, "")))

    # Editing

    def save(self, ref: "Ref | int", text: str) -> Result:
        return self.evaluator.save(self.target(ref), text)

    def rebuild(self, ref: "Ref | int") -> Result:
        return self.evaluator.rebuild(self.target(ref))

    def delete(self, ref: "Ref | int") -> Result:
        return self.evaluator.delete(self.target(ref))

    # Sessions

    def write_session(self) -> "Written":
        return self.evaluator.write_session()

    def load_session(self, text: str) -> Result:
        return self.evaluator.load_session(text)

    def extract(self, root: str) -> "Written":
        return self.evaluator.extract(root)


################################################################
# The evaluator process: Served, over its protocol

# Where the REPL's evaluator is, as a program; and how long to wait for it
# to start, and to answer what doesn't run code.
EVALUATOR = str(pathlib.Path(__file__).resolve().parent.parent / "lenses" / "repl" / "evaluator")
START_TIMEOUT = 30.0
TIMEOUT = 10.0

# How long what a cell prints is held, to be sent with what follows it.
OUTPUT_DELAY = 0.05


class BusyError(darq.errors.DarqError):
    """A cell's running: what runs code waits for it."""


def to_wire_ref(ref: Ref) -> wire.Ref:
    return wire.Ref(id=ref.id, container=ref.container, name=ref.name, path=list(ref.path))


def from_wire_ref(ref: wire.Ref | None) -> Ref:
    assert ref is not None
    return Ref(ref.id, ref.container, ref.name, tuple(ref.path))


def to_wire_result(result: Result) -> wire.Result:
    return wire.Result(number=result.number, stdout=result.stdout, stderr=result.stderr, has_value=result.has_value,
                       shown=result.shown, failed=result.error is not None, error=result.error or "",
                       edited=result.edited, note=result.note)


def from_wire_result(result: wire.Result | None) -> Result:
    assert result is not None
    return Result(result.number, stdout=result.stdout, stderr=result.stderr, has_value=result.has_value,
                  error=result.error if result.failed else None, edited=result.edited, note=result.note,
                  shown=result.shown)


def to_wire_written(written: "Written") -> wire.Written:
    return wire.Written(text=written.text, left_out=[wire.LeftOut(name=n, why=w) for n, w in written.left_out],
                        names=list(written.names), results=list(written.results))


def from_wire_written(written: wire.Written | None) -> "Written":
    assert written is not None
    return Written(written.text, [(o.name, o.why) for o in written.left_out], list(written.names),
                   list(written.results))


class _Output:
    """What a cell prints, sent to its listener: held for OUTPUT_DELAY,
    so that what's printed together is sent together."""

    def __init__(self, listener: wire.Listener.Client):
        self.listener = listener
        self.lock = threading.Lock()
        self.held: list[tuple[str, str]] = []
        self.timer: threading.Timer | None = None

    def write(self, stream: str, text: str):
        with self.lock:
            if self.held and self.held[-1][0] == stream:
                self.held[-1] = (stream, self.held[-1][1] + text)
            else:
                self.held.append((stream, text))
            if self.timer is None:
                self.timer = threading.Timer(OUTPUT_DELAY, self.flush)
                self.timer.daemon = True
                self.timer.start()

    def flush(self):
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            held, self.held = self.held, []
            for stream, text in held:
                try:
                    self.listener.output(stream=stream, text=text)
                except darq.errors.DarqError as e:
                    logging.info(f"Can't send a cell's output: {e}")
                    return


class Service(wire.Evaluator.Handler):
    """Serves Served, as the REPL's evaluator: what runs code, on the
    thread that calls serve(), the program's main thread, one at a time;
    and anything else at once, on the protocol's thread.

    A port it's told is dead, the lens's, ends it."""

    def __init__(self, served: Served):
        super().__init__()
        self.served = served
        self.jobs: queue.Queue[typing.Callable[[], None] | None] = queue.Queue()
        self.lock = threading.Lock()
        self.pending = 0
        self.main: int | None = None

    # The main thread

    def serve(self):
        """Run what's asked, till it's ended.  Called on the main thread:
        an interrupt is a SIGINT to it."""
        self.main = threading.get_ident()
        signal.signal(signal.SIGINT, signal.default_int_handler)
        while True:
            try:
                job = self.jobs.get()
            except KeyboardInterrupt:
                logging.info("Interrupted between cells")
                continue
            if job is None:
                return
            try:
                job()
            except KeyboardInterrupt:
                logging.info("Interrupted between cells")
            except Exception:
                logging.exception("A job failed")
            finally:
                with self.lock:
                    self.pending -= 1

    def queue(self, job: typing.Callable[[], None]):
        with self.lock:
            self.pending += 1
        self.jobs.put(job)

    def busy(self) -> bool:
        with self.lock:
            return self.pending > 0

    def end(self):
        """Stop, even if a cell's running."""
        self.jobs.put(None)
        self.interrupt_main()

        def last():
            time.sleep(2.0)
            logging.info("The evaluator didn't stop: exiting")
            os._exit(0)

        threading.Thread(target=last, daemon=True).start()

    def interrupt_main(self) -> bool:
        if self.main is None or not self.busy():
            return False
        signal.pthread_kill(self.main, signal.SIGINT)
        return True

    def port_destroyed(self, handle: int):
        logging.info("The REPL's gone: the evaluator's ending")
        self.end()

    # What runs code

    def start(self, request: wire.StartRequest, source: int) -> wire.StartResponse | darq.Deferred:
        deferred = self.defer()
        self.queue(lambda: deferred.reply(wire.StartResponse(said=self.served.start())))
        return deferred

    def run(self, request: wire.RunRequest, source: int) -> wire.RunResponse:
        if request.listener is None:
            return wire.RunResponse(status=wire.Status.FAILED)
        listener = request.listener
        text = request.source

        def job():
            client = wire.Listener.Client(listener)
            output = _Output(client)
            try:
                client.started(number=self.served.evaluator.count + 1)
            except darq.errors.DarqError as e:
                logging.info(f"Can't say a cell's started: {e}")
            try:
                result = self.served.run(text, output.write)
            finally:
                output.flush()
            for attempt in range(2):
                try:
                    client.finished(result=to_wire_result(result))
                    break
                except KeyboardInterrupt:
                    continue
                except darq.errors.DarqError as e:
                    logging.info(f"Can't say a cell's finished: {e}")
                    break
            # The listener's handle isn't closed: each run's is the same
            # handle, as the REPL listens on one port, so closing it after
            # one run could close it under the next.

        self.queue(job)
        return wire.RunResponse(status=wire.Status.OK)

    def interrupt(self, request: wire.InterruptRequest, source: int) -> wire.InterruptResponse:
        return wire.InterruptResponse(running=self.interrupt_main())

    def changing(self, deferred: darq.Deferred, response: type, change: typing.Callable[[], Result]):
        """Run a change to a target on the main thread, and reply with
        its result."""

        def job():
            try:
                result = change()
            except KeyError:
                deferred.reply(response(status=wire.Status.UNKNOWN))
                return
            except Exception as e:
                deferred.reply(response(status=wire.Status.FAILED, message=str(e)))
                return
            deferred.reply(response(result=to_wire_result(result), status=wire.Status.OK))

        self.queue(job)
        return deferred

    def save(self, request: wire.SaveRequest, source: int) -> wire.SaveResponse | darq.Deferred:
        if self.busy():
            return wire.SaveResponse(status=wire.Status.BUSY)
        return self.changing(self.defer(), wire.SaveResponse, lambda: self.served.save(request.ref, request.text))

    def rebuild(self, request: wire.RebuildRequest, source: int) -> wire.RebuildResponse | darq.Deferred:
        if self.busy():
            return wire.RebuildResponse(status=wire.Status.BUSY)
        return self.changing(self.defer(), wire.RebuildResponse, lambda: self.served.rebuild(request.ref))

    def delete(self, request: wire.DeleteRequest, source: int) -> wire.DeleteResponse | darq.Deferred:
        if self.busy():
            return wire.DeleteResponse(status=wire.Status.BUSY)
        return self.changing(self.defer(), wire.DeleteResponse, lambda: self.served.delete(request.ref))

    def load_session(self, request: wire.LoadSessionRequest, source: int) -> wire.LoadSessionResponse | darq.Deferred:
        if self.busy():
            return wire.LoadSessionResponse(status=wire.Status.BUSY)
        deferred = self.defer()
        text = request.text
        self.queue(lambda: deferred.reply(wire.LoadSessionResponse(
            result=to_wire_result(self.served.load_session(text)), status=wire.Status.OK)))
        return deferred

    # What's answered at once

    def is_complete(self, request: wire.IsCompleteRequest, source: int) -> wire.IsCompleteResponse:
        return wire.IsCompleteResponse(complete=self.served.is_complete(request.source))

    def complete(self, request: wire.CompleteRequest, source: int) -> wire.CompleteResponse:
        names, prefix = self.served.complete(request.before)
        return wire.CompleteResponse(names=names, prefix=prefix)

    def help(self, request: wire.HelpRequest, source: int) -> wire.HelpResponse:
        found = self.served.help(request.before)
        if found is None:
            return wire.HelpResponse(found=False, help=wire.Help())
        return wire.HelpResponse(found=True, help=wire.Help(name=found.name, signatures=list(found.signatures),
                                                            docstring=found.docstring))

    def tops(self, request: wire.TopsRequest, source: int) -> wire.TopsResponse:
        return wire.TopsResponse(tops=[wire.Top(label=label, ref=to_wire_ref(ref))
                                       for label, ref in self.served.tops(request.keep)])

    def members(self, request: wire.MembersRequest, source: int) -> wire.MembersResponse:
        try:
            members = self.served.members(request.ref, request.private)
        except KeyError:
            return wire.MembersResponse(status=wire.Status.UNKNOWN)
        return wire.MembersResponse(members=[wire.Member(ref=to_wire_ref(m.ref), kind=m.kind, type_name=m.type_name,
                                                         expandable=m.expandable) for m in members],
                                    status=wire.Status.OK)

    def source_of(self, request: wire.SourceOfRequest, source: int) -> wire.SourceOfResponse:
        try:
            found = self.served.source_of(request.ref)
        except KeyError:
            return wire.SourceOfResponse(status=wire.Status.UNKNOWN)
        return wire.SourceOfResponse(source=wire.Source(text=found.text, editable=found.editable, why=found.why),
                                     status=wire.Status.OK)

    def whole_class(self, request: wire.WholeClassRequest, source: int) -> wire.WholeClassResponse:
        try:
            return wire.WholeClassResponse(text=self.served.whole_class(request.ref),
                                           status=wire.Status.OK)
        except KeyError:
            return wire.WholeClassResponse(status=wire.Status.UNKNOWN)

    def actions(self, request: wire.ActionsRequest, source: int) -> wire.ActionsResponse:
        try:
            found = self.served.actions(request.ref)
        except KeyError:
            return wire.ActionsResponse(status=wire.Status.UNKNOWN)
        return wire.ActionsResponse(actions=wire.Actions(add_member=found.add_member, rebuild=found.rebuild,
                                                         whole_class=found.whole_class, extract=found.extract,
                                                         delete=found.delete), status=wire.Status.OK)

    def new_member(self, request: wire.NewMemberRequest, source: int) -> wire.NewMemberResponse:
        try:
            return wire.NewMemberResponse(ref=to_wire_ref(self.served.new_member(request.ref)),
                                          status=wire.Status.OK)
        except KeyError:
            return wire.NewMemberResponse(status=wire.Status.UNKNOWN)

    def write_session(self, request: wire.WriteSessionRequest, source: int) -> wire.WriteSessionResponse:
        return wire.WriteSessionResponse(written=to_wire_written(self.served.write_session()), status=wire.Status.OK)

    def extract(self, request: wire.ExtractRequest, source: int) -> wire.ExtractResponse:
        return wire.ExtractResponse(written=to_wire_written(self.served.extract(request.root)), status=wire.Status.OK)

    def history(self, request: wire.HistoryRequest, source: int) -> wire.HistoryResponse:
        return wire.HistoryResponse(cells=list(self.served.history))

    def set_history(self, request: wire.SetHistoryRequest, source: int) -> wire.SetHistoryResponse:
        self.served.history = list(request.cells)
        return wire.SetHistoryResponse(status=wire.Status.OK)


class _Listening(wire.Listener.Handler):
    """Hears a running cell, for Remote."""

    def __init__(self, remote: "Remote"):
        super().__init__()
        self.remote = remote

    def started(self, request: wire.Started, source: int) -> None:
        if self.remote.on_started is not None:
            self.remote.on_started(request.number)

    def output(self, request: wire.Output, source: int) -> None:
        if self.remote.on_output is not None:
            self.remote.on_output(request.stream, request.text)

    def finished(self, request: wire.Finished, source: int) -> None:
        done, self.remote.on_finished = self.remote.on_finished, None
        self.remote.on_started = self.remote.on_output = None
        if done is not None:
            done(from_wire_result(request.result))


class Local(Served):
    """Served, asked as a Remote is, in-process: run() tells what it's
    run as it runs, then returns.  Tests use it in the REPL's place."""

    def run_cell(self, source: str, on_started: typing.Callable[[int], None],
                 on_output: typing.Callable[[str, str], None], on_finished: typing.Callable[[Result], None]):
        on_started(self.evaluator.count + 1)
        on_finished(self.run(source, on_output))

    def interrupt(self) -> bool:
        return False

    def close(self):
        pass


def check(status: wire.Status, what: str, message: str = ""):
    """Raise for a status other than OK.

    :raises KeyError: for UNKNOWN, a forgotten ref.
    :raises BusyError: for BUSY.
    :raises darq.errors.DarqError: for FAILED."""
    if status == wire.Status.OK:
        return
    if status == wire.Status.UNKNOWN:
        raise KeyError(what)
    if status == wire.Status.BUSY:
        raise BusyError(f"{what}: a cell's running")
    raise darq.errors.DarqError(f"{what}: {message or status.name}")


class Remote:
    """An evaluator process, as Served is in-process: what the REPL's
    window asks of it.  But run() is asynchronous: what's printed, and the
    result, are told on the loop it's given."""

    def __init__(self, handle: int, loop: darq.loop.EventLoopInterface, dpid: int = 0, bootstrap: int | None = None):
        """Constructor.

        :param bootstrap: The port it was started with, which it watches:
                          it ends when that's closed, as by close()."""
        self.handle = handle
        self.dpid = dpid
        self.bootstrap = bootstrap
        self.asking = wire.Evaluator.Client(handle, timeout=TIMEOUT)
        self.running = wire.Evaluator.Client(handle, timeout=None)
        self.on_started: typing.Callable[[int], None] | None = None
        self.on_output: typing.Callable[[str, str], None] | None = None
        self.on_finished: typing.Callable[[Result], None] | None = None
        self.listener = darq.open_port()
        self.server = darq.Server()
        self.server.add(self.listener, _Listening(self))
        self.server.attach(loop)

    def close(self):
        """Stop hearing it, and end it."""
        for handle in (self.listener, self.handle, self.bootstrap):
            if handle is None:
                continue
            try:
                darq.close_port(handle)
            except darq.errors.DarqError:
                pass
        if self.dpid:
            try:
                darq.kill_process(self.dpid)
            except darq.errors.DarqError:
                pass

    def start(self) -> str:
        return self.running.start().said

    def run_cell(self, source: str, on_started: typing.Callable[[int], None],
                 on_output: typing.Callable[[str, str], None], on_finished: typing.Callable[[Result], None]):
        """Run a cell: it's told on the loop when it starts, with its
        number, what it prints, and its result."""
        self.on_started, self.on_output, self.on_finished = on_started, on_output, on_finished
        check(self.asking.run(source=source, listener=self.listener).status, "run")

    def interrupt(self) -> bool:
        return self.asking.interrupt().running

    def is_complete(self, source: str) -> bool:
        return self.asking.is_complete(source=source).complete

    def complete(self, before: str) -> tuple[list[str], int]:
        found = self.asking.complete(before=before)
        return list(found.names), found.prefix

    def help(self, before: str) -> Help | None:
        found = self.asking.help(before=before)
        if not found.found or found.help is None:
            return None
        return Help(found.help.name, tuple(found.help.signatures), found.help.docstring)

    @property
    def history(self) -> list[str]:
        return list(self.asking.history().cells)

    @history.setter
    def history(self, cells: list[str]):
        check(self.asking.set_history(cells=list(cells)).status, "set the history")

    def tops(self, keep: typing.Iterable[int] = ()) -> list[tuple[str, Ref]]:
        return [(top.label, from_wire_ref(top.ref)) for top in self.asking.tops(keep=list(keep)).tops]

    def members(self, ref: Ref, private: bool = False) -> list[Member]:
        found = self.asking.members(ref=ref.id, private=private)
        check(found.status, ref.label)
        return [Member(from_wire_ref(m.ref), m.kind, m.type_name, m.expandable) for m in found.members]

    def source_of(self, ref: Ref) -> Source:
        found = self.asking.source_of(ref=ref.id)
        check(found.status, ref.label)
        assert found.source is not None
        return Source(found.source.text, found.source.editable, found.source.why)

    def whole_class(self, ref: Ref) -> str:
        found = self.asking.whole_class(ref=ref.id)
        check(found.status, ref.label)
        return found.text

    def actions(self, ref: Ref) -> Actions:
        found = self.asking.actions(ref=ref.id)
        check(found.status, ref.label)
        a = found.actions
        assert a is not None
        return Actions(a.add_member, a.rebuild, a.whole_class, a.extract, a.delete)

    def new_member(self, ref: Ref) -> Ref:
        found = self.asking.new_member(ref=ref.id)
        check(found.status, ref.label)
        return from_wire_ref(found.ref)

    def save(self, ref: Ref, text: str) -> Result:
        found = self.running.save(ref=ref.id, text=text)
        check(found.status, f"save {ref.label}", found.message)
        return from_wire_result(found.result)

    def rebuild(self, ref: Ref) -> Result:
        found = self.running.rebuild(ref=ref.id)
        check(found.status, f"rebuild {ref.label}", found.message)
        return from_wire_result(found.result)

    def delete(self, ref: Ref) -> Result:
        found = self.running.delete(ref=ref.id)
        check(found.status, f"delete {ref.label}", found.message)
        return from_wire_result(found.result)

    def write_session(self) -> "Written":
        found = self.asking.write_session()
        check(found.status, "write the session")
        return from_wire_written(found.written)

    def load_session(self, text: str) -> Result:
        found = self.running.load_session(text=text)
        check(found.status, "load the session")
        return from_wire_result(found.result)

    def extract(self, root: str) -> "Written":
        found = self.asking.extract(root=root)
        check(found.status, f"extract {root}")
        return from_wire_written(found.written)


def start_evaluator(loop: darq.loop.EventLoopInterface, notify: int | None = None,
                    path: str = EVALUATOR, timeout: float = START_TIMEOUT) -> Remote:
    """Start an evaluator process, as this one's child, and wait for it to
    be Ready.

    :param loop: Where a running cell's output, and result, are told.
    :param notify: A port to be told, PortDestroyed, when it ends.
    :raises darq.errors.DarqError: if it can't be started, or isn't Ready
                                   in time."""
    bootstrap = darq.open_port()
    ready = None
    try:
        dpid = darq.spawn_process(path, bootstrap=bootstrap)
        deadline = time.monotonic() + timeout
        while True:
            message = darq.receive_message(bootstrap, timeout=max(0.0, deadline - time.monotonic()))
            if message is None:
                darq.kill_process(dpid)
                raise darq.errors.DarqError(f"The evaluator wasn't Ready within {timeout}s")
            source, payload, handles = message
            if darq.system.decode_port_destroyed(source, payload) is not None:
                continue
            ready = wire.Ready()
            try:
                if darq.dipc.decode_method(payload) != ready.METHOD:
                    continue
                ready.decode(payload, handles)
            except darq.errors.MessageDecodingError:
                continue
            if ready.evaluator is not None:
                break
    finally:
        # It watches the bootstrap port, and ends when it's closed: it's
        # kept open, by the Remote, till it's closed.
        if ready is None or ready.evaluator is None:
            darq.close_port(bootstrap)
    assert ready is not None and ready.evaluator is not None
    if notify is not None:
        darq.notify_on_death(ready.evaluator, notify)
    return Remote(ready.evaluator, loop, dpid, bootstrap)


def evaluator_main() -> int:
    """The evaluator process's darq_main (lenses/repl/evaluator): serve
    Served to the REPL that started it, and run what it asks on this, the
    main thread."""
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: the evaluator's started by the REPL")
        return 1
    service = Service(Served())
    port = darq.open_port()
    loop = darq.loop.SelectEventLoop()
    server = darq.Server()
    server.add(port, service)
    server.attach(loop)
    darq.notify_on_death(bootstrap, port)
    threading.Thread(target=loop.run, name="evaluator-protocol", daemon=True).start()
    wire.Starter.Client(bootstrap).ready(evaluator=port)
    service.serve()
    return 0


def from_file(value: typing.Any, filename: str) -> bool:
    """Return whether a function's, or a property's, code is from a
    cell."""
    value = unwrapped(value)
    if isinstance(value, property):
        value = value.fget
    code = getattr(value, "__code__", None)
    return code is not None and code.co_filename == filename


def library_source(value: typing.Any) -> Source:
    """Return a library's source of something, to read: or, if it has
    none, as for a built-in, its signature and documentation."""
    value = unwrapped(value)
    shown: typing.Any = value.fget if isinstance(value, property) and value.fget is not None else value
    try:
        return Source(inspect.getsource(shown), False, "It's a library's: only what's defined in a cell is edited")
    except (OSError, TypeError):
        pass
    try:
        signature = str(inspect.signature(shown))
    except (TypeError, ValueError):
        signature = ""
    doc = inspect.getdoc(shown) or ""
    name = getattr(shown, "__name__", type(shown).__name__)
    return Source(f"# {name}{signature}\n" + "".join(f"# {line}\n" for line in doc.splitlines()), False,
                  "It has no source here")


def instance_attributes(value: typing.Any) -> dict[str, typing.Any]:
    """Return an object's own attributes, without running its code."""
    try:
        found = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        return {}
    return found if isinstance(found, dict) else {}


def expandable(value: typing.Any) -> bool:
    """Return whether a value has members to show."""
    if isinstance(value, Results):
        return bool(value)
    value = unwrapped(value)
    if inspect.ismodule(value) or inspect.isclass(value):
        return True
    if isinstance(value, PLAIN) or inspect.isroutine(value) or isinstance(value, property):
        return False
    return bool(instance_attributes(value))


def functions_of(value: typing.Any) -> list[types.FunctionType]:
    """Return the functions in a definition: a function, a static or class
    method's, or a property's accessors."""
    if isinstance(value, property):
        candidates = [value.fget, value.fset, value.fdel]
    else:
        candidates = [unwrapped(value)]
    return [f for f in candidates if isinstance(f, types.FunctionType)]


def update_function(old: types.FunctionType, new: types.FunctionType):
    """Change a function in place, to another's code, defaults and
    documentation, so what holds it sees the change."""
    old.__code__ = new.__code__
    old.__defaults__ = new.__defaults__
    old.__kwdefaults__ = new.__kwdefaults__
    old.__doc__ = new.__doc__
    old.__annotations__ = new.__annotations__
    old.__dict__.update(new.__dict__)


def import_lines(node: ast.Import | ast.ImportFrom) -> list[tuple[str, str]]:
    """Return an import statement's names, and for each, an import of it
    alone: "import os.path" binds os, "from x import a as b" binds b."""
    lines = []
    for alias in node.names:
        if isinstance(node, ast.ImportFrom):
            if alias.name == "*":
                continue
            line = f"from {'.' * node.level}{node.module or ''} import {alias.name}"
            name = alias.asname or alias.name
        else:
            line = f"import {alias.name}"
            name = alias.asname or alias.name.partition(".")[0]
        if alias.asname:
            line += f" as {alias.asname}"
        lines.append((name, line))
    return lines


# What symtable calls a comprehension's scope: run where it's written.
COMPREHENSIONS = {"listcomp", "setcomp", "dictcomp", "genexpr", "<listcomp>", "<setcomp>", "<dictcomp>",
                  "<genexpr>"}


def needs(source: str) -> tuple[set[str], set[str]]:
    """Return the global names a statement uses: those it needs as it's
    run, as a class's body, a decorator, a default value, or an
    assignment's value; and those it needs only once what it defines is
    called, in a function's body.  Attributes aren't names: in
    terminal.windows(), it needs terminal."""
    now: set[str] = set()
    later: set[str] = set()

    def walk(table: symtable.SymbolTable, immediate: bool):
        defined = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()} \
            if table.get_type() == "module" else set()
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if not symbol.is_referenced() or name in defined:
                continue
            is_global = symbol.is_global() if table.get_type() != "module" else True
            if is_global and not (table.get_type() != "module" and symbol.is_assigned() and symbol.is_local()):
                (now if immediate else later).add(name)
        for child in table.get_children():
            kind = child.get_type()
            runs_now = immediate and (kind == "class" or child.get_name() in COMPREHENSIONS)
            walk(child, runs_now)

    walk(symtable.symtable(source, "<session>", "exec"), True)
    return now, later - now


def library_import(value: typing.Any) -> str | None:
    """Return an import of something from a library, by its module and
    name, if that imports it back, or None."""
    module = getattr(value, "__module__", None)
    qualified = getattr(value, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualified, str) or module == "__main__" \
            or "." in qualified or "<" in qualified:
        return None
    try:
        if getattr(importlib.import_module(module), qualified, None) is not value:
            return None
    except Exception:
        return None
    return f"from {module} import {qualified}"
