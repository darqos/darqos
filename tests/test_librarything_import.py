# darqos
# Copyright (C) 2026 David Arnold

# Tests for the LibraryThing import, tools/librarything-import: reading
# an export; importing one, with a KB service and KB type implementation
# in this process (see test_kb_type.py), and History faked; and then the
# whole system, through a real p-kernel, with an export made up for them.

import importlib
import importlib.util
import json
import pathlib
import sys
import typing

import pytest

import darq.book
import darq.kb as model

from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_vcard_import import History, Implementation


TOOL_PATH = pathlib.Path(__file__).parent.parent / "tools" / "librarything-import"


def load():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(TOOL_PATH))
    try:
        modules = [importlib.import_module(name) for name in ("lt_export", "lt_importer")]
        spec = importlib.util.spec_from_file_location("librarything_import", TOOL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
    finally:
        sys.path.remove(str(TOOL_PATH))
    return (*modules, tool)


lt_export, lt_importer, tool = load()


def record(book_id="1", **fields) -> dict:
    """A record, as an export has it, with what they all have."""
    made: dict[str, typing.Any] = {
        "books_id": book_id,
        "title": "Bliss: A Novel",
        "authors": [{"lf": "Carey, Peter", "fl": "Peter Carey", "role": "Author"}],
        "workcode": "w1",
        "isbn": {"0": "0330268031", "2": "9780330268035"},
        "originalisbn": "0330268031",
        "publication": "Picador (1982), Edition: Reprint, 296 pages",
        "date": "1982",
        "pages": "296 ",
        "format": [{"code": "1.1.1", "text": "Paperback"}],
        "language": ["English"],
        "copies": "1",
    }
    return made | fields


################################################################
# Reading
################################################################

def parsed(**fields):
    return lt_export.parse_record("1", record(**fields))


def test_a_record():
    read = parsed(ddc={"code": ["823"], "wording": ["Fiction"]}, lcc={"code": "PR9619"},
                  dateacquired="2020-03-04", fromwhere="A shop", price="$9.95 (USD)", tags=["fiction"],
                  physical_description="296 p.; 20 cm")
    assert (read.title, read.subtitle) == ("Bliss", "A Novel")
    assert (read.publisher, read.edition, read.year, read.pages) == ("Picador", "Reprint", "1982", "296")
    assert (read.isbn_13, read.isbn_10, read.binding) == ("9780330268035", "0330268031", "paperback")
    assert (read.ddc, read.lcc, read.language) == ("823", "PR9619", "English")
    assert (read.acquired, read.acquired_from, read.price) == ("2020-03-04", "A shop", "$9.95 (USD)")
    assert (read.tags, read.physical) == (["fiction"], "296 p.; 20 cm")


@pytest.mark.parametrize("text, publisher, edition", [
    ("Picador (1982), 296 pages", "Picador", ""),
    ("Harper & Row (1981), Edition: 1st, 296 pages", "Harper & Row", "1st"),
    ("Penguin Books (Australia) (2003), 300 pages", "Penguin Books (Australia)", ""),
    ("Picador (c1982)", "Picador", ""),
    ("London : Picador, 1982.", "Picador", ""),
    ("Melbourne ; Sydney : University of Queensland Press, 1981, c1980.", "University of Queensland Press", ""),
    ("Sydney, Australian Broadcasting Commission, 1973.", "Australian Broadcasting Commission", ""),
    ("Koln, Taschen,, cop. 1992", "Taschen", ""),
    ("Boston : Little, Brown, 1973.", "Little, Brown", ""),
    ("Little, Brown and Company (2007), 400 pages", "Little, Brown and Company", ""),
    ("Penguin UK", "Penguin UK", ""),
    ("London Fontana 1974", "", ""),
    ("Picador 1982", "", ""),
    ("", "", ""),
])
def test_publications(text, publisher, edition):
    assert lt_export.parse_publication(text) == (publisher, edition)


@pytest.mark.parametrize("title, main, subtitle", [
    ("Bliss", "Bliss", ""),
    ("Bliss: A Novel", "Bliss", "A Novel"),
    ("Bliss : A Novel", "Bliss", "A Novel"),
    ("Bliss:", "Bliss:", ""),
    (": x", ": x", ""),
])
def test_titles(title, main, subtitle):
    assert lt_export.split_title(title) == (main, subtitle)


@pytest.mark.parametrize("role, read", [
    ("Author", "author"), ("Autor", "author"), ("", "author"), (None, "author"),
    ("Editor", "editor"), ("Translator", "translator"), ("&Uuml;bersetzer", "translator"),
    ("Foreword", "Foreword"), ("Avec la contribution de", "Avec la contribution de"),
])
def test_roles(role, read):
    author: dict = {"fl": "A Person", "lf": "Person, A"}
    if role is not None:
        author["role"] = role
    assert parsed(authors=[author]).people[0].role == read


def test_people_with_nothing_in_them_are_left_out():
    assert parsed(authors=[[], {"fl": "", "lf": ""}, {"fl": "A"}]).people == [lt_export.Person("A", "author")]


def test_html_entities_are_resolved():
    assert parsed(title="Caf&eacute; &amp; Bar").title == "Café & Bar"


@pytest.mark.parametrize("isbn, thirteen, ten", [
    ({"0": "0330268031", "2": "9780330268035"}, "9780330268035", "0330268031"),
    (["0330268031", "9780330268035"], "9780330268035", "0330268031"),
    ([], "", "0330268031"),
    ({"0": "019853453X"}, "", "019853453X"),
])
def test_isbns(isbn, thirteen, ten):
    read = parsed(isbn=isbn)
    assert (read.isbn_13, read.isbn_10) == (thirteen, ten)


def test_repeated_series_are_one():
    assert parsed(series=["1Q84", "1Q84"]).series == [("1Q84", "")]


@pytest.mark.parametrize("name, read", [
    ("The Laundry Files", ("The Laundry Files", "")), ("The Laundry Files ; 2", ("The Laundry Files", "2")),
    ("The Laundry Files (2)", ("The Laundry Files", "2")), ("Catch (22 of them)", ("Catch (22 of them)", "")),
])
def test_series(name, read):
    assert parsed(series=[name]).series == [read]


@pytest.mark.parametrize("subject, read", [
    (None, []), ([], []), ([["Fiction", "Australia"], ["Satire"]], ["Fiction -- Australia", "Satire"]),
    ({"0": ["Fiction"], "2": ["Satire"]}, ["Fiction", "Satire"]),
])
def test_subjects(subject, read):
    assert parsed(subject=subject).subjects == read


def test_all_isnt_a_language():
    assert parsed(language=["All", "English"]).language == "English"
    assert parsed(language=[]).language == ""


@pytest.mark.parametrize("text, kind", [("Paper Book", ""), ("Other", ""), ("Hardcover", "hardcover")])
def test_bindings(text, kind):
    assert parsed(format=[{"code": "1", "text": text}]).binding == kind


def test_pages_are_the_main_count():
    assert parsed(pages="xii; 300 ").pages == "300"


def test_a_year_acquired_is_kept_and_nonsense_isnt():
    assert parsed(dateacquired="2020").acquired == "2020"
    assert parsed(dateacquired="last year").acquired == ""


def test_an_export_that_isnt():
    with pytest.raises(ValueError):
        lt_export.read_export("[]")
    with pytest.raises(ValueError):
        lt_export.read_export('{"1": {"title": "No identifier"}}')


################################################################
# Importing
################################################################

@pytest.fixture
def target(kb, implementation):  # noqa: F811
    return kb, Implementation(implementation), History()


def run(target, *records):
    client, adapter, history = target
    items = model.KB(client.client, Writer(adapter.handler))
    export = json.dumps({r["books_id"]: r for r in records})
    return lt_importer.Importer(client, items, history, lt_export.read_export(export),
                                lambda step, done, total: None).run()


def strings(entity, property_id):
    return [s.get_value().value for s in entity.get_statements(property_id)]


def one(client, property_id, value):
    [found] = list(client.find_by_value(property_id, value))
    return client.get(found)


def target_of(entity, property_id):
    return [s.get_value().entity_id for s in entity.get_statements(property_id)]


def test_first_import(target):
    result = run(target, record())
    assert result.created == {darq.book.WORK_TYPE: 1, darq.book.EXPRESSION_TYPE: 1,
                              darq.book.MANIFESTATION_TYPE: 1, darq.book.ITEM_TYPE: 1}
    assert result.failed == 0 and result.unparsed == [] and result.matches == []


def test_levels_are_linked(target):
    run(target, record())
    client = target[0]
    edition = one(client, darq.book.ISBN_13, "9780330268035")
    [text] = target_of(edition, darq.book.VERSION_OF)
    [work] = target_of(client.get(text), darq.book.VERSION_OF)
    assert client.get(work).get_label() == "Bliss"
    assert one(client, darq.book.LIBRARYTHING_BOOK, "1") is not None
    assert model.recorded_type(client.get(work)) == darq.book.WORK_TYPE
    assert model.recorded_type(edition) == darq.book.MANIFESTATION_TYPE


def test_every_statement_cites_librarything(target):
    run(target, record())
    edition = one(target[0], darq.book.ISBN_13, "9780330268035")
    for statement in edition.statements:
        if statement.get_property_id() != model.DARQOS_TYPE and statement.get_property_id() != model.INSTANCE_OF:
            assert any(a.value.entity_id == darq.book.LIBRARYTHING for r in statement.references for a in r.snaks)


def test_importing_again_changes_nothing(target):
    records = [record(), record("2", workcode="w2", isbn=["0000000000"], title="Other", originalisbn="0000000000")]
    run(target, *records)
    again = run(target, *records)
    assert again.created == {}
    assert again.updated == 0 and again.unchanged == 8


def test_two_records_of_a_work_are_two_editions_of_it(target):
    hardback = record("2", isbn=["0060150300"], originalisbn="0060150300", publication="Harper & Row (1981), 296 pages",
                      format=[{"code": "1", "text": "Hardcover"}])
    result = run(target, record(), hardback)
    assert result.created[darq.book.WORK_TYPE] == 1 and result.created[darq.book.EXPRESSION_TYPE] == 1
    assert result.created[darq.book.MANIFESTATION_TYPE] == 2 and result.created[darq.book.ITEM_TYPE] == 2


def test_two_records_of_an_edition_are_two_copies_of_it(target):
    result = run(target, record(), record("2"))
    assert result.created[darq.book.MANIFESTATION_TYPE] == 1 and result.created[darq.book.ITEM_TYPE] == 2


def test_copies_in_a_record(target):
    assert run(target, record(copies="3")).created[darq.book.ITEM_TYPE] == 3


def test_a_translation_is_another_text(target):
    german = record("2", language=["German"], isbn=["3000000000"], originalisbn="3000000000")
    assert run(target, record(), german).created[darq.book.EXPRESSION_TYPE] == 2


def test_roles_go_to_their_levels(target):
    client = target[0]
    run(target, record(authors=[{"fl": "An Author"}, {"fl": "An Editor", "role": "Editor"},
                                {"fl": "A Translator", "role": "Translator"}, {"fl": "A Writer", "role": "Foreword"}]))
    edition = one(client, darq.book.ISBN_13, "9780330268035")
    text = client.get(target_of(edition, darq.book.VERSION_OF)[0])
    work = client.get(target_of(text, darq.book.VERSION_OF)[0])
    assert [client.get(i).get_label() for i in target_of(work, darq.book.AUTHOR)] == ["An Author"]
    assert [client.get(i).get_label() for i in target_of(text, darq.book.EDITOR)] == ["An Editor"]
    assert [client.get(i).get_label() for i in target_of(text, darq.book.TRANSLATOR)] == ["A Translator"]
    [contributor] = text.get_statements(darq.book.CONTRIBUTOR)
    assert contributor.qualifiers[0].value.value == "Foreword"


def test_names_are_shared(target):
    client = target[0]
    other = record("2", workcode="w2", title="Illywhacker", isbn=["1000000000"], originalisbn="1000000000")
    run(target, record(), other)
    assert len(list(client.find_by_label("Peter Carey", "en"))) == 1
    assert len(list(client.find_by_label("Picador", "en"))) == 1


def test_a_person_found_by_name_is_listed(target):
    client, adapter, _ = target
    items = model.KB(client.client, Writer(adapter.handler))
    carey = model.Item()
    carey.set_label("Peter Carey")
    carey.claim(model.INSTANCE_OF, model.EntityValue("Q5"))
    items.create("darq.person", carey)
    result = run(target, record())
    assert [name for name, _ in result.matches] == ["Peter Carey"]


def test_people_it_made_arent_news(target):
    run(target, record())
    assert run(target, record()).matches == []


def test_a_series_named_as_its_book_stays_a_series(target):
    """The book has that label by then, on a second import."""
    client = target[0]
    book = record(title="1Q84", series=["1Q84"])
    run(target, book)
    assert run(target, book).updated == 0
    work = one(client, darq.book.LIBRARYTHING_WORK, "w1")
    assert target_of(work, darq.book.SERIES) != [work.id]


def test_a_book_entered_by_hand_isnt_made_twice(target):
    client, adapter, _ = target
    items = model.KB(client.client, Writer(adapter.handler))
    work = model.Item()
    work.set_label("Bliss, by hand")
    items.create(darq.book.WORK_TYPE, work)
    text = model.Item()
    text.claim(darq.book.VERSION_OF, model.EntityValue(work.id))
    items.create(darq.book.EXPRESSION_TYPE, text)
    edition = model.Item()
    edition.claim(darq.book.VERSION_OF, model.EntityValue(text.id))
    edition.claim(darq.book.ISBN_13, model.StringValue("9780330268035"))
    edition.claim(darq.book.BINDING, model.StringValue("mine"))
    items.create(darq.book.MANIFESTATION_TYPE, edition)

    result = run(target, record())
    assert result.created == {darq.book.ITEM_TYPE: 1}
    assert [title for title, _ in result.editions_found] == ["Bliss"]
    read = client.get(edition.id)
    assert client.get(work.id).get_label() == "Bliss, by hand"
    assert set(strings(read, darq.book.BINDING)) == {"mine", "paperback"}
    assert strings(read, darq.book.ISBN_10) == ["0330268031"]

    # And finding it again isn't news.
    again = run(target, record())
    assert again.created == {} and again.updated == 0


def test_a_hand_edit_survives_a_reimport(target):
    client, adapter, _ = target
    run(target, record())
    items = model.KB(client.client, Writer(adapter.handler))
    edition = one(client, darq.book.ISBN_13, "9780330268035")
    [binding] = edition.get_statements(darq.book.BINDING)
    changed = model.apply_edits(edition, model.change_value(edition, binding.id, model.StringValue("sewn"),
                                                            model.ENTITY_EDITOR))
    items.put(changed)
    run(target, record())
    read = client.get(edition.id)
    assert [s.get_value().value for s in read.get_statements(darq.book.BINDING)
            if s.rank != model.Rank.Deprecated] == ["sewn"]


def test_a_change_in_the_export_is_made(target):
    client = target[0]
    run(target, record())
    assert run(target, record(pages="300 ")).updated == 1
    edition = one(client, darq.book.ISBN_13, "9780330268035")
    assert [s.get_value().amount for s in edition.get_statements(darq.book.PAGES)] == [300]


def test_a_publication_not_understood_is_kept(target):
    client = target[0]
    result = run(target, record(publication="Picador 1982"))
    assert result.unparsed == ["Bliss"]
    edition = one(client, darq.book.ISBN_13, "9780330268035")
    assert strings(edition, darq.book.PUBLICATION_TEXT) == ["Picador 1982"]
    assert edition.get_statements(darq.book.PUBLISHER) == []


def test_a_book_with_no_isbn(target):
    result = run(target, record(isbn=[], originalisbn=""), record("2", isbn=[], originalisbn=""))
    assert result.created[darq.book.MANIFESTATION_TYPE] == 2


def test_a_book_with_no_author(target):
    assert run(target, record(authors=[])).failed == 0


def test_history(target):
    run(target, record())
    [(item_id, event, source)] = target[2].events
    assert item_id.startswith("darq.book.work:") and (event, source) == ("imported", "librarything-import")


def test_summary():
    result = lt_importer.Result()
    result.matches.append(("A", "darq.person:a"))
    result.unparsed.append("T")
    result.failed = 1
    lines = tool.summary(result)
    assert any("A: darq.person:a" in line for line in lines) and any("  T" == line for line in lines)
    assert any("importing again" in line for line in lines)


################################################################
# Live
################################################################

def test_librarything_import(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel
    export_path = tmp_path / "export.json"
    second = record("2", isbn=["0060150300"], originalisbn="0060150300")
    export_path.write_text(json.dumps({"1": record(), "2": second}))
    lens_path = TOOL_PATH.parent.parent / "lenses" / "book"

    client_path = write_app(tmp_path / "client", f"""
        import importlib.util
        import pathlib
        import sys
        import time
        import darq
        import darq.book
        import darq.kb as model
        import darq.launch
        from darq.protocols.type import Type

        TOOL = pathlib.Path({str(TOOL_PATH)!r})
        sys.path.insert(0, {str(lens_path)!r})
        import book_store

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.describe(type_id="darq.book.work").implementation:
                if time.monotonic() > deadline:
                    return "books never registered"
                time.sleep(0.05)

            spec = importlib.util.spec_from_file_location("librarything_import", TOOL / "main.py")
            tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool)

            result = tool.run_import({str(export_path)!r}, lambda step, done, total: None)
            if result.failed or result.created.get("darq.book.item") != 2:
                return f"imported {{result.lines()!r}}"
            again = tool.run_import({str(export_path)!r}, lambda step, done, total: None)
            if again.created or again.updated:
                return f"again {{again.lines()!r}}"

            kb = model.KB.connect(timeout=30)
            [copy] = list(kb.find_by_value(darq.book.LIBRARYTHING_BOOK, "2"))
            item_id = model.item_id(copy, darq.book.ITEM_TYPE)
            store = book_store.ItemStore(["en"])
            store.adopt(item_id, darq.launch.open_item(item_id))
            book = darq.book.read(store, item_id)
            store.close()
            if (book.title, book.authors, len(book.editions)) != ("Bliss", ["Peter Carey"], 2):
                return f"read {{book!r}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
