# darqos
# Copyright (C) 2026 David Arnold

# Tests for the e-book type, types/ebook (see doc/book.rst): EPUB files,
# made here; Mobipocket files, made here too, if libmobi's installed; the
# item protocol, with Storage and the Index faked; and then the whole
# system, through a real p-kernel.

import importlib
import importlib.util
import io
import pathlib
import typing
import struct
import sys
import zipfile

import pytest

import darq
import darq.kb as model
from darq.protocols.ebook import (
    CloseRequest,
    ContentsRequest,
    GetPartRequest,
    GetPositionRequest,
    GetRequest,
    SetPositionRequest,
)
from darq.protocols.ebook import Status as EbookStatus
from darq.protocols.type import (
    CreateFromRequest,
    CreateRequest,
    DestroyRequest,
    ExportRequest,
    IndexEntryRequest,
    OpenRequest,
    Status,
)

from fake_kb import FakeKB, file_name, rename
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_text_type import Caller, FakePublisher, FakeStorage, fake_ports, handles  # noqa: F401


EBOOK_PATH = pathlib.Path(__file__).parent.parent / "types" / "ebook"


def load():
    # Its modules import each other by name, as they do run from there.
    sys.path.insert(0, str(EBOOK_PATH))
    try:
        modules = [importlib.import_module(name) for name in ("bookfile", "mobifile")]
        spec = importlib.util.spec_from_file_location("ebook_type", EBOOK_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(EBOOK_PATH))
    return (*modules, module)


bookfile, mobifile, ebook_module = load()

IMPLEMENTATION_PORT = 50
EPUB = "darq.book.epub"
AZW = "darq.book.azw"

CONTAINER = b"""<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

PACKAGE = b"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>A  Book</dc:title><dc:creator>Ada Lovelace</dc:creator><dc:creator>Charles Babbage</dc:creator>
<dc:identifier id="id">urn:isbn:9780596007126</dc:identifier></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="one" href="text/one%20a.xhtml" media-type="application/xhtml+xml"/>
<item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
<item id="gone" href="text/gone.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="one"/><itemref idref="two"/><itemref idref="gone"/><itemref idref="nope"/></spine>
</package>"""

