# darqos
# Copyright (C) 2026 David Arnold

# Tests for books (see doc/book.rst): the four levels read as one book,
# fields saved to their levels, the card, the form, and, live, the lens
# and its store, through a real KB.

import importlib.util
import json
import pathlib
import sys
import types as python_types
import typing

import pytest

import darq
import darq.book
import darq.kb as model
import darq.message
import darq.url
from darq.book import Book, BookError, Copy, Edition
from darq.protocols.lens import Status, UseRequest
from darq.ui import types as ui_types

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "book"


def load():
    sys.path.insert(0, str(LENS_PATH))
    try:
        spec = importlib.util.spec_from_file_location("book_lens", LENS_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LENS_PATH))
    return module


lens = load()
card = lens.book_card
form = lens.book_form


class MemoryStore:
    """A store of entities in memory, as an item's ports would be."""

    def __init__(self):
        self.entities: dict[str, model.Entity] = {}
        self.kinds: dict[str, str] = {}
        self.writes = 0
        self.fail_after: int | None = None

    def add(self, type_id: str, label: str, entity_id: str | None = None) -> model.Entity:
        entity = model.Item(entity_id)
        entity.set_label(label)
        self.entities[entity.id] = entity
        self.kinds[entity.id] = type_id
        return entity

    def content(self, *item_ids: str) -> "MemoryStore":
        """Make items whose content's elsewhere, as e-books and covers are,
        by their identifiers: a link's to an entity that's there."""
        for item_id in item_ids:
            kind, entity_id = darq.split_item_id(item_id)
            self.add(kind, "", entity_id)
        return self

    def item(self, entity: typing.Any) -> str:
        return model.item_id(entity.id, self.kinds[entity.id])

    def of_kind(self, type_id: str) -> list[model.Entity]:
        return [e for e in self.entities.values() if self.kinds[e.id] == type_id]

    def get(self, item_id: str) -> model.Entity | None:
        return self.entities.get(darq.split_item_id(item_id)[1])

    def children(self, item_id: str, property_id: str, type_id: str) -> list[model.Entity]:
        target = darq.split_item_id(item_id)[1]
        return [e for e in self.of_kind(type_id)
                if any(typing.cast(typing.Any, s.get_value()).entity_id == target
                       for s in e.get_statements(property_id) if isinstance(s.get_value(), model.EntityValue))]

    def label(self, entity_id: str) -> str:
        entity = self.entities.get(entity_id)
        return (entity.get_label() or "") if entity is not None else ""

    def item_of(self, entity_id: str) -> str:
        return self.item(self.entities[entity_id]) if entity_id in self.entities else ""

    def find(self, label: str, type_id: str) -> str:
        kind = model.ENTITY_TYPE if type_id == darq.book.LANGUAGE_KIND else type_id
        return next((e.id for e in self.of_kind(kind) if e.get_label() == label), "")

    def create(self, type_id: str, label: str) -> model.Entity:
        return self.add(type_id, label)

    def edition_with(self, property_id: str, isbn: str) -> str:
        return next((self.item(e) for e in self.of_kind(darq.book.MANIFESTATION_TYPE)
                     if any(getattr(v, "value", "") == isbn for v in values(e, property_id))), "")

    def works_titled(self, title: str) -> list[str]:
        return [self.item(e) for e in self.of_kind(darq.book.WORK_TYPE) if e.get_label() == title]

    def destroy(self, item_id: str):
        del self.entities[darq.split_item_id(item_id)[1]]

    def change(self, entity: model.Entity, edits: list[model.Edit]) -> model.Entity:
        if self.fail_after == 0:
            raise BookError("the disk is full")
        if self.fail_after is not None:
            self.fail_after -= 1
        if edits:
            self.writes += 1
            self.entities[entity.id] = model.apply_edits(entity, edits)
        return self.entities[entity.id]


def read_book(store: typing.Any, item_id: str) -> typing.Any:
    return darq.book.read(store, item_id)


def bliss() -> Book:
    return Book(title="Bliss", subtitle="A novel", authors=["Peter Carey"], language="English", editions=[
        Edition(publisher="Harper & Row", date="1981", binding="hardcover", edition="First US", isbn_10="0060150300",
                copies=[Copy(location="study, shelf 3", printing="1")]),
        Edition(publisher="Picador", binding="paperback", date="1982", pages="296",
                copies=[Copy(location="office", printing="9")])])


def saved() -> tuple[typing.Any, typing.Any]:
    store = MemoryStore()
    book = bliss()
    darq.book.save(store, book)
    return store, read_book(store, store.item(book.work))


def values(entity: typing.Any, property_id: str) -> list:
    return [s.get_value() for s in entity.get_statements(property_id) if s.rank != model.Rank.Deprecated]


################################################################
# The model
################################################################

def test_a_book_has_four_kinds():
    assert {"darq.book.work", "darq.book.expression", "darq.book.manifestation",
            "darq.book.item"} <= set(model.KIND_TYPES)


def test_saving_makes_the_levels():
    store, _ = saved()
    assert [len(store.of_kind(t)) for t in (darq.book.WORK_TYPE, darq.book.EXPRESSION_TYPE,
                                            darq.book.MANIFESTATION_TYPE, darq.book.ITEM_TYPE)] == [1, 1, 2, 2]


def test_two_editions_are_one_text():
    store, book = saved()
    [expression] = store.of_kind(darq.book.EXPRESSION_TYPE)
    [work] = store.of_kind(darq.book.WORK_TYPE)
    assert [v.entity_id for v in values(expression, darq.book.VERSION_OF)] == [work.id]
    for edition in book.editions:
        assert [v.entity_id for v in values(edition.entity, darq.book.VERSION_OF)] == [expression.id]
        for copy in edition.copies:
            assert [v.entity_id for v in values(copy.entity, darq.book.EXEMPLAR_OF)] == [edition.entity.id]


def test_each_field_goes_to_its_level():
    store, book = saved()
    work = book.work
    expression = book.expression
    picador = book.editions[1]
    assert work.get_label() == "Bliss"
    assert values(work, darq.book.SUBTITLE)[0].text == {"en": "A novel"}
    assert values(work, darq.book.AUTHOR) and not values(expression, darq.book.AUTHOR)
    assert values(expression, darq.book.LANGUAGE) and not values(work, darq.book.LANGUAGE)
    assert values(picador.entity, darq.book.PAGES)[0].amount == 296
    assert values(picador.entity, darq.book.PUBLICATION_DATE)[0].year == 1982
    assert values(picador.copies[0].entity, darq.book.LOCATION)[0].value == "office"

    # A printing's of the copy, and a date's of the edition.
    assert values(picador.copies[0].entity, darq.book.PRINTING)[0].value == "9"
    assert not values(picador.entity, darq.book.PRINTING)


def test_read_from_any_level_is_the_same_book():
    store, book = saved()
    edition = book.editions[0]
    for entity in (book.work, book.expression, edition.entity, edition.copies[0].entity):
        read = read_book(store, store.item(entity))
        assert (read.title, read.authors, read.language) == ("Bliss", ["Peter Carey"], "English")
        assert [e.publisher for e in read.editions] == ["Harper & Row", "Picador"]
        assert read.opened == entity.id


