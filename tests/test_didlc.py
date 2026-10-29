# darqos
# Copyright (C) 2026 David Arnold

# Tests for the DIDL compiler, bin/didlc.

import importlib.machinery
import importlib.util
import json
import pathlib
import textwrap
import typing

import pytest


DIDLC_PATH = pathlib.Path(__file__).parent.parent / "bin" / "didlc"


def load_didlc():
    """Import bin/didlc as a module (it has no .py extension)."""
    loader = importlib.machinery.SourceFileLoader("didlc", str(DIDLC_PATH))
    spec = importlib.util.spec_from_loader("didlc", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


didlc = load_didlc()


def compile_source(source: str) -> dict:
    return didlc.compile_ir(didlc.Parser(didlc.lex(textwrap.dedent(source))).parse())


def compile_error(source: str) -> typing.Any:
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

def test_ir_version():
    ir = compile_source("library t;")
    assert ir["version"] == didlc.IR_VERSION
    assert list(ir)[0] == "version"


def test_no_message_header_in_ir():
    # The header is fixed, and defined by darq.dipc.
    assert "message_header" not in compile_source("library t;")


@pytest.mark.parametrize("type_, size, align", [
    ("bool", 1, 1),
    ("int8", 1, 1), ("uint8", 1, 1),
    ("int16", 2, 2), ("uint16", 2, 2),
    ("int32", 4, 4), ("uint32", 4, 4),
    ("int64", 8, 8), ("uint64", 8, 8),
    ("real32", 4, 4), ("real64", 8, 8),
    ("opaque", 8, 4),
    ("string", 8, 4),
    ("sequence<uint8>", 8, 4),
    ("port", 2, 2),
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
        ("a", 0), ("b", 2), ("c", 4), ("d", 8), ("e", 16), ("f", 24), ("g", 28), ("h", 36)]
    assert foo["request"]["total_size"] == 40


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
# Sequences
################################################################

def test_sequence_element_in_ir():
    layout = request_layout("sequence<uint16> xs, string s")
    assert layout["fields"][0] == {
        "name": "xs", "type": "sequence", "offset": 0, "size": 8, "alignment": 4,
        "element": {"type": "uint16", "size": 2, "alignment": 2}}
    assert "element" not in layout["fields"][1]


def test_nested_sequence_of_structs():
    layout = request_layout("sequence<sequence<S>> deep", "struct S { uint8 a; uint64 b; }")
    assert layout["fields"][0]["element"] == {
        "type": "sequence", "size": 8, "alignment": 4,
        "element": {"type": "S", "size": 16, "alignment": 8}}


def test_sequence_of_enum():
    layout = request_layout("sequence<E> es", "enum E : uint32 { A = 0; }")
    assert layout["fields"][0]["element"] == {"type": "E", "size": 4, "alignment": 4}


def test_sequence_in_struct():
    ir = compile_source("library t;\nstruct S { uint8 x; sequence<string> names; }")
    assert offsets(ir["structs"][0]) == [("x", 0), ("names", 4)]
    assert ir["structs"][0]["fields"][1]["element"]["type"] == "string"


@pytest.mark.parametrize("source, message, position", [
    ("protocol P { 1: M(sequence<float32> x); }", "Unknown type: float32", (2, 28)),
    ("protocol P { 1: M(sequence x); }", "Expected LT", (2, 28)),
    ("protocol P { 1: M(sequence<uint8 x); }", "Expected GT", (2, 34)),
    ("struct E {}\nprotocol P { 1: M(sequence<E> x); }", "Sequence element type E is empty", (3, 28)),
    ("struct A { sequence<A> children; }", "Recursive struct containment: A -> A", (2, 21)),
    ("struct string {}", "struct string has the name of a primitive type", (2, 8)),
    ("struct sequence {}", "struct sequence has the name of a primitive type", (2, 8)),
])
def test_sequence_errors(source, message, position):
    error = compile_error("library t;\n" + source)
    assert message in str(error)
    assert (error.line, error.col) == position


################################################################
# Enums
################################################################

def test_enum_members():
    ir = compile_source("library t;\nenum E : int8 { A = -128; B = 0x10; C = 127; }")
    assert ir["enums"] == [{"name": "E", "type": "int8", "members": [
        {"name": "A", "value": -128}, {"name": "B", "value": 16}, {"name": "C", "value": 127}]}]


def test_enum_field_layout():
    # An enum field is laid out as its base type.
    ir = compile_source("""
        library t;
        protocol P { 1: M(uint8 a, Wide w, Narrow n); }
        enum Wide : uint64 { X = 1; }
        enum Narrow : uint8 { Y = 1; }
        """)
    layout = method(ir, "M")["request"]
    assert offsets(layout) == [("a", 0), ("w", 8), ("n", 16)]
    assert [f["type"] for f in layout["fields"]] == ["uint8", "Wide", "Narrow"]
    assert (layout["total_size"], layout["alignment"]) == (24, 8)


def test_enum_in_struct():
    ir = compile_source("library t;\nenum E : uint32 { A = 0; }\nstruct S { uint8 x; E e; }")
    assert offsets(ir["structs"][0]) == [("x", 0), ("e", 4)]


@pytest.mark.parametrize("source, message", [
    ("enum E : real32 { A = 0; }", "must be based on an integer type, not real32"),
    ("enum E : bool { A = 0; }", "must be based on an integer type, not bool"),
    ("enum E : uint32 { }", "enum E has no members"),
    ("enum E : uint32 { A = 0; A = 1; }", "Duplicate member: E.A"),
    ("enum E : uint32 { A = 0; B = 0; }", "Duplicate value 0: E.B and E.A"),
    ("enum E : uint8 { A = 256; }", "Value out of range for uint8: E.A is 256"),
    ("enum E : uint8 { A = -1; }", "Value out of range for uint8: E.A is -1"),
    ("enum E : int8 { A = -129; }", "Value out of range for int8: E.A is -129"),
    ("enum uint32 : uint32 { A = 0; }", "enum uint32 has the name of a primitive type"),
    ("enum E : uint32 { A = 0; }\nstruct E {}", "struct E has the same name as enum E"),
    ("enum E : uint32 { A; }", "Expected EQUALS"),
    ("enum E { A = 0; }", "Expected COLON"),
])
def test_enum_errors(source, message):
    assert message in str(compile_error("library t;\n" + source))


def test_negative_method_number_out_of_range():
    assert "out of range" in str(compile_error("library t;\nprotocol P { -1: M(); }"))


def test_hex_method_number():
    assert method(compile_source("library t;\nprotocol P { 0x10: M(); }"), "M")["number"] == 16


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


################################################################
# Names
################################################################

@pytest.mark.parametrize("source, message, position", [
    # One library, and only its name may have dots.
    ("struct S {}", "No library declaration", (1, 12)),
    ("library a;\nlibrary b;", "Duplicate library declaration: b", (2, 9)),
    ("library t;\nstruct a.b {}", "Struct name a.b may not contain dots", (2, 8)),
    ("library t;\nenum a.E : uint8 { A = 0; }", "Enum name a.E may not contain dots", (2, 6)),
    ("library t;\nenum E : uint8 { A.B = 0; }", "Member name A.B may not contain dots", (2, 18)),
    ("library t;\nstruct S { uint8 a.b; }", "Field name a.b may not contain dots", (2, 18)),
    ("library t;\nprotocol a.P {}", "Protocol name a.P may not contain dots", (2, 10)),
    ("library t;\nprotocol P { 1: a.M(); }", "Method name a.M may not contain dots", (2, 17)),
    ("library t;\nprotocol P { 1: M(uint8 a.b); }", "Field name a.b may not contain dots", (2, 25)),

    # Fields are unique within their struct, or request, or response.
    ("library t;\nstruct S { uint8 x; uint16 x; }", "Duplicate field in S: x", (2, 28)),
    ("library t;\nprotocol P { 1: M(uint8 x, uint8 x); }", "Duplicate field in P.M request: x", (2, 34)),
    ("library t;\nprotocol P { 1: M() -> (uint8 y, bool y); }", "Duplicate field in P.M response: y", (2, 39)),

    # A message's fields can't have its header's names.
    ("library t;\nprotocol P { 1: M(uint32 txid); }",
     "P.M request field txid has the name of a message header field", (2, 26)),
    ("library t;\nprotocol P { 1: M() -> (uint32 txid); }",
     "P.M response field txid has the name of a message header field", (2, 32)),

    # Protocols share the type names' namespace.
    ("library t;\nprotocol P {}\nprotocol P {}", "protocol P has the same name as protocol P", (3, 10)),
    ("library t;\nenum Status : uint8 { OK = 0; }\nprotocol Status {}",
     "protocol Status has the same name as enum Status", (3, 10)),
    ("library t;\nstruct Key {}\nprotocol Key {}", "protocol Key has the same name as struct Key", (3, 10)),
    ("library t;\nprotocol string {}", "protocol string has the name of a primitive type", (2, 10)),
])
def test_name_errors(source, message, position):
    error = compile_error(source)
    assert message in str(error)
    assert (error.line, error.col) == position


def test_names_allowed():
    ir = compile_source("""
        library darq.example.names;
        struct S { uint32 txid; }
        protocol P {
            1: Get(uint8 key) -> (uint8 key);
        }
        protocol Q {
            1: Put(uint8 key);
        }
        """)
    # A struct's field may be txid; a request and its response may
    # share a name, as may different methods' fields.
    assert ir["library"] == "darq.example.names"
    assert [f["name"] for f in ir["structs"][0]["fields"]] == ["txid"]
    assert [m["name"] for p in ir["protocols"] for m in p["methods"]] == ["Get", "Put"]
