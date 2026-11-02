# darqos
# Copyright (C) 2026 David Arnold

"""Images, with Pillow: the formats of the image types, checking data,
rendering an original with its edits, and reading and editing metadata.

It knows nothing of items or IPC, so it can be tested alone.  An image
is the original data with its edits made in order (see Edit), after
the original's EXIF orientation.  With no edits, it's the original,
unchanged.  Rendering re-encodes it in its type's format, keeping its
metadata (EXIF, XMP, ICC profile, PNG text, GIF comment), and, for a
JPEG, its quantization tables, so its quality.  See doc/image-type.rst.
"""

import datetime
import io
import re
import typing

from PIL import ExifTags, Image, ImageCms, ImageOps, JpegImagePlugin, PngImagePlugin, TiffImagePlugin

import pillow_heif

pillow_heif.register_heif_opener()


# Largest side, and most pixels, an edit can make.
MAX_SIDE = 32768
MAX_PIXELS = 100_000_000

# Quality for formats that don't keep their own (JPEG keeps its tables).
QUALITY = 90


class Format(typing.NamedTuple):
    """An image type's format: its MIME type, the Pillow formats that
    read it, and the one that writes it."""

    mime_type: str
    readers: frozenset[str]
    writer: str


FORMATS: dict[str, Format] = {
    "public.png": Format("image/png", frozenset({"PNG"}), "PNG"),
    "public.jpeg": Format("image/jpeg", frozenset({"JPEG", "MPO"}), "JPEG"),
    "com.compuserve.gif": Format("image/gif", frozenset({"GIF"}), "GIF"),
    "org.webmproject.webp": Format("image/webp", frozenset({"WEBP"}), "WEBP"),
    "public.tiff": Format("image/tiff", frozenset({"TIFF"}), "TIFF"),
    "com.microsoft.bmp": Format("image/bmp", frozenset({"BMP", "DIB"}), "BMP"),
    "public.heif": Format("image/heif", frozenset({"HEIF"}), "HEIF"),
    "public.heic": Format("image/heic", frozenset({"HEIF"}), "HEIF"),
}

# HEIF brands of HEVC-coded images: HEIC, rather than other HEIF.
HEVC_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs"})


class ImageError(Exception):
    """An image can't be read, edited, or rendered."""


class InvalidData(ImageError):
    """Data isn't an image of the type."""


class TooLarge(ImageError):
    """An image has more pixels than Pillow will decode."""


class InvalidEdit(ImageError):
    """An edit doesn't fit the image."""


# Edit types, and editable metadata fields.
CROP = "crop"
SCALE = "scale"
ROTATE = "rotate"
FLIP = "flip"
SET_FIELD = "set_field"
REMOVE_LOCATION = "remove_location"

TITLE = "title"
ARTIST = "artist"
COPYRIGHT = "copyright"
DATE_TAKEN = "date_taken"
COMMENT = "comment"
FIELDS = (TITLE, ARTIST, COPYRIGHT, DATE_TAKEN, COMMENT)


class Edit(typing.NamedTuple):
    """An edit: only its type's fields are used (see lib/didl/image.didl)."""

    type: str
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    turns: int = 0
    vertical: bool = False
    field: str = ""
    value: str = ""

    def to_dict(self) -> dict[str, typing.Any]:
        """Return it as a dict for JSON, without its unused fields."""
        defaults = Edit("")
        return {name: value for name, value in self._asdict().items()
                if name == "type" or value != getattr(defaults, name)}

    @classmethod
    def from_dict(cls, d: dict[str, typing.Any]) -> "Edit":
        return cls(**{name: d[name] for name in cls._fields if name in d})


# Where each field is kept: in EXIF, by IFD and tag; in PNG text, by
# keyword; and in a GIF, only its comment.
EXIF_IFD = ExifTags.IFD.Exif
GPS_IFD = ExifTags.IFD.GPSInfo
EXIF_FIELDS = {
    TITLE: (None, ExifTags.Base.ImageDescription),
    ARTIST: (None, ExifTags.Base.Artist),
    COPYRIGHT: (None, ExifTags.Base.Copyright),
    DATE_TAKEN: (EXIF_IFD, ExifTags.Base.DateTimeOriginal),
    COMMENT: (EXIF_IFD, ExifTags.Base.UserComment),
}
PNG_FIELDS = {
    TITLE: "Title",
    ARTIST: "Author",
    COPYRIGHT: "Copyright",
    DATE_TAKEN: "Creation Time",
    COMMENT: "Comment",
}