def test_saved_fields_read_back():
    _, book = saved()
    assert (book.subtitle, book.editions[1].date, book.editions[1].pages) == ("A novel", "1982", "296")
    assert book.editions[0].isbn_10 == "0060150300"
    assert (book.editions[1].copies[0].location, book.editions[1].copies[0].printing) == ("office", "9")


def test_editions_are_in_order_of_date():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="None"), Edition(publisher="Late", date="1999"),
                                     Edition(publisher="Early", date="1950")])
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert [e.publisher for e in read.editions] == ["Early", "Late", "None"]


def test_two_copies_of_one_printing():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="P", copies=[Copy(location="a"), Copy(location="b")])])
    darq.book.save(store, book)
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1
    assert [c.location for c in read_book(store, store.item(book.work)).editions[0].copies] == ["a", "b"]


def test_a_second_format():
    store, book = saved()
    store.content("darq.book.epub:abc")
    book.editions.append(Edition(binding="EPUB", copies=[Copy(content="darq.book.epub:abc")]))
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert darq.book.content_format(read.editions[2].copies[0]) == "EPUB"


def test_a_pdf_content_format():
    assert darq.book.content_format(Copy(content="com.adobe.pdf:abc")) == "PDF"
    assert darq.book.content_format(Copy(location="shelf")) == ""


def test_saving_unchanged_writes_nothing():
    store, book = saved()
    store.writes = 0
    darq.book.save(store, book)
    assert store.writes == 0


def test_a_new_work_is_filled_in():
    """New makes the work: the rest is made under it."""
    store = MemoryStore()
    work = store.add(darq.book.WORK_TYPE, "")
    del work.labels["en"]
    book = read_book(store, store.item(work))
    book.title = "Bliss"
    book.editions = [Edition(publisher="Picador")]
    darq.book.save(store, book)
    assert typing.cast(typing.Any, book.work).id == work.id and store.entities[work.id].get_label() == "Bliss"
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1


def test_an_empty_edition_or_copy_isnt_made():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(copies=[Copy()]), Edition(publisher="P", copies=[Copy()])])
    darq.book.save(store, book)
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1
    assert store.of_kind(darq.book.ITEM_TYPE) == []


def test_an_author_is_found_or_made():
    store = MemoryStore()
    carey = store.add(darq.book.PERSON_TYPE, "Peter Carey")
    book = Book(title="T", authors=["Peter Carey", "New Person"])
    darq.book.save(store, book)
    assert values(book.work, darq.book.AUTHOR)[0].entity_id == carey.id
    assert [e.get_label() for e in store.of_kind(darq.book.PERSON_TYPE)] == ["Peter Carey", "New Person"]


def test_an_imprint_is_text_on_the_edition():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="Penguin UK", imprint="Penguin Plays", date="1971")])
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert read.editions[0].imprint == "Penguin Plays"
    entity = read.editions[0].entity
    assert values(entity, darq.book.IMPRINT)[0].value == "Penguin Plays"
    assert entity.get_label() == "Penguin UK, Penguin Plays, 1971"
    assert {e.get_label() for e in store.of_kind(darq.book.ORGANISATION_TYPE)} == {"Penguin UK"}


def test_an_imprint_alone_makes_an_edition():
    store = MemoryStore()
    darq.book.save(store, Book(title="T", editions=[Edition(imprint="Penguin Plays")]))
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1


def test_clearing_an_imprint():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(imprint="Penguin Plays")])
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    read.editions[0].imprint = ""
    darq.book.save(store, read)
    assert values(read.editions[0].entity, darq.book.IMPRINT) == []


def test_a_publisher_is_an_organisation():
    store, book = saved()
    assert {e.get_label() for e in store.of_kind(darq.book.ORGANISATION_TYPE)} == {"Harper & Row", "Picador"}


def test_a_name_thats_kept_keeps_its_entity():
    """Two people can have one name, so it isn't looked up again."""
    store = MemoryStore()
    first = store.add(darq.book.PERSON_TYPE, "John Smith")
    store.add(darq.book.PERSON_TYPE, "John Smith")
    work = store.add(darq.book.WORK_TYPE, "T")
    work.claim(darq.book.AUTHOR, model.EntityValue(store.of_kind(darq.book.PERSON_TYPE)[1].id))
    book = read_book(store, store.item(work))
    book.subtitle = "Changed"
    darq.book.save(store, book)
    assert values(book.work, darq.book.AUTHOR)[0].entity_id != first.id


def test_removing_a_name():
    store, book = saved()
    book.authors = ["Somebody Else"]
    darq.book.save(store, book)
    assert read_book(store, store.item(book.work)).authors == ["Somebody Else"]


def test_clearing_a_field():
    store, book = saved()
    book.subtitle = ""
    book.editions[1].pages = ""
    darq.book.save(store, book)
    assert values(book.work, darq.book.SUBTITLE) == [] and values(book.editions[1].entity, darq.book.PAGES) == []


def test_series_and_volume():
    store = MemoryStore()
    book = Book(title="T", series="The Laundry", volume="1")
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert (read.series, read.volume) == ("The Laundry", "1")
    read.volume = "2"
    darq.book.save(store, read)
    read = read_book(store, store.item(book.work))
    assert (read.series, read.volume) == ("The Laundry", "2")
    assert len(store.of_kind(model.ENTITY_TYPE)) == 1


def test_other_statements_are_left_alone():
    store, book = saved()
    edition = book.editions[0].entity
    store.entities[edition.id].claim(darq.book.DDC, model.StringValue("823"))
    read = read_book(store, store.item(book.work))
    read.editions[0].pages = "300"
    darq.book.save(store, read)
    assert values(read.editions[0].entity, darq.book.DDC)[0].value == "823"


def source_statement(entity: model.Entity, property_id: str, text: str) -> model.Statement:
    statement = entity.claim(property_id, model.StringValue(text))
    statement.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue("library")))
    return statement


def test_a_sources_statement_stays_the_sources():
    """So that a re-import doesn't fight an edit."""
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="P")])
    darq.book.save(store, book)
    entity = store.entities[typing.cast(typing.Any, book.editions[0].entity).id]
    source_statement(entity, darq.book.ISBN_13, "9780000000000")
    read = read_book(store, store.item(book.work))
    read.editions[0].isbn_13 = "9781111111111"
    darq.book.save(store, read)

    statements = read.editions[0].entity.get_statements(darq.book.ISBN_13)
    all_values = [typing.cast(typing.Any, s.get_value()).value for s in statements]
    assert all_values == ["9780000000000", "9781111111111"]
    assert values(read.editions[0].entity, darq.book.ISBN_13)[0].value == "9781111111111"


def test_a_translation_is_another_text():
    store, book = saved()
    german = store.add(darq.book.EXPRESSION_TYPE, "German text")
    german.claim(darq.book.VERSION_OF, model.EntityValue(book.work.id))
    read = read_book(store, store.item(book.work))
    assert [e.id for e in read.others] == [german.id]
    other = read_book(store, store.item(german))
    assert other.expression.id == german.id and [e.id for e in other.others] == [book.expression.id]


def test_a_failed_save_can_be_finished():
    store = MemoryStore()
    book = bliss()
    store.fail_after = 3
    with pytest.raises(BookError):
        darq.book.save(store, book)
    store.fail_after = None
    darq.book.save(store, book)
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert len(store.of_kind(darq.book.EXPRESSION_TYPE)) == 1
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 2


def test_reading_something_missing():
    with pytest.raises(BookError):
        read_book(MemoryStore(), "darq.book.work:nothing")


