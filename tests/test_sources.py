# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.ui.sources: where an import's data comes from, an item,
# fetched, chosen, or made from a local file; and remembering it, against
# a KB service in this process (see test_kb_type.py).

import types

import pytest

import darq
import darq.history
import darq.launch
import darq.items as items
import darq.kb as model
import darq.url
from darq.protocols.history import Event
from darq.protocols.http import Status as HttpStatus
from darq.ui import sources

from test_kb_type import implementation, index, kb, kb_service, ports  # noqa: F401


VCF = "0123456789abcdef0123456789abcdef"
JSON = "fedcba9876543210fedcba9876543210"


def data_item(store, key, name, media_type):
    items.create(store, items.DATA_TYPE, key, label=name, file_name=name,
                 statements=[(items.MEDIA_TYPE, model.StringValue(media_type))])
    return f"{items.DATA_TYPE}:{key}"


################################################################
# Accepting
################################################################

def test_accepting_by_media_type_or_extension():
    accepts = sources.accepting(["text/vcard"], ["vcf"])
    assert accepts("text/vcard; charset=utf-8", "")
    assert accepts("application/octet-stream", "Contacts.VCF")
    assert not accepts("application/json", "books.json")


################################################################
# Fetching, importing, reading
################################################################

class Http:
    def __init__(self, **response):
        self.response = types.SimpleNamespace(**{"status": HttpStatus.OK, "http_status": 200, "item": "", **response})
        self.asked: list[str] = []

    def snapshot(self, url):
        self.asked.append(url)
        return self.response


def test_fetching_a_url():
    http = Http(item=f"public.data:{VCF}")
    source = sources.fetch("https://example.com/cards/Contacts.vcf", http)
    assert source == sources.Source(item_id=f"public.data:{VCF}", name="Contacts.vcf",
                                    url="https://example.com/cards/Contacts.vcf")


@pytest.mark.parametrize("http, said", [
    (None, "The HTTP service isn't running"),
    (Http(status=HttpStatus.NETWORK), "Can't reach https://example.com/x"),
    (Http(http_status=404), "https://example.com/x: the server said 404"),
])
def test_fetching_what_cant_be(http, said):
    with pytest.raises(sources.SourceError, match=said):
        sources.fetch("https://example.com/x", http)


def test_importing_a_local_file(tmp_path, monkeypatch):
    path = tmp_path / "Contacts.vcf"
    path.write_text("BEGIN:VCARD\nEND:VCARD\n")
    made = []

    def import_data(media_type, extension, data, name, context):
        made.append((media_type, extension, data, name, context.event, context.source, context.note))
        return f"public.data:{VCF}"

    monkeypatch.setattr(darq.launch, "import_data", import_data)
    assert sources.import_file(str(path), "vcard-import") == sources.Source(item_id=f"public.data:{VCF}",
                                                                           name="Contacts.vcf", file=str(path))
    assert made == [("text/vcard", "vcf", b"BEGIN:VCARD\nEND:VCARD\n", "Contacts.vcf", "imported", "vcard-import",
                     str(path))]

    with pytest.raises(sources.SourceError, match="Can't read"):
        sources.import_file(str(tmp_path / "missing.vcf"), "vcard-import")
    monkeypatch.setattr(darq.launch, "import_data", lambda *args: None)
    with pytest.raises(sources.SourceError, match="No type can hold"):
        sources.import_file(str(path), "vcard-import")


def test_reading_an_item(monkeypatch):
    monkeypatch.setattr(darq.launch, "export_item", lambda item_id: (b"data", "text/vcard"))
    assert sources.read(sources.Source(item_id=f"public.data:{VCF}")) == b"data"

    def fail(item_id):
        raise darq.LaunchError("gone")

    monkeypatch.setattr(darq.launch, "export_item", fail)
    with pytest.raises(sources.SourceError, match="Can't read Contacts.vcf: gone"):
        sources.read(sources.Source(item_id=f"public.data:{VCF}", name="Contacts.vcf"))


################################################################
# Choosing, and remembering
################################################################

class History:
    def __init__(self, *item_ids):
        self.item_ids = item_ids

    def get_events(self, events=None, max=0):
        return types.SimpleNamespace(events=[Event(time=100 - n, item_id=i, event="imported", source="test", count=1)
                                             for n, i in enumerate(self.item_ids)])


