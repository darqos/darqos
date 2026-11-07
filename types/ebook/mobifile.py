# darqos
# Copyright (C) 2026 David Arnold

"""MOBI and AZW files, read with libmobi, through ctypes (see
doc/book.rst).

libmobi is a C library, under the LGPL, so it's used as it's installed.
It rebuilds a book's parts, and links, as ordinary HTML, from memory.  It
reads untrusted files, and it's C, so it's only used in the type's
process, where a crash can't take a lens with it.

Its structures are declared here, as of libmobi 0.12: MOBIPart and
MOBIRawml are all that's touched.  DARQ_LIBMOBI names the library, for a
development tree without it installed."""

import ctypes
import os
import re
import typing

import darq.book

import bookfile
from bookfile import Encrypted, Entry, NotABook, Unreadable


LIBRARY = "libmobi.so.0"

# Where a Palm database says what it is: BOOKMOBI, or TEXtREAd.
TYPE_OFFSET = 60
TYPES = (b"BOOKMOBI", b"TEXtREAd")

# MOBIFiletype, as of 0.12, and how they're named and served.
FILE_TYPES = {
    1: ("html", bookfile.HTML),
    2: ("css", "text/css"),
    3: ("svg", "image/svg+xml"),
    4: ("opf", "application/oebps-package+xml"),
    5: ("ncx", "application/x-dtbncx+xml"),
    6: ("jpg", "image/jpeg"),
    7: ("gif", "image/gif"),
    8: ("png", "image/png"),
    9: ("bmp", "image/bmp"),
    10: ("otf", "font/otf"),
    11: ("ttf", "font/ttf"),
    12: ("mp3", "audio/mpeg"),
    13: ("mpg", "video/mpeg"),
    14: ("pdf", "application/pdf"),
}
HTML_TYPE = 1
OPF_TYPE = 4
NCX_TYPE = 5

SUCCESS = 0


class Part(ctypes.Structure):
    pass


Part._fields_ = [("uid", ctypes.c_size_t), ("type", ctypes.c_int), ("size", ctypes.c_size_t),
                 ("data", ctypes.POINTER(ctypes.c_ubyte)), ("next", ctypes.POINTER(Part))]


class Rawml(ctypes.Structure):
    _fields_ = [("version", ctypes.c_size_t),
                *((name, ctypes.c_void_p) for name in ("fdst", "skel", "frag", "guide", "ncx", "orth", "infl")),
                ("flow", ctypes.POINTER(Part)), ("markup", ctypes.POINTER(Part)),
                ("resources", ctypes.POINTER(Part))]


_library: typing.Any = None


def library() -> typing.Any:
    """Return libmobi, and libc, with their functions declared.

    :raises Unreadable: if it isn't installed."""
    global _library
    if _library is not None:
        return _library
    try:
        mobi = ctypes.CDLL(os.environ.get("DARQ_LIBMOBI") or LIBRARY)
        libc = ctypes.CDLL("libc.so.6")
    except OSError as e:
        raise Unreadable(f"libmobi isn't installed: {e}") from None

    pointer = ctypes.c_void_p
    for name, result, arguments in (
            ("mobi_init", pointer, []),
            ("mobi_free", None, [pointer]),
            ("mobi_load_file", ctypes.c_int, [pointer, pointer]),
            ("mobi_is_encrypted", ctypes.c_bool, [pointer]),
            ("mobi_init_rawml", ctypes.POINTER(Rawml), [pointer]),
            ("mobi_parse_rawml", ctypes.c_int, [ctypes.POINTER(Rawml), pointer]),
            ("mobi_free_rawml", None, [ctypes.POINTER(Rawml)]),
            ("mobi_meta_get_title", pointer, [pointer]),
            ("mobi_meta_get_author", pointer, [pointer]),
            ("mobi_meta_get_isbn", pointer, [pointer]),
            ("mobi_meta_get_publisher", pointer, [pointer]),
            ("mobi_meta_get_publishdate", pointer, [pointer]),
            ("mobi_meta_get_language", pointer, [pointer])):
        function = getattr(mobi, name)
        function.restype = result
        function.argtypes = arguments
    libc.fmemopen.restype = pointer
    libc.fmemopen.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    libc.fclose.argtypes = [pointer]
    libc.free.argtypes = [pointer]
    _library = (mobi, libc)
    return _library


def parts_of(first: typing.Any) -> list[tuple[int, int, bytes]]:
    """Return a list of parts: their identifiers, types and data."""
    found = []
    part = first
    while part:
        p = part.contents
        data = ctypes.string_at(p.data, p.size) if p.size else b""
        found.append((p.uid, p.type, data))
        part = p.next
    return found


