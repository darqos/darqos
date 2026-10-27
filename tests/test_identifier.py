# darqos
# Copyright (C) 2026 David Arnold

# Tests for type and item identifiers, names, and URL serialization,
# in darq/identifier.py.

import pytest

from darq.errors import InvalidIdentifierError, InvalidURLError
from darq.identifier import (
    MAX_ITEM_ID_LENGTH,
    MAX_ITEM_SPECIFIER_LENGTH,
    MAX_NAME_LENGTH,
    MAX_TYPE_ID_LENGTH,
    canonical_item_id,
    canonical_name,
    canonical_type_id,
    is_valid_item_id,
    is_valid_type_id,
    make_item_id,
    serialize_url,
    split_item_id,
)


################################################################
# Type identifiers
################################################################

@pytest.mark.parametrize("type_id", [
    "public.item",
    "public.utf8-plain-text",
    "net.daringfireball.markdown",
    "text/plain",
    "a",
    "\u00e9tat.document",
    "日本語.文書",
])
def test_valid_type_ids(type_id):
    assert is_valid_type_id(type_id)
    assert canonical_type_id(type_id) == type_id


@pytest.mark.parametrize("type_id", [
    "",
    "public:item",        # colons separate an item identifier's parts
    "_private",           # underscore is XID_Continue, not XID_Start
    "-x",
    ".x",
    "1password.vault",
    "public item",
    "public\titem",
    "public.item\n",
    "cat🐱",
])
def test_invalid_type_ids(type_id):
    assert not is_valid_type_id(type_id)
    with pytest.raises(InvalidIdentifierError):
        canonical_type_id(type_id)


@pytest.mark.parametrize("given, canonical", [
    ("Public.Item", "public.item"),
    ("PUBLIC.ITEM", "public.item"),
    ("Straße", "strasse"),                # full case folding
    ("ｆｕｌｌ.ｗｉｄｔｈ", "full.width"),   # NFKC
    ("ﬁle.type", "file.type"),            # ligature
    ("soft\u00adhyphen", "softhyphen"),   # default-ignorable, removed
    ("public.it\u202eem", "public.item"), # bidi override, too
    ("e\u0301", "\u00e9"),                # composed
])
def test_type_ids_are_nfkc_casefolded(given, canonical):
    assert canonical_type_id(given) == canonical


def test_type_id_length_is_after_folding():
    assert is_valid_type_id("a" * MAX_TYPE_ID_LENGTH)
    assert not is_valid_type_id("a" * (MAX_TYPE_ID_LENGTH + 1))

    # Each "İ" is 2 bytes, but folds to "i" and a combining dot, which
    # are 3, so this is too long once folded.
    assert len(("a" + "İ" * 63).encode()) == MAX_TYPE_ID_LENGTH
    assert not is_valid_type_id("a" + "İ" * 63)


################################################################
# Item identifiers
################################################################

@pytest.mark.parametrize("item_id, parts", [
    ("net.daringfireball.markdown:c5f1ad2a-ea33-4248-b116-9c363cbade66",
     ("net.daringfireball.markdown", "c5f1ad2a-ea33-4248-b116-9c363cbade66")),
    ("public.url:https://example.com/a%20b?q=1#top",
     ("public.url", "https://example.com/a%20b?q=1#top")),
    ("public.item:a:b:c", ("public.item", "a:b:c")),
    ("public.item::", ("public.item", ":")),
    ("public.item:9", ("public.item", "9")),
    ("public.item:Mixed.Case", ("public.item", "Mixed.Case")),
    ("public.item:日本語", ("public.item", "日本語")),
    ("Public.Item:x", ("public.item", "x")),
])
def test_split_item_ids(item_id, parts):
    assert is_valid_item_id(item_id)
    assert split_item_id(item_id) == parts
    assert canonical_item_id(item_id) == make_item_id(*parts) == ":".join(parts)