NAV = b"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="landmarks"><ol><li><a href="text/two.xhtml">Not this</a></li></ol></nav>
<nav epub:type="toc"><ol><li><a href="text/one%20a.xhtml">One</a><ol>
<li><a href="text/one%20a.xhtml#sec">  Section  </a></li></ol></li>
<li><a href="text/two.xhtml">Two</a></li></ol></nav></body></html>"""

NCX = b"""<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
<navPoint id="a"><navLabel><text>From NCX</text></navLabel><content src="text/two.xhtml"/>
<navPoint id="b"><navLabel><text>Inner</text></navLabel><content src="text/two.xhtml#x"/></navPoint></navPoint>
</navMap></ncx>"""


def make_epub(*, nav: bool = True, encryption: bytes | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    files = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": CONTAINER,
        "OEBPS/content.opf": PACKAGE if nav else PACKAGE.replace(b' properties="nav"', b""),
        "OEBPS/nav.xhtml": NAV,
        "OEBPS/toc.ncx": NCX,
        "OEBPS/text/one a.xhtml": b"<html><body><p>The first words.</p><script>no()</script></body></html>",
        "OEBPS/text/two.xhtml": b"<html><body><p>The second &amp; last.</p></body></html>",
        "OEBPS/style.css": b"p { color: red }",
    } | (extra or {})
    if encryption is not None:
        files["META-INF/encryption.xml"] = encryption
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def encryption_of(algorithm: str) -> bytes:
    return (f'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            f'<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod Algorithm="{algorithm}"/>'
            f"</EncryptedData></encryption>").encode()


def make_mobi(text: bytes = b"<html><body><p>Hello</p><mbp:pagebreak/><p>World</p></body></html>",
              encryption: int = 0, title: bytes = b"A Mobi") -> bytes:
    """Make a Mobipocket file: a header record, and uncompressed text."""
    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), 1, 4096, encryption, 0)
    header = bytearray(b"\xff" * 232)
    header[:4] = b"MOBI"
    struct.pack_into(">IIII", header, 4, 232, 2, 65001, 12345)
    struct.pack_into(">I", header, 20, 6)
    struct.pack_into(">I", header, 72, 2)
    struct.pack_into(">II", header, 68, 248 + 16 - 16 + 0, len(title))
    struct.pack_into(">I", header, 128, 0)
    struct.pack_into(">I", header, 228 - 4, 0)
    record0 = palmdoc + bytes(header) + title + b"\x00" * 16
    records = [record0, text, b"\xe9\x8e\r\n"]
    start = 78 + 8 * len(records) + 2
    head = bytearray(78)
    head[:7] = b"A_Mobi\x00"
    head[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", head, 76, len(records))
    listing = b""
    offset = start
    for n, record in enumerate(records):
        listing += struct.pack(">II", offset, n * 2)
        offset += len(record)
    return bytes(head) + listing + b"\x00\x00" + b"".join(records)


def has_libmobi() -> bool:
    try:
        mobifile.library()
    except bookfile.Unreadable:
        return False
    return True


needs_libmobi = pytest.mark.skipif(not has_libmobi(), reason="libmobi isn't installed")


################################################################
# EPUB
################################################################

def test_an_epub():
    book = bookfile.open_book(EPUB, make_epub())
    assert (book.title, book.authors, book.isbn) == ("A Book", ["Ada Lovelace", "Charles Babbage"], "9780596007126")


def test_an_epubs_details():
    extra = PACKAGE.replace(b"<dc:title>", b"<dc:publisher>O'Reilly</dc:publisher>"
                            b"<dc:date>2009-08-01T00:00:00Z</dc:date>"
                            b"<dc:language>en-AU</dc:language><dc:title>")
    book = bookfile.open_book(EPUB, make_epub(extra={"OEBPS/content.opf": extra}))
    assert (book.publisher, book.date, book.language) == ("O'Reilly", "2009-08-01", "English")


def test_an_epub_3_cover():
    package = PACKAGE.replace(b'<item id="css" href="style.css" media-type="text/css"/>',
                              b'<item id="css" href="style.css" media-type="text/css"/>'
                              b'<item id="c" href="c.jpg" media-type="image/jpeg" properties="cover-image"/>')
    book = bookfile.open_book(EPUB, make_epub(extra={"OEBPS/content.opf": package, "OEBPS/c.jpg": b"jpeg"}))
    assert book.cover == "OEBPS/c.jpg"


def test_an_epub_2_cover():
    package = PACKAGE.replace(b'<item id="css" href="style.css" media-type="text/css"/>',
                              b'<item id="css" href="style.css" media-type="text/css"/>'
                              b'<item id="c" href="c.png" media-type="image/png"/>').replace(
        b"</metadata>", b'<meta name="cover" content="c"/></metadata>')
    book = bookfile.open_book(EPUB, make_epub(extra={"OEBPS/content.opf": package, "OEBPS/c.png": b"png"}))
    assert book.cover == "OEBPS/c.png"


def test_a_cover_thats_not_an_image_isnt_one():
    package = PACKAGE.replace(b"</metadata>", b'<meta name="cover" content="css"/></metadata>')
    assert bookfile.open_book(EPUB, make_epub(extra={"OEBPS/content.opf": package})).cover == ""


def test_parts_are_in_reading_order_and_exist():
    book = bookfile.open_book(EPUB, make_epub())
    assert book.parts == ["OEBPS/text/one a.xhtml", "OEBPS/text/two.xhtml"]


def test_contents_come_from_the_nav():
    book = bookfile.open_book(EPUB, make_epub())
    assert [(e.title, e.target, e.level) for e in book.contents] == [
        ("One", "OEBPS/text/one a.xhtml", 0), ("Section", "OEBPS/text/one a.xhtml#sec", 1),
        ("Two", "OEBPS/text/two.xhtml", 0)]


def test_contents_fall_back_on_the_ncx():
    book = bookfile.open_book(EPUB, make_epub(nav=False))
    assert [(e.title, e.target, e.level) for e in book.contents] == [
        ("From NCX", "OEBPS/text/two.xhtml", 0), ("Inner", "OEBPS/text/two.xhtml#x", 1)]


def test_getting_what_a_book_holds():
    book = bookfile.open_book(EPUB, make_epub())
    assert book.get("OEBPS/style.css") == ("text/css", b"p { color: red }")
    assert book.get("OEBPS/nothing") is None


@pytest.mark.parametrize("name", ["../../etc/passwd", "/etc/passwd", "OEBPS/../../x"])
def test_a_name_is_only_looked_up(name):
    assert bookfile.open_book(EPUB, make_epub()).get(name) is None


def test_text_for_the_index():
    text = bookfile.text_of(bookfile.open_book(EPUB, make_epub()))
    assert "The first words." in text and "The second & last." in text and "no()" not in text


@pytest.mark.parametrize("data", [b"", b"PK", b"not a zip", b"x" * 1000])
def test_not_an_epub(data):
    with pytest.raises(bookfile.NotABook):
        bookfile.open_book(EPUB, data)


def test_a_zip_thats_not_an_epub():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", b"Hello")
    with pytest.raises(bookfile.NotABook):
        bookfile.open_book(EPUB, buffer.getvalue())


def test_an_epub_with_drm():
    with pytest.raises(bookfile.Encrypted):
        bookfile.open_book(EPUB, make_epub(encryption=encryption_of("http://www.w3.org/2001/04/xmlenc#aes128-cbc")))


def test_obfuscated_fonts_arent_drm():
    assert bookfile.open_book(EPUB, make_epub(encryption=encryption_of("http://www.idpf.org/2008/embedding"))).parts


def test_a_broken_package_is_unreadable():
    with pytest.raises(bookfile.Unreadable):
        bookfile.open_book(EPUB, make_epub(extra={"OEBPS/content.opf": b"<not xml"}))


def test_a_zip_too_large_unpacked(monkeypatch):
    monkeypatch.setattr(bookfile, "MAX_UNPACKED", 10)
    with pytest.raises(bookfile.Unreadable):
        bookfile.open_book(EPUB, make_epub())


def test_a_part_that_cant_be_read_is_unreadable():
    book = bookfile.open_book(EPUB, make_epub())
    book.zip.close()
    with pytest.raises(bookfile.Unreadable):
        book.get("OEBPS/style.css")


################################################################
# Mobipocket
################################################################

@pytest.mark.parametrize("data", [b"", b"x" * 100, b"\x00" * 60 + b"NOTAMOBI"])
def test_not_a_mobi(data):
    with pytest.raises(bookfile.NotABook):
        bookfile.open_book(AZW, data)


def test_page_breaks():
    assert b"<mbp" not in bookfile.page_breaks(b"a<mbp:pagebreak/>b<MBP:PAGEBREAK></mbp:pagebreak>c")


def test_no_libmobi_is_unreadable(monkeypatch):
    monkeypatch.setattr(mobifile, "_library", None)
    monkeypatch.setenv("DARQ_LIBMOBI", "/nonexistent/libmobi.so")
    with pytest.raises(bookfile.Unreadable, match="isn't installed"):
        bookfile.open_book(AZW, make_mobi())


@needs_libmobi
def test_a_mobi():
    book = bookfile.open_book(AZW, make_mobi())
    assert len(book.parts) == 1
    media_type, data = book.get(book.parts[0])
    assert media_type == bookfile.HTML and b"Hello" in data


@needs_libmobi
def test_a_mobis_page_breaks_are_a_browsers():
    book = bookfile.open_book(AZW, make_mobi())
    assert b"break-before" in book.get(book.parts[0])[1]


@needs_libmobi
def test_a_mobi_with_drm():
    with pytest.raises(bookfile.Encrypted):
        bookfile.open_book(AZW, make_mobi(encryption=2))


@needs_libmobi
@pytest.mark.parametrize("keep", [70, 80, 100, 200, 300])
def test_a_damaged_mobi_is_unreadable_or_read(keep):
    """It mustn't crash, whatever's left."""
    data = make_mobi()[:keep]
    try:
        bookfile.open_book(AZW, data)
    except bookfile.Unreadable:
        pass


################################################################
# The implementation
################################################################

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
    return ebook_module.EbookImplementation(blobs, index, typing.cast(typing.Any, kb))


@pytest.fixture
def server(implementation, fake_ports, handles):  # noqa: F811
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


@pytest.fixture
def call(fake_ports, server):  # noqa: F811
    return Caller(fake_ports, server)


def create_from(call, data, type_id=EPUB):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=type_id, data=data))
    assert response.status == Status.OK
    return response.item_id, response.item


def test_blob_round_trip():
    blob = ebook_module.encode_blob(b"file", "2:0.5", ebook_module.DESTROYED)
    assert ebook_module.decode_blob(blob) == (b"file", "2:0.5", ebook_module.DESTROYED)


@pytest.mark.parametrize("blob", [b"", b"XXXX\x00\x01\x00\x00\x00\x00\x00\x00"])
def test_a_blob_thats_not_one(blob):
    with pytest.raises(ebook_module.FormatError):
        ebook_module.decode_blob(blob)


def test_create_makes_nothing(call):
    assert call(IMPLEMENTATION_PORT, CreateRequest(type_id=EPUB)).status == Status.UNSUPPORTED


def test_create_from_refuses_other_types(call):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id="com.adobe.pdf", data=make_epub()))
    assert response.status == Status.NOT_FOUND


def test_create_from_refuses_what_isnt_one(call):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=EPUB, data=b"not a book"))
    assert response.status == Status.INVALID_DATA


def test_an_item(call):
    data = make_epub()
    item_id, port = create_from(call, data)
    assert item_id.startswith("darq.book.epub:")
    assert call(port, GetRequest()).data == data
    contents = call(port, ContentsRequest())
    assert (contents.status, contents.title, contents.isbn) == (EbookStatus.OK, "A Book", "9780596007126")
    assert list(contents.authors) == ["Ada Lovelace", "Charles Babbage"]
    assert contents.parts[0] == "OEBPS/text/one a.xhtml"
    assert contents.contents[1].target == "OEBPS/text/one a.xhtml#sec" and contents.contents[1].level == 1


def test_getting_a_part(call):
    _, port = create_from(call, make_epub())
    part = call(port, GetPartRequest(name="OEBPS/style.css"))
    assert (part.status, part.media_type, part.data) == (EbookStatus.OK, "text/css", b"p { color: red }")
    assert call(port, GetPartRequest(name="nothing")).status == EbookStatus.NOT_FOUND


def test_it_goes_in_the_index(call, index):
    create_from(call, make_epub())
    assert index.names == [["A Book", "Ada Lovelace", "Charles Babbage"]]
    assert "first words" in index.sent[0][2]


def test_a_book_with_drm_is_kept(call, index):
    data = make_epub(encryption=encryption_of("aes"))
    item_id, port = create_from(call, data)
    contents = call(port, ContentsRequest())
    assert contents.status == EbookStatus.UNREADABLE and "DRM" in contents.reason
    assert call(port, GetRequest()).data == data
    assert call(port, GetPartRequest(name="x")).status == EbookStatus.UNREADABLE
    assert call(IMPLEMENTATION_PORT, ExportRequest(item_id=item_id)).data == data


@needs_libmobi
def test_a_mobi_with_drm_is_kept(call):
    _, port = create_from(call, make_mobi(encryption=2), AZW)
    assert call(port, ContentsRequest()).status == EbookStatus.UNREADABLE


def test_a_position_is_kept(call, blobs):
    item_id, port = create_from(call, make_epub())
    assert call(port, GetPositionRequest()).position == ""
    call(port, SetPositionRequest(position="1:0.25"))
    call(port, CloseRequest())
    [(value, _)] = blobs.blobs.values()
    data, position, flags = ebook_module.decode_blob(value)
    assert (position, flags) == ("1:0.25", 0) and bookfile.open_book(EPUB, data).title == "A Book"
    port = call(IMPLEMENTATION_PORT, OpenRequest(item_id=item_id)).item
    assert call(port, GetPositionRequest()).position == "1:0.25"


def test_the_same_position_writes_nothing(call, blobs):
    _, port = create_from(call, make_epub())
    call(port, SetPositionRequest(position="a"))
    [(_, generation)] = blobs.blobs.values()
    call(port, SetPositionRequest(position="a"))
    assert list(blobs.blobs.values())[0][1] == generation


def test_a_lost_position_isnt_a_failure(call, blobs, monkeypatch):
    _, port = create_from(call, make_epub())

    def fail(**fields):
        raise darq.DarqError("gone")

    monkeypatch.setattr(blobs, "put", fail)
    call(port, SetPositionRequest(position="a"))
    assert call(port, GetPositionRequest()).position == ""


def test_index_entry(call):
    item_id, _ = create_from(call, make_epub())
    assert "A Book" in call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=item_id)).names


def test_destroying(call, index):
    item_id, port = create_from(call, make_epub())
    call(port, CloseRequest())
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=item_id)).status == Status.OK
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=item_id)).status == Status.NOT_FOUND
    assert index.sent[-1][1] == item_id


@pytest.mark.parametrize("item_id, status", [("not an id", Status.INVALID_ID), ("darq.book.epub:zz", Status.NOT_FOUND),
                                             ("com.adobe.pdf:" + "0" * 32, Status.NOT_FOUND),
                                             ("darq.book.epub:" + "0" * 32, Status.NOT_FOUND)])
def test_opening_what_isnt_there(call, item_id, status):
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=item_id)).status == status


################################################################
# Live
################################################################

def test_ebook_type(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel
    epub = tmp_path / "book.epub"
    epub.write_bytes(make_epub())
    client_path = write_app(tmp_path / "client", f"""
        import time
        import darq
        import darq.launch
        from darq.protocols.ebook import Ebook, Status
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="darq.book.epub").implementation:
                if time.monotonic() > deadline:
                    return "never registered"
                time.sleep(0.05)
            data = open({str(epub)!r}, "rb").read()
            found = darq.launch.find_types(extension="epub")
            if found != ["darq.book.epub"]:
                return f"types {{found!r}}"
            for extension, wanted in (("azw3", "darq.book.azw"), ("mobi", "darq.book.mobi")):
                if darq.launch.find_types(extension=extension) != [wanted]:
                    return f"no type for {{extension}}"
            item_id, port = darq.launch.create_item_from("darq.book.epub", data)
            item = Ebook.Client(port, timeout=30)
            contents = item.contents()
            if (contents.status, contents.title) != (Status.OK, "A Book"):
                return f"contents {{contents!r}}"
            item.set_position(position="1:0.5")
            item.close()
            darq.close_port(port)
            again = Ebook.Client(darq.launch.open_item(item_id), timeout=30)
            if again.get_position().position != "1:0.5":
                return "no position"
            if types.describe(type_id="darq.ebook").implementation:
                return "the abstract type has an implementation"
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