def extension(file_type: int) -> str:
    return FILE_TYPES.get(file_type, ("bin", ""))[0]


def check_type(data: bytes):
    """Check a file says it's one of these.

    :raises NotABook: if it doesn't."""
    if data[TYPE_OFFSET:TYPE_OFFSET + 8] not in TYPES:
        raise NotABook("It isn't a Mobipocket file")


class Mobi:
    """A MOBI or AZW file, unpacked: it's small, and libmobi needs it
    whole, so it's read once."""

    def __init__(self, data: bytes):
        """Constructor.

        :raises NotABook: if it isn't one.
        :raises Encrypted: if it has DRM.
        :raises Unreadable: if libmobi isn't installed, or can't read it."""
        check_type(data)
        self.title = ""
        self.authors: list[str] = []
        self.isbn = ""
        self.publisher = ""
        self.date = ""
        self.language = ""

        # libmobi doesn't say which image's the cover.
        self.cover = ""
        self.parts: list[str] = []
        self.contents: list[Entry] = []
        self.held: dict[str, tuple[str, bytes]] = {}

        mobi, libc = library()
        handle = mobi.mobi_init()
        if not handle:
            raise Unreadable("libmobi can't start")
        try:
            stream = libc.fmemopen(data, len(data), b"rb")
            if not stream:
                raise Unreadable("Can't read it from memory")
            try:
                status = mobi.mobi_load_file(handle, stream)
            finally:
                libc.fclose(stream)
            if status != SUCCESS:
                raise Unreadable(f"It's damaged: libmobi says {status}")
            if mobi.mobi_is_encrypted(handle):
                raise Encrypted("It has DRM")
            self.read_details(handle)
            rawml = mobi.mobi_init_rawml(handle)
            if not rawml:
                raise Unreadable("libmobi can't start")
            try:
                status = mobi.mobi_parse_rawml(rawml, handle)
                if status != SUCCESS:
                    raise Unreadable(f"It's damaged: libmobi says {status}")
                self.read_parts(rawml.contents)
            finally:
                mobi.mobi_free_rawml(rawml)
        finally:
            mobi.mobi_free(handle)

    @staticmethod
    def detail(function: typing.Any, handle: int) -> str:
        """Return a detail of the file: libmobi's copy is the caller's."""
        _, libc = library()
        found = function(handle)
        if not found:
            return ""
        try:
            return ctypes.string_at(found).decode("utf-8", "replace").strip()
        finally:
            libc.free(found)

    def read_details(self, handle: int):
        mobi, _ = library()
        self.title = self.detail(mobi.mobi_meta_get_title, handle)
        author = self.detail(mobi.mobi_meta_get_author, handle)
        self.authors = [a.strip() for a in re.split(r"[;&]", author) if a.strip()]
        self.isbn = darq.book.clean_isbn(self.detail(mobi.mobi_meta_get_isbn, handle))
        self.publisher = self.detail(mobi.mobi_meta_get_publisher, handle)
        self.date = darq.book.date_of(self.detail(mobi.mobi_meta_get_publishdate, handle))
        self.language = darq.book.language_name(self.detail(mobi.mobi_meta_get_language, handle))

    def read_parts(self, rawml: Rawml):
        markup = parts_of(rawml.markup)
        flow = parts_of(rawml.flow)
        for uid, file_type, data in markup:
            name = f"part{uid:05d}.{extension(file_type)}"
            self.held[name] = FILE_TYPES.get(file_type, ("", "application/octet-stream"))[1], \
                bookfile.page_breaks(data)
            if file_type == HTML_TYPE:
                self.parts.append(name)

        # The first flow's the text the parts are rebuilt from.
        for uid, file_type, data in flow[1:]:
            self.held[f"flow{uid:05d}.{extension(file_type)}"] = \
                FILE_TYPES.get(file_type, ("", "application/octet-stream"))[1], data

        for uid, file_type, data in parts_of(rawml.resources):
            if file_type == NCX_TYPE:
                self.contents = bookfile.parse_ncx(data)
            elif file_type != OPF_TYPE:
                self.held[f"resource{uid:05d}.{extension(file_type)}"] = \
                    FILE_TYPES.get(file_type, ("", "application/octet-stream"))[1], data
        if not self.parts:
            raise Unreadable("It has nothing to read")

    def get(self, name: str) -> tuple[str, bytes] | None:
        return self.held.get(name)