@pytest.mark.parametrize("change, problem", [
    (lambda b: setattr(b, "title", " "), "title"),
    (lambda b: setattr(b, "finished", "March"), "finished"),
    (lambda b: setattr(b, "started", "2026-13-01"), "started"),
    (lambda b: setattr(b.editions[0], "date", "1982/3"), "date"),
    (lambda b: setattr(b.editions[0], "pages", "many"), "Pages"),
    (lambda b: setattr(b.editions[0].copies[0], "printed", "soon"), "printed"),
])
def test_check_refuses(change, problem):
    book = bliss()
    change(book)
    assert problem in darq.book.check(book)
    with pytest.raises(BookError):
        darq.book.save(MemoryStore(), book)


@pytest.mark.parametrize("date", ["1982", "1982-03", "1982-03-04"])
def test_dates_keep_their_precision(date):
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(date=date)])
    darq.book.save(store, book)
    assert read_book(store, store.item(book.work)).editions[0].date == date


def test_vocabulary_has_inverse_labels():
    entities = {e.id for e in darq.book.vocabulary()}
    assert {item for _, item, _ in darq.book.INVERSE_LABELS} <= entities


def test_vocabulary_has_no_repeats():
    everything = [e.id for e in (*darq.message.vocabulary(), *darq.url.vocabulary(), *darq.book.vocabulary())]
    assert len(everything) == len(set(everything))


################################################################
# The card
################################################################

def page(book: typing.Any) -> tuple[str, list[str]]:
    rendered = card.Page(book)
    return rendered.render(), rendered.targets


def test_card_shows_the_book():
    _, book = saved()
    text, targets = page(book)
    for words in ("Bliss", "A novel", "Peter Carey", "study, shelf 3", "office", "printing 9", "296 pages"):
        assert words in text
    assert "Harper &amp; Row" in text
    assert book.links["Peter Carey"] in targets


def test_card_escapes():
    edition = Edition(publisher="<u>", copies=[Copy(location="<s>")])
    text, _ = page(Book(title="<b>x</b>", authors=["<i>"], editions=[edition]))
    assert "<b>x" not in text and "<i>" not in text and "<u>" not in text and "<s>" not in text


def test_card_shows_an_imprint():
    edition = Edition(publisher="Penguin UK", imprint="Penguin Plays", binding="paperback", date="1971")
    assert "Penguin UK, Penguin Plays, paperback, 1971" in page(Book(title="T", editions=[edition]))[0]


def test_card_links_content_to_read():
    book = Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(content="darq.book.epub:a")]),
                                     Edition(binding="PDF", copies=[Copy(content="com.adobe.pdf:b")])])
    text, targets = page(book)
    assert [t for t in targets if isinstance(t, str)] == ["darq.book.epub:a", "com.adobe.pdf:b"]
    assert text.count(">Read<") == 2


def test_card_doesnt_repeat_a_format():
    text, _ = page(Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(content="darq.book.epub:a")])]))
    assert text.count("EPUB") == 1


def test_card_says_the_format_of_content_with_no_binding():
    text, _ = page(Book(title="T", editions=[Edition(copies=[Copy(content="com.adobe.pdf:b")])]))
    assert ">PDF<" in text and text.count("PDF") == 1


def test_card_has_one_list_in_order():
    copies = [Copy(location="shelf"), Copy(content="com.adobe.pdf:b")]
    text, _ = page(Book(title="T", editions=[Edition(publisher="P", copies=copies)]))
    assert "Formats" not in text and "Paper" not in text
    assert text.index("shelf") < text.index("Read")


def test_a_line_separates_each_entity():
    edition = Edition(publisher="P", copies=[Copy(location="a"), Copy(location="b")])
    other = Edition(publisher="Q", copies=[Copy(location="c")])
    text, _ = page(Book(title="T", language="English", editions=[edition, other]))
    assert text.count(card.LINE) == 3


def test_copies_are_indented_under_their_edition():
    text, _ = page(Book(title="T", editions=[Edition(publisher="P", copies=[Copy(location="a"), Copy(location="b")])]))
    assert text.count('class="copy') == 2
    assert text.index(">P<") < text.index(">a<") < text.index(">b<")


def test_card_says_when_theres_no_location():
    assert card.NO_LOCATION in page(Book(title="T", editions=[Edition(copies=[Copy(printing="2")])]))[0]


def test_card_highlights_what_was_opened():
    store, book = saved()
    copy = book.editions[1].copies[0].entity
    text, _ = page(read_book(store, store.item(copy)))
    assert text.count(" opened") == 1


def test_card_shows_reading():
    assert "Finished 2026-03-03" in page(Book(expression=model.Item(), title="T", finished="2026-03-03"))[0]


def harry_potter(opened="English"):
    """English and Latin, as a store, and the book as it's read from one."""
    store = MemoryStore()
    book: typing.Any = Book(title="Harry Potter and the Philosopher's Stone", authors=["J. K. Rowling"],
                            language="English",
                            editions=[Edition(publisher="Bloomsbury", date="1997", copies=[Copy(location="study")])])
    darq.book.save(store, book)
    latin = store.add(darq.book.EXPRESSION_TYPE, "Latin text")
    latin.claim(darq.book.VERSION_OF, model.EntityValue(book.work.id))
    edition = store.add(darq.book.MANIFESTATION_TYPE, "Bloomsbury, 2003")
    edition.claim(darq.book.VERSION_OF, model.EntityValue(latin.id))
    edition.claim(darq.book.PUBLICATION_DATE, model.TimeValue.parse("2003"))
    copy = store.add(darq.book.ITEM_TYPE, "Copy")
    copy.claim(darq.book.EXEMPLAR_OF, model.EntityValue(edition.id))
    copy.claim(darq.book.LOCATION, model.StringValue("lounge"))
    item = store.item(book.work if opened == "English" else latin)
    return store, latin, read_book(store, item)


def test_card_shows_every_text_with_its_editions():
    store, latin, book = harry_potter()
    text, targets = page(book)
    assert "Latin text" in text and "lounge" in text and "study" in text
    assert text.index("study") < text.index("Latin text") < text.index("lounge")
    assert store.item(latin) in targets


def test_opening_a_text_puts_it_first():
    _, latin, book = harry_potter("Latin")
    text, _ = page(book)
    assert book.expression.id == latin.id
    assert text.index("lounge") < text.index("study")


def test_a_save_leaves_other_texts_alone():
    store, latin, book = harry_potter()
    book.subtitle = "Changed"
    darq.book.save(store, book)
    [other] = read_book(store, store.item(book.work)).texts
    assert other.entity.id == latin.id and other.editions[0].copies[0].location == "lounge"


def test_each_level_steps_in():
    _, _, book = harry_potter()
    text, _ = page(book)
    assert 'class="text' in text and 'class="edition' in text and 'class="copy' in text
    assert f"margin-left: {card.INDENT}px" in text and f"margin-left: {3 * card.INDENT}px" in text


def add_targets(targets: list) -> list[typing.Any]:
    return [t for t in targets if isinstance(t, card.Add)]


def test_an_unknown_text_still_shows():
    """Its link needs somewhere to go."""
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="P", copies=[Copy(location="a")])])
    darq.book.save(store, book)
    text, targets = page(read_book(store, store.item(book.work)))
    assert [t.level for t in add_targets(targets)] == ["Copy", "Manifestation", "Expression"]
    assert darq.book.TEXT_LABEL in text


