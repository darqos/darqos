# darqos
# Copyright (C) 2026 David Arnold

# Tests for the image type implementation, types/image: rendering and
# metadata (imaging.py), on small images made for them; the handlers
# (main.py), through a Server with faked ports and Storage; and then
# the whole system, through a real p-kernel (see test_pkernel.py for
# the fixtures).

import importlib.util
import io
import pathlib
import sys
import typing

import pytest
from PIL import ExifTags, Image

import darq
from darq.protocols import image as wire
from darq.protocols.image import CloseRequest, GetMetadataRequest, GetRequest, SetEditsRequest, StatRequest
from darq.protocols.type import (
    CreateFromRequest,
    CreateRequest,
    DestroyRequest,
    ExportRequest,
    IndexEntryRequest,
    OpenRequest,
    Status,
)

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_text_type import CLIENT, Caller, FakeHandles, FakePublisher, FakeStorage, fake_ports, handles  # noqa: F401


IMAGE_PATH = pathlib.Path(__file__).parent.parent / "types" / "image"


def load():
    # Its modules import each other by name, as they do run from there.
    sys.path.insert(0, str(IMAGE_PATH))
    try:
        spec = importlib.util.spec_from_file_location("image_type", IMAGE_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(IMAGE_PATH))
    return module


image_module = load()
imaging = image_module.imaging
Edit = imaging.Edit

IMPLEMENTATION_PORT = 50

# Colours of the pixels of a 3 x 2 image, by position.
RED, GREEN, BLUE, WHITE, BLACK, GREY = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0), \
    (128, 128, 128)
PIXELS = [[RED, GREEN, BLUE], [WHITE, BLACK, GREY]]


def encode(image: Image.Image, format: str, **options) -> bytes:
    out = io.BytesIO()
    image.save(out, format=format, **options)
    return out.getvalue()


def make(format: str = "PNG", size=(40, 20), mode="RGB", **options: typing.Any) -> bytes:
    return encode(Image.new(mode, size, (10, 20, 30) if mode == "RGB" else 0), format, **options)


def pixels() -> Image.Image:
    image = Image.new("RGB", (3, 2))
    for y, row in enumerate(PIXELS):
        for x, colour in enumerate(row):
            image.putpixel((x, y), colour)
    return image


def rows(data: bytes) -> list[list[typing.Any]]:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    return [[image.getpixel((x, y)) for x in range(image.width)] for y in range(image.height)]


def located() -> Image.Exif:
    """EXIF with a location, in Brisbane, and turned (orientation 6)."""
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "S"
    gps[ExifTags.GPS.GPSLatitude] = (27.0, 28.0, 12.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (153.0, 1.0, 48.0)
    return exif


def entries(type_id: str, data: bytes) -> dict[tuple[str, str], str]:
    return {(e.group, e.name): e.value for e in imaging.metadata(type_id, data)}


def fields(type_id: str, data: bytes) -> dict[str, str]:
    return {e.field: e.value for e in imaging.metadata(type_id, data) if e.field}


################################################################
# Formats
################################################################

# Each type, and how to make an image of it.
SAMPLES = {
    "public.png": lambda: make("PNG"),
    "public.jpeg": lambda: make("JPEG"),
    "com.compuserve.gif": lambda: make("GIF", mode="P"),
    "org.webmproject.webp": lambda: make("WEBP"),
    "public.tiff": lambda: make("TIFF"),
    "com.microsoft.bmp": lambda: make("BMP"),
    "public.heif": lambda: make("HEIF"),
    "public.heic": lambda: make("HEIF"),
}


@pytest.mark.parametrize("type_id", sorted(SAMPLES))
def test_check(type_id):
    assert imaging.check(type_id, SAMPLES[type_id]()) == (40, 20, 1)


@pytest.mark.parametrize("type_id, data", [
    ("public.png", make("JPEG")),
    ("public.jpeg", make("PNG")),
    ("public.png", b"not an image"),
    ("public.png", b""),
    ("public.png", make("PNG")[:40]),
])
def test_check_refuses_other_data(type_id, data):
    with pytest.raises(imaging.InvalidData):
        imaging.check(type_id, data)


def test_heic_needs_an_hevc_brand():
    data = make("HEIF")
    assert data[8:12] == b"heic"
    other = data[:8] + b"mif1" + data[12:]
    assert imaging.check("public.heif", other)
    with pytest.raises(imaging.InvalidData, match="HEIC"):
        imaging.check("public.heic", other)


def test_too_large(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(imaging.TooLarge):
        imaging.check("public.png", make("PNG", size=(100, 100)))


def test_orientation_is_made():
    # Turned by EXIF: it's seen 20 wide and 40 high.
    data = make("JPEG", exif=located().tobytes())
    assert imaging.check("public.jpeg", data) == (20, 40, 1)
    rendered = imaging.render("public.jpeg", data, [Edit("flip")])
    assert Image.open(io.BytesIO(rendered)).size == (20, 40)
    assert ExifTags.Base.Orientation not in Image.open(io.BytesIO(rendered)).getexif()


def test_animated():
    frames = [Image.new("RGB", (4, 4), colour) for colour in (RED, GREEN, BLUE)]
    data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:])
    info = imaging.check("com.compuserve.gif", data)
    assert info.frames == 3
    imaging.check_edits("com.compuserve.gif", info, [])
    with pytest.raises(imaging.InvalidEdit, match="animated"):
        imaging.check_edits("com.compuserve.gif", info, [Edit("flip")])