# PNG's keyword for XMP, and TIFF's tag.
PNG_XMP = "XML:com.adobe.xmp"
TIFF_XMP = 700

# EXIF tags that point to other IFDs, rather than being values.
POINTERS = {ExifTags.Base.ExifOffset, ExifTags.Base.GPSInfo, ExifTags.Base.ExifInteroperabilityOffset}

# A TIFF's own tags, which describe its pixels: its EXIF is its first
# IFD, so these are left out of it, and written by Pillow.
TIFF_STRUCTURE = {256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 317, 320, 322, 323, 324, 325,
                  338, 339, 347, TIFF_XMP, 34675}


def fields(type_id: str) -> tuple[str, ...]:
    """Return the metadata fields a type's images can have."""
    writer = FORMATS[type_id].writer
    if writer == "GIF":
        return (COMMENT,)
    if writer == "BMP":
        return ()
    return FIELDS


def open_image(data: bytes) -> Image.Image:
    """Return the image some data is, its first frame loaded.

    :raises TooLarge: if it has too many pixels.
    :raises InvalidData: if it isn't an image Pillow reads."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Image.DecompressionBombError as e:
        raise TooLarge(str(e)) from None
    except Exception as e:
        # Pillow raises many kinds, for many kinds of bad data.
        raise InvalidData(f"not an image: {e}") from None
    return image


def frame_count(image: Image.Image) -> int:
    """Return an image's frames: a JPEG's others, as a camera's MPO
    has, are previews, not animation."""
    if image.format == "MPO":
        return 1
    return getattr(image, "n_frames", 1)


def oriented_size(image: Image.Image) -> tuple[int, int]:
    """Return an image's size, once its EXIF orientation's made."""
    orientation = image.getexif().get(ExifTags.Base.Orientation, 1)
    width, height = image.size
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


class Info(typing.NamedTuple):
    """An image's size, as it's seen, and frames."""

    width: int
    height: int
    frames: int


def check(type_id: str, data: bytes) -> Info:
    """Check that data is an image of a type, and return its size.

    :raises InvalidData: if it isn't.
    :raises TooLarge: if it's too large to decode."""
    image = open_image(data)
    if image.format not in FORMATS[type_id].readers:
        raise InvalidData(f"a {image.format} image, not {type_id}")
    if type_id == "public.heic" and data[8:12] not in HEVC_BRANDS:
        raise InvalidData(f"a HEIF image of brand {data[8:12]!r}, not HEIC")
    return Info(*oriented_size(image), frame_count(image))


def parse_date(text: str) -> datetime.datetime:
    """Return the time a date taken is, from "YYYY-MM-DD HH:MM:SS", or
    a shorter part of it, or EXIF's "YYYY:MM:DD HH:MM:SS".

    :raises InvalidEdit: if it isn't one."""
    match = re.fullmatch(r"(\d{4})[-:](\d{2})[-:](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?", text.strip())
    if match is None:
        raise InvalidEdit(f"{text!r} isn't a date: try 2024-06-30 14:05")
    year, month, day, hour, minute, second = (int(n) if n else 0 for n in match.groups())
    try:
        return datetime.datetime(year, month, day, hour, minute, second)
    except ValueError as e:
        raise InvalidEdit(f"{text!r} isn't a date: {e}") from None


def check_edits(type_id: str, info: Info, edits: typing.Sequence[Edit]):
    """Check that edits fit an image, one after another.

    :raises InvalidEdit: if one doesn't."""
    if edits and info.frames > 1:
        raise InvalidEdit("an animated image can't be edited")
    width, height = info.width, info.height
    for n, e in enumerate(edits, 1):
        if e.type == CROP:
            if e.width == 0 or e.height == 0 or e.x + e.width > width or e.y + e.height > height:
                raise InvalidEdit(f"edit {n}: crop {e.width}x{e.height} at {e.x},{e.y} "
                                  f"isn't within {width}x{height}")
            width, height = e.width, e.height
        elif e.type == SCALE:
            if not (0 < e.width <= MAX_SIDE and 0 < e.height <= MAX_SIDE and e.width * e.height <= MAX_PIXELS):
                raise InvalidEdit(f"edit {n}: can't scale to {e.width}x{e.height}")
            width, height = e.width, e.height
        elif e.type == ROTATE:
            if e.turns % 2:
                width, height = height, width
        elif e.type == SET_FIELD:
            if e.field not in fields(type_id):
                raise InvalidEdit(f"edit {n}: {type_id} images have no {e.field or 'such'} field")
            if e.field == DATE_TAKEN and e.value:
                parse_date(e.value)
        elif e.type not in (FLIP, REMOVE_LOCATION):
            raise InvalidEdit(f"edit {n}: no such edit: {e.type!r}")


