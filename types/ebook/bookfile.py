# darqos
# Copyright (C) 2026 David Arnold

"""An e-book file, in one form, whatever its format (see doc/book.rst).

A Book is its details, its parts in reading order, its table of contents,
and whatever else it holds, by name.  EPUB is read here: it's a zip file
of XHTML, CSS and images.  MOBI and AZW are read by mobifile.py.  It uses
neither Qt nor Darq, so it's tested alone.

A file comes from anywhere, so it's untrusted: nothing's extracted to
disk, a name's only ever looked up, and sizes are limited."""

import dataclasses
import html.parser
import io
import posixpath
import re
import typing
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import darq.book


# Most bytes of an EPUB, unpacked: a zip file can claim anything.
MAX_UNPACKED = 1024 * 1024 * 1024

# Most characters of a book's text to index.
MAX_TEXT = 4 * 1024 * 1024

HTML = "text/html;charset=utf-8"
XHTML = "application/xhtml+xml"

# EPUB encryption that isn't DRM: obfuscated fonts, which are still
# readable.  Anything else in encryption.xml is.
FONT_OBFUSCATION = frozenset({"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"})


class NotABook(ValueError):
    """The data isn't a file of this format."""


class Unreadable(Exception):
    """The file's what it should be, but can't be read: it's kept, and
    the reason's said."""


class Encrypted(Unreadable):
    """The file has DRM."""


@dataclasses.dataclass
class Entry:
    """An entry in a table of contents."""

    title: str
    target: str
    level: int = 0


class Book(typing.Protocol):
    """A book, in one form."""

    title: str
    authors: list[str]
    isbn: str
    publisher: str
    date: str
    language: str
    cover: str
    parts: list[str]
    contents: list[Entry]

    def get(self, name: str) -> tuple[str, bytes] | None:
        """Return a thing the book holds: its media type, and data."""
        ...


class TextOf(html.parser.HTMLParser):
    """Collects the text of a part, without its styles and scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skipping += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping and data.strip():
            self.pieces.append(data.strip())


def text_of(book: Book) -> str:
    """Return a book's text, for the Index."""
    pieces: list[str] = []
    length = 0
    for name in book.parts:
        found = book.get(name)
        if found is None:
            continue
        reader = TextOf()
        reader.feed(found[1].decode("utf-8", "replace"))
        pieces += reader.pieces
        length += sum(len(p) + 1 for p in reader.pieces)
        if length > MAX_TEXT:
            break
    return "\n".join(pieces)[:MAX_TEXT]


def local(element: ET.Element) -> str:
    """Return an element's name, without its namespace."""
    return element.tag.rpartition("}")[2]


def children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if local(child) == name]


def words(element: ET.Element | None) -> str:
    """Return an element's text, tidied."""
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def parse_ncx(data: bytes, base: str = "") -> list[Entry]:
    """Return the table of contents of an NCX file, with targets relative
    to a directory.  Nothing's said if it can't be read: a book's still a
    book without one."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    found: list[Entry] = []

    def walk(parent: ET.Element, level: int):
        for point in children(parent, "navPoint"):
            label = next(iter(children(point, "navLabel")), None)
            content = next(iter(children(point, "content")), None)
            target = content.get("src", "") if content is not None else ""
            if target:
                found.append(Entry(words(label), resolve(base, target), level))
            walk(point, level + 1)

    for navigation in children(root, "navMap"):
        walk(navigation, 0)
    return found


def resolve(base: str, href: str) -> str:
    """Return a link's target, as a name, and perhaps a fragment."""
    path, hash_mark, fragment = href.partition("#")
    name = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(path))) if path else base
    return f"{name}#{fragment}" if hash_mark else name