################################################################
# Edits
################################################################

def test_no_edits_is_the_original():
    data = make("JPEG")
    assert imaging.render("public.jpeg", data, []) is data


@pytest.mark.parametrize("edits, expected", [
    ([Edit("rotate", turns=1)], [[WHITE, RED], [BLACK, GREEN], [GREY, BLUE]]),
    ([Edit("rotate", turns=-1)], [[BLUE, GREY], [GREEN, BLACK], [RED, WHITE]]),
    ([Edit("rotate", turns=2)], [[GREY, BLACK, WHITE], [BLUE, GREEN, RED]]),
    ([Edit("rotate", turns=4)], PIXELS),
    ([Edit("flip")], [[BLUE, GREEN, RED], [GREY, BLACK, WHITE]]),
    ([Edit("flip", vertical=True)], [[WHITE, BLACK, GREY], [RED, GREEN, BLUE]]),
    ([Edit("crop", x=1, y=0, width=2, height=1)], [[GREEN, BLUE]]),
    ([Edit("rotate", turns=1), Edit("crop", x=0, y=1, width=2, height=2)], [[BLACK, GREEN], [GREY, BLUE]]),
])
def test_render(edits, expected):
    data = encode(pixels(), "PNG")
    assert rows(imaging.render("public.png", data, edits)) == expected


def test_scale():
    data = make("PNG", size=(40, 20))
    rendered = imaging.render("public.png", data, [Edit("scale", width=10, height=7)])
    assert Image.open(io.BytesIO(rendered)).size == (10, 7)


@pytest.mark.parametrize("edits, why", [
    ([Edit("crop", x=30, y=0, width=20, height=10)], "isn't within"),
    ([Edit("crop", x=0, y=0, width=0, height=10)], "isn't within"),
    # Turned, it's 20 wide: a crop 30 wide doesn't fit.
    ([Edit("rotate", turns=1), Edit("crop", x=0, y=0, width=30, height=10)], "edit 2"),
    ([Edit("scale", width=0, height=10)], "can't scale"),
    ([Edit("scale", width=40000, height=10)], "can't scale"),
    ([Edit("set_field", field="date_taken", value="last Tuesday")], "isn't a date"),
    ([Edit("set_field", field="date_taken", value="2024-02-30")], "isn't a date"),
    ([Edit("set_field", field="mood", value="happy")], "no mood field"),
    ([Edit("sharpen")], "no such edit"),
])
def test_edits_that_dont_fit(edits, why):
    with pytest.raises(imaging.InvalidEdit, match=why):
        imaging.check_edits("public.png", imaging.Info(40, 20, 1), edits)


def test_jpeg_keeps_its_quality():
    data = encode(Image.effect_noise((64, 64), 40).convert("RGB"), "JPEG", quality=30)
    rendered = imaging.render("public.jpeg", data, [Edit("flip")])
    def tables(jpeg: bytes):
        return getattr(Image.open(io.BytesIO(jpeg)), "quantization")
    assert tables(rendered) == tables(data)


################################################################
# Metadata
################################################################

def set_fields(type_id: str) -> list:
    return [Edit("set_field", field=f, value="2024-06-30 14:05" if f == "date_taken" else f"{f}: Zoë")
            for f in imaging.fields(type_id)]