class Metadata:
    """An image's metadata, carried through rendering, and edited."""

    def __init__(self, image: Image.Image):
        self.exif = image.getexif()
        if image.format == "TIFF":
            for tag in TIFF_STRUCTURE:
                self.exif.pop(tag, None)

        # Pillow reads EXIF's ASCII tags as Latin-1 strings, and writes
        # strings as ASCII: as bytes, they're written as they were read,
        # UTF-8 included.
        for tags in (self.exif, self.exif.get_ifd(EXIF_IFD)):
            for tag, value in list(tags.items()):
                if isinstance(value, str):
                    try:
                        tags[tag] = value.encode("latin-1")
                    except UnicodeEncodeError:
                        pass
        self.icc: bytes | None = image.info.get("icc_profile")
        self.dpi = image.info.get("dpi")
        self.comment: bytes | None = image.info.get("comment")
        xmp = image.info.get("xmp")
        self.text: dict[str, str] = {}
        if image.format == "PNG":
            self.text = dict(getattr(image, "text", {}))
            text_xmp = self.text.pop(PNG_XMP, None)
            xmp = xmp or text_xmp
        elif image.format == "TIFF" and isinstance(image, TiffImagePlugin.TiffImageFile):
            xmp = xmp or image.tag_v2.get(TIFF_XMP)
        self.xmp: bytes | None = xmp.encode("utf-8") if isinstance(xmp, str) else xmp

    def set_field(self, writer: str, field: str, value: str):
        """Set a field, or remove it, if value is empty."""
        if writer == "GIF":
            self.comment = value.encode("utf-8") if value else None
            return
        if writer == "PNG":
            keyword = PNG_FIELDS[field]
            if field == DATE_TAKEN and value:
                value = parse_date(value).isoformat(" ")
            if value:
                self.text[keyword] = value
            else:
                self.text.pop(keyword, None)
            return

        ifd, tag = EXIF_FIELDS[field]
        tags = self.exif if ifd is None else self.exif.get_ifd(ifd)
        # A TIFF's may be in its first IFD (see save_options).
        self.exif.pop(tag, None)
        if not value:
            tags.pop(tag, None)
        elif field == DATE_TAKEN:
            tags[tag] = parse_date(value).strftime("%Y:%m:%d %H:%M:%S")
        elif field == COMMENT:
            tags[tag] = b"UNICODE\0" + value.encode("utf-16-le")
        else:
            # EXIF's ASCII tags hold UTF-8, as exiftool writes them.
            tags[tag] = value.encode("utf-8")

    def remove_location(self):
        """Remove EXIF's GPS tags, and XMP, if it has a location: XMP
        can't be edited."""
        self.exif.get_ifd(GPS_IFD).clear()
        self.exif.pop(ExifTags.Base.GPSInfo, None)
        if self.xmp is not None and b"GPS" in self.xmp:
            self.xmp = None

    def save_options(self, writer: str) -> dict[str, typing.Any]:
        """Return the options that save it, in a format."""
        options: dict[str, typing.Any] = {}
        tiffinfo: dict[int, typing.Any] = {}
        if writer == "TIFF":
            # A TIFF's first IFD is written from tiffinfo, which keeps
            # UTF-8 in ASCII tags, as its exif doesn't; and Pillow
            # doesn't write its EXIF IFD, so the fields are all kept in
            # the first.
            for field in FIELDS:
                ifd, tag = EXIF_FIELDS[field]
                value = (self.exif if ifd is None else self.exif.get_ifd(ifd)).pop(tag, None)
                value = self.exif.pop(tag, value)
                if value is not None:
                    tiffinfo[tag] = value.encode("latin-1", "replace") if isinstance(value, str) else value
            if self.xmp:
                tiffinfo[TIFF_XMP] = self.xmp
        if self.icc and writer not in ("GIF", "BMP"):
            options["icc_profile"] = self.icc
        if self.dpi and writer in ("JPEG", "PNG", "TIFF", "BMP"):
            options["dpi"] = self.dpi
        exif = self.exif.tobytes()
        if len(self.exif) and writer in ("JPEG", "PNG", "WEBP", "TIFF", "HEIF"):
            options["exif"] = exif
        if writer == "GIF" and self.comment:
            options["comment"] = self.comment
        if writer == "PNG":
            info = PngImagePlugin.PngInfo()
            for keyword, value in self.text.items():
                info.add_itxt(keyword, value)
            if self.xmp:
                info.add_itxt(PNG_XMP, self.xmp.decode("utf-8", "replace"))
            options["pnginfo"] = info
        elif self.xmp and writer in ("JPEG", "WEBP", "HEIF"):
            options["xmp"] = self.xmp
        if tiffinfo:
            options["tiffinfo"] = tiffinfo
        return options


