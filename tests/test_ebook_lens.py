# darqos
# Copyright (C) 2026 David Arnold

# Tests for reading (see doc/book.rst): the shared keys, the e-book lens's
# paging, without Chromium, cataloguing a file, and, live, the real lens,
# with WebEngine, under pyloader, through a real p-kernel.

import importlib.util
import pathlib
import sys
import typing

import pytest

import darq.book
import darq.kb as model
from darq.ui import catalogue, reader

from test_book import MemoryStore, read_book
from test_ebook_type import make_epub
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "ebook"


def load():
    sys.path.insert(0, str(LENS_PATH))
    try:
        spec = importlib.util.spec_from_file_location("ebook_pages", LENS_PATH / "ebook_pages.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LENS_PATH))
    return module


pages = load()


################################################################
# Keys
################################################################

K = reader.Key


@pytest.mark.parametrize("key, control, action", [
    (K.Key_Space, False, reader.NEXT), (K.Key_PageDown, False, reader.NEXT), (K.Key_F, True, reader.NEXT),
    (K.Key_Backspace, False, reader.PREVIOUS), (K.Key_PageUp, False, reader.PREVIOUS), (K.Key_B, True, reader.PREVIOUS),
    (K.Key_Home, False, reader.FIRST), (K.Key_End, False, reader.LAST),
    (K.Key_Plus, False, reader.LARGER), (K.Key_Minus, False, reader.SMALLER),
    (K.Key_O, False, reader.CONTENTS), (K.Key_S, True, reader.FIND), (K.Key_M, False, reader.MODE),
    (K.Key_B, False, reader.CATALOGUE), (K.Key_J, False, None), (K.Key_W, True, None), (K.Key_Slash, False, None),
])
def test_keys(key, control, action):
    assert reader.action_for(key, control) == action


def test_modes_go_round():
    names = [mode[0] for mode in reader.MODES]
    assert [reader.next_mode(n) for n in names] == [*names[1:], names[0]]


################################################################
# Paging
################################################################

class FakePage:
    """A page of a part: it answers the scripts paging runs."""

    def __init__(self, sizes: dict[str, int]):
        self.sizes = sizes
        self.loaded: list[str] = []
        self.scripts: list[str] = []
        self.page_of = -1
        self.pager: typing.Any = None

    def load(self, name: str):
        self.loaded.append(name)

    def run(self, script: str, done):
        self.scripts.append(script)
        pages_now = self.sizes[self.loaded[-1]]
        if script.startswith("window.darqPageOf") or "getSelection" in script:
            done(self.page_of)
        else:
            done(pages_now)


def pager(sizes=None, position=""):
    sizes = sizes or {"a": 3, "b": 1, "c": 2}
    page = FakePage(sizes)
    made = pages.Pager(page, list(sizes), "dark")
    made.open_at(position)
    made.part_loaded()
    return made, page


def turn(made, page, how):
    how()
    if not made.loaded:
        made.part_loaded()


def where(made):
    return made.part, made.page


def test_it_starts_at_the_start():
    assert where(pager()[0]) == (0, 0)


def test_turning_goes_through_parts():
    made, page = pager()
    seen = []
    for _ in range(7):
        turn(made, page, made.next)
        seen.append(where(made))
    assert seen == [(0, 1), (0, 2), (1, 0), (2, 0), (2, 1), (2, 1), (2, 1)]


def test_turning_back_arrives_at_the_end_of_a_part():
    made, page = pager(position="2:0.0000")
    seen = []
    for _ in range(5):
        turn(made, page, made.previous)
        seen.append(where(made))
    assert seen == [(1, 0), (0, 2), (0, 1), (0, 0), (0, 0)]


def test_first_and_last():
    made, page = pager()
    turn(made, page, made.last)
    assert where(made) == (2, 1)
    turn(made, page, made.first)
    assert where(made) == (0, 0)


def test_turning_waits_for_a_part():
    made, page = pager()
    made.go(1)
    made.next()
    assert made.part == 1 and page.loaded == ["a", "b"]


@pytest.mark.parametrize("position, part, page_at", [
    ("0:1.0000", 0, 2), ("0:0.5000", 0, 1), ("2:1.0000", 2, 1), ("", 0, 0), ("nonsense", 0, 0), ("9:0.5", 0, 0),
    ("1:7", 0, 0), ("-1:0.5", 0, 0),
])
def test_a_position_is_restored(position, part, page_at):
    assert where(pager(position=position)[0]) == (part, page_at)


def test_position_round_trip():
    made, _ = pager(position="0:0.5000")
    assert made.position() == "0:0.5000"
    assert pages.parse_position(made.position(), 3) == (0, 0.5)


def test_a_new_size_keeps_the_place():
    made, page = pager({"a": 11}, "0:0.5000")
    assert made.page == 5
    page.sizes["a"] = 21
    made.relaid()
    assert made.pages == 21 and made.page == 10


def test_a_target_goes_to_its_part_and_place():
    made, page = pager()
    page.page_of = 1
    made.go_to("c#there")
    made.part_loaded()
    assert where(made) == (2, 1)
    assert any("there" in s for s in page.scripts)


def test_a_target_thats_not_there_is_the_start_of_its_part():
    made, page = pager()
    made.go_to("c#missing")
    made.part_loaded()
    assert where(made) == (2, 0)


def test_a_target_in_no_part_goes_nowhere():
    made, page = pager()
    made.go_to("elsewhere.html")
    assert page.loaded == ["a"]


def test_a_mode_is_applied():
    made, page = pager()
    made.set_mode("sepia")
    assert any("#f4ecd8" in s for s in page.scripts)


def test_light_leaves_a_books_colours():
    assert "color: inherit" not in pages.theme("light") and "color: inherit" in pages.theme("dark")


@pytest.mark.parametrize("name", ["text/one a.xhtml", "part00001.html", "a/ü.html"])
def test_names_survive_urls(name):
    assert pages.name_of("/" + pages.url_of(name).removeprefix("book:///")) == name


def test_a_fragment_stays_in_a_url():
    assert pages.url_of("a.html#x").endswith("a.html#x")


def test_a_part_is_served_laid_out_in_its_mode_and_hidden():
    """So it's never painted as the book has it, or at the wrong page,
    before it's set up and at its page: see styled."""
    served = pages.styled(b'<html><head lang="en"><title>One</title></head><body>Hi</body></html>',
                          "application/xhtml+xml", "dark")
    assert served.startswith(b'<html><head lang="en"><style id="darq-first">')
    style = served.decode()
    assert "column-width" in style and "#1f1f1f" in style and "visibility: hidden" in style
    assert served.endswith(b"</style><title>One</title></head><body>Hi</body></html>")


@pytest.mark.parametrize("document, start", [
    (b"<HTML><BODY>Hi</BODY></HTML>", b'<HTML><style id="darq-first">'),
    (b"<p>Hi</p>", b'<style id="darq-first">'),
    (b"<html><body><header>Hi</header></body></html>", b'<html><style id="darq-first">'),
])
def test_a_part_without_a_head_is_styled_at_its_start(document, start):
    assert pages.styled(document, "text/html; charset=utf-8", "sepia").startswith(start)


def test_what_isnt_a_document_is_served_as_it_is():
    assert pages.styled(b"\x89PNG<head>", "image/png", "dark") == b"\x89PNG<head>"


def test_showing_a_page_shows_the_part():
    assert 'getElementById("darq-first")' in pages.set_up("dark") and pages.FIRST_STYLE == "darq-first"


def test_the_layout_measures_without_the_transform():
    """A part with no doctype's width is the viewport's: see SET_UP."""
    assert 'style.transform = ""' in pages.set_up("dark")


################################################################
# Cataloguing a file
################################################################

def files() -> MemoryStore:
    """A store with the files these tests catalogue: a link's to an entity
    that's there."""
    return MemoryStore().content("darq.book.epub:a", "com.adobe.pdf:b", "public.jpeg:c")


def add_content(store, content="darq.book.epub:a", title="Bliss", authors=("Peter Carey",), isbn="", answer=True,
                **details):
    return catalogue.add_content(store, content, catalogue.Details(title, list(authors), isbn, **details),
                                 lambda match: answer)


def test_a_new_file_is_a_new_book():
    store = files()
    item = add_content(store)
    book = read_book(store, item)
    assert (book.title, book.authors) == ("Bliss", ["Peter Carey"])
    assert book.editions[0].copies[0].content == "darq.book.epub:a"
    assert book.editions[0].binding == "EPUB"


def test_a_second_format_goes_on_the_same_book():
    store = files()
    first = add_content(store)
    second = add_content(store, "com.adobe.pdf:b")
    assert first == second
    book = read_book(store, first)
    assert [c.content for e in book.editions for c in e.copies] == ["darq.book.epub:a", "com.adobe.pdf:b"]
    assert book.editions[1].binding == "PDF"


def test_saying_its_not_that_book_makes_a_new_one():
    store = files()
    first = add_content(store)
    assert add_content(store, "com.adobe.pdf:b", answer=False) != first
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 2


def test_it_asks_only_if_theres_a_match():
    store = files()
    asked = []
    catalogue.add_content(store, "darq.book.epub:a", catalogue.Details("Bliss"),
                          lambda match: asked.append(match) or True)
    assert asked == []


def test_an_isbn_finds_the_paper_edition():
    store = files()
    add_content(store, isbn="0596007124")
    add_content(store, "com.adobe.pdf:b", title="Something Else", isbn="9780596007126")
    assert len(store.of_kind(darq.book.WORK_TYPE)) == 1
    assert len(store.of_kind(darq.book.MANIFESTATION_TYPE)) == 1


def test_a_files_cover_goes_on_its_manifestation():
    store = files()
    item = add_content(store, cover="public.jpeg:c")
    assert read_book(store, item).editions[0].cover == "public.jpeg:c"


def test_a_files_details_go_to_their_levels():
    store = files()
    item = add_content(store, publisher="O'Reilly", date="2009-08", language="English")
    book = read_book(store, item)
    assert book.language == "English"
    assert (book.editions[0].publisher, book.editions[0].date) == ("O'Reilly", "2009-08")


@pytest.mark.parametrize("text, isbn", [
    ("nothing here", ""), ("ISBN 0-596-00712-4", "0596007124"), ("ISBN-13: 978-0-596-00712-6", "9780596007126"),
    ("ISBN: 0596007125 (typo)", ""), ("Published 2004\n9780596007126\n", "9780596007126"),
    ("ISBN-10: 0596007124 ISBN-13: 9780596007126", "0596007124"),
])
def test_finding_an_isbn(text, isbn):
    assert darq.book.find_isbn(text) == isbn


class Reader:
    """A KB: copy c links to item a, and, from before links, copy d has
    item e's identifier."""

    def find_links(self, target_id, property_id):
        return ["c"] if (target_id, property_id) == ("a", darq.book.CONTENT) else []

    def find_by_value(self, property_id, value):
        return ["d"] if (property_id, value) == (darq.book.OLD_CONTENT, "com.adobe.pdf:e") else []


def test_find_copy():
    assert catalogue.find_copy("com.adobe.pdf:a", Reader()) == model.item_id("c", darq.book.ITEM_TYPE)
    assert catalogue.find_copy("com.adobe.pdf:e", Reader()) == model.item_id("d", darq.book.ITEM_TYPE)
    assert catalogue.find_copy("com.adobe.pdf:b", Reader()) == ""


def test_find_copy_with_no_kb():
    class Reader:
        def find_links(self, target_id, property_id):
            raise TimeoutError

    assert catalogue.find_copy("com.adobe.pdf:a", Reader()) == ""


################################################################
# Live
################################################################

def test_ebook_lens(live_pkernel, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    kernel = live_pkernel
    epub = tmp_path / "book.epub"
    epub.write_bytes(make_epub())

    # Plays the Terminal: a position's only saved once a part's been laid
    # out by the real thing, so its being there says it was.
    client_path = write_app(tmp_path / "client", f"""
        import time
        import darq
        import darq.launch
        from darq.protocols.ebook import Ebook
        from darq.protocols.history import History
        from darq.protocols.lens import Lens
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not (types.find_lenses(type_id="darq.book.epub", context="terminal").lenses
                       and types.describe(type_id="darq.book.epub").implementation):
                if time.monotonic() > deadline:
                    return "no lens"
                time.sleep(0.05)
            for type_id in ("darq.book.epub", "darq.book.mobi", "darq.book.azw"):
                path = darq.launch.find_lens(type_id)
                if not path.endswith("lenses/ebook"):
                    return f"lens for {{type_id}} is {{path}}"

            item_id, port = darq.launch.create_item_from("darq.book.epub", open({str(epub)!r}, "rb").read())
            lens = Lens.Client(darq.launch.use_item(item_id, port), timeout=30)
            time.sleep(6)
            history = History.Client(darq.connect("darq.history"), timeout=10)
            events = [(e.event, e.source) for e in history.get_events(item_ids=[item_id]).events]
            if ("opened", "ebook-lens") not in events:
                return f"events are {{events!r}}"
            lens.close()
            time.sleep(1.5)
            again = Ebook.Client(darq.launch.open_item(item_id), timeout=30)
            if again.get_position().position != "0:0.0000":
                return f"position {{again.get_position().position!r}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