def parse_nav(data: bytes, base: str) -> list[Entry]:
    """Return the table of contents of an EPUB 3 navigation document."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    found: list[Entry] = []

    def walk(items: ET.Element, level: int):
        for item in children(items, "li"):
            link = next((e for e in item.iter() if local(e) == "a" and e.get("href")), None)
            if link is not None:
                found.append(Entry(words(link), resolve(base, link.get("href", "")), level))
            for sublist in children(item, "ol"):
                walk(sublist, level + 1)

    for element in root.iter():
        if local(element) == "nav" and any(k.endswith("type") and v == "toc" for k, v in element.attrib.items()):
            for items in children(element, "ol"):
                walk(items, 0)
            break
    return found


class Epub:
    """An EPUB, read as it's asked for."""

    def __init__(self, data: bytes):
        """Constructor.

        :raises NotABook: if it isn't an EPUB.
        :raises Encrypted: if it has DRM."""
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            names = set(self.zip.namelist())
            total = sum(i.file_size for i in self.zip.infolist())
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            raise NotABook(f"It isn't a zip file: {e}") from None
        if "META-INF/container.xml" not in names:
            raise NotABook("It has no META-INF/container.xml")
        if total > MAX_UNPACKED:
            raise Unreadable("It's too large, unpacked")
        self.names = names
        self.types: dict[str, str] = {}
        self.title = ""
        self.authors: list[str] = []
        self.isbn = ""
        self.publisher = ""
        self.date = ""
        self.language = ""
        self.cover = ""
        self.parts: list[str] = []
        self.contents: list[Entry] = []
        self.check_encryption()
        self.read_package()

    def read(self, name: str) -> bytes:
        try:
            return self.zip.read(name)
        except (KeyError, zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError) as e:
            raise Unreadable(f"Can't read {name}: {e}") from None

    def check_encryption(self):
        if "META-INF/encryption.xml" not in self.names:
            return
        try:
            root = ET.fromstring(self.read("META-INF/encryption.xml"))
        except ET.ParseError:
            raise Encrypted("It has DRM") from None
        for element in root.iter():
            if local(element) == "EncryptionMethod" and element.get("Algorithm") not in FONT_OBFUSCATION:
                raise Encrypted("It has DRM")

    def read_package(self):
        try:
            container = ET.fromstring(self.read("META-INF/container.xml"))
            path = next(e.get("full-path", "") for e in container.iter() if local(e) == "rootfile")
            package = ET.fromstring(self.read(path))
        except (ET.ParseError, StopIteration):
            raise Unreadable("Its package can't be read") from None

        base = posixpath.dirname(path)
        metadata = next(iter(children(package, "metadata")), None)
        if metadata is not None:
            self.title = words(next(iter(children(metadata, "title")), None))
            self.authors = [words(e) for e in children(metadata, "creator") if words(e)]
            self.publisher = words(next(iter(children(metadata, "publisher")), None))
            self.date = darq.book.date_of(words(next(iter(children(metadata, "date")), None)))
            self.language = darq.book.language_name(words(next(iter(children(metadata, "language")), None)))
            for identifier in children(metadata, "identifier"):
                isbn = darq.book.clean_isbn(words(identifier).removeprefix("urn:isbn:"))
                if isbn:
                    self.isbn = isbn

        items: dict[str, str] = {}
        nav = ""
        for manifest in children(package, "manifest"):
            for item in children(manifest, "item"):
                name = resolve(base, item.get("href", ""))
                items[item.get("id", "")] = name
                self.types[name] = item.get("media-type", "application/octet-stream")
                if "nav" in item.get("properties", "").split():
                    nav = name
                if "cover-image" in item.get("properties", "").split():
                    self.cover = name

        # EPUB 2 says which is its cover with a meta.
        if not self.cover and metadata is not None:
            named = next((m.get("content", "") for m in children(metadata, "meta") if m.get("name") == "cover"), "")
            self.cover = items.get(named, "")
        if self.cover and not self.types.get(self.cover, "").startswith("image/"):
            self.cover = ""

        spines = children(package, "spine")
        for spine in spines:
            self.parts += [items[r.get("idref", "")] for r in children(spine, "itemref")
                           if r.get("idref", "") in items and items[r.get("idref", "")] in self.names]
        if not self.parts:
            raise Unreadable("It has nothing to read")

        if nav in self.names:
            self.contents = parse_nav(self.read(nav), posixpath.dirname(nav))
        ncx = items.get(spines[0].get("toc", "")) if spines else None
        if not self.contents and ncx in self.names:
            self.contents = parse_ncx(self.read(typing.cast(str, ncx)), posixpath.dirname(typing.cast(str, ncx)))

    def get(self, name: str) -> tuple[str, bytes] | None:
        if name not in self.names:
            return None
        media_type = self.types.get(name) or "application/octet-stream"
        return media_type, self.read(name)


# What a part of a Mobipocket file says of a page break, which a browser
# doesn't know.
PAGE_BREAK = re.compile(rb"<mbp:pagebreak\s*/?>(\s*</mbp:pagebreak>)?", re.IGNORECASE)
COLUMN_BREAK = b'<div style="break-before: column"></div>'
LAST_BREAKS = re.compile(rb"(\s*<mbp:pagebreak\s*/?>(\s*</mbp:pagebreak>)?)+\s*(?=</body>)", re.IGNORECASE)


def page_breaks(data: bytes) -> bytes:
    """Return a part with Mobipocket's page breaks made ones a browser
    understands: an old book's one long part, broken into chapters.  A
    break at the very end would only make an empty page."""
    return PAGE_BREAK.sub(COLUMN_BREAK, LAST_BREAKS.sub(b"", data))


def open_book(type_id: str, data: bytes) -> Book:
    """Read a file of a type.

    :raises NotABook: if it isn't one.
    :raises Unreadable: if it can't be read."""
    if type_id == "darq.book.epub":
        return Epub(data)
    import mobifile
    return mobifile.Mobi(data)