# Pillow's transposes for clockwise quarter turns.
TURNS = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}

# Modes each writer takes: others are converted to RGBA, if they have
# transparency and it's taken, or else to RGB.
MODES = {
    "JPEG": {"L", "RGB", "CMYK"},
    "BMP": {"1", "L", "P", "RGB", "RGBA"},
    "HEIF": {"RGB", "RGBA"},
    "WEBP": {"RGB", "RGBA"},
}


def render(type_id: str, data: bytes, edits: typing.Sequence[Edit]) -> bytes:
    """Return an image with its edits made, in its type's format: the
    original, unchanged, if there are none.

    :raises ImageError: if it can't be rendered."""
    if not edits:
        return data
    writer = FORMATS[type_id].writer
    original = open_image(data)
    metadata = Metadata(original)
    jpeg = original.format in ("JPEG", "MPO")
    quantization = getattr(original, "quantization", None) if jpeg else None
    sampling = JpegImagePlugin.get_sampling(original) if jpeg else -1
    compression = original.info.get("compression")

    image = ImageOps.exif_transpose(original)
    metadata.exif.pop(ExifTags.Base.Orientation, None)
    for e in edits:
        if e.type == CROP:
            image = image.crop((e.x, e.y, e.x + e.width, e.y + e.height))
        elif e.type == SCALE:
            if image.mode in ("1", "P"):
                image = image.convert("RGBA")
            image = image.resize((e.width, e.height), Image.Resampling.LANCZOS)
        elif e.type == ROTATE and e.turns % 4:
            image = image.transpose(TURNS[e.turns % 4])
        elif e.type == FLIP:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM if e.vertical
                                    else Image.Transpose.FLIP_LEFT_RIGHT)
        elif e.type == SET_FIELD:
            metadata.set_field(writer, e.field, e.value)
        elif e.type == REMOVE_LOCATION:
            metadata.remove_location()

    modes = MODES.get(writer)
    if modes is not None and image.mode not in modes:
        alpha = "A" in image.mode or "transparency" in image.info
        image = image.convert("RGBA" if alpha and "RGBA" in modes else "RGB")
    # Pillow saves what's in an image's info, unless it's told otherwise:
    # only what isn't metadata is kept, so removed metadata stays so.
    image.info = {k: v for k, v in image.info.items() if k in ("transparency", "background")}
    options = metadata.save_options(writer)
    if writer == "JPEG":
        if quantization:
            options["qtables"] = quantization
            if sampling >= 0:
                options["subsampling"] = sampling
        else:
            options["quality"] = QUALITY
    elif writer in ("WEBP", "HEIF"):
        options["quality"] = QUALITY
    elif writer == "TIFF" and compression in ("raw", "tiff_lzw", "tiff_adobe_deflate", "packbits"):
        options["compression"] = compression

    out = io.BytesIO()
    try:
        image.save(out, format=writer, **options)
    except Exception as e:
        raise ImageError(f"can't save as {writer}: {e}") from None
    return out.getvalue()


# Metadata, as entries

class Entry(typing.NamedTuple):
    """A metadata entry, as text, and the field it is, if any."""

    group: str
    name: str
    value: str
    field: str = ""