@pytest.mark.parametrize("type_id", sorted(SAMPLES))
def test_fields(type_id):
    data = SAMPLES[type_id]()
    rendered = imaging.render(type_id, data, set_fields(type_id))
    expected = {f: "2024-06-30 14:05:00" if f == "date_taken" else f"{f}: Zoë" for f in imaging.fields(type_id)}
    assert fields(type_id, rendered) == expected

    # And again: they survive another rendering, and can be removed.
    again = imaging.render(type_id, rendered, [Edit("flip")])
    assert fields(type_id, again) == expected
    cleared = imaging.render(type_id, rendered, [Edit("set_field", field=f) for f in imaging.fields(type_id)])
    assert fields(type_id, cleared) == {f: "" for f in imaging.fields(type_id)}


def test_fields_by_format():
    assert imaging.fields("com.compuserve.gif") == ("comment",)
    assert imaging.fields("com.microsoft.bmp") == ()
    assert imaging.fields("public.jpeg") == imaging.FIELDS


def test_where_fields_are_kept():
    jpeg = imaging.render("public.jpeg", make("JPEG"), set_fields("public.jpeg"))
    exif = Image.open(io.BytesIO(jpeg)).getexif()
    assert exif[ExifTags.Base.Artist].encode("latin-1").decode("utf-8") == "artist: Zoë"
    assert exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] == "2024:06:30 14:05:00"

    png = imaging.render("public.png", make("PNG"), set_fields("public.png"))
    assert Image.open(io.BytesIO(png)).text["Author"] == "artist: Zoë"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("type_id, format", [("public.jpeg", "JPEG"), ("org.webmproject.webp", "WEBP"),
                                             ("public.png", "PNG"), ("public.heic", "HEIF")])
def test_remove_location(type_id, format):
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:GPSLatitude="27,28.2S"/>'
           b'</rdf:RDF></x:xmpmeta>')
    options: dict[str, typing.Any] = {"exif": located().tobytes()}
    if format == "PNG":
        info = imaging.PngImagePlugin.PngInfo()
        info.add_itxt(imaging.PNG_XMP, xmp.decode())
        options["pnginfo"] = info
    else:
        options["xmp"] = xmp
    data = make(format, **options)
    before = entries(type_id, data)
    assert before[("GPS", "Location")] == "27.47000° S, 153.03000° E"
    assert ("XMP", "XMP") in before

    rendered = imaging.render(type_id, data, [Edit("remove_location")])
    after = entries(type_id, rendered)
    assert not any(group in ("GPS", "XMP") for group, _ in after)
    assert not Image.open(io.BytesIO(rendered)).getexif().get_ifd(ExifTags.IFD.GPSInfo)


def test_xmp_without_a_location_is_kept():
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
    rendered = imaging.render("public.jpeg", make("JPEG", xmp=xmp), [Edit("remove_location"), Edit("flip")])
    assert entries("public.jpeg", rendered)[("XMP", "XMP")] == xmp.decode()


def test_metadata_entries():
    exif = located()
    exif[ExifTags.Base.Make] = "Camera Co"
    exif[ExifTags.Base.MakerNote] = bytes(range(256))
    found = entries("public.jpeg", make("JPEG", exif=exif.tobytes(), dpi=(300, 300)))
    assert found[("Image", "Format")] == "JPEG"
    assert found[("Image", "Size")] == "20 × 40"
    assert found[("Image", "Resolution")] == "300 × 300 dpi"
    assert found[("EXIF", "Make")] == "Camera Co"
    assert found[("EXIF", "MakerNote")] == "(256 bytes)"
    assert found[("GPS", "GPSLatitudeRef")] == "S"


def test_gif_comment():
    rendered = imaging.render("com.compuserve.gif", make("GIF", mode="P"),
                              [Edit("set_field", field="comment", value="Hello")])
    assert Image.open(io.BytesIO(rendered)).info["comment"] == b"Hello"


################################################################
# Blobs and item identifiers
################################################################

def test_blob_round_trip():
    edits = [Edit("crop", x=1, y=2, width=3, height=4), Edit("set_field", field="title", value="Hi"), Edit("flip")]
    blob = image_module.encode_blob(b"original", edits, image_module.DESTROYED)
    assert blob[:4] == b"DIMG"
    assert image_module.decode_blob(blob) == (b"original", edits, image_module.DESTROYED)


@pytest.mark.parametrize("blob", [b"", b"DIMG", b"DTXT\x00\x01\x00\x00\x00\x00\x00\x00",
                                  b"DIMG\x00\x01\x00\x00\x00\x00\x00\x05[{]}x"])
