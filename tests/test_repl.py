# darqos
# Copyright (C) 2026 David Arnold

# Tests for the REPL: its engine, darq.repl, which needs no window; its
# window, tools/repl, offscreen; and the engine as a DarqOS process,
# whose cells can call the darq API.

import importlib.util
import json
import pathlib

import pytest

from darq.repl import Engine, rewrite

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401

TOOL_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "repl"


def load():
    spec = importlib.util.spec_from_file_location("repl_window", TOOL_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


window = load()


################################################################
# The engine
################################################################

def test_rewrite_replaces_dollars():
    assert rewrite("$1 + $23") == "_out[1] + _out[23]"


def test_rewrite_leaves_strings_and_comments():
    src = "x = '$1'  # $2\n"
    assert rewrite(src) == src


def test_rewrite_inside_fstring_expression():
    assert rewrite('f"{$2}"') == 'f"{_out[2]}"'


def test_rewrite_multiline():
    assert rewrite("a = (\n  $1,\n  $2)\n") == "a = (\n  _out[1],\n  _out[2])\n"


def test_rewrite_attribute():
    # ``1.`` tokenizes as a float.
    assert rewrite("$1.real") == "_out[1].real"


def test_rewrite_incomplete_falls_back():
    assert rewrite("x = ($1,") == "x = (_out[1],"


def test_results_numbered_and_referenced():
    e = Engine()
    r1 = e.run("6 * 7")
    assert r1.number == 1 and r1.has_value and r1.value == 42
    r2 = e.run("x = $1")
    assert r2.number == 2 and not r2.has_value
    r3 = e.run("$1 + x")
    assert r3.value == 84
    assert e.namespace["_"] == 84


def test_none_is_not_a_result():
    e = Engine()
    r = e.run("print('hi')")
    assert r.stdout == "hi\n"
    assert not r.has_value
    assert 1 not in e.out


def test_multi_statement_cell_returns_last_expression():
    e = Engine()
    r = e.run("a = 2\nb = 3\na * b")
    assert r.value == 6


def test_undefined_dollar():
    e = Engine()
    r = e.run("$9")
    assert r.error is not None
    assert "NameError: $9 is not defined" in r.error


def test_traceback_shows_cell_frames_only():
    e = Engine()
    r = e.run("def f():\n    1/0\nf()")
    assert r.error is not None
    assert "darq/repl.py" not in r.error
    assert '<cell 1>", line 2' in r.error
    assert "1/0" in r.error


def test_syntax_error_is_reported():
    e = Engine()
    r = e.run("x = = 1")
    assert r.error is not None and "SyntaxError" in r.error


def test_is_complete():
    e = Engine()
    assert e.is_complete("x = 1")
    assert not e.is_complete("def f():")
    assert not e.is_complete("x = ($1,")
    assert e.is_complete("x = = 1")      # Broken: run it to show the error.


def test_complete_namespace_and_dollar():
    e = Engine()
    e.run("value_one = 1")
    names, prefix = e.complete("value_o")
    assert "value_one" in names and prefix == 7
    e.run("'abc'")
    names, prefix = e.complete("$2.upp")
    assert names == ["upper"] and prefix == 3


def test_the_darq_api_is_ready():
    e = Engine()
    said = e.start()
    assert "darq" in e.namespace and "DarqError" not in e.namespace, said
    assert e.namespace["terminal"].source == "repl"
    assert e.namespace["terminal"].__name__ == "darq.terminal"


def test_a_prelude_that_fails_says_why():
    e = Engine()
    assert "couldn't be imported" in e.start("import no_such_module")


################################################################
# Browsing, and editing, the namespace
################################################################

from darq import repl as engine_module  # noqa: E402
from darq.repl import CLASS, FUNCTION, MODULE, PROPERTY, VARIABLE, Target  # noqa: E402


def ran(*cells: str) -> Engine:
    e = Engine()
    for cell in cells:
        result = e.run(cell)
        assert result.error is None, result.error
    return e


def target(e: Engine, *path: str) -> Target:
    """A target, by its path from the session."""
    found = e.session()
    for name in path:
        [found] = [b.target for b in e.members(found, private=True) if b.target.name == name]
    return found


def saved(e: Engine, where: Target, text: str):
    result = e.save(where, text)
    assert result.error is None, result.error
    return result


SHAPES = '''import json
class Shape:
    sides = 3
    def area(self):
        return 1
    @property
    def name(self):
        return 'shape'
shape = Shape()
shape.colour = 'red'
def double(x):
    return 2 * x
count = 4
_private = 1
'''


def test_the_sessions_members_and_their_kinds():
    e = ran(SHAPES, "count")
    found = {b.target.name: (b.kind, b.type_name, b.expandable) for b in e.members(e.session())}
    assert found == {"json": (MODULE, "module", True), "Shape": (CLASS, "type", True),
                     "shape": (VARIABLE, "Shape", True), "double": (FUNCTION, "function", False),
                     "count": (VARIABLE, "int", False)}
    assert "_private" in {b.target.name for b in e.members(e.session(), private=True)}
    shape = {b.target.name: b.kind for b in e.members(target(e, "Shape"))}
    assert shape == {"area": FUNCTION, "name": PROPERTY, "sides": VARIABLE}
    assert [b.target.name for b in e.members(target(e, "shape"))] == ["colour"]
    assert "dumps" in {b.target.name for b in e.members(target(e, "json"))}


DEFINED = '''import json
def deco(f):
    return f
@deco
def double(x):
    return 2 * x
class Shape(object):
    """A shape."""
    sides = 3
    def area(self):
        return 1
numbers = [1, 2]
shape = Shape()
'''


def test_sources_shown():
    e = ran(DEFINED)
    double = e.source_of(target(e, "double"))
    assert double.editable and double.text == "@deco\ndef double(x):\n    return 2 * x\n"
    header = e.source_of(target(e, "Shape"))
    assert header.editable and header.text == 'class Shape(object):\n    """A shape."""\n'
    area = e.source_of(target(e, "Shape", "area"))
    assert area.editable and area.text == "def area(self):\n    return 1\n"
    assert e.source_of(target(e, "Shape", "sides")).text == "sides = 3\n"
    assert e.source_of(target(e, "numbers")).text == "numbers = [1, 2]\n"
    assert e.source_of(target(e, "shape")).text == "shape = Shape()\n"
    assert "class Shape" in e.whole_class(target(e, "Shape"))

    # A library's is shown, but not edited; a built-in's has no source.
    dumps = e.source_of(target(e, "json", "dumps"))
    assert not dumps.editable and dumps.text.startswith("def dumps(")
    length = e.source_of(Target(engine_module.builtins, "len", ("builtins", "len")))
    assert not length.editable and length.text.startswith("# len(")


def test_a_variable_is_rebound():
    e = ran("count = 1")
    saved(e, target(e, "count"), "count = 2 + 3")
    assert e.namespace["count"] == 5
    saved(e, target(e, "count"), "count * 2")
    assert e.namespace["count"] == 10
    assert "must assign count" in (e.save(target(e, "count"), "other = 1").error or "")


def test_a_function_is_changed_in_place():
    e = ran("def answer():\n    return 1", "held = answer")
    result = saved(e, target(e, "answer"), 'def answer():\n    """Now 42."""\n    return 42')
    assert result.edited == "answer" and result.note == "" and result.number == 3
    assert e.namespace["held"]() == 42 and e.namespace["held"] is e.namespace["answer"]
    assert e.source_of(target(e, "answer")).text.startswith('def answer():\n    """Now 42."""')

    # Its errors are its save's.
    saved(e, target(e, "answer"), "def answer():\n    return 1 / 0")
    error = e.run("answer()").error or ""
    assert "<cell 4>" in error and "ZeroDivisionError" in error


CLOSURE = '''def deco(f):
    def inner():
        return f() + 1
    return inner
@deco
def answer():
    return 1
held = answer
'''


def test_a_changed_closure_is_bound_anew():
    e = ran(CLOSURE)
    result = saved(e, target(e, "answer"), "def answer():\n    return 10")
    assert "bound anew" in result.note
    assert e.namespace["answer"]() == 10 and e.namespace["held"]() == 2


METHODS = '''class A:
    def hi(self):
        return 'A'
class B(A):
    def hi(self):
        return 'B' + super().hi()
    def plain(self):
        return 'plain'
b = B()
bound = b.hi
'''


def test_a_method_with_super_is_changed_for_every_instance():
    e = ran(METHODS)
    saved(e, target(e, "B", "hi"), "def hi(self):\n    return 'b2' + super().hi()")
    assert e.run("b.hi(), bound()").value == ("b2A", "b2A")

    # Gaining super(), its closure's changed: the class has the new one.
    result = saved(e, target(e, "B", "plain"), "def plain(self):\n    return 'p' + super().hi()")
    assert "bound anew" in result.note and e.run("b.plain()").value == "pA"


def test_a_property_is_replaced():
    e = ran("class Shape:\n    @property\n    def name(self):\n        return 'shape'\nshape = Shape()")
    saved(e, target(e, "Shape", "name"), "@property\ndef name(self):\n    return 'square'")
    assert e.run("shape.name").value == "square"


BASES = '''class A:
    pass
class B:
    def b(self):
        return 'b'
class C(A):
    def c(self):
        return 'c'
c = C()
'''


def test_a_header_changes_docstring_and_bases_in_place():
    e = ran(BASES)
    saved(e, target(e, "C"), 'class C(B):\n    """Now a B."""\n')
    assert e.run("c.b(), c.c(), C.__doc__, type(c) is C").value == ("b", "c", "Now a B.", True)
    assert e.source_of(target(e, "C")).text == 'class C(B):\n    """Now a B."""\n'
    assert "edit its members" in (e.save(target(e, "C"), "class C(B):\n    x = 1").error or "")


DECORATED = '''from dataclasses import dataclass
class P:
    x: int = 1
    def twice(self):
        return 2 * self.x
old = P()
'''


def test_a_changed_decorator_rebuilds_the_class():
    e = ran(DECORATED)
    result = saved(e, target(e, "P"), "@dataclass\nclass P:\n")
    assert result.note.startswith("Rebuilt")
    assert e.run("P(5).twice(), type(old) is P").value == (10, False)


def test_a_dataclass_is_rebuilt_with_a_new_default():
    e = ran("from dataclasses import dataclass\n@dataclass\nclass P:\n    x: int = 1")
    saved(e, target(e, "P", "x"), "x: int = 5")
    assert e.run("P().x").value == 1
    assert e.rebuild(target(e, "P")).error is None
    assert e.run("P().x").value == 5


def test_members_are_added_and_deleted():
    e = ran("class Shape:\n    sides = 3\nshape = Shape()")
    shape = e.namespace["Shape"]
    saved(e, Target(shape, "", ("Shape", "")), "def added(self):\n    return 3")
    assert e.run("shape.added()").value == 3
    assert e.source_of(target(e, "Shape", "added")).editable
    assert e.delete(target(e, "Shape", "sides")).error is None
    assert e.run("hasattr(shape, 'sides')").value is False


def test_a_class_whole_is_as_it_is_now():
    """With what's been added and edited, and, as comments, what was made
    otherwise."""
    e = ran("from dataclasses import dataclass\n@dataclass\nclass P:\n    x: int = 1\n"
            "    def one(self):\n        return 1")
    saved(e, Target(e.namespace["P"], "", ("P", "")), "def added(self):\n    return 2")
    saved(e, target(e, "P", "one"), "def one(self):\n    return 'one'")
    saved(e, target(e, "P"), '@dataclass\nclass P:\n    """A P."""\n')
    e.run("P.loose = 3")
    whole = e.whole_class(target(e, "P"))
    assert whole.startswith('@dataclass\nclass P:\n    """A P."""\n    x: int = 1\n')
    assert "    def one(self):\n        return 'one'\n" in whole
    assert "    def added(self):\n        return 2\n" in whole
    for made in ("__init__", "__repr__", "__eq__", "loose"):
        assert f"    # {made}: no source here" in whole
    assert "__module__" not in whole and "__dict__" not in whole


def test_an_objects_attribute_is_edited():
    e = ran("class Box:\n    pass\nbox = Box()\nbox.size = 1")
    saved(e, target(e, "box", "size"), "size = 7")
    assert e.run("box.size").value == 7


def test_libraries_and_built_ins_arent_changed():
    e = ran("import json")
    assert "library's" in (e.save(target(e, "json", "dumps"), "def dumps(x):\n    return ''").error or "")
    assert "built-ins" in (e.save(Target(engine_module.builtins, "len", ("builtins", "len")), "len = 1").error or "")
    e.run("1 + 1")
    results = Target(e.namespace, "_out", ("Results",))
    [result] = e.members(results)
    assert result.target.name == "$4" and not e.source_of(result.target).editable


def test_what_a_failed_cell_defined_is_recorded():
    e = Engine()
    assert e.run("def kept():\n    return 1\nraise ValueError").error is not None
    assert e.source_of(target(e, "kept")).editable


################################################################
# Highlighting, and the window
################################################################

def test_lex_marks_dollar():
    spans = window.lex_spans("$12 + 1")
    assert spans[0][2] is window.DOLLAR and spans[1][2] is window.DOLLAR
    assert spans[1][:2] == (1, 3)


def test_lex_dollar_attribute():
    spans = window.lex_spans("$1.real")
    assert spans[1] == (1, 2, window.DOLLAR)
    assert spans[2][:2] == (2, 3)


def test_token_colours_from_darq_palette():
    from darq.ui import style
    from pygments.token import Token
    token_colour = window.token_colour
    assert token_colour(Token)[0] == style.TEXT
    # A token type without an entry takes its ancestor's colour.
    for ttype, colour in [(Token.Keyword.Namespace, style.ACCENT),
                          (Token.String.Doc, style.LINK),
                          (Token.Name.Function, style.TEXT),
                          (window.DOLLAR, style.ACCENT)]:
        assert token_colour(ttype)[0] == colour
    assert token_colour(window.DOLLAR)[1]      # Bold.
    assert token_colour(Token.Comment.Single)[2]    # Italic.


@pytest.fixture
def repl(qt_app):
    engine = Engine()
    engine.start("x = 1")
    shown = window.Window(engine)
    yield shown
    shown.closing_quietly = True
    shown.close()


def test_a_cell_is_run_and_shown(repl):
    repl.editor.setPlainText("6 * 7")
    repl.editor.run()
    text = repl.transcript.toPlainText()
    assert "[1] 6 * 7" in text and "$1 = 42" in text
    assert repl.editor.toPlainText() == "" and repl.editor.history == ["6 * 7"]

    repl.editor.setPlainText("print($1 + x)")
    repl.editor.run()
    assert "43" in repl.transcript.toPlainText()


def test_ctrl_a_goes_to_the_start_of_the_line(repl):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    repl.editor.setPlainText("first line\n    second line")
    cursor = repl.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    repl.editor.setTextCursor(cursor)
    repl.editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "a"))
    cursor = repl.editor.textCursor()
    assert not cursor.hasSelection()
    assert (cursor.blockNumber(), cursor.positionInBlock()) == (1, 0)


