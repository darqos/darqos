# darqos
# Copyright (C) 2026 David Arnold

# Tests for adding books (see doc/book.rst): ISBNs, Open Library's
# answers, in the shapes it gives them, what a book is to what's here,
# the add loop, and, live, a lookup through the real HTTP service.

import json
import types as python_types
import typing

import pytest

import darq.book
import darq.kb as model
import darq.openlibrary as ol
from darq.book import Book, Copy, Edition
from darq.protocols.lens import UseRequest

from test_book import LENS_PATH, FakeOpener, LensStore, MemoryStore, lens, read_book, values
from test_http_service import Handler, Web, web  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


ISBN = "0596007124"

# As Open Library answered, cut down.
ANSWER = {f"ISBN:{ISBN}": {
    "url": "http://openlibrary.org/books/OL3436354M/Head_First_design_patterns",
    "key": "/books/OL3436354M",
    "title": "Head First design patterns",
    "authors": [{"url": "u1", "name": "Eric Freeman"}, {"url": "u2", "name": "Kathy Sierra"},
                {"url": "u3", "name": "Eric Freeman"}],
    "number_of_pages": 638,
    "identifiers": {"isbn_10": [ISBN], "isbn_13": ["9780596007126"], "openlibrary": ["OL3436354M"]},
    "publishers": [{"name": "O'Reilly"}],
    "publish_date": "2004",
}}

# Asked by its ISBN-13, with only the ISBN-10 in the answer.
ONLY_TEN = {"ISBN:9780330268035": {
    "title": "The money book", "subtitle": "your money and your life",
    "authors": [{"name": "Margaret Allen"}], "publishers": [{"name": "Pan"}], "publish_date": "1982",
    "identifiers": {"isbn_10": ["0330268031"], "openlibrary": ["OL14992759M"]},
}}

# What a junk ISBN found.
STRAY = {"ISBN:0000000000": {
    "title": "The three voices of poetry",
    "identifiers": {"isbn_13": ["9780000000002"], "openlibrary": ["OL43684247M"]},
}}

SEARCH = {"numFound": 2, "docs": [
    {"key": "/works/OL1707639W", "title": "Bliss", "author_name": ["Peter Carey"]},
    {"key": "/works/OL2W", "title": "Bliss and other stories", "author_name": ["Katherine Mansfield"], "subtitle": "x"},
]}


def encoded(answer) -> bytes:
    return json.dumps(answer).encode()


################################################################
# ISBNs
################################################################

@pytest.mark.parametrize("text, isbn", [
    ("0596007124", "0596007124"), ("0-596-00712-4", "0596007124"), ("978-0-596-00712-6", "9780596007126"),
    ("080442957x", "080442957X"), (" 0 596 00712 4 ", "0596007124"), ("9790000000001", "9790000000001"),
    ("0596007125", ""), ("9780596007127", ""), ("1234567890123", ""), ("", ""), ("abc", ""),
    ("0000000000", ""), ("1111111111", ""), ("019853453X", ""),
])
def test_clean_isbn(text, isbn):
    assert darq.book.clean_isbn(text) == isbn


def test_isbns_convert():
    assert darq.book.isbn_13_of("0596007124") == "9780596007126"
    assert darq.book.isbn_10_of("9780596007126") == "0596007124"
    assert darq.book.isbn_10_of("9790000000001") == ""
    assert darq.book.isbn_13_of("080442957X") == "9780804429573"


################################################################
# Open Library
################################################################

@pytest.mark.parametrize("text, date", [
    ("1982", "1982"), ("March 1982", "1982-03"), ("Jan 5, 2004", "2004-01-05"), ("5 January 2004", "2004-01-05"),
    ("c1999", ""), ("[1982?]", "1982"), ("", ""), ("someday", ""), ("Spring 1982", "1982"),
])
def test_dates(text, date):
    assert ol.parse_date(text) == date


def test_an_answer():
    book = ol.parse_isbn(encoded(ANSWER), ISBN)
    assert book is not None
    edition = book.editions[0]
    assert (book.title, edition.publisher, edition.date, edition.pages) == \
        ("Head First design patterns", "O'Reilly", "2004", "638")
    assert (edition.isbn_13, edition.isbn_10, edition.open_library) == ("9780596007126", ISBN, "OL3436354M")