def test_bad_blob(blob):
    with pytest.raises(image_module.FormatError):
        image_module.decode_blob(blob)


def test_item_id_round_trip():
    from darq.protocols import storage
    key = storage.Key(0x0123456789abcdef, 0xfedcba9876543210)
    iid = image_module.item_id("public.png", key)
    assert iid == "public.png:0123456789abcdeffedcba9876543210"
    assert image_module.parse_item_id(iid) == ("public.png", key)
    assert image_module.parse_item_id("public.utf8-plain-text:" + iid.split(":")[1]) is None
    assert image_module.parse_item_id("public.png:xyz") is None


def test_edits_round_trip_the_protocol():
    edits = [Edit("crop", x=1, y=2, width=3, height=4), Edit("scale", width=5, height=6), Edit("rotate", turns=-1),
             Edit("flip", vertical=True), Edit("set_field", field="date_taken", value="2024-01-01"),
             Edit("remove_location")]
    assert [image_module.edit_from_wire(image_module.edit_to_wire(e)) for e in edits] == edits


################################################################
# Handlers
################################################################

@pytest.fixture
def blobs():
    return FakeStorage()


@pytest.fixture
def index():
    return FakePublisher()


@pytest.fixture
def implementation(blobs, index):
    return image_module.ImageImplementation(blobs, index)


@pytest.fixture
def server(implementation, fake_ports, handles):  # noqa: F811
    server = darq.Server()
    server.add(IMPLEMENTATION_PORT, implementation)
    return server


@pytest.fixture
def call(fake_ports, server):  # noqa: F811
    return Caller(fake_ports, server)


def create_from(call, data, type_id="public.png"):
    response = call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=type_id, data=data))
    assert response.status == Status.OK
    return response.item_id, response.item


def saved(blobs, iid):
    """An item's saved original, edits and flags."""
    _, key = image_module.parse_item_id(iid)
    return image_module.decode_blob(blobs.blobs[(key.high, key.low)][0])


@pytest.mark.parametrize("type_id", sorted(SAMPLES))
def test_field_values(type_id):
    # From the original, as its edits leave them, without rendering.
    data = imaging.render(type_id, SAMPLES[type_id](), set_fields(type_id))
    values = imaging.field_values(type_id, data, [Edit("flip")])
    assert set(values) == set(imaging.fields(type_id))
    assert all(v.endswith("Zoë") or v.startswith("2024-06-30") for v in values.values())
    if imaging.COMMENT in values:
        edited = imaging.field_values(type_id, data, [Edit("set_field", field="comment", value="New"),
                                                      Edit("set_field", field="mood", value="happy")])
        assert edited[imaging.COMMENT] == "New" and "mood" not in edited


def test_field_values_of_bad_data():
    with pytest.raises(imaging.InvalidData):
        imaging.field_values("public.png", b"not a png", [])


def test_index_entry(call):
    data = imaging.render("public.jpeg", make("JPEG"), [
        Edit("set_field", field="title", value="Brisbane"), Edit("set_field", field="artist", value="Zoë"),
        Edit("set_field", field="comment", value="From the river")])
    iid, port = create_from(call, data, "public.jpeg")
    copyright = wire.Edit(wire.EditType.SET_FIELD, field=wire.Field.COPYRIGHT, value="CC BY")
    assert call(port, SetEditsRequest(edits=[copyright])).status == wire.Status.OK
    response = call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=iid))
    assert (response.status, response.names, response.body) == \
        (Status.OK, ["Brisbane", "Zoë", "CC BY"], "From the river\nJPEG image")

    # A GIF has only a comment; an empty item, nothing.
    gif, _ = create_from(call, make("GIF", mode="P"), "com.compuserve.gif")
    assert call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=gif)).body == "GIF image"
    empty = call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.png")).item_id
    response = call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=empty))
    assert (response.status, response.names, response.body) == (Status.OK, [], "")

    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id=iid)).status == Status.NOT_FOUND
    assert call(IMPLEMENTATION_PORT, IndexEntryRequest(item_id="bad id")).status == Status.INVALID_ID