def metadata(type_id: str, data: bytes) -> list[Entry]:
    """Return an image's metadata, as entries: its size and format, its
    editable fields, then EXIF, GPS, PNG text, GIF comment, ICC profile
    and XMP.

    :raises ImageError: if it can't be read."""
    image = open_image(data)
    writer = FORMATS[type_id].writer
    meta = Metadata(image)
    width, height = oriented_size(image)
    entries = [Entry("Image", "Format", image.format or ""),
               Entry("Image", "Size", f"{width} × {height}"),
               Entry("Image", "Mode", image.mode)]
    if frame_count(image) > 1:
        entries.append(Entry("Image", "Frames", str(frame_count(image))))
    if meta.dpi:
        entries.append(Entry("Image", "Resolution", " × ".join(f"{float(d):g}" for d in meta.dpi) + " dpi"))

    for field in fields(type_id):
        entries.append(Entry("Fields", _FIELD_NAMES[field], field_value(writer, meta, field), field))

    for tag, value in meta.exif.items():
        if tag not in POINTERS:
            entries.append(Entry("EXIF", ExifTags.TAGS.get(tag, f"0x{tag:04x}"), _text(value)))
    for tag, value in meta.exif.get_ifd(EXIF_IFD).items():
        entries.append(Entry("EXIF", ExifTags.TAGS.get(tag, f"0x{tag:04x}"), _text(value)))
    gps = meta.exif.get_ifd(GPS_IFD)
    location = _location(gps)
    if location:
        entries.append(Entry("GPS", "Location", location))
    for tag, value in gps.items():
        entries.append(Entry("GPS", ExifTags.GPSTAGS.get(tag, f"0x{tag:04x}"), _text(value)))
    for keyword, value in meta.text.items():
        entries.append(Entry("PNG", keyword, value))
    if meta.comment and writer == "GIF":
        entries.append(Entry("GIF", "Comment", _text(meta.comment)))
    if meta.icc:
        entries.append(Entry("Colour", "ICC profile", _profile(meta.icc)))
    if meta.xmp:
        entries.append(Entry("XMP", "XMP", meta.xmp.decode("utf-8", "replace").strip("\0 \n")))
    return entries


_FIELD_NAMES = {TITLE: "Title", ARTIST: "Artist", COPYRIGHT: "Copyright", DATE_TAKEN: "Date taken",
                COMMENT: "Comment"}


def field_value(writer: str, meta: Metadata, field: str) -> str:
    """Return a field's value, as it's edited, or empty if it has none."""
    if writer == "GIF":
        return _text(meta.comment) if meta.comment else ""
    if writer == "PNG":
        return meta.text.get(PNG_FIELDS[field], "")
    ifd, tag = EXIF_FIELDS[field]
    value = (meta.exif if ifd is None else meta.exif.get_ifd(ifd)).get(tag)
    if value is None:
        # A TIFF keeps them all in its first IFD (see save_options).
        value = meta.exif.get(tag)
    if value is None:
        return ""
    if field == COMMENT:
        return _user_comment(value)
    text = _text(value)
    if field == DATE_TAKEN:
        match = re.fullmatch(r"(\d{4}):(\d{2}):(\d{2})( .*)?", text)
        if match:
            text = f"{match[1]}-{match[2]}-{match[3]}{match[4] or ''}"
    return text


def _user_comment(value: typing.Any) -> str:
    """Return EXIF's UserComment as text: it starts with its encoding."""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return _text(value)
    code, body = value[:8], value[8:]
    if code == b"UNICODE\0":
        encoding = "utf-16-be" if body[:1] == b"\0" else "utf-16-le"
        return body.decode(encoding, "replace").rstrip("\0")
    return body.decode("utf-8" if code == b"ASCII\0\0\0" else "latin-1", "replace").rstrip("\0 ")


def _text(value: typing.Any) -> str:
    """Return a value as text: binary data as its size, unless it's
    text."""
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8").rstrip("\0")
        except UnicodeDecodeError:
            return f"({len(value)} bytes)"
        return text if text.isprintable() and len(text) <= 1000 else f"({len(value)} bytes)"
    if isinstance(value, str):
        # Pillow reads EXIF's ASCII tags as Latin-1: UTF-8 in them, as
        # exiftool writes, is decoded again.
        try:
            value = value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return value.rstrip("\0")
    if isinstance(value, tuple):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, float) or type(value).__name__ == "IFDRational":
        return f"{float(value):g}"
    return str(value).rstrip("\0")


def _location(gps: typing.Mapping[int, typing.Any]) -> str:
    """Return a GPS IFD's location, in degrees, or empty if it has none."""
    try:
        lat = gps[ExifTags.GPS.GPSLatitude]
        lon = gps[ExifTags.GPS.GPSLongitude]
        lat_ref = gps.get(ExifTags.GPS.GPSLatitudeRef, "N")
        lon_ref = gps.get(ExifTags.GPS.GPSLongitudeRef, "E")
        degrees = [float(d) + float(m) / 60 + float(s) / 3600 for d, m, s in (lat, lon)]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return ""
    return f"{degrees[0]:.5f}° {_text(lat_ref)}, {degrees[1]:.5f}° {_text(lon_ref)}"


def _profile(icc: bytes) -> str:
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.getProfileDescription(profile).strip() or f"({len(icc)} bytes)"
    except (OSError, ImageCms.PyCMSError):
        return f"({len(icc)} bytes)"