def test_card_links_add_each_level():
    _, _, book = harry_potter()
    _, targets = page(book)
    adds = add_targets(targets)
    assert [a.level for a in adds] == ["Copy", "Manifestation", "Copy", "Manifestation", "Expression"]
    assert adds[-1].parent.id == book.work.id
    assert adds[1].parent.id == book.expression.id
    assert adds[3].parent.id == book.others[0].id


def test_a_digital_copy_has_no_add_copy():
    store = MemoryStore().content("darq.book.epub:a")
    book = Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(content="darq.book.epub:a")])])
    darq.book.save(store, book)
    _, targets = page(read_book(store, store.item(book.work)))
    assert "Copy" not in [a.level for a in add_targets(targets)]


def test_a_manifestation_with_no_copies_can_have_one():
    store = MemoryStore()
    book = Book(title="T", editions=[Edition(publisher="P")])
    darq.book.save(store, book)
    text, targets = page(read_book(store, store.item(book.work)))
    assert "No copies" in text and "Copy" in [a.level for a in add_targets(targets)]


def test_an_expression_shows_its_own_title():
    store = MemoryStore()
    book: typing.Any = Book(title="Harry Potter", language="Latin", text_title="Harrius Potter")
    darq.book.save(store, book)
    assert book.expression.get_label() == "Harrius Potter"
    read = read_book(store, store.item(book.work))
    assert read.text_title == "Harrius Potter"
    assert values(read.expression, darq.book.TEXT_TITLE)[0].text == {"en": "Harrius Potter"}
    assert "Harrius Potter" in page(read)[0]


################################################################
# Adding a level
################################################################

def test_adding_a_text():
    store, _, book = harry_potter()
    darq.book.add_text(store, book.work, darq.book.Text(title="Harrius", language="Greek", translators=["A"]))
    read = read_book(store, store.item(book.work))
    assert {t.language for t in read.texts} == {"Greek", ""}
    made = next(t for t in read.texts if t.language == "Greek")
    assert (made.title, made.translators, made.editions) == ("Harrius", ["A"], [])


def test_a_text_needs_a_title_or_language():
    store, _, book = harry_potter()
    with pytest.raises(BookError):
        darq.book.add_text(store, book.work, darq.book.Text())


def test_adding_an_edition_adds_no_copy():
    store, _, book = harry_potter()
    copies = len(store.of_kind(darq.book.ITEM_TYPE))
    darq.book.add_edition(store, book.expression, Edition(publisher="Scholastic", date="1998"))
    read = read_book(store, store.item(book.work))
    assert [e.publisher for e in read.editions] == ["Bloomsbury", "Scholastic"]
    assert read.editions[1].copies == [] and len(store.of_kind(darq.book.ITEM_TYPE)) == copies


@pytest.mark.parametrize("edition", [Edition(), Edition(date="soon")])
def test_an_edition_needs_sensible_details(edition):
    store, _, book = harry_potter()
    with pytest.raises(BookError):
        darq.book.add_edition(store, book.expression, edition)


def test_adding_a_copy_with_nothing_known():
    store, _, book = harry_potter()
    darq.book.add_copy(store, book.editions[0].entity, Copy())
    assert len(read_book(store, store.item(book.work)).editions[0].copies) == 2


def test_adding_a_copy_with_a_bad_date():
    store, _, book = harry_potter()
    with pytest.raises(BookError):
        darq.book.add_copy(store, book.editions[0].entity, Copy(printed="soon"))


def test_card_links_other_texts():
    store, book = saved()
    german = store.add(darq.book.EXPRESSION_TYPE, "German text")
    german.claim(darq.book.VERSION_OF, model.EntityValue(book.work.id))
    text, targets = page(read_book(store, store.item(book.work)))
    assert "German text" in text and store.item(german) in targets


################################################################
# The form
################################################################

def test_form_shows_and_applies(qt_app):
    book = bliss()
    view = form.BookForm()
    view.show_book(book)
    fields = {(id(t), a): e for t, a, e in view.fields}
    assert fields[(id(book), "title")].text() == "Bliss"
    fields[(id(book), "authors")].setText("A One; B Two ;")
    fields[(id(book.editions[0]), "publisher")].setText(" Other ")
    view.apply()
    assert book.authors == ["A One", "B Two"]
    assert book.editions[0].publisher == "Other"


def test_form_has_the_same_divisions_as_the_card(qt_app):
    assert ("title", "Title") in form.WORK_FIELDS and ("language", "Language") in form.TEXT_FIELDS
    assert not {a for a, _ in form.WORK_FIELDS} & {a for a, _ in form.TEXT_FIELDS}


################################################################
# The lens
################################################################

class FakeOpener:
    def __init__(self):
        self.used: list[tuple[str, str]] = []

    def use(self, item_id: str, lens_for: str = ""):
        self.used.append((item_id, lens_for))


class LensStore(MemoryStore):
    def adopt(self, item_id: str, port: int):
        pass

    def settled(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def opened(qt_app):
    """A lens on a saved book's work, with an opener and its store."""
    store, book = saved()
    store.__class__ = LensStore
    opener = FakeOpener()
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, opener, store)
    assert used.use(UseRequest(item_id=store.item(book.work), item=5), 0).status == Status.OK
    yield used, store, opener
    if used.window is not None:
        used.window.close()


def test_lens_shows_the_book(opened):
    used, _, _ = opened
    assert "Bliss" in used.window.browser.toPlainText()
    assert used.window.windowTitle() == "Bliss"
    assert not used.window.editing


def test_lens_opens_a_new_book_for_adding(qt_app):
    store = MemoryStore()
    work = store.add(darq.book.WORK_TYPE, "x")
    del work.labels["en"]
    store.__class__ = LensStore
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(work), item=5), 0)
    assert used.window.stack.currentWidget() is used.window.adding
    used.window.close()


def test_lens_refuses_a_second_item(opened):
    used, _, _ = opened
    assert used.use(UseRequest(item_id="darq.book.work:x", item=6), 0).status == Status.BUSY


def test_lens_saves_and_shows(opened):
    used, store, _ = opened
    used.edit()
    next(e for t, a, e in used.window.form.fields if a == "subtitle").setText("Changed")
    used.save()
    assert not used.window.editing
    assert "Changed" in used.window.browser.toPlainText()
    assert values(store.of_kind(darq.book.WORK_TYPE)[0], darq.book.SUBTITLE)[0].text == {"en": "Changed"}


