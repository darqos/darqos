# darqos
# Copyright (C) 2026 David Arnold

# Tests for the PDF type implementation, types/pdf: reading PDFs
# (pdfdoc.py), on small PDFs made for them; the handlers (main.py),
# through a Server with faked ports and Storage; and then the whole
# system, through a real p-kernel (see test_pkernel.py for the fixtures).

import importlib.util
import pathlib
import typing
import sys

import pytest

import darq
from darq.protocols import storage
from darq.protocols.pdf import CloseRequest, GetRequest, StatRequest
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
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_text_type import CLIENT, Caller, FakeHandles, FakePublisher, FakeStorage, fake_ports, handles  # noqa: F401


PDF_PATH = pathlib.Path(__file__).parent.parent / "types" / "pdf"


def load():
    # Its modules import each other by name, as they do run from there.
    sys.path.insert(0, str(PDF_PATH))
    try:
        spec = importlib.util.spec_from_file_location("pdf_type", PDF_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PDF_PATH))
    return module


pdf_module = load()
pdfdoc = pdf_module.pdfdoc

IMPLEMENTATION_PORT = 50


def make_pdf(pages=("Hello",), title="", author="", outline=(), protected=False) -> bytes:
    """Make a PDF: a page for each string, with an outline of (title,
    page) entries."""
    objects: dict[int, bytes] = {1: b"", 2: b"", 3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    number = 4
    kids = []
    for text in pages:
        content = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
        objects[number] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
        objects[number + 1] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                               b"/Resources << /Font << /F1 3 0 R >> >> >>" % number)
        kids.append(number + 1)
        number += 2
    refs = " ".join(f"{kid} 0 R" for kid in kids).encode()
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids))

    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if outline:
        root = number
        entries = list(range(root + 1, root + 1 + len(outline)))
        objects[root] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" \
            % (entries[0], entries[-1], len(entries))
        for n, (name, page) in zip(entries, outline):
            links = b""
            if n != entries[0]:
                links += b" /Prev %d 0 R" % (n - 1)
            if n != entries[-1]:
                links += b" /Next %d 0 R" % (n + 1)
            objects[n] = b"<< /Title (%s) /Parent %d 0 R /Dest [%d 0 R /Fit]%s >>" \
                % (name.encode(), root, kids[page], links)
        catalog += b" /Outlines %d 0 R" % root
        number = entries[-1] + 1
    objects[1] = catalog + b" >>"

    trailer = b""
    if title or author:
        objects[number] = b"<< /Title (%s) /Author (%s) >>" % (title.encode(), author.encode())
        trailer += b" /Info %d 0 R" % number
        number += 1
    if protected:
        # The standard handler's checks fail for the empty password.
        filler = b"00" * 32
        objects[number] = b"<< /Filter /Standard /V 1 /R 2 /P -4 /O <%s> /U <%s> >>" % (filler, filler)
        trailer += b" /Encrypt %d 0 R /ID [<00> <00>]" % number
        number += 1

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for n in sorted(objects):
        offsets[n] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (n, objects[n])
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % number
    for n in range(1, number):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (number, trailer, start)
    return bytes(out)


def test_a_first_version_blob_is_still_read():
    blob = pdf_module.HEADER.pack(pdf_module.MAGIC, 1, 0) + b"%PDF"
    assert pdf_module.decode_blob(blob) == (b"%PDF", 0, "")


def test_a_position_goes_in_the_blob():
    blob = pdf_module.encode_blob(b"%PDF", 0, "12")
    assert pdf_module.decode_blob(blob) == (b"%PDF", 0, "12")


# Reading PDFs


def test_inspect(qt_app):
    info = pdfdoc.inspect(make_pdf(("One", "Two", "Three"), title="A Title", author="An Author"))
    assert (info.pages, info.title, info.author, info.protected) == (3, "A Title", "An Author", False)


def test_inspect_without_details(qt_app):
    info = pdfdoc.inspect(make_pdf())
    assert (info.pages, info.title, info.author) == (1, "", "")


def test_text(qt_app):
    text = pdfdoc.text(make_pdf(("Coffee shop", "Tea room")))
    assert "Coffee shop" in text and "Tea room" in text


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-1.4\nthat's all"])
def test_not_a_pdf(qt_app, data):
    with pytest.raises(pdfdoc.PdfError):
        pdfdoc.inspect(data)
    with pytest.raises(pdfdoc.PdfError):
        pdfdoc.text(data)


