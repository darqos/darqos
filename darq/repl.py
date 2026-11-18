# darqos
# Copyright (C) 2026 David Arnold

"""The REPL's engine: Python, run a cell at a time, in one namespace, by
the REPL's window (tools/repl), as a DarqOS process, so its cells can use
the darq API.  See doc/repl.rst.

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
import pprint
import re
import symtable
import textwrap
import tokenize
import traceback
import types
import typing
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass

import jedi


# Control exports
__all__ = ["Binding", "CLASS", "Engine", "FUNCTION", "Help", "MODULE", "PRELUDE", "PROPERTY", "Result", "Results",
           "SOURCE", "Source", "Target", "VARIABLE", "Written", "kind_of", "needs", "rewrite"]

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


class Results(dict[int, typing.Any]):
    """Cell results, by cell number: ``$n`` is ``_out[n]``."""

    def __missing__(self, key: int) -> typing.Any:
        raise NameError(f"${key} is not defined")


def rewrite(source: str) -> str:
    """Replace each ``$n`` with ``_out[n]``.

    Tokenizing leaves ``$n`` in strings and comments alone.  Source
    that won't tokenize (it's incomplete, or broken) falls back to a
    plain substitution."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return _DOLLAR_RE.sub(r"_out[\1]", source)

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
                          f"_out[{int(digits.group())}]"))
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
    why; and the names written, in order."""
    text: str
    left_out: list[tuple[str, str]]
    names: list[str]


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
HIDDEN = {"__name__", "__builtins__", "_out", "_", "__doc__", "__loader__", "__spec__", "__package__"}

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


class Engine:
    """The interpreter: a namespace, and numbered cells run in it."""

    def __init__(self) -> None:
        self.out = Results()
        self.namespace: dict[str, typing.Any] = {
            "__name__": "__main__",
            "__builtins__": __builtins__,
            "_out": self.out,
        }
        self.count = 0

        # The source of what cells defined: functions, properties and
        # classes, by object, kept alive with it, so ids aren't reused;
        # classes' headers, and assignments, by container and name.
        self.sources: dict[int, tuple[typing.Any, str]] = {}
        self.headers: dict[int, tuple[typing.Any, str]] = {}
        self.assignments: dict[tuple[int, str], str] = {}

        # The session's imports, a statement for each name they bound, as
        # "from os.path import join as j"; what the REPL provides, which
        # isn't the session's own, by name; and the cells run, in order.
        self.imports: dict[str, str] = {}
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

    def run(self, source: str) -> Result:
        """Run a cell.  A final expression's value becomes its result."""
        self.history.append(source)
        self.count += 1
        n = self.count
        filename = f"<cell {n}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        result = Result(n)
        stdout, stderr = io.StringIO(), io.StringIO()
        statements: list[ast.stmt] = []
        rewritten = source
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                rewritten = rewrite(source)
                tree = ast.parse(rewritten, filename, "exec")
                statements = list(tree.body)
                last = None
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last = ast.Expression(tree.body[-1].value)
                    del tree.body[-1]
                exec(compile(tree, filename, "exec"), self.namespace)
                if last is not None:
                    value = eval(compile(last, filename, "eval"), self.namespace)
                    if value is not None:
                        result.value = value
                        result.has_value = True
                        self.out[n] = value
                        self.namespace["_"] = value
            except BaseException as e:
                result.error = "".join(traceback.format_exception(type(e), e, _cell_frames(e.__traceback__)))
            else:
                self.record(self.namespace, statements, rewritten, filename, assignments=True)
            if result.error is not None:
                # What it defined before it failed.
                self.record(self.namespace, statements, rewritten, filename, assignments=False)
        result.stdout = stdout.getvalue()
        result.stderr = stderr.getvalue()
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
                    self.imports[name] = line
            elif assignments and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.assignments[(id(container), target.id)] = statement_source(lines, node)

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
        names = [n for n in names if not (n in self.provided and self.provided[n] is self.namespace[n])]
        return self.write(names, "# A DarqOS REPL session.\n\n")

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
            if name == "_out":
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

    def write(self, names: list[str], head: str) -> "Written":
        """Return a module of some names, as statement_for writes them, in
        an order they can be run in, and what's left out of it."""
        texts: dict[str, str] = {}
        left_out: dict[str, str] = {}
        for name in names:
            text, why = self.statement_for(name, self.namespace[name])
            if text is None:
                left_out[name] = why
            else:
                texts[name] = text

        # What each needs as it's run, of what's written.
        now: dict[str, set[str]] = {}
        for name, text in texts.items():
            try:
                needed, _ = needs(text)
            except SyntaxError:
                needed = set()
            now[name] = needed - {name}
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
        order = [n for n in self.namespace if n in texts]
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
        statements = []
        for name in ordered:
            if texts[name] not in seen:
                seen.add(texts[name])
                statements.append(texts[name].rstrip() + "\n")
        body = "".join(s for s in statements if s.startswith(("import ", "from ")))
        others = "\n\n".join(s for s in statements if not s.startswith(("import ", "from ")))
        return Written(head + body + ("\n\n" if body and others else "") + others, sorted(left_out.items()),
                       ordered)

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
            return self.imports[name], ""
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