def test_lens_keys_reach_the_window_from_a_field(opened):
    used, _, _ = opened
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    keys = typing.cast(typing.Any, QTest)
    keys.keyClick(used.window.browser, Qt.Key.Key_E)
    assert used.window.editing
    field = next(e for t, a, e in used.window.form.fields if a == "subtitle")
    field.setText("By key")
    keys.keyClick(field, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert not used.window.editing and used.book.subtitle == "By key"

    keys.keyClick(used.window.browser, Qt.Key.Key_E)
    field = next(e for t, a, e in used.window.form.fields if a == "subtitle")
    field.setText("Not kept")
    keys.keyClick(field, Qt.Key.Key_Escape)
    assert not used.window.editing and used.book.subtitle == "By key"


def test_lens_keeps_the_form_on_a_bad_save(opened):
    used, _, _ = opened
    used.edit()
    next(e for t, a, e in used.window.form.fields if a == "finished").setText("someday")
    used.save()
    assert used.window.editing
    assert "finished" in used.window.status.text()
    assert next(e for t, a, e in used.window.form.fields if a == "finished").text() == "someday"


def test_lens_cancel_forgets(opened):
    used, _, _ = opened
    used.edit()
    next(e for t, a, e in used.window.form.fields if a == "title").setText("Typed")
    used.window.form.apply()
    used.cancel()
    assert not used.window.editing and used.book.title == "Bliss"


def find_add(used, level):
    return next(t for t in used.window.targets if isinstance(t, card.Add) and t.level == level)


def test_a_link_opens_a_levels_form(opened):
    used, store, _ = opened
    used.start_add(find_add(used, card.COPY))
    assert used.window.stack.currentWidget() is used.window.level
    used.window.level.edits["location"].setText("attic")
    before = len(store.of_kind(darq.book.ITEM_TYPE))
    used.save_level()
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == before + 1
    assert used.window.stack.currentWidget() is used.window.browser and "attic" in used.window.browser.toPlainText()


def test_following_an_add_link(opened):
    used, _, _ = opened
    from PyQt6.QtCore import QUrl
    number = used.window.targets.index(find_add(used, card.EXPRESSION))
    used.window.on_anchor(QUrl(f"#{number}"))
    assert used.window.stack.currentWidget() is used.window.level
    assert used.window.level.heading.text() == "Add Expression"


def test_adding_an_expression_from_the_card(opened):
    used, store, _ = opened
    used.start_add(find_add(used, card.EXPRESSION))
    used.window.level.edits["title"].setText("Glück")
    used.window.level.edits["language"].setText("German")
    used.window.level.edits["translators"].setText("A One; B Two")
    used.save_level()
    assert "Glück" in used.window.browser.toPlainText()
    assert len(store.of_kind(darq.book.EXPRESSION_TYPE)) == 2


def test_a_bad_manifestation_keeps_its_form(opened):
    used, _, _ = opened
    used.start_add(find_add(used, card.MANIFESTATION))
    used.window.level.edits["date"].setText("soon")
    used.save_level()
    assert used.window.stack.currentWidget() is used.window.level and "date" in used.window.status.text()


def test_escape_leaves_a_levels_form(opened):
    used, store, _ = opened
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    used.start_add(find_add(used, card.COPY))
    typing.cast(typing.Any, QTest).keyClick(used.window.level.edits["location"], Qt.Key.Key_Escape)
    assert used.window.stack.currentWidget() is used.window.browser


def test_only_a_manifestation_looks_up_an_isbn(opened):
    used, _, _ = opened
    for level, shown in ((card.MANIFESTATION, True), (card.COPY, False), (card.EXPRESSION, False)):
        used.start_add(find_add(used, level))
        assert used.window.level.query.isVisibleTo(used.window.level) == shown
        used.cancel()


def test_a_lookup_fills_a_manifestations_form(opened):
    used, _, _ = opened
    used.run = lambda job: job()
    answer = {"ISBN:0596007124": {"title": "T", "publishers": [{"name": "O'Reilly"}],
                                  "identifiers": {"isbn_10": ["0596007124"], "openlibrary": ["OL1M"]}}}
    used.fetch = lambda url: json.dumps(answer).encode()
    used.start_add(find_add(used, card.MANIFESTATION))
    used.window.level.query.setText("0596007124")
    used.window.level.search()
    assert used.window.level.edits["publisher"].text() == "O'Reilly"
    assert used.window.level.open_library == "OL1M"


def find_edit(used, level, number=0):
    return [t for t in used.window.targets if isinstance(t, card.Edit) and t.level == level][number]


@pytest.mark.parametrize("level, field, value, words", [
    (card.WORK, "title", "Changed", "Changed"), (card.EXPRESSION, "language", "German", "German"),
    (card.MANIFESTATION, "publisher", "Faber", "Faber"), (card.COPY, "location", "attic", "attic"),
])
def test_editing_each_level(opened, level, field, value, words):
    used, _, _ = opened
    used.start_edit(find_edit(used, level))
    assert used.window.level.heading.text() == f"Edit {level}"
    assert used.window.level.remove.isVisibleTo(used.window.level)
    used.window.level.edits[field].setText(value)
    used.save_level()
    assert words in used.window.browser.toPlainText()


def test_an_edit_form_is_filled_in(opened):
    used, _, _ = opened
    used.start_edit(find_edit(used, card.MANIFESTATION))
    assert used.window.level.edits["publisher"].text() == "Harper & Row"
    used.start_edit(find_edit(used, card.WORK))
    assert used.window.level.edits["authors"].text() == "Peter Carey"


def test_an_add_form_has_no_remove(opened):
    used, _, _ = opened
    used.start_add(find_add(used, card.EXPRESSION))
    assert not used.window.level.remove.isVisibleTo(used.window.level)


def test_a_copy_of_another_text_can_be_edited(qt_app):
    store, latin, book = harry_potter()
    store.__class__ = LensStore
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    used.start_edit(find_edit(used, card.COPY, 1))
    assert used.window.level.edits["location"].text() == "lounge"
    used.window.level.edits["location"].setText("attic")
    used.save_level()
    assert "attic" in used.window.browser.toPlainText() and "study" in used.window.browser.toPlainText()
    used.window.close()


def test_removing_a_copy(opened):
    used, store, _ = opened
    used.confirm = lambda question: True
    used.start_edit(find_edit(used, card.COPY))
    before = len(store.of_kind(darq.book.ITEM_TYPE))
    used.remove_level()
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == before - 1
    assert used.window.stack.currentWidget() is used.window.browser


def test_saying_no_keeps_it(opened):
    used, store, _ = opened
    asked = []
    used.confirm = lambda question: asked.append(question) or False
    used.start_edit(find_edit(used, card.COPY))
    before = len(store.of_kind(darq.book.ITEM_TYPE))
    used.remove_level()
    assert asked == ["Remove this Copy?"] and len(store.of_kind(darq.book.ITEM_TYPE)) == before


def test_what_has_something_under_it_stays(opened):
    used, store, _ = opened
    used.confirm = lambda question: True
    used.start_edit(find_edit(used, card.MANIFESTATION))
    used.remove_level()
    assert "Remove its copies first" in used.window.status.text()
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 2


def test_removing_from_the_bottom_up(qt_app):
    store, book = saved()
    store.__class__ = LensStore
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    used.confirm = lambda question: True
    for level, number in ((card.COPY, 0), (card.COPY, 0), (card.MANIFESTATION, 0), (card.MANIFESTATION, 0),
                          (card.EXPRESSION, 0), (card.WORK, 0)):
        used.start_edit(find_edit(used, level, number))
        used.remove_level()
    assert store.of_kind(darq.book.WORK_TYPE) == [] and store.of_kind(darq.book.ITEM_TYPE) == []
    assert not used.window.isVisible()


def test_removing_what_it_was_opened_on(qt_app):
    store, book = saved()
    store.__class__ = LensStore
    copy = book.editions[0].copies[0].entity
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(copy), item=5), 0)
    used.confirm = lambda question: True
    used.start_edit(next(t for t in used.window.targets if isinstance(t, card.Edit) and t.entity.id == copy.id))
    used.remove_level()
    assert used.item_id.startswith("darq.book.work:") and "Bliss" in used.window.browser.toPlainText()
    used.window.close()


