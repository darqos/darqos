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

TOOL_PATH = pathlib.Path(__file__).parent.parent / "tools" / "repl"


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
    assert set(actions) == {"Add member", "Rebuild", "View whole class", "Delete"}

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