def test_protected(qt_app):
    data = make_pdf(protected=True)
    assert pdfdoc.inspect(data) == pdfdoc.Info(protected=True)
    assert pdfdoc.text(data) == ""


# Blobs and identifiers


def test_blob_round_trip():
    blob = pdf_module.encode_blob(b"%PDF", pdf_module.DESTROYED)
    assert pdf_module.decode_blob(blob) == (b"%PDF", pdf_module.DESTROYED, "")


@pytest.mark.parametrize("blob", [b"", b"DPD", b"XXXX\x00\x01\x00\x00", b"DPDF\x00\x09\x00\x00"])
def test_bad_blob(blob):
    with pytest.raises(pdf_module.FormatError):
        pdf_module.decode_blob(blob)


def test_item_id_round_trip():
    key = storage.Key(0x0123456789abcdef, 0xfedcba9876543210)
    iid = pdf_module.item_id(key)
    assert iid == "com.adobe.pdf:0123456789abcdeffedcba9876543210"
    assert pdf_module.parse_item_id(iid) == key
    assert pdf_module.parse_item_id("public.png:0123456789abcdeffedcba9876543210") is None
    assert pdf_module.parse_item_id("com.adobe.pdf:nothex") is None


# The handlers


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
    return pdf_module.PdfImplementation(blobs, index, typing.cast(typing.Any, kb))


@pytest.fixture
def server(implementation, fake_ports, handles):  # noqa: F811
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


@pytest.fixture
def call(fake_ports, server):  # noqa: F811
    return Caller(fake_ports, server)


def create_from(call, data):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=pdf_module.TYPE_ID, data=data))
    assert response.status == Status.OK
    return response.item_id, response.item


def test_create_is_unsupported(call, blobs, qt_app):
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id=pdf_module.TYPE_ID)).status == Status.UNSUPPORTED
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.png")).status == Status.NOT_FOUND
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id="bad:id")).status == Status.INVALID_ID
    assert blobs.blobs == {}


def test_create_from(call, blobs, index, qt_app):
    data = make_pdf(("One", "Two"), title="A Title", author="An Author")
    iid, port = create_from(call, data)
    assert call(port, GetRequest()).data == data
    stat = call(port, StatRequest())
    assert (stat.size, stat.pages) == (len(data), 2)

    assert [pdf_module.decode_blob(value) for value, _ in blobs.blobs.values()] == [(data, 0, "")]
    assert index.names == [["A Title", "An Author"]]
    action, published, body = index.sent[0]
    assert (action, published) == ("publish", iid) and "One" in body and "Two" in body


@pytest.mark.parametrize("type_id, data, status", [
    ("com.adobe.pdf", b"not a pdf", Status.INVALID_DATA),
    ("public.png", make_pdf(), Status.NOT_FOUND),
    ("bad:id", make_pdf(), Status.INVALID_ID),
])
def test_create_from_refused(call, blobs, qt_app, type_id, data, status):
    assert call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=type_id, data=data)).status == status
    assert blobs.blobs == {}


def test_protected_is_accepted(call, index, qt_app):
    data = make_pdf(protected=True)
    _, port = create_from(call, data)
    assert call(port, StatRequest()).pages == 0
    assert call(port, GetRequest()).data == data
    assert index.sent[0][2] == ""


def test_index_entry(call, qt_app):
    iid, _ = create_from(call, make_pdf(("Coffee shop",), title="A Title"))
    response = call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=iid))
    assert response.status == Status.OK
    assert response.names == ["A Title"] and "Coffee shop" in response.body


def test_export(call, qt_app):
    data = make_pdf()
    iid, _ = create_from(call, data)
    response = call(IMPLEMENTATION_PORT, ExportRequest(item_id=iid))
    assert (response.status, response.data, response.mime_type) == (Status.OK, data, "application/pdf")


def test_close_reopen_and_destroy(call, blobs, implementation, index, qt_app):
    data = make_pdf()
    iid, port = create_from(call, data)
    call(port, CloseRequest())
    assert implementation.documents == {}

    response = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid))
    assert response.status == Status.OK
    assert call(response.item, GetRequest()).data == data

    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert index.sent[-1] == ("withdraw", iid)

    # An open client goes on, but it can't be opened again.
    assert call(response.item, GetRequest()).data == data
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND
    call(response.item, CloseRequest())

    assert [pdf_module.decode_blob(value) for value, _ in blobs.blobs.values()] == [(data, pdf_module.DESTROYED, "")]
    for request in (OpenRequest(item_id=iid), IndexEntryRequest(item_id=iid), ExportRequest(item_id=iid),
                    DestroyRequest(item_id=iid)):
        assert call(IMPLEMENTATION_PORT, request).status == Status.NOT_FOUND