def test_history_and_completion(repl):
    for source in ("value_one = 1", "value_two = 2"):
        repl.editor.setPlainText(source)
        repl.editor.run()
    repl.editor.history_back()
    assert repl.editor.toPlainText() == "value_two = 2"
    repl.editor.history_back()
    repl.editor.history_forward()
    repl.editor.history_forward()
    assert repl.editor.toPlainText() == ""

    repl.editor.setPlainText("value_o")
    cursor = repl.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    repl.editor.setTextCursor(cursor)
    repl.editor.complete(auto=False)
    assert repl.editor.toPlainText() == "value_one"


def ran_in(repl, source: str):
    repl.editor.setPlainText(source)
    repl.editor.run()


def tree_item(repl, *path: str):
    """The tree's item at a path of names, expanding what's on the way."""
    tree = repl.tree
    items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    found = None
    for name in path:
        found = next(i for i in items if i is not None and i.text(0).removesuffix(window.DRAFT_MARK) == name)
        found.setExpanded(True)
        items = [found.child(i) for i in range(found.childCount())]
    assert found is not None
    return found


def key(repl, code, modifiers=None):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    repl.editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, modifiers or Qt.KeyboardModifier.NoModifier))


def test_the_tree_shows_the_session(repl):
    ran_in(repl, "import json\ndef double(x):\n    return 2 * x\nclass Shape:\n    def area(self):\n        return 1")
    double = tree_item(repl, "Session", "double")
    assert double.text(1) == "function" and not double.icon(0).isNull()
    assert {"Shape", "double", "json", "darq", "terminal", "x"} <= {
        tree_item(repl, "Session").child(i).text(0) for i in range(tree_item(repl, "Session").childCount())}

    # A module's read when it's expanded; and what's expanded stays so.
    assert tree_item(repl, "Session", "json", "dumps").text(1) == "function"
    ran_in(repl, "y = 2")
    assert tree_item(repl, "Session", "json").isExpanded()
    assert tree_item(repl, "Session", "Shape", "area").text(1) == "function"
    assert tree_item(repl, "Built-ins", "len").text(1) == "builtin_function_or_method"


