# darqos
# Copyright (C) 2026 David Arnold

# Type and item identifiers (see doc/types.rst and doc/item.rst), and
# short names, such as History's event and source names.
#
# A type identifier is stored and compared in its NFKC_Casefold form.
# An item identifier is a type identifier, a colon, and an item
# specifier: it's parsed by splitting at the first colon.  Item
# specifiers are case-sensitive, and a URL used as one must be in its
# WHATWG serialized form (see serialize_url()).

import unicodedata

import ada_url

from .errors import InvalidIdentifierError, InvalidURLError


__all__ = [
    "MAX_TYPE_ID_LENGTH",
    "MAX_ITEM_SPECIFIER_LENGTH",
    "MAX_ITEM_ID_LENGTH",
    "canonical_type_id",
    "is_valid_type_id",
    "canonical_item_id",
    "is_valid_item_id",
    "split_item_id",
    "make_item_id",
    "serialize_url",
    "MAX_NAME_LENGTH",
    "canonical_name",
]


# Maximum lengths, in bytes of UTF-8, after normalization, and not
# including any terminating U+0000.  An item identifier fits in 1024
# bytes with its terminator.
MAX_TYPE_ID_LENGTH = 127
MAX_ITEM_SPECIFIER_LENGTH = 895
MAX_ITEM_ID_LENGTH = MAX_TYPE_ID_LENGTH + 1 + MAX_ITEM_SPECIFIER_LENGTH

# Maximum length of a name, likewise: it fits in 32 bytes with its
# terminator.
MAX_NAME_LENGTH = 31

# Characters allowed in a type identifier, after the first, as well as
# XID_Continue.
TYPE_ID_EXTRA = frozenset("-./")

# Unicode's Default_Ignorable_Code_Point property, which NFKC_Casefold
# removes, and Python's unicodedata doesn't expose.  From
# DerivedCoreProperties.txt.
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_default_ignorable(c: str) -> bool:
    code = ord(c)
    return any(low <= code <= high for low, high in DEFAULT_IGNORABLE_RANGES)


def _is_xid_start(c: str) -> bool:
    # Python identifiers start with XID_Start, or an underscore.
    return c != "_" and c.isidentifier()


def _is_xid_continue(c: str) -> bool:
    return ("a" + c).isidentifier()


def _nfkc_casefold(s: str) -> str:
    """Return s in Unicode's NFKC_Casefold form.

    That's NFKC, case folding, and removing default-ignorable code
    points, repeated until it's stable."""
    while True:
        folded = unicodedata.normalize("NFKC", s)
        folded = "".join(c for c in folded if not _is_default_ignorable(c))
        folded = unicodedata.normalize("NFKC", folded.casefold())
        if folded == s:
            return folded
        s = folded


def _utf8_length(s: str) -> int:
    return len(s.encode("utf-8"))


def canonical_type_id(type_id: str) -> str:
    """Return a type identifier in its canonical, NFKC_Casefold form.

    :param type_id: Type identifier, in any case or normalization.
    :raises InvalidIdentifierError: If it isn't a valid type identifier."""
    canonical = _nfkc_casefold(type_id)
    if not canonical:
        raise InvalidIdentifierError("Empty type identifier")
    if _utf8_length(canonical) > MAX_TYPE_ID_LENGTH:
        raise InvalidIdentifierError(f"Type identifier is longer than {MAX_TYPE_ID_LENGTH} bytes")
    if not _is_xid_start(canonical[0]):
        raise InvalidIdentifierError(f"Type identifier can't start with U+{ord(canonical[0]):04X}")
    for c in canonical[1:]:
        if not (_is_xid_continue(c) or c in TYPE_ID_EXTRA):
            raise InvalidIdentifierError(f"Type identifier can't contain U+{ord(c):04X}")
    return canonical


def is_valid_type_id(type_id: str) -> bool:
    """Return True if type_id is a valid type identifier, in any case."""
    try:
        canonical_type_id(type_id)
    except InvalidIdentifierError:
        return False
    return True


def _canonical_specifier(specifier: str) -> str:
    """Return an item specifier in its canonical, NFKC form.

    Every character must be printable ASCII (U+0021 to U+007E), or a
    non-ASCII XID_Continue character that isn't default-ignorable (and
    so invisible), like U+200D ZERO WIDTH JOINER."""
    canonical = unicodedata.normalize("NFKC", specifier)
    if not canonical:
        raise InvalidIdentifierError("Empty item specifier")
    if _utf8_length(canonical) > MAX_ITEM_SPECIFIER_LENGTH:
        raise InvalidIdentifierError(f"Item specifier is longer than {MAX_ITEM_SPECIFIER_LENGTH} bytes")
    for c in canonical:
        if not ("!" <= c <= "~" or (c > "\x7f" and _is_xid_continue(c) and not _is_default_ignorable(c))):
            raise InvalidIdentifierError(f"Item specifier can't contain U+{ord(c):04X}")
    return canonical


def split_item_id(item_id: str) -> tuple[str, str]:
    """Split an item identifier into its canonical type identifier and
    item specifier.

    :param item_id: Item identifier: a type identifier, a colon, and an
        item specifier.
    :raises InvalidIdentifierError: If it isn't a valid item identifier."""
    type_id, colon, specifier = item_id.partition(":")
    if not colon:
        raise InvalidIdentifierError("Item identifier has no colon")
    return canonical_type_id(type_id), _canonical_specifier(specifier)


def make_item_id(type_id: str, specifier: str) -> str:
    """Return the canonical item identifier for a type and item specifier.

    :raises InvalidIdentifierError: If either part isn't valid."""
    return f"{canonical_type_id(type_id)}:{_canonical_specifier(specifier)}"


def canonical_item_id(item_id: str) -> str:
    """Return an item identifier in its canonical form: its type
    identifier in NFKC_Casefold form, and its item specifier in NFKC.

    :raises InvalidIdentifierError: If it isn't a valid item identifier."""
    return make_item_id(*split_item_id(item_id))


def is_valid_item_id(item_id: str) -> bool:
    """Return True if item_id is a valid item identifier."""
    try:
        split_item_id(item_id)
    except InvalidIdentifierError:
        return False
    return True


def serialize_url(url: str) -> str:
    """Return a URL in its WHATWG serialized form, for use as an item
    specifier.

    The serialized form is printable ASCII: non-ASCII host names are
    converted to Punycode, and other non-ASCII characters are
    percent-encoded.  Parsing also normalizes, for instance lower-casing
    the scheme and host, and removing a default port.

    :param url: Absolute URL, as it might be typed or displayed.
    :raises InvalidURLError: If it can't be parsed as a URL."""
    try:
        return ada_url.URL(url).href
    except ValueError:
        raise InvalidURLError(f"Not a valid URL: {url!r}") from None


def canonical_name(name: str) -> str:
    """Return a name, such as a History event or source, in its
    canonical, NFKC_Casefold form.

    It must have no whitespace or control characters.

    :raises InvalidIdentifierError: If it isn't a valid name."""
    canonical = _nfkc_casefold(name)
    if not canonical:
        raise InvalidIdentifierError("Empty name")
    if _utf8_length(canonical) > MAX_NAME_LENGTH:
        raise InvalidIdentifierError(f"Name is longer than {MAX_NAME_LENGTH} bytes")
    for c in canonical:
        # isspace() is Unicode's White_Space, plus U+001C to U+001F,
        # which are control characters anyway.
        if c.isspace() or unicodedata.category(c) == "Cc":
            raise InvalidIdentifierError(f"Name can't contain U+{ord(c):04X}")
    return canonical