def test_candidates_are_the_items_of_a_kind_newest_first(kb, implementation):  # noqa: F811
    vcf = data_item(kb, VCF, "Contacts.vcf", "text/vcard")
    json = data_item(kb, JSON, "books.json", "application/json")
    other = data_item(kb, "00112233445566778899aabbccddeeff", "Old.vcf", "application/octet-stream")
    offered = sources.candidates(kb, History(vcf, json, vcf, other), sources.accepting(["text/vcard"], ["vcf"]))
    assert [(s.item_id, s.name) for s in offered] == [(vcf, "Contacts.vcf"), (other, "Old.vcf")]
    assert sources.candidates(kb, None, sources.accepting(["text/vcard"], [])) == []


def test_remembering_what_an_import_came_from(kb, implementation):  # noqa: F811
    vcf = data_item(kb, VCF, "Contacts.vcf", "text/vcard")
    book = model.Item("book")
    book.set_label("Address book")
    kb.ensure(book)
    assert sources.previous(kb, "book") is None

    sources.remember(kb, "book", sources.Source(item_id=vcf))
    assert sources.previous(kb, "book") == sources.Source(item_id=vcf, name="Contacts.vcf")

    # Only the last is remembered; and one fetched is fetched again from
    # its URL.
    json = data_item(kb, JSON, "cards.json", "application/json")
    sources.remember(kb, "book", sources.Source(item_id=json))
    url = "https://example.com/cards.json"
    page = model.Item(darq.url.entity_id(url))
    page.claim(darq.url.URL, model.StringValue(url))
    page.claim(darq.url.SNAPSHOT, model.EntityValue(JSON))
    kb.ensure(page)
    assert [len(kb.get("book").get_statements(items.IMPORTED_FROM))] == [1]
    assert sources.previous(kb, "book") == sources.Source(item_id=json, name="cards.json", url=url)

    # A directory, read as it is, isn't an item, so isn't remembered.
    sources.remember(kb, "book", sources.Source(path="/tmp/export"))
    last = sources.previous(kb, "book")
    assert last is not None and last.item_id == json


################################################################
# The chooser, offscreen
################################################################

def inline(job, done):
    try:
        outcome = job()
    except Exception as e:
        outcome = e
    done(outcome)


@pytest.fixture
def chooser(qt_app):
    from PyQt6.QtWidgets import QWidget
    window = QWidget()
    made = sources.SourceChooser(window, "vcard-import", sources.accepting(["text/vcard"], ["vcf"]), inline)
    yield made
    window.close()


def test_the_chooser_shows_whats_chosen(chooser):
    assert chooser.shown.text() == "Nothing chosen" and not chooser.again.isEnabled()
    chooser.choose(sources.Source(item_id=f"public.data:{VCF}", name="Contacts.vcf", url="https://example.com/c.vcf"))
    assert chooser.shown.text() == "Contacts.vcf  (https://example.com/c.vcf)" and chooser.again.isEnabled()
    chooser.set_enabled(False)
    assert not any(b.isEnabled() for b in chooser.buttons) and not chooser.again.isEnabled()


def test_the_chooser_fetches_and_fetches_again(chooser):
    http = Http(item=f"public.data:{VCF}")
    chooser.http = lambda: http
    chooser.fetch("https://example.com/c.vcf")
    assert chooser.source is not None and chooser.source.item_id == f"public.data:{VCF}"
    chooser.fetch_again()
    assert http.asked == ["https://example.com/c.vcf"] * 2

    http.response.status = HttpStatus.NETWORK
    chooser.fetch("https://example.com/c.vcf")
    assert chooser.source is None and chooser.shown.text() == "Can't reach https://example.com/c.vcf"


def test_the_chooser_offers_items_of_its_kind(chooser, monkeypatch):
    offered = [sources.Source(item_id=f"public.data:{VCF}", name="Contacts.vcf"),
               sources.Source(item_id=f"public.data:{JSON}", name="Other.vcf")]
    monkeypatch.setattr(sources, "candidates", lambda store, history, accepts: offered)
    chooser.kb = chooser.history = lambda: None
    asked = []
    monkeypatch.setattr(sources.dialogs, "choose", lambda parent, title, question, choices: asked.append(choices) or 1)
    chooser.ask_item()
    assert asked == [["Contacts.vcf", "Other.vcf"]] and chooser.source == offered[1]

    monkeypatch.setattr(sources, "candidates", lambda store, history, accepts: [])
    chooser.ask_item()
    assert chooser.source == offered[1]
    assert chooser.shown.text() == "Other.vcf"


def test_every_fetch_failure_is_described():
    from darq.protocols.http import Status
    from darq.ui.launcher import FETCH_FAILURES
    assert set(FETCH_FAILURES) == set(Status) - {Status.OK}