def test_an_author_listed_twice_is_one():
    book = ol.parse_isbn(encoded(ANSWER), ISBN)
    assert book is not None and book.authors == ["Eric Freeman", "Kathy Sierra"]


def test_an_answer_with_only_the_other_isbn():
    book = ol.parse_isbn(encoded(ONLY_TEN), "9780330268035")
    assert book is not None
    assert (book.subtitle, book.editions[0].isbn_13, book.editions[0].isbn_10) == \
        ("your money and your life", "9780330268035", "0330268031")


def test_a_stray_record_is_refused():
    assert ol.parse_isbn(encoded(STRAY), "0000000001") is None
    answer = {"ISBN:0596007124": STRAY["ISBN:0000000000"]}
    assert ol.parse_isbn(encoded(answer), ISBN) is None


@pytest.mark.parametrize("answer", [{}, [], {"ISBN:0596007124": {}}, {"ISBN:0596007124": {"title": "No identifiers"}}])
def test_nothing_found(answer):
    assert ol.parse_isbn(encoded(answer), ISBN) is None


def test_an_answers_cover():
    answer = {f"ISBN:{ISBN}": ANSWER[f"ISBN:{ISBN}"] | {"cover": {
        "small": "https://covers.openlibrary.org/b/id/1-S.jpg", "large": "https://covers.openlibrary.org/b/id/1-L.jpg"}}}
    book = ol.parse_isbn(encoded(answer), ISBN)
    assert book is not None and book.editions[0].cover_url == "https://covers.openlibrary.org/b/id/1-L.jpg"


@pytest.mark.parametrize("covers", [None, {}, {"large": "http://covers.openlibrary.org/x.jpg"},
                                    {"large": "https://elsewhere.example/x.jpg"}])
def test_a_cover_from_anywhere_else_isnt_taken(covers):
    assert ol.cover_url(covers) == ""


def test_a_broken_answer():
    with pytest.raises(ol.LookupError):
        ol.parse_isbn(b"<html>", ISBN)


def test_a_search():
    found = ol.parse_search(encoded(SEARCH))
    assert [(b.title, b.authors) for b in found] == [("Bliss", ["Peter Carey"]),
                                                     ("Bliss and other stories", ["Katherine Mansfield"])]
    assert found[0].editions[0].publisher == "" and found[1].subtitle == "x"


def test_lookup_chooses_by_what_was_typed():
    urls = []

    def fetch(url):
        urls.append(url)
        return encoded(ANSWER if "bibkeys" in url else SEARCH)

    assert len(ol.lookup("0-596-00712-4", fetch, "http://x").books) == 1
    assert len(ol.lookup("bliss peter carey", fetch, "http://x").books) == 2
    assert urls == ["http://x/api/books?bibkeys=ISBN:0596007124&format=json&jscmd=data",
                    "http://x/search.json?q=bliss+peter+carey&fields=key%2Ctitle%2Csubtitle%2Cauthor_name&limit=8"]


def test_a_mistyped_isbn_isnt_sent():
    def fetch(url):
        raise AssertionError("it was sent")

    for text in ("0596007125", "0000000000"):
        with pytest.raises(ol.LookupError):
            ol.lookup(text, fetch)


def test_a_title_of_digits_is_searched():
    assert ol.lookup("1984", lambda url: encoded({"docs": [{"title": "1984"}]})).books[0].title == "1984"


################################################################
# What it is to what's here
################################################################

def bought(title="Bliss", isbn="0596007124", authors=("Peter Carey",), location="shelf") -> Book:
    return Book(title=title, authors=list(authors),
                editions=[Edition(isbn_10=isbn, publisher="Picador", copies=[Copy(location=location)])])


def added(store, book=None):
    book = book or bought()
    return darq.book.add(store, book, darq.book.find_match(store, book))


def test_a_new_book():
    store = MemoryStore()
    assert darq.book.find_match(store, bought()).kind == darq.book.NEW
    added(store)
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1