@pytest.mark.parametrize("item_id", [
    "",
    "public.item",
    "public.item:",
    ":x",
    "bad type:x",
    "public.item:a b",
    "public.item:a\tb",
    "public.item:a\nb",
    "public.item:a\x00b",
    "public.item:cat🐱",
    "public.item:a\u202eb",          # bidi override
    "public.item:a\u200db",          # zero-width joiner
    "public.item:\u00a0",            # no-break space
])
def test_invalid_item_ids(item_id):
    assert not is_valid_item_id(item_id)
    with pytest.raises(InvalidIdentifierError):
        split_item_id(item_id)


def test_item_specifiers_are_case_sensitive_nfkc():
    assert split_item_id("public.item:ABC")[1] == "ABC"
    assert split_item_id("public.item:ｆｕｌｌ")[1] == "full"


def test_item_id_lengths():
    longest = make_item_id("a" * MAX_TYPE_ID_LENGTH, "b" * MAX_ITEM_SPECIFIER_LENGTH)
    assert len(longest.encode()) == MAX_ITEM_ID_LENGTH == 1023
    with pytest.raises(InvalidIdentifierError):
        make_item_id("a", "b" * (MAX_ITEM_SPECIFIER_LENGTH + 1))


################################################################
# Names
################################################################

@pytest.mark.parametrize("name, canonical", [
    ("edited", "edited"),
    ("Edited", "edited"),
    ("type_lens", "type_lens"),
    ("STRASSE", "strasse"),
    ("Straße", "strasse"),
    ("ﬁle", "file"),
    ("ｌｅｎｓ", "lens"),
    ("le\u00ADns", "lens"),
    ("gelöscht", "gelöscht"),
    ("😀", "😀"),
])
def test_canonical_name(name, canonical):
    assert canonical_name(name) == canonical


@pytest.mark.parametrize("name", [
    "",
    "\u00AD",
    "two words",
    " lens",
    "lens\t",
    "a\u00A0b",
    "a\u3000b",
    "a\u2028b",
    "nul\x00",
    "bell\x07",
    "del\x7f",
    "c1\x85",
])
def test_invalid_names(name):
    with pytest.raises(InvalidIdentifierError):
        canonical_name(name)


def test_name_length():
    assert canonical_name("a" * MAX_NAME_LENGTH) == "a" * MAX_NAME_LENGTH
    with pytest.raises(InvalidIdentifierError):
        canonical_name("a" * (MAX_NAME_LENGTH + 1))

    # Bytes of UTF-8, after normalizing: "½" is 2 bytes, but its NFKC
    # form, "1⁄2", is 5.
    assert canonical_name("é" * 15) == "é" * 15
    with pytest.raises(InvalidIdentifierError):
        canonical_name("é" * 16)
    assert canonical_name("a" * 26 + "½") == "a" * 26 + "1⁄2"
    with pytest.raises(InvalidIdentifierError):
        canonical_name("a" * 27 + "½")


################################################################
# URLs
################################################################

@pytest.mark.parametrize("url, serialized", [
    ("https://example.com/", "https://example.com/"),
    ("HTTPS://Example.COM:443/Path", "https://example.com/Path"),
    ("https://bücher.example/ä b?q=ü#😀",
     "https://xn--bcher-kva.example/%C3%A4%20b?q=%C3%BC#%F0%9F%98%80"),
    ("http://[::1]:8080/x", "http://[::1]:8080/x"),
    ("mailto:someone@example.com", "mailto:someone@example.com"),
])
def test_serialize_url(url, serialized):
    assert serialize_url(url) == serialized
    # Serialized URLs are valid item specifiers.
    assert split_item_id("public.url:" + serialize_url(url))[1] == serialized


def test_serialize_url_is_idempotent():
    once = serialize_url("https://bücher.example/ä b")
    assert serialize_url(once) == once


@pytest.mark.parametrize("url", ["", "not a url", "/relative/path", "http://"])
def test_invalid_urls(url):
    with pytest.raises(InvalidURLError):
        serialize_url(url)
