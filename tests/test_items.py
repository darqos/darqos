# darqos
# Copyright (C) 2026 David Arnold

# Tests for items' entities (see doc/item.rst): every item a KB
# entity, identified by its blob's Storage key, with its kind, name, and
# when it was made, against a KB service in this process (see
# test_kb_type.py).

import datetime
import json
import pathlib
import typing

import pytest

import darq.items as items
import darq.kb as model
from darq.ui import links

from test_kb_type import implementation, index, kb, kb_service, ports  # noqa: F401


KEY = "03d1824dc667bb6b343e27a5a610b06b"
WHEN = datetime.datetime(2026, 11, 8, 15, 30, 12, 345678, tzinfo=datetime.timezone(datetime.timedelta(hours=11)))
ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def store(kb, implementation):  # noqa: F811
    """The KB, with the vocabulary the KB type implementation ensures."""
    return kb


def value(statement: model.Statement) -> model.Value:
    return statement.get_value()


def only(entity: model.Entity, property_id: str) -> model.Statement:
    [statement] = entity.get_statements(property_id)
    return statement


def sources(statement: model.Statement) -> list[str]:
    return [a.value.entity_id for r in statement.references for a in r.snaks
            if isinstance(a.value, model.EntityValue)]


################################################################
# An item's entity
################################################################

def test_its_kind_and_class():
    entity = items.made("public.jpeg", KEY)
    assert model.recorded_type(entity) == "public.jpeg"
    assert typing_entity_id(value(only(entity, model.INSTANCE_OF))) == items.IMAGE_FILE
    assert sources(only(entity, model.DARQOS_TYPE)) == [model.DARQOS]


def typing_entity_id(v: model.Value) -> str:
    assert isinstance(v, model.EntityValue)
    return v.entity_id


def test_its_name_and_file():
    entity = items.made("com.adobe.pdf", KEY, label=" Bliss ", file_name="bliss.pdf", source="an-import")
    assert entity.get_label() == "Bliss"
    name = only(entity, items.FILE_NAME)
    assert typing_value(name) == "bliss.pdf" and sources(name) == ["an-import"]


def typing_value(statement: model.Statement) -> str:
    v = value(statement)
    assert isinstance(v, model.StringValue)
    return v.value


def test_one_without_a_name_has_no_label():
    entity = items.made("public.utf8-plain-text", KEY)
    assert entity.labels == {} and entity.get_statements(items.FILE_NAME) == []


def test_when_it_was_made_is_to_the_second_in_utc():
    made = value(only(items.made("public.png", KEY, created=WHEN), items.CREATED))
    assert isinstance(made, model.TimeValue)
    assert (made.year, made.month, made.day, made.hour, made.minute, made.second) == (2026, 11, 8, 4, 30, 12)
    assert made.precision == model.TimePrecision.Seconds


def test_a_kind_thats_not_in_storage_is_refused():
    with pytest.raises(ValueError):
        items.made("darq.person", KEY)


def test_the_kinds_are_the_storage_types_implementations():
    """Every type text, images, PDFs and e-books implement, and no other."""
    implemented = set()
    for name in ("text", "image", "pdf", "ebook", "data"):
        manifest = json.loads((ROOT / "types" / name / "type.json").read_text())
        implemented |= {t["id"] for t in manifest["types"] if t.get("implemented")}
    assert implemented == set(items.KINDS)


def test_a_kinds_name():
    assert links.type_name("public.jpeg") == "JPEG Image"
    assert links.type_name("com.adobe.pdf", 3) == "PDFs"


################################################################
# In the KB
################################################################

def test_creating_one(store):
    entity = items.create(store, "public.jpeg", KEY, label="Uluru", file_name="IMG_0001.JPG", created=WHEN)
    got = store.get(KEY)
    assert got is not None and got.get_label() == "Uluru"
    assert model.kind_of(got, store.get) == "public.jpeg"
    assert model.item_id(got.id, model.recorded_type(got)) == f"public.jpeg:{KEY}"
    assert entity.revision == got.revision


def test_creating_one_thats_there_changes_nothing(store):
    first = items.create(store, "public.jpeg", KEY, label="Uluru")
    again = items.create(store, "public.jpeg", KEY, label="Something else")
    assert again.revision == first.revision and again.get_label() == "Uluru"


def test_its_class_is_found_with_its_instances(store):
    items.create(store, "public.png", KEY)
    items.create(store, "public.utf8-plain-text", "0348e75710eaf008796f266d7a03618c")
    assert list(store.instances(items.IMAGE_FILE)) == [KEY]


