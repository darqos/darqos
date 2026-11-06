# darqos
# Copyright (C) 2026 David Arnold

# Tests for books (see doc/book.rst): the four levels read as one book,
# fields saved to their levels, the card, the form, and, live, the lens
# and its store, through a real KB.

import importlib.util
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
        return next((e.id for e in self.of_kind(type_id) if e.get_label() == label), "")

    def create(self, type_id: str, label: str) -> model.Entity:
        return self.add(type_id, label)

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
    book.editions.append(Edition(binding="EPUB", copies=[Copy(file="darq.book.epub:abc")]))
    darq.book.save(store, book)
    read = read_book(store, store.item(book.work))
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert darq.book.file_format(read.editions[2].copies[0]) == "EPUB"


def test_a_pdf_file_format():
    assert darq.book.file_format(Copy(file="com.adobe.pdf:abc")) == "PDF"
    assert darq.book.file_format(Copy(location="shelf")) == ""


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


def test_card_links_a_file_to_read():
    book = Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(file="darq.book.epub:a")]),
                                     Edition(binding="PDF", copies=[Copy(file="com.adobe.pdf:b")])])
    text, targets = page(book)
    assert targets == ["darq.book.epub:a", "com.adobe.pdf:b"]
    assert "Formats" in text and "Paper" not in text


def test_card_doesnt_repeat_a_format():
    text, _ = page(Book(title="T", editions=[Edition(binding="EPUB", copies=[Copy(file="darq.book.epub:a")])]))
    assert text.count("EPUB") == 1


def test_card_separates_paper_from_files():
    copies = [Copy(location="shelf"), Copy(file="com.adobe.pdf:b")]
    book = Book(title="T", editions=[Edition(publisher="P", copies=copies)])
    text, _ = page(book)
    assert "Formats" in text and "Paper" in text


def test_card_says_when_theres_no_location():
    assert card.NO_LOCATION in page(Book(title="T", editions=[Edition(copies=[Copy(printing="2")])]))[0]


def test_card_highlights_what_was_opened():
    store, book = saved()
    copy = book.editions[1].copies[0].entity
    text, _ = page(read_book(store, store.item(copy)))
    assert text.count('class="opened"') == 1


def test_card_with_nothing_yet():
    assert "No editions yet" in page(Book(work=model.Item(), title="T"))[0]


def test_card_links_other_texts():
    store, book = saved()
    german = store.add(darq.book.EXPRESSION_TYPE, "German text")
    german.claim(darq.book.VERSION_OF, model.EntityValue(book.work.id))
    text, targets = page(read_book(store, store.item(book.work)))
    assert "German text" in text and store.item(german) in targets


def test_card_shows_reading():
    assert "Finished 2026-03-03" in page(Book(title="T", finished="2026-03-03"))[0]


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


def test_form_gives_a_new_book_an_edition(qt_app):
    book = Book()
    form.BookForm().show_book(book)
    assert len(book.editions) == 1 and len(book.editions[0].copies) == 1


def test_form_adding_keeps_whats_typed(qt_app):
    book = bliss()
    view = form.BookForm()
    view.show_book(book)
    next(e for t, a, e in view.fields if a == "title").setText("Typed")
    view.add_copy(book.editions[0])
    view.add_edition()
    assert book.title == "Typed"
    assert len(book.editions[0].copies) == 2 and len(book.editions) == 3


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


def test_lens_opens_a_new_book_for_editing(qt_app):
    store = MemoryStore()
    work = store.add(darq.book.WORK_TYPE, "x")
    del work.labels["en"]
    store.__class__ = LensStore
    loop = python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None)
    used = lens.BookLens(loop, None, FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(work), item=5), 0)
    assert used.window.editing
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


def test_lens_reads_the_first_file(opened):
    used, _, opener = opened
    used.read_book()
    assert opener.used == [] and "no file" in used.window.status.text()
    used.book.editions[0].copies.append(Copy(file="com.adobe.pdf:a"))
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

        sys.path.insert(0, str(pathlib.Path(darq.__file__).parent.parent / "lenses" / "book"))
        import book_store

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
            store = book_store.ItemStore(["en"])
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
            store.close()

            lens = Lens.Client(darq.launch.use_item(copy_item), timeout=30)
            history = History.Client(darq.connect("darq.history"), timeout=10)
            events = [(e.event, e.source) for e in history.get_events(item_ids=[copy_item]).events]
            if events != [("opened", "book-lens")]:
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