def test_editing_a_function_and_saving_it(repl):
    from PyQt6.QtCore import Qt
    ran_in(repl, "def double(x):\n    return 2 * x")
    repl.editor.setPlainText("double(4)")
    repl.tree.choose(tree_item(repl, "Session", "double"), 0)
    assert repl.editor.toPlainText() == "def double(x):\n    return 2 * x\n"
    assert repl.heading.text() == "Editing: double" and repl.header.isVisibleTo(repl)

    # Enter's a new line, not a run; Ctrl+S saves, as a cell.
    repl.editor.setPlainText("def double(x):\n    return 3 * x\n")
    key(repl, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert "# edit double" in repl.transcript.toPlainText()
    assert repl.engine.namespace["double"](4) == 12

    # Escape goes back to the cell, as it was.
    key(repl, Qt.Key.Key_Escape)
    assert repl.target is None and repl.editor.toPlainText() == "double(4)" and not repl.header.isVisibleTo(repl)


def test_drafts_are_kept_and_shown(repl):
    ran_in(repl, "count = 1")
    repl.tree.choose(tree_item(repl, "Session", "count"), 0)
    repl.editor.setPlainText("count = 99")
    repl.back_to_cell()
    assert tree_item(repl, "Session", "count").text(0) == "count" + window.DRAFT_MARK
    assert tree_item(repl, "Drafts", "count").text(0) == "count" + window.DRAFT_MARK
    repl.tree.choose(tree_item(repl, "Drafts", "count"), 0)
    assert repl.editor.toPlainText() == "count = 99"

    # Saved, it's gone.
    repl.save()
    repl.back_to_cell()
    assert repl.engine.namespace["count"] == 99 and repl.tree.drafts == {}


def test_a_librarys_is_only_viewed(repl):
    ran_in(repl, "import json")
    repl.tree.choose(tree_item(repl, "Session", "json", "dumps"), 0)
    assert repl.editor.isReadOnly() and repl.heading.text().startswith("Viewing: json.dumps")
    repl.save()
    assert "# edit" not in repl.transcript.toPlainText()


def test_a_classs_actions(repl):
    ran_in(repl, "class Shape:\n    sides = 3\nshape = Shape()")
    shape = tree_item(repl, "Session", "Shape").data(0, window.TARGET_ROLE)
    actions = dict(repl.actions_for(shape))
    assert set(actions) == {"Add member", "Rebuild", "View whole class", "Extract package…", "Delete"}

    actions["View whole class"]()
    assert repl.editor.isReadOnly() and "sides = 3" in repl.editor.toPlainText()

    actions["Add member"]()
    assert repl.heading.text() == "Editing: a new member of Shape"
    repl.editor.setPlainText("def added(self):\n    return 3\n")
    repl.save()
    assert repl.target is None and repl.engine.namespace["shape"].added() == 3

    sides = tree_item(repl, "Session", "Shape", "sides").data(0, window.TARGET_ROLE)
    dict(repl.actions_for(sides))["Delete"]()
    assert "del Shape.sides" in repl.transcript.toPlainText()
    assert not hasattr(repl.engine.namespace["Shape"], "sides")


################################################################
# Saving and loading a session
################################################################

SESSION_CELLS = [
    "import json\nfrom os.path import join as pj",
    "DEFAULT_SIDES = 4",
    "def helper(x):\n    return later(x) * 2",
    "class Shape:\n    sides = DEFAULT_SIDES\n    def area(self):\n        return helper(self.sides)",
    "items = [1, 2, 3]",
    "items.append(4)",
    "shape = Shape()",
    "def later(x):\n    return x + 1",
    "alias = helper",
]


def test_needs():
    from darq.repl import needs
    assert needs("@deco\ndef f(x=default):\n    return g(x) + h\n") == ({"deco", "default"}, {"g", "h"})
    assert needs("class A(Base):\n    k = konst\n    def m(self):\n        return other.thing()\n") == (
        {"Base", "konst"}, {"other"})
    assert needs("x = [y for y in items if pred(y)]") == ({"items", "pred"}, set())
    assert needs("total = terminal.windows()") == ({"terminal"}, set())


def test_a_session_is_written_and_loaded_again():
    e = ran(*SESSION_CELLS)
    e.provide("loop", object())
    written = e.write_session()
    assert written.left_out == []
    text = written.text
    assert text.startswith("# A DarqOS REPL session.\n\nimport json\nfrom os.path import join as pj\n")
    # Literals as they are now; a class after what its body needs.
    assert "items = [1, 2, 3, 4]" in text
    assert text.index("DEFAULT_SIDES = 4") < text.index("class Shape")
    assert "alias = helper" in text and "loop" not in text and "terminal" not in text

    again = Engine()
    loaded = again.load_session(text)
    assert loaded.error is None and loaded.note == "Loaded 9 of 9 statements"
    assert again.run("shape.area(), items, alias(1), pj('a', 'b')").value == (10, [1, 2, 3, 4], 4, "a/b")
    # What it defined is editable, as a cell's.
    assert again.source_of(target(again, "helper")).editable


def test_what_cant_be_written_is_left_out_with_what_needs_it():
    e = ran("import threading\nglobals()['made'] = object()", "def make():\n    return made",
            "kept = make() if False else 1", "box = [made]")
    written = e.write_session()
    assert dict(written.left_out) == {"made": "its value, a object, can't be written",
                                      "box": "it needs made, which is left out"}
    assert "def make()" in written.text and "threading" in written.text


def test_whats_provided_isnt_written_unless_its_rebound():
    e = Engine()
    e.start()
    e.provide("loop", object())
    assert e.write_session().names == []
    e.run("loop = 3")
    assert e.write_session().names == ["loop"]


def test_a_statement_that_fails_doesnt_stop_the_rest():
    e = Engine()
    result = e.load_session("a = 1\nb = undefined\nc = 3\n")
    assert result.note == "Loaded 2 of 3 statements"
    assert result.error == "line 2: NameError: name 'undefined' is not defined\n"
    assert e.run("a + c").value == 4


def test_cells_are_its_history():
    e = ran("x = 1", "x + 1")
    assert e.history == ["x = 1", "x + 1"]


class SessionItem:
    """A session's item: its package, and generation, kept as its type
    keeps them."""

    def __init__(self, package: bytes = b"", generation: int = 1):
        import darq.packages
        self.package = package or darq.packages.make({})
        self.generation = generation
        self.puts: list = []
        self.closed = False
        self.viewed_by: list[str] = []

    def get(self):
        import types
        return types.SimpleNamespace(package=self.package, generation=self.generation)

    def put(self, expected=0, package=b"", source=""):
        import types
        from darq.protocols.python import Status
        self.puts.append(source)
        if expected != self.generation:
            return types.SimpleNamespace(status=Status.CONFLICT, generation=self.generation, message="")
        self.package, self.generation = package, self.generation + 1
        return types.SimpleNamespace(status=Status.OK, generation=self.generation, message="")

    def viewed(self, source="", note=""):
        self.viewed_by.append(source)

    def close(self):
        self.closed = True


def test_a_session_is_opened_and_saved(repl):
    import json
    import darq.packages
    saved = darq.packages.make({"main.py": "def twice(n):\n    return 2 * n\n\nseen = [1]\n",
                                "history.json": json.dumps(["def twice(n): ...", "seen = [1]"]),
                                "notes.txt": b"kept"})
    item = SessionItem(saved, 3)
    repl.attach(item, item.package, item.generation)
    assert repl.engine.namespace["twice"](4) == 8
    assert repl.editor.history == ["def twice(n): ...", "seen = [1]"]
    assert "Loaded 2 of 2 statements, from 2 cells run" in repl.transcript.toPlainText()
    assert not repl.unsaved

    ran_in(repl, "seen.append(2)")
    assert repl.unsaved
    repl.save_session()
    assert item.puts == ["repl"] and repl.generation == 4 and not repl.unsaved
    files = darq.packages.contents(item.package)
    assert "seen = [1, 2]" in files["main.py"].decode() and files["notes.txt"] == b"kept"
    assert json.loads(files["history.json"])[-1] == "seen.append(2)"
    assert "Saved the session: " in repl.transcript.toPlainText()


def test_whats_left_out_is_said(repl):
    item = SessionItem()
    repl.attach(item, item.package, item.generation)
    ran_in(repl, "globals()['thing'] = object()")
    repl.save_session()
    assert "left out: thing, as its value, a object, can't be written" in repl.transcript.toPlainText()


def test_a_session_saved_elsewhere_is_saved_over_only_if_asked(repl):
    item = SessionItem()
    repl.attach(item, item.package, item.generation)
    item.generation = 7
    asked: list[str] = []
    repl.confirm = lambda question: asked.append(question) or False
    ran_in(repl, "x = 1")
    repl.save_session()
    assert "Not saved: it was saved elsewhere" in repl.transcript.toPlainText() and repl.unsaved
    repl.confirm = lambda question: True
    repl.save_session()
    assert item.generation == 8 and not repl.unsaved and len(asked) == 1


def test_closing_with_whats_unsaved_asks(repl):
    item = SessionItem()
    repl.attach(item, item.package, item.generation)
    ran_in(repl, "x = 1")
    repl.confirm = lambda question: False
    repl.show()
    repl.close()
    assert repl.isVisible()
    repl.confirm = lambda question: True
    repl.close()
    assert not repl.isVisible()


################################################################
# Extracting a package
################################################################

PROGRAM = [
    "import json\nfrom os.path import join as pj",
    "LIMIT = 3",
    "unrelated = 99",
    "class Box:\n    size = LIMIT\n    def dump(self):\n        return json.dumps(self.size)",
    "def helper(x):\n    return pj('a', str(x))",
    "def darq_main():\n    terminal.notify(helper(Box().dump()))\n    return 0",
]


def started(*cells: str) -> Engine:
    """An engine with the REPL's prelude, which gives darq and terminal."""
    e = Engine()
    e.start()
    for cell in cells:
        result = e.run(cell)
        assert result.error is None, result.error
    return e


def test_a_function_and_what_it_needs_are_extracted():
    e = started(*PROGRAM)
    written = e.extract("darq_main")
    assert written.left_out == []
    assert written.text.startswith("# darq_main, extracted from a DarqOS REPL session.\n\n"
                                   "import darq.terminal as terminal\nimport json\nfrom os.path import join as pj\n")
    assert "unrelated" not in written.text
    assert written.text.index("LIMIT = 3") < written.text.index("class Box")
    assert set(written.names) == {"terminal", "json", "pj", "LIMIT", "Box", "helper", "darq_main"}

    # It runs, as a module, by itself.
    namespace: dict = {}
    exec(written.text.replace("terminal.notify(", "print("), namespace)
    assert namespace["helper"](1) == "a/1"


def test_a_class_is_extracted_with_what_its_methods_need():
    e = started(*PROGRAM)
    assert set(e.extract("Box").names) == {"Box", "LIMIT", "json"}


def test_what_cant_be_extracted_says_why():
    e = started("1 + 1", "def bad():\n    return $1 + missing",
                "globals()['made'] = object()", "def uses():\n    return made, loop", "loop = None")
    e.provide("loop", object())
    assert dict(e.extract("bad").left_out) == {"$n": "a result's used: assign it to a name",
                                               "missing": "it's used, but isn't defined"}
    assert dict(e.extract("uses").left_out) == {
        "loop": "it's the REPL's: it isn't the session's to extract",
        "made": "its value, a object, can't be written"}


class Store:
    """Package items, as darq.packages makes and changes them."""

    def __init__(self):
        self.items: dict[str, tuple[str, bytes]] = {}
        self.calls: list = []
        self.taken = {"json"}

    def name_of(self, item_id):
        return self.items[item_id][0] if item_id in self.items else ""

    def unusable(self, name, item_id=""):
        if name in self.taken:
            return f"{name} is a module already, which import would find first"
        others = [i for i, (n, _) in self.items.items() if n == name and i != item_id]
        return f"another package is named {name}: {', '.join(others)}" if others else ""

    def create(self, data, name, source):
        item_id = "darq.python:" + f"{len(self.items) + 1}" * 32
        self.items[item_id] = (name, data)
        self.calls.append(("create", name, source))
        return item_id

    def update(self, item_id, data, source, overwrite=None):
        self.items[item_id] = (self.items[item_id][0], data)
        self.calls.append(("update", item_id, source))

    def rename(self, item_id, name):
        self.items[item_id] = (name, self.items[item_id][1])
        self.calls.append(("rename", item_id, name))


@pytest.fixture
def extracting(repl):
    """A REPL with a session, and the program in it, whose dialogs are
    answered as a test says."""
    item = SessionItem()
    repl.attach(item, item.package, item.generation)
    for cell in PROGRAM:
        ran_in(repl, cell)
    repl.store = Store()
    repl.answers = {"choose": [], "ask": []}
    repl.choose = lambda title, question, choices: repl.answers["choose"].pop(0)
    repl.ask_text = lambda title, question, text: repl.answers["ask"].pop(0)
    return repl


def test_extracting_makes_a_package(extracting):
    import darq.packages
    darq_main = extracting.engine.session()
    target = [b.target for b in extracting.engine.members(darq_main) if b.target.name == "darq_main"][0]
    assert "Extract package…" in dict(extracting.actions_for(target))
    extracting.answers["ask"] = ["tidy"]
    extracting.extract_package(target)
    [(item_id, (name, data))] = extracting.store.items.items()
    assert name == "tidy" and extracting.store.calls == [("create", "tidy", "repl")]
    assert "def darq_main():" in darq.packages.modules(data)["main.py"]
    assert extracting.extracts == {"darq_main": item_id} and extracting.unsaved
    assert f"Extracted darq_main, with the 6 names it needs, to tidy ({item_id})" in \
        extracting.transcript.toPlainText()

    # Again: it offers to update the one it was extracted to, and renames it.
    extracting.answers = {"choose": [0], "ask": ["tidier"]}
    extracting.extract_package(target)
    assert extracting.store.calls[1:] == [("update", item_id, "repl"), ("rename", item_id, "tidier")]

    # Or another, made new.
    extracting.answers = {"choose": [1], "ask": ["tidy2"]}
    extracting.extract_package(target)
    assert len(extracting.store.items) == 2 and extracting.extracts["darq_main"] != item_id


def test_a_name_that_cant_be_is_refused(extracting):
    target = [b.target for b in extracting.engine.members(extracting.engine.session())
              if b.target.name == "darq_main"][0]
    extracting.answers["ask"] = ["json"]
    extracting.extract_package(target)
    assert extracting.store.items == {}
    assert "Not extracted: json is a module already" in extracting.transcript.toPlainText()


def test_whats_extracted_is_kept_with_the_session(extracting):
    import json
    import darq.packages
    target = [b.target for b in extracting.engine.members(extracting.engine.session())
              if b.target.name == "darq_main"][0]
    extracting.answers["ask"] = ["tidy"]
    extracting.extract_package(target)
    extracting.save_session()
    item = extracting.item
    files = darq.packages.contents(item.package)
    assert json.loads(files["extracts.json"]) == extracting.extracts

    again = window.Window(Engine())
    again.closing_quietly = True
    again.attach(item, item.package, item.generation)
    assert again.extracts == extracting.extracts
    again.close()


def test_names_for_packages(monkeypatch):
    import darq.packages
    monkeypatch.setattr(darq.packages, "finder", lambda name: ["darq.python:" + "1" * 32] if name == "tidy" else [])
    assert darq.packages.unusable("2fast").endswith("isn't a name Python can import")
    assert darq.packages.unusable("json") == "json is a module already, which import would find first"
    assert darq.packages.unusable("tidy").startswith("another package is named tidy")
    assert darq.packages.unusable("tidy", "darq.python:" + "1" * 32) == ""
    assert darq.packages.unusable("certainly_nothing_called_this") == ""


################################################################
# As a DarqOS process
################################################################

def test_a_cell_calls_the_darq_api(live_pkernel, tmp_path):  # noqa: F811
    """Run as the REPL is, a DarqOS process, its cells can make system
    calls."""
    results = tmp_path / "results.json"
    program = write_app(tmp_path / "repl", f"""
        import json
        import os
        from darq.repl import Engine

        def darq_main():
            engine = Engine()
            said = engine.start()
            result = engine.run("darq.get_version().version != '', darq.getpid() > 0")
            with open({str(results)!r} + ".tmp", "w") as f:
                json.dump({{"said": said, "value": result.value, "error": result.error}}, f)
            os.rename({str(results)!r} + ".tmp", {str(results)!r})
        """)
    live_pkernel.do_spawn_process(None, program)
    run_kernel_until(live_pkernel, results.exists, "the REPL's cell to run")
    report = json.loads(results.read_text())
    assert report["error"] is None and report["value"] == [True, True]
    assert report["said"].startswith("DarqOS ") and "can't be had" not in report["said"]


################################################################
# As the lens for darq.python items: a session each
################################################################

SESSION = "darq.python:" + "5" * 32


def test_the_lens_uses_a_session(qt_app, monkeypatch):
    import types
    from darq.protocols.lens import Close, ShownRequest, Status, UseRequest
    item = SessionItem()
    monkeypatch.setattr(window, "Python", types.SimpleNamespace(Client=lambda port, timeout=None: item))
    stopped: list[bool] = []
    loop = types.SimpleNamespace(stop=lambda: stopped.append(True))
    lens = window.ReplLens(loop)
    lens.name_of = lambda item_id: "session"

    assert lens.use(UseRequest(item_id=SESSION, item=9), 0).status == Status.OK
    assert lens.window is not None and lens.window.windowTitle() == "session"
    assert f"Session session ({SESSION})" in lens.window.transcript.toPlainText()
    assert item.viewed_by == ["repl"]
    assert lens.shown(ShownRequest(), 0).item_id == SESSION
    assert lens.use(UseRequest(item_id=SESSION, item=10), 0).status == Status.BUSY

    # Closed, the lens is done, and so is its item.
    lens.close(Close(), 0)
    assert stopped == [True] and item.closed


def test_a_lens_not_used_gives_up(monkeypatch):
    import types
    stopped: list[bool] = []
    lens = window.ReplLens(types.SimpleNamespace(stop=lambda: stopped.append(True)))
    lens.expire()
    assert stopped == [True]


def test_a_session_in_its_lens(live_pkernel, tmp_path):  # noqa: F811
    """At boot: the REPL's registered as darq.python's lens, uses a
    session, running its code, says it's showing it, and exits once it's
    closed."""
    kernel = live_pkernel
    ran = tmp_path / "ran"
    client_path = write_app(tmp_path / "client", f"""
        import pathlib
        import time
        import darq
        import darq.launch
        import darq.packages as packages
        from darq.protocols.lens import Lens, Status as LensStatus
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not any(lens.endswith("/repl")
                          for lens in types.find_lenses(type_id="darq.python", context="terminal").lenses):
                if time.monotonic() > deadline:
                    return "no REPL lens registered"
                time.sleep(0.05)

            code = "open({str(ran)!r}, 'w').write('ran')\\n"
            item_id, port = darq.launch.create_item_from("darq.python", packages.make({{"main.py": code}}),
                                                         "session")
            lens = Lens.Client(darq.launch.use_item(item_id, port).port, timeout=30)
            if lens.shown().item_id != item_id:
                return "not shown"
            if not pathlib.Path({str(ran)!r}).exists():
                return "its code wasn't run"
            other_id, other = darq.launch.create_item_from("darq.python", packages.make({{}}), "other")
            if lens.use(item_id=other_id, item=other).status != LensStatus.BUSY:
                return "second use wasn't refused"
            lens.close()
            return 0
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
    [lens_process] = [p for p in kernel._process_table.values() if p._image_path == str(TOOL_PATH)]
    run_kernel_until(kernel, lambda: lens_process.get_status() == zombie, "the lens to exit")
    assert (lens_process.get_exit_status(), lens_process.get_exit_error()) == (0, None)