def test_the_same_isbn_is_another_copy():
    store = MemoryStore()
    added(store)
    match = darq.book.find_match(store, bought(location="office"))
    assert match.kind == darq.book.ANOTHER_COPY
    saved = added(store, bought(location="office"))
    assert [c.location for c in saved.editions[0].copies] == ["shelf", "office"]
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1


def test_the_same_isbn_in_its_other_form_is_another_copy():
    store = MemoryStore()
    added(store)
    book = bought(isbn="")
    book.editions[0].isbn_13 = "9780596007126"
    assert darq.book.find_match(store, book).kind == darq.book.ANOTHER_COPY


def test_a_copy_keeps_the_card_as_it_was():
    store = MemoryStore()
    added(store)
    other = bought(location="office")
    other.title = "Typed differently"
    other.editions[0].publisher = "Someone Else"
    saved = added(store, other)
    assert saved.title == "Bliss" and saved.editions[0].publisher == "Picador"


def test_the_same_title_and_author_is_another_edition():
    store = MemoryStore()
    added(store)
    other = bought(isbn="0060150300")
    assert darq.book.find_match(store, other).kind == darq.book.ANOTHER_EDITION
    saved = added(store, other)
    assert len(saved.editions) == 2 and len(store.of_kind(darq.book.WORK_TYPE)) == 1


def test_the_same_title_by_someone_else_is_a_new_book():
    store = MemoryStore()
    added(store)
    assert darq.book.find_match(store, bought(isbn="0060150300", authors=["Katherine Mansfield"])).kind == darq.book.NEW


def test_a_title_with_no_details_still_records_a_copy():
    """You have it, even if nothing else is known."""
    store = MemoryStore()
    book = Book(title="An Old Book", editions=[Edition(copies=[Copy()])])
    added(store, book)
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1


################################################################
# The loop
################################################################

class DestroyingStore(LensStore):
    destroyed: typing.ClassVar[list[str]] = []

    def destroy(self, item_id: str):
        self.destroyed.append(item_id)


@pytest.fixture
def loop(qt_app):
    """A lens on a new book, looking things up in what's given."""
    store = MemoryStore()
    work = store.add(darq.book.WORK_TYPE, "x")
    del work.labels["en"]
    store.__class__ = DestroyingStore
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), FakeOpener(), store)
    used.run = lambda job: job()
    used.fetch = lambda url: encoded(ANSWER if "bibkeys" in url else SEARCH)
    DestroyingStore.destroyed = []
    used.use(UseRequest(item_id=store.item(work), item=5), 0)
    yield used, typing.cast(typing.Any, store), used.window.adding
    if used.window is not None:
        used.window.close()


def look_up(view, text):
    view.query.setText(text)
    view.search()


def test_the_fields_are_divided_as_a_card_is():
    add = lens.book_add
    assert [[n for n, _ in level][0] for level in add.LEVELS] == ["title", "language", "publisher", "printing"]
    names = [n for level in add.LEVELS for n, _ in level]
    assert len(names) == len(set(names)) and "location" in add.LEVELS[-1][-1]


def test_a_lookup_fills_the_fields(loop):
    _, _, view = loop
    look_up(view, ISBN)
    assert view.fields["title"].text() == "Head First design patterns"
    assert view.fields["authors"].text() == "Eric Freeman; Kathy Sierra"
    assert view.fields["publisher"].text() == "O'Reilly"
    assert "new book" in view.match.text()


def test_enter_adds_and_readies_the_next(loop):
    used, store, view = loop
    look_up(view, ISBN)
    view.fields["location"].setText("study, shelf 3")
    used.add_shown()
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1
    assert view.fields["title"].text() == "" and view.query.text() == ""
    assert view.fields["location"].text() == "study, shelf 3"
    assert view.focusWidget() is view.query
    assert "1 so far" in used.window.status.text()