def test_a_new_book_is_named_by_its_title(call, kb):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=EPUB, data=make_epub(), name="book.epub"))
    entity = kb.get(darq.split_item_id(response.item_id)[1])
    assert entity.get_label() == "A Book" and file_name(entity) == "book.epub"
    assert model.recorded_type(entity) == EPUB


def test_renaming_a_book_publishes_its_new_name(call, kb, index, implementation):
    iid, _ = create_from(call, make_epub())
    rename(call, implementation, IMPLEMENTATION_PORT, iid, "Notes on the Analytical Engine")
    assert index.names[-1][0] == "Notes on the Analytical Engine" and index.sent[-1][:2] == ("publish", iid)


def test_destroying_a_book_destroys_its_entity(call, kb):
    iid, _ = create_from(call, make_epub())
    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert kb.entities[darq.split_item_id(iid)[1]].destroyed


def test_an_old_book_is_given_its_entity_named_by_its_title(call, kb):
    kb.failing = darq.DarqError("no KB")
    iid, _ = create_from(call, make_epub())
    kb.failing = None
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.OK
    assert kb.label(darq.split_item_id(iid)[1]) == "A Book"


def test_viewing_is_recorded(call, server, fake_ports, recorded):  # noqa: F811
    from test_text_type import send
    from darq.protocols.ebook import Viewed
    iid, port = create_from(call, make_epub())
    send(fake_ports, server, port, Viewed(source="ebook-lens", note="Chapter 1"))
    assert recorded.events == [(iid, "created", "ebook-type", ""), (iid, "viewed", "ebook-lens", "Chapter 1")]