def test_published_as_saved(call, index):
    iid, port = create_from(call, make("PNG"))
    assert index.sent == [("publish", iid, "PNG image")]
    title = wire.Edit(wire.EditType.SET_FIELD, field=wire.Field.TITLE, value="Sunset")
    call(port, SetEditsRequest(edits=[title]))
    call(port, CloseRequest())
    assert index.sent[-1] == ("publish", iid, "PNG image")
    assert index.names[-1] == ["Sunset"]

    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert index.sent[-1] == ("withdraw", iid)

    empty = call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.png")).item_id
    assert index.sent[-1] == ("publish", empty, "")


def test_create_from(call, blobs):
    data = encode(pixels(), "PNG")
    iid, port = create_from(call, data)
    assert iid.startswith("public.png:")
    assert saved(blobs, iid) == (data, [], 0)

    got = call(port, GetRequest())
    assert (got.status, got.data, got.width, got.height, got.frames, got.version) == \
        (wire.Status.OK, data, 3, 2, 1, 1)
    stat = call(port, StatRequest())
    assert (stat.width, stat.height, stat.size) == (3, 2, len(data))


@pytest.mark.parametrize("type_id, data, status", [
    ("public.png", make("JPEG"), Status.INVALID_DATA),
    ("public.png", b"junk", Status.INVALID_DATA),
    ("public.utf8-plain-text", make("PNG"), Status.NOT_FOUND),
    ("not a type!", make("PNG"), Status.INVALID_ID),
])
def test_create_from_refused(call, blobs, type_id, data, status):
    # Nothing is created.
    assert call(IMPLEMENTATION_PORT, CreateFromRequest(type_id=type_id, data=data)).status == status
    assert blobs.blobs == {}


def test_create_from_is_canonical(call):
    iid, _ = create_from(call, make("JPEG"), "Public.JPEG")
    assert iid.startswith("public.jpeg:")


def test_create_empty(call):
    response = call(IMPLEMENTATION_PORT, CreateRequest(type_id="public.png"))
    assert response.status == Status.OK
    got = call(response.item, GetRequest())
    assert (got.status, got.data) == (wire.Status.EMPTY, b"")
    assert call(response.item, SetEditsRequest(edits=[wire.Edit(wire.EditType.FLIP)])).status == wire.Status.EMPTY
    assert call(response.item, GetMetadataRequest()).status == wire.Status.EMPTY
    exported = call(IMPLEMENTATION_PORT, ExportRequest(item_id=response.item_id))
    assert (exported.status, exported.data, exported.mime_type) == (Status.OK, b"", "image/png")


def test_set_edits(call, blobs, server, monkeypatch):
    iid, port = create_from(call, encode(pixels(), "PNG"))
    turn = wire.Edit(wire.EditType.ROTATE, turns=1)
    response = call(port, SetEditsRequest(edits=[turn], expected=1))
    assert (response.status, response.version) == (wire.Status.OK, 2)

    got = call(port, GetRequest())
    assert (got.width, got.height, got.version) == (2, 3, 2)
    assert rows(got.data) == [[WHITE, RED], [BLACK, GREEN], [GREY, BLUE]]
    assert [e.turns for e in got.edits] == [1]

    # Saved after a delay; the original's kept.
    monkeypatch.setattr(image_module.time, "monotonic", lambda: 1e9)
    server.run_once(timeout=image_module.SAVE_DELAY + 1)
    original, edits, _ = saved(blobs, iid)
    assert original == encode(pixels(), "PNG")
    assert edits == [Edit("rotate", turns=1)]

    # Undone, it's the original again.
    assert call(port, SetEditsRequest(edits=[], expected=2)).status == wire.Status.OK
    assert call(port, GetRequest()).data == original


def test_set_edits_conflict(call):
    _, port = create_from(call, make("PNG"))
    flip = wire.Edit(wire.EditType.FLIP)
    assert call(port, SetEditsRequest(edits=[flip], expected=1)).status == wire.Status.OK
    response = call(port, SetEditsRequest(edits=[], expected=1))
    assert (response.status, response.version) == (wire.Status.CONFLICT, 2)


def test_set_edits_refused(call):
    _, port = create_from(call, make("PNG"))
    crop = wire.Edit(wire.EditType.CROP, x=0, y=0, width=50, height=10)
    response = call(port, SetEditsRequest(edits=[crop]))
    assert (response.status, response.version) == (wire.Status.INVALID_EDIT, 1)
    assert call(port, GetRequest()).edits == []