def test_what_it_links_to_lists_it_by_its_kind(store):
    """An image file's page lists its instances as JPEG Images."""
    items.create(store, "public.jpeg", KEY)
    assert [(p, t, n) for p, _, t, n in store.link_counts(items.IMAGE_FILE)] == [
        (model.INSTANCE_OF, "public.jpeg", 1)]


def test_reading_one(store):
    assert items.read(store, KEY) is None
    items.create(store, "public.jpeg", KEY)
    assert items.read(store, KEY) is not None
    items.destroy(store, KEY)
    assert items.read(store, KEY) is None


def test_renaming_one(store):
    items.create(store, "public.jpeg", KEY)
    assert items.rename(store, KEY, " Uluru at dusk ").get_label() == "Uluru at dusk"
    before = store.get(KEY)
    assert items.rename(store, KEY, "Uluru at dusk").revision == before.revision
    assert items.rename(store, KEY, "").labels == {}
    assert items.rename(store, KEY, "").revision == store.get(KEY).revision


def test_renaming_one_without_an_entity(store):
    with pytest.raises(LookupError):
        items.rename(store, KEY, "Uluru")


class Conflicting:
    """A KB whose first writes conflict, as another's change would."""

    def __init__(self, store: model.KB, conflicts: int):
        self.kb = store
        self.conflicts = conflicts

    def get(self, entity_id: str, follow: bool = True):
        return self.kb.get(entity_id, follow)

    def put(self, *entities: model.Entity):
        if self.conflicts:
            self.conflicts -= 1
            raise model.ConflictError("changed", entities[0].id)
        return self.kb.put(*entities)


def test_renaming_tries_again_after_a_conflict(store):
    items.create(store, "public.jpeg", KEY)
    assert items.rename(typing.cast(model.KB, Conflicting(store, 2)), KEY, "Uluru").get_label() == "Uluru"


def test_renaming_gives_up_after_conflicts(store):
    items.create(store, "public.jpeg", KEY)
    with pytest.raises(model.ConflictError):
        items.rename(typing.cast(model.KB, Conflicting(store, items.TRIES)), KEY, "Uluru")


def test_destroying_one(store):
    items.create(store, "public.jpeg", KEY)
    items.destroy(store, KEY)
    assert store.get(KEY, follow=False).destroyed
    items.destroy(store, KEY)
    items.destroy(store, "not-there")


################################################################
# The vocabulary
################################################################

def test_the_kb_type_implementation_ensures_the_vocabulary(implementation, kb):  # noqa: F811
    created = kb.get(items.CREATED)
    assert isinstance(created, model.Property) and created.get_label() == "created"
    assert kb.get(items.IMAGE_FILE).get_label() == "image file"


################################################################
# Tags, depicts and image
################################################################

@pytest.fixture
def tags(kb, implementation):  # noqa: F811
    """The KB, to read, and written through the KB type implementation."""
    from test_kb_type import Writer
    return kb, model.KB(kb.client, Writer(implementation))


def test_a_tag_is_made_once(tags):
    reader, writer = tags
    made = items.tag(reader, writer, "  To   read ")
    tag = reader.get(made)
    assert tag is not None and tag.get_label() == "To read"
    assert model.kind_of(tag, reader.get) == items.TAG_TYPE
    assert list(reader.instances(model.TAG_CLASS)) == [made]

    # Found again by its name, whatever its case, or an alias.
    assert items.tag(reader, writer, "to READ") == made
    tag.add_alias("Reading list")
    writer.put(tag)
    assert items.tag(reader, writer, "reading list") == made
    assert items.tag(reader, writer, "To") != made


def test_a_tag_needs_a_name(tags):
    with pytest.raises(ValueError):
        items.tag(tags[0], tags[1], "   ")


def test_tags_depicts_and_image_are_in_the_vocabulary(store):
    for property_id, label in [(items.HAS_TAG, "has tag"), (items.DEPICTS, "depicts"), (items.IMAGE, "image")]:
        found = store.get(property_id)
        assert found is not None and found.get_label() == label
        assert found.datatype == model.Datatype.Item
    assert store.get(model.TAG_CLASS).get_label() == "tag"
    assert links.type_name("darq.tag", 2) == "Tags"
    for _, inverse, label in items.INVERSE_LABELS:
        assert store.get(inverse).get_label() == label


def test_what_has_a_tag_links_to_it(store, tags):
    reader, writer = tags
    made = items.tag(reader, writer, "Fiction")
    book = items.create(store, "public.jpeg", KEY, label="Cover")
    book.claim(items.HAS_TAG, model.EntityValue(made))
    writer.put(book)
    assert [(p, t, n) for p, _, t, n in store.link_counts(made)] == [(items.HAS_TAG, "public.jpeg", 1)]