def test_the_first_book_uses_the_new_work(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert used.spare is None


def test_the_next_book_is_another_work(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    view.fields["title"].setText("Bliss")
    used.add_shown()
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 2


def key(view, name):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    typing.cast(typing.Any, QTest).keyClick(view, getattr(Qt.Key, name))


def test_a_new_book_needs_no_question(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    assert not view.asking and len(store.of_kind(darq.book.ITEM_TYPE)) == 1


def test_an_isbn_match_asks_before_adding_a_copy(loop):
    used, store, view = loop
    look_up(view, ISBN)
    view.fields["location"].setText("study, shelf 3")
    used.add_shown()
    look_up(view, ISBN)
    assert "You have Head First design patterns already" in view.match.text()
    used.add_shown()
    assert view.asking and "study, shelf 3" in view.question.text()
    assert view.match.text() == "" and used.window.status.text() == ""
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1
    key(view, "Key_Y")
    assert not view.asking and len(store.of_kind(darq.book.ITEM_TYPE)) == 2
    assert "copy of" in used.window.status.text()


def test_saying_no_to_a_copy_adds_nothing(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    look_up(view, ISBN)
    used.add_shown()
    key(view, "Key_N")
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1
    assert view.fields["title"].text() == "" and view.query.text() == ""


@pytest.mark.parametrize("stray", ["Key_Return", "Key_Enter", "Key_Space", "Key_5", "Key_M"])
def test_a_stray_key_isnt_a_yes(loop, stray):
    """A scanner sends Enter by itself."""
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    look_up(view, ISBN)
    used.add_shown()
    key(view, stray)
    assert view.asking and len(store.of_kind(darq.book.ITEM_TYPE)) == 1


def test_adding_while_asking_does_nothing(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    look_up(view, ISBN)
    used.add_shown()
    used.add_shown()
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1


def test_escape_leaves_the_question_and_keeps_the_fields(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    look_up(view, ISBN)
    used.add_shown()
    key(view, "Key_Escape")
    assert not view.asking and view.fields["title"].text() != ""
    assert len(store.of_kind(darq.book.ITEM_TYPE)) == 1


def by_title(view, title="Bliss", authors="Peter Carey", language="", isbn=""):
    view.fields["title"].setText(title)
    view.fields["authors"].setText(authors)
    view.fields["language"].setText(language)
    view.fields["isbn_10"].setText(isbn)


def test_a_title_match_asks(loop):
    used, store, view = loop
    by_title(view, language="English")
    used.add_shown()
    by_title(view, language="English")
    used.add_shown()
    assert view.asking and "M adds it as a new Manifestation" in view.question.text()
    key(view, "Key_M")
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 2


def test_a_title_match_can_be_a_new_book(loop):
    used, store, view = loop
    by_title(view)
    used.add_shown()
    by_title(view)
    used.add_shown()
    key(view, "Key_N")
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 2


def test_another_language_cant_go_on_the_same_text(loop):
    used, store, view = loop
    by_title(view, language="English")
    used.add_shown()
    by_title(view, language="Latin")
    used.add_shown()
    assert view.asking and "is in English, and this is in Latin" in view.question.text()
    key(view, "Key_M")
    assert view.asking and len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1
    key(view, "Key_N")
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 2


def test_a_title_offers_a_choice(loop):
    used, _, view = loop
    look_up(view, "bliss")
    assert view.choices.isVisibleTo(view) and view.choices.count() == 2
    assert view.fields["title"].text() == ""
    view.choices.setCurrentRow(1)
    view.choose()
    assert view.fields["title"].text() == "Bliss and other stories"
    assert not view.choices.isVisibleTo(view)


def test_an_unknown_isbn_is_kept_to_type_the_rest(loop):
    used, _, view = loop
    used.fetch = lambda url: b"{}"
    look_up(view, "0-596-00712-4")
    assert view.fields["isbn_10"].text() == ISBN
    assert "doesn't know" in used.window.status.text()


def test_a_failed_lookup_says_so(loop):
    used, _, view = loop

    def fail(url):
        raise ol.LookupError("Open Library said 503")

    used.fetch = fail
    look_up(view, ISBN)
    assert "503" in used.window.status.text()


def test_a_mistyped_isbn_says_so(loop):
    used, _, view = loop
    look_up(view, "0596007125")
    assert "valid ISBN" in used.window.status.text()


def test_a_book_needs_a_title(loop):
    used, store, _ = loop
    used.add_shown()
    assert "title" in used.window.status.text() and store.of_kind(darq.book.ITEM_TYPE) == []


def test_escape_clears_and_then_finishes(loop):
    used, store, view = loop
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    keys = typing.cast(typing.Any, QTest)
    look_up(view, ISBN)
    used.add_shown()
    look_up(view, ISBN)
    keys.keyClick(view.fields["title"], Qt.Key.Key_Escape)
    assert view.fields["title"].text() == "" and used.window.stack.currentWidget() is view
    keys.keyClick(view.query, Qt.Key.Key_Escape)
    assert used.window.stack.currentWidget() is used.window.browser
    assert "Head First" in used.window.browser.toPlainText()


def test_an_unused_new_book_is_cleared_away(loop):
    used, store, _ = loop
    work = used.item_id
    used.stop_adding()
    assert store.destroyed == [work]
    assert used.window is None or not used.window.isVisible()


def test_a_copy_first_clears_the_new_book_away(loop):
    used, store, view = loop
    existing = bought(isbn=ISBN)
    darq.book.save(store, existing)
    work = used.spare.work
    look_up(view, ISBN)
    used.add_shown()
    used.stop_adding()
    assert store.destroyed == [model.item_id(work.id, darq.book.WORK_TYPE)]


def test_an_open_library_id_is_kept(loop):
    used, store, view = loop
    look_up(view, ISBN)
    used.add_shown()
    [edition] = store.of_kind(darq.book.MANIFESTATION_TYPE)
    assert values(edition, darq.book.OPEN_LIBRARY)[0].value == "OL3436354M"


def test_adding_from_a_card(qt_app):
    store = MemoryStore()
    book = bought()
    darq.book.save(store, book)
    store.__class__ = LensStore
    used = lens.BookLens(python_types.SimpleNamespace(post=lambda job: job(), stop=lambda: None), FakeOpener(), store)
    used.use(UseRequest(item_id=store.item(book.work), item=5), 0)
    used.start_adding()
    assert used.window.stack.currentWidget() is used.window.adding and used.spare is None
    used.window.close()


def test_read_back_after_adding(loop):
    used, store, view = loop
    look_up(view, ISBN)
    view.fields["binding"].setText("paperback")
    used.add_shown()
    read = read_book(store, used.item_id)
    assert (read.title, read.editions[0].binding) == ("Head First design patterns", "paperback")


################################################################
# Live
################################################################

def test_lookup_through_the_http_service(live_pkernel, web, tmp_path):  # noqa: F811
    kernel = live_pkernel
    web.pages[f"/api/books?bibkeys=ISBN:{ISBN}&format=json&jscmd=data"] = (200, "application/json", encoded(ANSWER), {})
    client_path = write_app(tmp_path / "client", f"""
        import importlib.util
        import sys
        import time
        import darq
        import darq.launch
        import darq.openlibrary as ol
        from darq.protocols.type import Type

        LENS = {str(LENS_PATH)!r}
        sys.path.insert(0, LENS)

        def darq_main():
            spec = importlib.util.spec_from_file_location("book_lens", LENS + "/main.py")
            lens = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(lens)
            deadline = time.monotonic() + 30
            while darq.lookup_name("darq.http", blocking=False) is None:
                if time.monotonic() > deadline:
                    return "no HTTP service"
                time.sleep(0.05)
            found = ol.lookup({ISBN!r}, lens.fetch_url, {web.base!r})
            if [b.title for b in found.books] != ["Head First design patterns"]:
                return f"found {{found!r}}"

            types = Type.Client(darq.connect("darq.type"), timeout=30)
            while not types.describe(type_id="darq.book.work").implementation:
                if time.monotonic() > deadline:
                    return "books never registered"
                time.sleep(0.05)

            item_id, port = darq.launch.create_item("darq.book.work")
            darq.close_port(port)
            darq.launch.destroy_item(item_id)
            try:
                darq.launch.open_item(item_id)
            except darq.DarqError:
                return 0
            return "it wasn't destroyed"
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
