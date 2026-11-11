# darqos
# Copyright (C) 2026 David Arnold

# Tests for the data type implementation, types/data (see
# doc/data-type.rst): a file of a type DarqOS has no implementation for,
# kept as it came, with its media type, and its entity.

import importlib.util
import pathlib
import sys
import typing

import pytest

import darq
import darq.items as items
import darq.kb as model
from darq.protocols.data import CloseRequest, GetRequest, StatRequest
from darq.protocols.type import (
    CreateFromRequest,
    CreateRequest,
    DestroyRequest,
    ExportRequest,
    IndexEntryRequest,
    OpenEntityRequest,
    OpenRequest,
    Status,
)

from fake_kb import FakeKB, file_name, rename
from test_text_type import Caller, FakePublisher, FakeStorage, fake_ports, handles  # noqa: F401


DATA_PATH = pathlib.Path(__file__).parent.parent / "types" / "data"


def load():
    sys.path.insert(0, str(DATA_PATH))
    try:
        spec = importlib.util.spec_from_file_location("data_type", DATA_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(DATA_PATH))
    return module


data_module = load()

IMPLEMENTATION_PORT = 50
ZIP = b"PK\x03\x04 not really a zip \x00\xff"


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
    return data_module.DataImplementation(blobs, index, typing.cast(typing.Any, kb))


@pytest.fixture
def server(implementation, fake_ports, handles):  # noqa: F811
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


@pytest.fixture
def call(fake_ports, server):  # noqa: F811
    return Caller(fake_ports, server)


def create_from(call, data=ZIP, name="archive.zip", media_type="application/zip"):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=data_module.TYPE_ID, data=data, name=name,
                                                           media_type=media_type))
    assert response.status == Status.OK
    return response.item_id, response.item


def strings(entity: model.Entity, property_id: str) -> list[str]:
    return [v.value for v in (s.get_value() for s in entity.get_statements(property_id))
            if isinstance(v, model.StringValue)]


################################################################
# The blob
################################################################

@pytest.mark.parametrize("data, media_type, flags", [(ZIP, "application/zip", 0), (b"", "", 1),
                                                     (b"x", "text/x-weird; charset=ü", 0)])
def test_blob_round_trip(data, media_type, flags):
    assert data_module.decode_blob(data_module.encode_blob(data, media_type, flags)) == (data, media_type, flags)


@pytest.mark.parametrize("value", [b"", b"DDA", b"DPDF\x00\x01\x00\x00\x00\x00", b"DDAT\x00\x09\x00\x00\x00\x00"])
def test_what_isnt_a_blob(value):
    with pytest.raises(data_module.FormatError):
        data_module.decode_blob(value)


@pytest.mark.parametrize("given, name, wanted", [
    ("application/zip", "a.bin", "application/zip"),
    ("", "notes.csv", "text/csv"),
    ("", "a.weird-extension", data_module.OCTET_STREAM),
    ("", "", data_module.OCTET_STREAM),
])
def test_its_media_type(given, name, wanted):
    assert data_module.media_type_of(given, name) == wanted


################################################################
# Items
################################################################

def test_anything_is_kept_as_it_came(call, blobs):
    iid, port = create_from(call)
    assert iid.startswith("public.data:")
    got = call(port, GetRequest())
    assert (got.data, got.media_type) == (ZIP, "application/zip")
    stat = call(port, StatRequest())
    assert (stat.size, stat.media_type) == (len(ZIP), "application/zip")
    call(port, CloseRequest())

    exported = call(IMPLEMENTATION_PORT, ExportRequest(item_id=iid))
    assert (exported.status, exported.data, exported.mime_type) == (Status.OK, ZIP, "application/zip")


def test_an_empty_one_is_made_only_from_data(call):
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.data")).status == Status.UNSUPPORTED


@pytest.mark.parametrize("type_id, status", [("public.png", Status.NOT_FOUND), ("bad:id", Status.INVALID_ID)])
def test_only_its_type(call, type_id, status):
    assert call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=type_id, data=ZIP)).status == status


def test_its_opened_again(call):
    iid, port = create_from(call)
    call(port, CloseRequest())
    opened = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid))
    assert opened.status == Status.OK and call(opened.item, GetRequest()).data == ZIP


def test_a_destroyed_one_cant_be_opened(call, kb):
    iid, _ = create_from(call)
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND
    assert kb.entities[darq.split_item_id(iid)[1]].destroyed


################################################################
# Its entity
################################################################

def test_its_entity_says_what_it_is(call, kb, index):
    iid, _ = create_from(call, name="Quarterly figures.xlsx",
                         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    entity = kb.get(darq.split_item_id(iid)[1])
    assert entity.get_label() == "Quarterly figures" and file_name(entity) == "Quarterly figures.xlsx"
    assert strings(entity, items.MEDIA_TYPE) == ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]
    assert strings(entity, items.FILE_EXTENSION) == ["xlsx"]
    assert model.recorded_type(entity) == "public.data"
    assert index.names[-1] == ["Quarterly figures"] and index.sent[-1] == ("publish", iid, "")


def test_one_with_no_name_has_no_extension(call, kb):
    iid, _ = create_from(call, name="", media_type="")
    entity = kb.get(darq.split_item_id(iid)[1])
    assert entity.labels == {} and strings(entity, items.FILE_EXTENSION) == []
    assert strings(entity, items.MEDIA_TYPE) == ["application/octet-stream"]


def test_renaming_it_publishes_its_new_name(call, kb, index, implementation):
    iid, _ = create_from(call)
    rename(call, implementation, IMPLEMENTATION_PORT, iid, "Backups, March")
    assert index.names[-1] == ["Backups, March"]
    assert call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=iid)).names == ["Backups, March"]


def test_an_old_one_is_given_its_entity(call, kb):
    kb.failing = darq.DarqError("no KB")
    iid, _ = create_from(call)
    kb.failing = None
    assert call(IMPLEMENTATION_PORT, OpenEntityRequest(item_id=iid)).status == Status.OK
    assert strings(kb.get(darq.split_item_id(iid)[1]), items.MEDIA_TYPE) == ["application/zip"]


def test_viewing_is_recorded(call, server, fake_ports, recorded):  # noqa: F811
    from test_text_type import send
    from darq.protocols.data import Viewed
    iid, port = create_from(call)
    send(fake_ports, server, port, Viewed(source="entity-lens", note=""))
    assert recorded.events == [(iid, "created", "data-type", ""), (iid, "viewed", "entity-lens", "")]
