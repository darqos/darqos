# darqos
# Copyright (C) 2026 David Arnold

# Tests for the DIDL compiler, bin/didlc.

import importlib.machinery
import importlib.util
import json
import pathlib
import textwrap

import pytest


DIDLC_PATH = pathlib.Path(__file__).parent.parent / "bin" / "didlc"


def load_didlc():
    """Import bin/didlc as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("didlc", str(DIDLC_PATH))
    spec = importlib.util.spec_from_loader("didlc", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


didlc = load_didlc()


def compile_source(source: str) -> dict:
    return didlc.compile_ir(didlc.Parser(didlc.lex(textwrap.dedent(source))).parse())


def compile_error(source: str) -> didlc.DidlError:
    with pytest.raises(didlc.DidlError) as info:
        compile_source(source)
    return info.value


def method(ir: dict, name: str, protocol: int = 0) -> dict:
    return next(m for m in ir["protocols"][protocol]["methods"] if m["name"] == name)


def offsets(layout: dict) -> list:
    return [(f["name"], f["offset"]) for f in layout["fields"]]


def request_layout(args: str, structs: str = "") -> dict:
    """Compile a single-method protocol and return its request layout."""
    ir = compile_source(f"""
        library t;
        {structs}
        protocol P {{
            1: M({args});
        }}
        """)
    return method(ir, "M")["request"]


################################################################
# Lexer
################################################################

def test_lex_skips_whitespace_and_comments():
    tokens = didlc.lex("library x; // comment\n")
    assert [(t.type, t.value) for t in tokens] == [
        ("LIBRARY", "library"), ("ID", "x"), ("SEMI", ";"), ("EOF", "")]


def test_lex_positions():
    tokens = didlc.lex("library x;\n  struct S {}\n")
    struct = tokens[3]
    assert (struct.type, struct.line, struct.col) == ("STRUCT", 2, 3)


def test_lex_positions_after_comments():
    tokens = didlc.lex("// one\n// two\nlibrary x;")
    assert (tokens[0].line, tokens[0].col) == (3, 1)


def test_lex_dotted_identifier():
    tokens = didlc.lex("library storage.filesystem;")
    assert tokens[1].value == "storage.filesystem"


def test_lex_bad_character():
    with pytest.raises(didlc.DidlError) as info:
        didlc.lex("library x;\nstruct S $")
    assert (info.value.line, info.value.col) == (2, 10)


################################################################
# Parser
################################################################

def test_library_name():
    assert compile_source("library storage.filesystem;")["library"] == "storage.filesystem"


def test_one_way_and_two_way_methods():
    ir = compile_source("""
        library t;
        protocol P {
            1: Ping();
            2: Get(uint32 key) -> (uint64 value);
        }
        """)
    assert method(ir, "Ping")["response"] is None
    assert method(ir, "Ping")["request"]["fields"] == []
    assert offsets(method(ir, "Get")["request"]) == [("key", 0)]
    assert offsets(method(ir, "Get")["response"]) == [("value", 0)]


def test_empty_response():
    ir = compile_source("""
        library t;
        protocol P {
            1: Sync() -> ();
        }
        """)
    assert method(ir, "Sync")["response"]["fields"] == []


def test_multiple_protocols():
    ir = compile_source("""
        library t;
        protocol A { 1: X(); }
        protocol B { 1: X(); }
        """)
    assert [p["name"] for p in ir["protocols"]] == ["A", "B"]


def test_unexpected_global_token():
    error = compile_error("library t;\nuint32 x;")
    assert (error.line, error.col) == (2, 1)


def test_missing_semicolon():
    error = compile_error("""
        library t;
        protocol P {
            1: M()
        }
        """)
    assert "Expected SEMI" in str(error)


################################################################
# Method numbers
################################################################

def test_method_numbers():
    ir = compile_source("""
        library t;
        protocol P {
            7: A();
            65535: B();
        }
        """)
    assert method(ir, "A")["number"] == 7
    assert method(ir, "B")["number"] == 65535


def test_method_number_required():
    error = compile_error("""
        library t;
        protocol P {
            A();
        }
        """)
    assert "Expected NUMBER" in str(error)


@pytest.mark.parametrize("number", [0, 65536])
def test_method_number_out_of_range(number):
    error = compile_error(f"""
        library t;
        protocol P {{
            {number}: A();
        }}
        """)
    assert "out of range" in str(error)
    assert (error.line, error.col) == (4, 5)


def test_duplicate_method_number():
    error = compile_error("""
        library t;
        protocol P {
            1: A();
            1: B();
        }
        """)
    assert "Duplicate method number 1" in str(error)
    assert (error.line, error.col) == (5, 5)


def test_same_number_in_different_protocols():
    compile_source("""
        library t;
        protocol A { 1: X(); }
        protocol B { 1: Y(); }
        """)


def test_duplicate_method_name():
    error = compile_error("""
        library t;
        protocol P {
            1: A();
            2: A();
        }
        """)
    assert "Duplicate method: P.A" in str(error)


################################################################
# Layout
################################################################

def test_message_header():
    header = compile_source("library t;")["message_header"]
    assert offsets(header) == [("txid", 0), ("flags", 4), ("method", 6)]
    assert [f["type"] for f in header["fields"]] == ["uint32", "uint16", "uint16"]
    assert header["total_size"] == 8


@pytest.mark.parametrize("type_, size, align", [
    ("bool", 1, 1),
    ("int8", 1, 1), ("uint8", 1, 1),
    ("int16", 2, 2), ("uint16", 2, 2),
    ("int32", 4, 4), ("uint32", 4, 4),
    ("int64", 8, 8), ("uint64", 8, 8),
    ("real32", 4, 4), ("real64", 8, 8),
    ("opaque", 16, 8),
    ("port", 4, 4),
])
def test_primitive_types(type_, size, align):
    layout = request_layout(f"uint8 pad, {type_} v")
    field = layout["fields"][1]
    assert (field["size"], field["alignment"], field["offset"]) == (size, align, align)


def test_unknown_type():
    error = compile_error("""
        library t;
        protocol P {
            1: M(float32 x);
        }
        """)
    assert "Unknown type: float32" in str(error)
    assert (error.line, error.col) == (4, 10)


def test_natural_alignment_padding():
    layout = request_layout("uint8 a, uint32 b, uint8 c, uint16 d, uint8 e, uint64 f")
    assert offsets(layout) == [("a", 0), ("b", 4), ("c", 8), ("d", 10), ("e", 12), ("f", 16)]
    assert layout["total_size"] == 24
    assert layout["alignment"] == 8


def test_trailing_padding():
    layout = request_layout("uint32 a, uint8 b")
    assert layout["total_size"] == 8


def test_doc_example():
    ir = compile_source("""
        library filesystem;

        struct FileAttributes {
            uint64 size;
            uint32 permissions;
            uint32 owner_id;
        }

        protocol FileServer {
            1: GetAttributes(uint64 fd) -> (FileAttributes attr, uint32 status);
            2: Foo(uint8 a, uint16 b, int8 c, real32 d, real64 e, bool f, opaque g, port h);
        }
        """)
    get = method(ir, "GetAttributes")
    assert offsets(get["response"]) == [("attr", 0), ("status", 16)]
    assert get["response"]["total_size"] == 24
    foo = method(ir, "Foo")
    assert offsets(foo["request"]) == [
        ("a", 0), ("b", 2), ("c", 4), ("d", 8), ("e", 16), ("f", 24), ("g", 32), ("h", 48)]
    assert foo["request"]["total_size"] == 56


################################################################
# Structs (C/C++ ABI rules)
################################################################

def test_struct_layout():
    ir = compile_source("""
        library t;
        struct S {
            uint8 a;
            uint64 b;
            uint8 c;
        }
        """)
    s = ir["structs"][0]
    assert offsets(s) == [("a", 0), ("b", 8), ("c", 16)]
    assert (s["total_size"], s["alignment"]) == (24, 8)


def test_struct_field_aligned_and_padded():
    # struct S { uint8 a; uint64 b; uint8 c; } is 24 bytes, 8-aligned in C.
    layout = request_layout(
        "uint8 w, S s, uint8 x",
        "struct S { uint8 a; uint64 b; uint8 c; }")
    assert offsets(layout) == [("w", 0), ("s", 8), ("x", 32)]
    assert layout["total_size"] == 40


def test_struct_alignment_is_largest_member():
    # struct S { uint16 a; uint8 b; } is 4 bytes, 2-aligned in C.
    layout = request_layout(
        "uint8 w, S s, uint8 x",
        "struct S { uint16 a; uint8 b; }")
    assert offsets(layout) == [("w", 0), ("s", 2), ("x", 6)]
    assert layout["total_size"] == 8
    assert layout["alignment"] == 2


def test_nested_structs():
    # struct Inner { uint32 a; uint8 b; };              8 bytes, 4-aligned
    # struct Outer { uint8 c; struct Inner i; uint8 d; }; 16 bytes, 4-aligned
    ir = compile_source("""
        library t;
        struct Outer { uint8 c; Inner i; uint8 d; }
        struct Inner { uint32 a; uint8 b; }
        """)
    outer, inner = ir["structs"]
    assert (inner["total_size"], inner["alignment"]) == (8, 4)
    assert offsets(outer) == [("c", 0), ("i", 4), ("d", 12)]
    assert (outer["total_size"], outer["alignment"]) == (16, 4)


def test_empty_struct():
    ir = compile_source("library t;\nstruct E {}")
    assert (ir["structs"][0]["total_size"], ir["structs"][0]["alignment"]) == (0, 1)


def test_recursive_struct():
    error = compile_error("""
        library t;
        struct A { B b; }
        struct B { A a; }
        """)
    assert "Recursive struct containment: A -> B -> A" in str(error)


def test_self_containing_struct():
    error = compile_error("library t;\nstruct A { A a; }")
    assert "Recursive struct containment: A -> A" in str(error)


################################################################
# Command line
################################################################

SOURCE = """\
library t;
protocol P {
    1: M(uint32 x);
}
"""


def run_main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["didlc", *argv])
    return didlc.main()


def test_main_default_output(tmp_path, monkeypatch):
    (tmp_path / "t.didl").write_text(SOURCE)
    assert run_main(monkeypatch, str(tmp_path / "t.didl")) == 0
    ir = json.loads((tmp_path / "t.dir").read_text())
    assert method(ir, "M")["number"] == 1


def test_main_output_option(tmp_path, monkeypatch):
    (tmp_path / "t.didl").write_text(SOURCE)
    assert run_main(monkeypatch, str(tmp_path / "t.didl"), "-o", str(tmp_path / "out.json")) == 0
    assert json.loads((tmp_path / "out.json").read_text())["library"] == "t"


def test_main_stdout(tmp_path, monkeypatch, capsys):
    (tmp_path / "t.didl").write_text(SOURCE)
    assert run_main(monkeypatch, str(tmp_path / "t.didl"), "-o", "-") == 0
    assert json.loads(capsys.readouterr().out)["library"] == "t"
    assert not (tmp_path / "t.dir").exists()


def test_main_compile_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bad.didl"
    path.write_text("library t;\nprotocol P {\n    0: M();\n}\n")
    assert run_main(monkeypatch, str(path)) == 1
    assert capsys.readouterr().err.startswith(f"{path}:3:5: error: Method number out of range")
    assert not (tmp_path / "bad.dir").exists()


def test_main_missing_file(tmp_path, monkeypatch, capsys):
    assert run_main(monkeypatch, str(tmp_path / "missing.didl")) == 1
    assert capsys.readouterr().err.startswith("didlc: ")