def test_animated_isnt_edited(call):
    frames = [Image.new("RGB", (4, 4), colour) for colour in (RED, GREEN, BLUE)]
    _, port = create_from(call, encode(frames[0], "GIF", save_all=True, append_images=frames[1:]),
                          "com.compuserve.gif")
    assert call(port, GetRequest()).frames == 3
    assert call(port, SetEditsRequest(edits=[wire.Edit(wire.EditType.FLIP)])).status == wire.Status.ANIMATED


def test_get_metadata(call):
    _, port = create_from(call, make("JPEG", exif=located().tobytes()), "public.jpeg")
    title = wire.Edit(wire.EditType.SET_FIELD, field=wire.Field.TITLE, value="Brisbane")
    assert call(port, SetEditsRequest(edits=[title])).status == wire.Status.OK
    response = call(port, GetMetadataRequest())
    assert response.status == wire.Status.OK
    found = {(e.group, e.name): (e.value, e.field) for e in response.entries}
    assert found[("Fields", "Title")] == ("Brisbane", wire.Field.TITLE)
    assert found[("GPS", "Location")][0].startswith("27.47")


def test_export(call):
    data = encode(pixels(), "PNG")
    iid, port = create_from(call, data)
    exported = call(IMPLEMENTATION_PORT, ExportRequest(item_id=iid))
    assert (exported.status, exported.data, exported.mime_type) == (Status.OK, data, "image/png")
    call(port, SetEditsRequest(edits=[wire.Edit(wire.EditType.FLIP)]))
    assert rows(call(IMPLEMENTATION_PORT, ExportRequest(item_id=iid)).data) == \
        [[BLUE, GREEN, RED], [GREY, BLACK, WHITE]]


def test_close_saves_reopen_and_destroy(call, blobs, implementation):
    iid, port = create_from(call, make("PNG"))
    call(port, SetEditsRequest(edits=[wire.Edit(wire.EditType.FLIP)]))
    call(port, CloseRequest())
    assert implementation.pictures == {}
    assert saved(blobs, iid)[1] == [Edit("flip")]

    opened = call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid))
    assert opened.status == Status.OK
    assert len(call(opened.item, GetRequest()).edits) == 1
    call(opened.item, CloseRequest())

    assert call(IMPLEMENTATION_PORT, DestroyRequest(item_id=iid)).status == Status.OK
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == Status.NOT_FOUND


@pytest.mark.parametrize("iid, status", [
    ("public.png:00000000000000000000000000000099", Status.NOT_FOUND),
    ("public.utf8-plain-text:00000000000000000000000000000001", Status.NOT_FOUND),
    ("public.png:nothex", Status.NOT_FOUND),
    ("bad id", Status.INVALID_ID),
])
def test_open_not_found(call, iid, status):
    assert call(IMPLEMENTATION_PORT, OpenRequest(item_id=iid)).status == status


################################################################
# End to end
################################################################

def test_image_type(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # A client that finds the type for a .jpg, creates an item from a
    # JPEG, edits it, and exports it.
    client_path = write_app(tmp_path / "client", """
        import io
        import time
        import darq
        import darq.launch
        from PIL import Image
        from darq.protocols import image as wire
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            for type_id in ("public.jpeg", "public.heic"):
                while not types.describe(type_id=type_id).implementation:
                    if time.monotonic() > deadline:
                        return f"{type_id} never registered"
                    time.sleep(0.05)

            found = darq.launch.find_types(extension="jpg")
            if found != ["public.jpeg"]:
                return f"found {found!r}"
            if darq.launch.find_types(mime_type="image/heic") != ["public.heic"]:
                return "image/heic not found"

            out = io.BytesIO()
            Image.new("RGB", (40, 20), (200, 0, 0)).save(out, "JPEG")
            try:
                darq.launch.create_item_from("public.png", out.getvalue())
                return "a JPEG was taken as a PNG"
            except darq.LaunchError:
                pass
            item_id, port = darq.launch.create_item_from("public.jpeg", out.getvalue())
            item = wire.Image.Client(port, timeout=30)
            turn = wire.Edit(wire.EditType.ROTATE, turns=1)
            if item.set_edits(edits=[turn], expected=0).status != wire.Status.OK:
                return "edit refused"
            got = item.get()
            if (got.width, got.height) != (20, 40):
                return f"turned, it's {got.width} x {got.height}"
            item.close()

            data, mime_type = darq.launch.export_item(item_id)
            if mime_type != "image/jpeg" or Image.open(io.BytesIO(data)).size != (20, 40):
                return f"exported {mime_type}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