@pytest.mark.parametrize("iid, status", [
    (f"com.adobe.pdf:{'0' * 31}9", Status.NOT_FOUND),
    (f"public.png:{'0' * 32}", Status.NOT_FOUND),
    ("com.adobe.pdf:nothex", Status.NOT_FOUND),
    ("bad", Status.INVALID_ID),
])
def test_open_not_found(call, qt_app, iid, status):
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == status


def test_corrupt_blob_fails(call, blobs, qt_app):
    iid, port = create_from(call, make_pdf())
    call(port, CloseRequest())
    for key in blobs.blobs:
        blobs.blobs[key] = (b"rubbish", 1)
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.FAILED


# The whole system


def test_pdf_type(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # A client that finds the type for a .pdf, creates an item from a
    # PDF, reads it, and exports it.
    data_path = tmp_path / "test.pdf"
    data_path.write_bytes(make_pdf(("One", "Two"), title="A Title"))
    client_path = write_app(tmp_path / "client", f"""
        import pathlib
        import time
        import darq
        import darq.launch
        from darq.protocols import pdf as wire
        from darq.protocols.type import Type

        def darq_main():
            data = pathlib.Path({str(data_path)!r}).read_bytes()
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="com.adobe.pdf").implementation:
                if time.monotonic() > deadline:
                    return "com.adobe.pdf never registered"
                time.sleep(0.05)

            found = darq.launch.find_types(extension="pdf")
            if found != ["com.adobe.pdf"]:
                return f"found {{found!r}}"
            if darq.launch.find_types(mime_type="application/pdf") != ["com.adobe.pdf"]:
                return "application/pdf not found"

            try:
                darq.launch.create_item_from("com.adobe.pdf", b"not a pdf")
                return "rubbish was taken as a PDF"
            except darq.LaunchError:
                pass
            item_id, port = darq.launch.create_item_from("com.adobe.pdf", data)
            item = wire.Pdf.Client(port, timeout=30)
            if item.stat().pages != 2 or item.get().data != data:
                return "read back wrong"
            item.close()

            exported, mime_type = darq.launch.export_item(item_id)
            if mime_type != "application/pdf" or exported != data:
                return f"exported {{mime_type}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


################################################################
# Its entity (see doc/entity-vs-item.rst)
################################################################

def test_a_new_pdf_is_named_by_its_title(call, kb, qt_app):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=pdf_module.TYPE_ID,
                                                           data=make_pdf(title="A Title"), name="scan0001.pdf"))
    entity = kb.get(darq.split_item_id(response.item_id)[1])
    assert entity.get_label() == "A Title" and file_name(entity) == "scan0001.pdf"


def test_one_without_a_title_is_named_from_its_file(call, kb, qt_app):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=pdf_module.TYPE_ID, data=make_pdf(),
                                                           name="Tax return 2025.pdf"))
    assert kb.label(darq.split_item_id(response.item_id)[1]) == "Tax return 2025"


def test_renaming_a_pdf_publishes_its_new_name_with_its_own(call, kb, index, implementation, qt_app):
    iid, _ = create_from(call, make_pdf(("One",), title="A Title", author="An Author"))
    rename(call, implementation, IMPLEMENTATION_PORT, iid, "My Thesis")
    assert index.names[-1] == ["My Thesis", "A Title", "An Author"] and "One" in index.sent[-1][2]


def test_destroying_a_pdf_destroys_its_entity(call, kb, qt_app):
    iid, _ = create_from(call, make_pdf())
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert kb.entities[darq.split_item_id(iid)[1]].destroyed


def test_an_old_pdf_is_given_its_entity_named_by_its_title(call, kb, qt_app):
    kb.failing = darq.DarqError("no KB")
    iid, _ = create_from(call, make_pdf(title="A Title"))
    kb.failing = None
    assert call(IMPLEMENTATION_PORT, OpenEntityRequest(item_id=iid)).status == Status.OK
    assert kb.label(darq.split_item_id(iid)[1]) == "A Title"
