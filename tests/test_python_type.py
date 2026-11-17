# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Python package type implementation, types/python (see
# doc/python-type.rst): a package, a zip of modules, kept in a blob,
# named as it's imported, and replaced only by the generation it was.

import importlib.util
import pathlib
import sys
import typing

import pytest

import darq
import darq.kb as model
import darq.packages as packages
from darq.protocols.python import CloseRequest, GetRequest, PutRequest, Viewed
from darq.protocols.python import Status as PythonStatus
from darq.protocols.type import (
    CreateFromRequest,
    CreateRequest,
    DestroyRequest,
    ExportRequest,
    IndexEntryRequest,
    OpenRequest,
    Status,
)

from fake_kb import FakeKB, rename
from test_text_type import Caller, FakePublisher, FakeStorage, fake_ports, handles, send  # noqa: F401


PYTHON_PATH = pathlib.Path(__file__).parent.parent / "types" / "python"


def load():
    sys.path.insert(0, str(PYTHON_PATH))
    try:
        spec = importlib.util.spec_from_file_location("python_type", PYTHON_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PYTHON_PATH))
    return module


python_module = load()

IMPLEMENTATION_PORT = 50
TIDY = packages.make({"main.py": "def darq_main():\n    return 0\n"})


@pytest.fixture
def blobs():
    return FakeStorage()


@pytest.fixture
def index():
    return FakePublisher()


@pytest.fixture
def kb():
    return FakeKB()


@pytest.fixture
def implementation(blobs, index, kb):
    return python_module.PythonImplementation(blobs, index, typing.cast(typing.Any, kb))


@pytest.fixture
def server(implementation, fake_ports, handles):  # noqa: F811
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


@pytest.fixture
def call(fake_ports, server):  # noqa: F811
    return Caller(fake_ports, server)


def create_from(call, data=TIDY, name="tidy"):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=packages.TYPE_ID, data=data, name=name))
    assert response.status == Status.OK
    return response.item_id, response.item


################################################################
# The blob, and the package
################################################################

@pytest.mark.parametrize("data, flags", [(TIDY, 0), (b"", 1)])
def test_blob_round_trip(data, flags):
    assert python_module.decode_blob(python_module.encode_blob(data, flags)) == (data, flags)


@pytest.mark.parametrize("value", [b"", b"DPY", b"DDAT\x00\x01\x00\x00", b"DPYP\x00\x09\x00\x00"])
def test_what_isnt_a_blob(value):
    with pytest.raises(python_module.FormatError):
        python_module.decode_blob(value)


def test_a_package_is_its_modules():
    assert packages.modules(TIDY) == {"__init__.py": "", "main.py": "def darq_main():\n    return 0\n"}


def zipped(**members: bytes) -> bytes:
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as made:
        for name, data in members.items():
            made.writestr(name.replace("__slash__", "/").replace("__dots__", ".."), data)
    return buffer.getvalue()


@pytest.mark.parametrize("data, why", [
    (b"not a zip", "isn't a zip"),
    (zipped(**{"main.py": b""}), "has no __init__.py"),
    (zipped(**{"__init__.py": b"", "__dots____slash__escape.py": b""}), "isn't a relative path"),
    (zipped(**{"__init__.py": b"", "bad.py": b"\xff\xfe"}), "isn't UTF-8"),
])
def test_what_isnt_a_package(data, why):
    with pytest.raises(packages.PackageError, match=why):
        packages.check(data)


@pytest.mark.parametrize("name, ok", [("tidy", True), ("photo_tools", True), ("2fast", False), ("class", False),
                                      ("a.b", False), ("", False)])
def test_what_can_be_a_name(name, ok):
    assert packages.is_name(name) == ok


################################################################
# Items
################################################################

def test_its_kept_named_and_read(call, kb, index):
    iid, port = create_from(call)
    assert iid.startswith("darq.python:")
    got = call(port, GetRequest())
    assert (got.package, got.generation) == (TIDY, 1)
    entity = kb.get(darq.split_item_id(iid)[1])
    assert entity.get_label() == "tidy" and model.recorded_type(entity) == "darq.python"
    assert index.names[-1] == ["tidy"]
    exported = call(IMPLEMENTATION_PORT, ExportRequest(item_id=iid))
    assert (exported.data, exported.mime_type) == (TIDY, "application/vnd.darq.python+zip")


def test_a_new_one_is_empty_and_unnamed(call, kb):
    response = call(IMPLEMENTATION_PORT, CreateRequest(type_id="darq.python"))
    assert response.status == Status.OK
    assert packages.modules(call(response.item, GetRequest()).package) == {"__init__.py": ""}
    assert kb.get(darq.split_item_id(response.item_id)[1]).labels == {}


def test_what_isnt_a_package_isnt_made(call):
    assert call(IMPLEMENTATION_PORT, CreateFromRequest(type_id="darq.python", data=b"junk")).status == Status.FAILED


def test_its_replaced_by_the_generation_it_was(call, recorded):
    iid, port = create_from(call)
    changed = packages.make({"main.py": "def darq_main():\n    return 1\n"})
    put = call(port, PutRequest(expected=1, package=changed, source="repl"))
    assert (put.status, put.generation) == (PythonStatus.OK, 2)
    assert call(port, GetRequest()).package == changed
    assert recorded.events[-1] == (iid, "edited", "repl", "")

    stale = call(port, PutRequest(expected=1, package=TIDY, source="repl"))
    assert (stale.status, stale.generation) == (PythonStatus.CONFLICT, 2)
    invalid = call(port, PutRequest(expected=2, package=b"junk", source="repl"))
    assert invalid.status == PythonStatus.INVALID and "isn't a zip" in invalid.message

    # Another client, opened since, has it as it is.
    call(port, CloseRequest())
    opened = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid))
    assert call(opened.item, GetRequest()).package == changed


def test_a_destroyed_one_cant_be_opened(call, kb):
    iid, _ = create_from(call)
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND
    assert kb.entities[darq.split_item_id(iid)[1]].destroyed


def test_renaming_it_publishes_its_new_name(call, index, implementation):
    iid, _ = create_from(call)
    rename(call, implementation, IMPLEMENTATION_PORT, iid, "tidier")
    assert index.names[-1] == ["tidier"]
    assert call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=iid)).names == ["tidier"]


def test_viewing_is_recorded(call, server, fake_ports, recorded):  # noqa: F811
    iid, port = create_from(call)
    send(fake_ports, server, port, Viewed(source="repl", note=""))
    assert recorded.events == [(iid, "created", "python-type", ""), (iid, "viewed", "repl", "")]