def test_a_digital_copys_content_isnt_touched():
    store = MemoryStore()
    book: typing.Any = Book(title="T", editions=[Edition(copies=[Copy(content="darq.book.epub:a")])])
    darq.book.save(store, book)
    destroyed = []
    typing.cast(typing.Any, store).destroy = destroyed.append
    darq.book.remove(store, darq.book.ITEM_TYPE, book.editions[0].copies[0].entity)
    assert len(destroyed) == 1 and destroyed[0].startswith("darq.book.item:")


def test_a_failed_add_leaves_nothing(qt_app):
    store, book = saved()
    texts = len(store.of_kind(darq.book.EXPRESSION_TYPE))
    store.fail_after = 0
    with pytest.raises(BookError):
        darq.book.add_text(store, book.work, darq.book.Text(language="Latin"))
    assert len(store.of_kind(darq.book.EXPRESSION_TYPE)) == texts


def test_lens_reads_the_first_content(opened):
    used, _, opener = opened
    used.read_book()
    assert opener.used == [] and "nothing to read" in used.window.status.text()
    used.book.editions[0].copies.append(Copy(content="com.adobe.pdf:a"))
    used.read_book()
    assert opener.used == [("com.adobe.pdf:a", "")]


def test_lens_shows_the_entity(opened):
    used, store, opener = opened
    used.show_entity()
    assert opener.used == [(used.item_id, model.ENTITY_TYPE)]


def test_lens_follows_a_link(opened):
    used, _, opener = opened
    from PyQt6.QtCore import QUrl
    used.window.on_anchor(QUrl("#0"))
    assert opener.used == [(used.window.targets[0], "")]


################################################################
# New
################################################################

def test_new_only_offers_a_book():
    class Types:
        def list_types(self, implemented):
            return python_types.SimpleNamespace(type_ids=["darq.book.work", *darq.book.PARTS])

        def describe(self, type_id):
            return python_types.SimpleNamespace(status=ui_types.Status.OK,
                                                description=python_types.SimpleNamespace(name="Book", description=""))

        def find_lenses(self, type_id, context):
            return python_types.SimpleNamespace(status=ui_types.Status.OK, lenses=["lenses/book"])

    assert [e.type_id for e in ui_types.load_types(typing.cast(typing.Any, Types()))] == ["darq.book.work"]


################################################################
# Live
################################################################

def test_book_lens(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # Plays the Terminal: New makes a work, a book's filled in through
    # the lens's store, on real item ports, and read from a copy.
    client_path = write_app(tmp_path / "client", """
        import pathlib
        import sys
        import time
        import darq
        import darq.book
        import darq.kb as model
        import darq.launch
        from darq.book import Copy, Edition
        from darq.protocols.history import History
        from darq.protocols.lens import Lens
        from darq.protocols.type import Type
        from darq.ui import book_store

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.find_lenses(type_id="darq.book.work", context="terminal").lenses:
                if time.monotonic() > deadline:
                    return "no lens registered"
                time.sleep(0.05)
            for type_id in ("darq.book.work", "darq.book.expression", "darq.book.manifestation", "darq.book.item"):
                path = darq.launch.find_lens(type_id)
                if not path.endswith("lenses/book"):
                    return f"lens for {type_id} is {path}"

            work_item, port = darq.launch.create_item("darq.book.work")
            history = History.Client(darq.connect("darq.history"), timeout=10)

            def record(item_id, event):
                history.add_event(item_id=item_id, event=event, source="book-test")

            store = book_store.ItemStore(["en"], record=record)
            store.adopt(work_item, port)
            book = darq.book.read(store, work_item)
            book.title = "Bliss"
            book.authors = ["Peter Carey"]
            book.language = "English"
            book.finished = "2026-03-03"
            book.editions = [Edition(publisher="Picador", date="1982", pages="296",
                                     copies=[Copy(location="office", printing="9")])]
            darq.book.save(store, book)

            # The same names find the same entities.
            again = darq.book.read(store, work_item)
            other = Edition(publisher="Picador", binding="hardcover", copies=[Copy(location="study")])
            again.editions.append(other)
            darq.book.save(store, again)
            store.settled()

            copy_item = model.item_id(other.copies[0].entity.id, darq.book.ITEM_TYPE)
            fresh = book_store.ItemStore(["en"])
            fresh.adopt(copy_item, darq.launch.open_item(copy_item))
            read = darq.book.read(fresh, copy_item)
            summary = (read.title, read.authors, read.language, read.finished,
                       [(e.publisher, e.date, e.pages, [c.location for c in e.copies]) for e in read.editions])
            expected = ("Bliss", ["Peter Carey"], "English", "2026-03-03",
                        [("Picador", "1982", "296", ["office"]), ("Picador", "", "", ["study"])])
            if summary != expected:
                return f"read {summary!r}"
            if read.links["Peter Carey"].partition(":")[0] != "darq.person":
                return f"links {read.links!r}"

            publishers = [model.item_id(e.entity.id, darq.book.MANIFESTATION_TYPE) for e in read.editions]
            ids = {v.entity_id for e in read.editions for v in
                   (s.get_value() for s in e.entity.get_statements(darq.book.PUBLISHER))}
            if len(ids) != 1 or len(publishers) != 2:
                return f"publishers {ids!r}"
            fresh.close()

            # Each level's made once, and then a change is an edit.
            levels = {"copy": copy_item,
                      "edition": model.item_id(read.editions[1].entity.id, darq.book.MANIFESTATION_TYPE),
                      "text": model.item_id(read.expression.id, darq.book.EXPRESSION_TYPE),
                      "author": read.links["Peter Carey"]}
            for name, level in levels.items():
                events = [e.event for e in history.get_events(item_ids=[level]).events]
                if events != ["created"]:
                    return f"{name}'s events are {events!r}"

            read.editions[1].binding = "paperback"
            store.close()
            edited = book_store.ItemStore(["en"], record=record)
            edited.adopt(copy_item, darq.launch.open_item(copy_item))
            again = darq.book.read(edited, copy_item)
            again.editions[1].binding = "paperback"
            darq.book.save(edited, again)
            edited.close()
            events = [e.event for e in history.get_events(item_ids=[levels["edition"]]).events]
            if sorted(events) != ["created", "edited"]:
                return f"edition's events are {events!r}"

            # Opened at the top, everything under it's read with its parent,
            # and can still be changed: a lens starts this way.
            top = book_store.ItemStore(["en"], record=record)
            top.adopt(work_item, darq.launch.open_item(work_item))
            card = darq.book.read(top, work_item)
            card.editions[0].pages = "300"
            card.editions[0].copies[0].location = "attic"
            darq.book.save(top, card)
            darq.book.add_copy(top, card.editions[0].entity, Copy(location="shed"))
            darq.book.add_edition(top, card.expression, Edition(publisher="Faber"))
            top.close()
            again = book_store.ItemStore(["en"])
            again.adopt(work_item, darq.launch.open_item(work_item))
            card = darq.book.read(again, work_item)
            again.close()
            first = card.editions[0]
            if (first.pages, sorted(c.location for c in first.copies)) != ("300", ["attic", "shed"]):
                return f"changed {first!r}"
            if len(card.editions) != 3:
                return f"editions {card.editions!r}"

            # What the real KB has to accept: a preference, a cover, and a move.
            top = book_store.ItemStore(["en"], record=record)
            top.adopt(work_item, darq.launch.open_item(work_item))
            card = darq.book.read(top, work_item)
            darq.book.prefer(top, card, card.editions[0].copies[0])
            import io
            from PIL import Image
            png = io.BytesIO()
            Image.new("RGB", (2, 2)).save(png, "PNG")
            cover, cover_port = darq.launch.create_item_from("public.png", png.getvalue(), "cover.png")
            darq.close_port(cover_port)
            darq.book.set_cover(top, card.editions[0].entity, cover)
            darq.book.move(top, darq.book.ITEM_TYPE, card.editions[0].copies[0].entity, card.editions[1].entity)
            top.settled()
            card = darq.book.read(top, work_item)
            moved = [c for e in card.editions for c in e.copies if c.preferred]
            if len(moved) != 1 or card.editions[0].cover != cover:
                return f"preferred, covered and moved {card.editions!r}"
            if top.names_like("Pet", "darq.person") != ["Peter Carey"]:
                return f"names {top.names_like('Pet', 'darq.person')!r}"
            if [title for _, title in top.works_like("Bli")] != ["Bliss"]:
                return f"works {top.works_like('Bli')!r}"
            top.close()

            # Removed from the bottom up: what's destroyed stops counting.
            top = book_store.ItemStore(["en"], record=record)
            top.adopt(work_item, darq.launch.open_item(work_item))
            card = darq.book.read(top, work_item)
            first = card.editions[0]
            try:
                darq.book.remove(top, darq.book.MANIFESTATION_TYPE, first.entity)
                return "it removed a manifestation with copies"
            except darq.book.BookError:
                pass
            for copy in first.copies:
                darq.book.remove(top, darq.book.ITEM_TYPE, copy.entity)
            darq.book.remove(top, darq.book.MANIFESTATION_TYPE, first.entity)
            gone = model.item_id(first.entity.id, darq.book.MANIFESTATION_TYPE)
            card = darq.book.read(top, work_item)
            if len(card.editions) != 2 or any(e.entity.id == first.entity.id for e in card.editions):
                return f"after removing {card.editions!r}"
            events = [e.event for e in history.get_events(item_ids=[gone]).events]
            if "destroyed" not in events:
                return f"removed events {events!r}"
            top.close()

            lens = Lens.Client(darq.launch.use_item(copy_item), timeout=30)
            events = [(e.event, e.source) for e in history.get_events(item_ids=[copy_item]).events]
            if ("opened", "book-lens") not in events:
                return f"events are {events!r}"
            lens.close()
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)

    [lens_process] = [p for p in kernel._process_table.values() if p._image_path == str(LENS_PATH)]
    run_kernel_until(kernel, lambda: lens_process.get_status() == zombie, "the lens to exit")
    assert (lens_process.get_exit_status(), lens_process.get_exit_error()) == (0, None)


################################################################
# Renamed vocabulary
################################################################

def test_a_renamed_property_is_relabelled():
    assert (darq.book.OLD_CONTENT, "file", "content (identifier, old)") in darq.book.RELABELLED
    assert (darq.book.OLD_CONTENT, "content", "content (identifier, old)") in darq.book.RELABELLED
    labels = {e.id: e.get_label() for e in darq.book.vocabulary()}
    assert labels[darq.book.CONTENT] == "content" and labels[darq.book.OLD_CONTENT] == "content (identifier, old)"


################################################################
# The one to read
################################################################

def formats() -> tuple[MemoryStore, typing.Any]:
    store = MemoryStore().content("darq.book.epub:a", "com.adobe.pdf:b")
    book = Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(content="darq.book.epub:a")]),
                                     Edition(binding="PDF", copies=[Copy(content="com.adobe.pdf:b")])])
    darq.book.save(store, book)
    return store, read_book(store, store.item(book.work))


def test_the_first_is_read_unless_one_is_preferred():
    store, book = formats()
    assert darq.book.preferred_content(book) == "darq.book.epub:a"
    darq.book.prefer(store, book, book.editions[1].copies[0])
    read = read_book(store, store.item(book.work))
    assert darq.book.preferred_content(read) == "com.adobe.pdf:b"
    assert [c.preferred for e in read.editions for c in e.copies] == [False, True]


def test_preferring_another_unprefers_the_first():
    store, book = formats()
    darq.book.prefer(store, book, book.editions[1].copies[0])
    book = read_book(store, store.item(book.work))
    darq.book.prefer(store, book, book.editions[0].copies[0])
    read = read_book(store, store.item(book.work))
    assert [c.preferred for e in read.editions for c in e.copies] == [True, False]


def test_preference_is_a_characteristic():
    store, book = formats()
    copy = book.editions[0].copies[0]
    darq.book.prefer(store, book, copy)
    [value] = values(store.entities[copy.entity.id], darq.book.HAS_CHARACTERISTIC)
    assert value.entity_id == darq.book.PREFERENCE


def test_other_characteristics_are_left():
    store, book = formats()
    copy = book.editions[0].copies[0]
    store.entities[copy.entity.id].claim(darq.book.HAS_CHARACTERISTIC, model.EntityValue("Q1"))
    darq.book.prefer(store, read_book(store, store.item(book.work)), book.editions[1].copies[0])
    assert [v.entity_id for v in values(store.entities[copy.entity.id], darq.book.HAS_CHARACTERISTIC)] == ["Q1"]


def test_the_card_offers_a_choice_only_with_two_to_read():
    _, book = formats()
    text, targets = page(book)
    assert len([t for t in targets if isinstance(t, card.Prefer)]) == 2 and "★" not in text
    one = Book(title="T", editions=[Edition(copies=[Copy(content="darq.book.epub:a")])])
    assert not [t for t in page(one)[1] if isinstance(t, card.Prefer)]


def test_the_card_marks_the_one_to_read():
    store, book = formats()
    darq.book.prefer(store, book, book.editions[1].copies[0])
    text, targets = page(read_book(store, store.item(book.work)))
    assert "★ the one to read" in text and len([t for t in targets if isinstance(t, card.Prefer)]) == 1


def test_the_lens_prefers_and_reads_it(qt_app):
    store, book = formats()
    store.__class__ = LensStore
    opener = FakeOpener()
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), None, opener, store)
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    used.prefer(used.book.editions[1].copies[0])
    used.read_book()
    assert opener.used == [("com.adobe.pdf:b", "")]
    used.window.close()


################################################################
# Covers
################################################################

def test_a_cover_is_on_its_manifestation():
    store = MemoryStore().content("public.jpeg:c")
    book = Book(title="T", editions=[Edition(publisher="P", cover="public.jpeg:c")])
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert read.editions[0].cover == "public.jpeg:c"
    rendered = card.Page(read)
    text = rendered.render()
    assert rendered.covers == ["public.jpeg:c"] and card.cover_url("public.jpeg:c") in text


def test_set_cover():
    store, book = saved()
    store.content("public.png:x")
    darq.book.set_cover(store, book.editions[0].entity, "public.png:x")
    assert read_book(store, store.item(book.work)).editions[0].cover == "public.png:x"


def test_the_lens_shows_a_cover(qt_app):
    store = MemoryStore().content("public.png:c")
    book = Book(title="T", editions=[Edition(publisher="P", cover="public.png:c")])
    darq.book.save(store, book)
    store.__class__ = LensStore
    asked = []
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QImage
    image = QImage(4, 6, QImage.Format.Format_RGB32)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), None,
                         FakeOpener(), store)
    data = bytes(buffer.data().data())
    used.cover = lambda item_id: asked.append(item_id) or data
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    document = used.window.browser.document()
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QTextDocument
    found = document.resource(QTextDocument.ResourceType.ImageResource.value, QUrl(card.cover_url("public.png:c")))
    assert asked == ["public.png:c"] and isinstance(found, QImage) and found.width() == 4
    used.window.close()


def test_a_looked_up_cover_is_fetched_after_adding(qt_app):
    store = MemoryStore().content("public.jpeg:fetched")
    work = store.add(darq.book.WORK_TYPE, "x")
    del work.labels["en"]
    store.__class__ = LensStore
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), None,
                         FakeOpener(), store)
    used.run = lambda job: job()
    used.snapshot = lambda url: "public.jpeg:fetched" if url == "https://covers.openlibrary.org/b/id/1-L.jpg" else ""
    used.cover = lambda item_id: b""
    used.use(UseRequest(item_id=store.item(work), item=5), 0)
    view = used.window.adding
    view.fields["title"].setText("T")
    view.cover_url = "https://covers.openlibrary.org/b/id/1-L.jpg"
    used.add_shown()
    [edition] = store.of_kind(darq.book.MANIFESTATION_TYPE)
    assert values(edition, darq.book.COVER)[0].entity_id == "fetched"
    used.window.close()


def test_a_cover_that_cant_be_had_is_no_failure(qt_app):
    store = MemoryStore()
    work = store.add(darq.book.WORK_TYPE, "x")
    del work.labels["en"]
    store.__class__ = LensStore
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), None,
                         FakeOpener(), store)
    used.run = lambda job: job()

    def fail(url):
        raise darq.DarqError("offline")

    used.snapshot = fail
    used.use(UseRequest(item_id=store.item(work), item=5), 0)
    used.window.adding.fields["title"].setText("T")
    used.window.adding.cover_url = "https://covers.openlibrary.org/b/id/1-L.jpg"
    used.add_shown()
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1 and "Added" in used.window.status.text()
    used.window.close()


################################################################
# Completing names
################################################################

def test_names_are_offered_and_chosen(qt_app):
    from PyQt6.QtWidgets import QLineEdit
    edit = QLineEdit()
    asked = []
    completer = form.NameCompleter(edit, darq.book.PERSON_TYPE,
                                   lambda text, kind: asked.append((text, kind)) or ["Peter Carey", "Peter Cook"])
    edit.setText("Kate Grenville; Pe")
    completer.typed(edit.text())
    assert asked == [("Pe", darq.book.PERSON_TYPE)]
    assert completer.offered.stringList() == ["Peter Carey", "Peter Cook"]
    completer.chosen("Peter Carey")
    assert edit.text() == "Kate Grenville; Peter Carey"


def test_nothing_is_offered_for_a_letter(qt_app):
    from PyQt6.QtWidgets import QLineEdit
    asked = []
    completer = form.NameCompleter(QLineEdit(), darq.book.PERSON_TYPE, lambda text, kind: asked.append(text) or [])
    completer.typed("P")
    assert asked == []


def test_what_names_things_is_completed():
    assert form.COMPLETED["authors"] == darq.book.PERSON_TYPE
    assert form.COMPLETED["publisher"] == darq.book.ORGANISATION_TYPE
    assert "title" not in form.COMPLETED and "location" not in form.COMPLETED


################################################################
# Moving
################################################################

def test_moving_a_copy_to_another_manifestation():
    store, book = saved()
    copy = book.editions[0].copies[0]
    darq.book.move(store, darq.book.ITEM_TYPE, copy.entity, book.editions[1].entity)
    read = read_book(store, store.item(book.work))
    assert [len(e.copies) for e in read.editions] == [0, 2]


def test_moving_an_expression_to_another_work():
    store, latin, book = harry_potter()
    other = Book(title="Harrius Potter")
    darq.book.save(store, other)
    darq.book.move(store, darq.book.EXPRESSION_TYPE, latin, typing.cast(typing.Any, other.work))
    assert read_book(store, store.item(book.work)).texts == []
    there = read_book(store, store.item(other.work))
    assert latin.id in {e.id for e in there.others} | {there.expression.id}


def test_the_lens_moves_a_manifestation_to_another_book(qt_app):
    store, latin, book = harry_potter()
    other = Book(title="Harry Potter (a copy)", language="English")
    darq.book.save(store, other)
    store.__class__ = LensStore
    typing.cast(typing.Any, store).works_like = lambda text: [(store.item(other.work), "Harry Potter (a copy)")]
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), None,
                         FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    used.ask_text = lambda question: "Harry"
    used.choose = lambda question, choices: 0
    used.start_edit(find_edit(used, card.MANIFESTATION))
    used.move_level()
    assert "Moved the Manifestation" in used.window.status.text()
    assert [e.publisher for e in read_book(store, store.item(other.work)).editions] == ["Bloomsbury"]
    used.window.close()


def test_moving_nowhere_does_nothing(opened):
    used, store, _ = opened
    used.ask_text = lambda question: None
    used.start_edit(find_edit(used, card.COPY))
    used.move_level()
    assert used.window.stack.currentWidget() is used.window.level


def test_a_work_has_no_move(opened):
    used, _, _ = opened
    used.start_edit(find_edit(used, card.WORK))
    assert not used.window.level.move_button.isVisibleTo(used.window.level)
    used.start_edit(find_edit(used, card.MANIFESTATION))
    assert used.window.level.move_button.isVisibleTo(used.window.level)
    assert used.window.level.cover_button.isVisibleTo(used.window.level)


################################################################
# Links to items (see doc/entity-vs-item.rst, step 4)
################################################################

def test_content_and_a_cover_are_links():
    store = MemoryStore().content("darq.book.epub:a", "public.png:c")
    book = Book(title="T", editions=[Edition(cover="public.png:c", copies=[Copy(content="darq.book.epub:a")])])
    darq.book.save(store, book)
    edition = store.of_kind(darq.book.MANIFESTATION_TYPE)[0]
    copy = store.of_kind(darq.book.ITEM_TYPE)[0]
    assert [v.entity_id for v in values(edition, darq.book.COVER)] == ["c"]
    assert [v.entity_id for v in values(copy, darq.book.CONTENT)] == ["a"]


def test_an_old_identifier_is_read_and_made_a_link_when_saved():
    store = MemoryStore().content("darq.book.epub:a")
    book = Book(title="T", editions=[Edition(copies=[Copy(location="here")])])
    darq.book.save(store, book)
    copy = store.of_kind(darq.book.ITEM_TYPE)[0]
    copy.claim(darq.book.OLD_CONTENT, model.StringValue("darq.book.epub:a"))
    read = read_book(store, store.item(book.work))
    assert read.editions[0].copies[0].content == "darq.book.epub:a"

    darq.book.save(store, read)
    copy = store.of_kind(darq.book.ITEM_TYPE)[0]
    assert values(copy, darq.book.OLD_CONTENT) == [] and [v.entity_id for v in values(copy, darq.book.CONTENT)] == ["a"]


def test_content_thats_not_an_item_is_refused():
    store, book = saved()
    book.editions[0].copies[0].content = "not an item"
    with pytest.raises(BookError, match="isn't an item"):
        darq.book.save(store, book)
