# darqos
# Copyright (C) 2026 David Arnold

# Tests for merging one entity into another (see doc/kb-service.rst):
# saying the same thing once; what's offered and which survives, with a
# KB service and KB type implementation in this process (see
# test_kb_type.py); and the entity lens's M.

import types as python_types
import typing

import pytest

import darq.history
import darq.kb as model
import darq.merging as merging

from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_vcard_import import Implementation
from test_wikidata import FakeWindow, entity_lens


ORGANISATION = "darq.organisation"


################################################################
# Saying the same thing once
################################################################

def cited(statement: model.Statement, source: str) -> model.Statement:
    statement.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue(source)))
    return statement


def sources(statement: model.Statement) -> list[str]:
    return sorted(typing.cast(model.EntityValue, a.value).entity_id for r in statement.references for a in r.snaks)


def test_the_same_statement_twice_is_one_cited_to_both():
    entity = model.Item("x")
    cited(entity.claim("P17", model.EntityValue("Q145")), "lt")
    cited(entity.claim("P17", model.EntityValue("Q145")), "wd")
    cited(entity.claim("P17", model.EntityValue("Q145")), "wd")
    assert model.combine_statements(entity)
    [country] = entity.get_statements("P17")
    assert sources(country) == ["lt", "wd"]


@pytest.mark.parametrize("change", ["value", "qualifier", "rank"])
def test_statements_that_differ_stay(change):
    entity = model.Item("x")
    entity.claim("P17", model.EntityValue("Q145"))
    other = entity.claim("P17", model.EntityValue("Q408" if change == "value" else "Q145"))
    if change == "qualifier":
        other.add_qualifier("P580", model.StringValue("1986"))
    if change == "rank":
        other.rank = model.Rank.Preferred
    assert not model.combine_statements(entity)
    assert len(entity.get_statements("P17")) == 2


################################################################
# What's offered, and which survives
################################################################

@pytest.fixture
def items(kb, implementation):  # noqa: F811
    return model.KB(kb.client, Writer(Implementation(implementation).handler))


def made(items, label, type_id=ORGANISATION, entity_id=None, description=""):
    entity = model.Item(entity_id)
    entity.set_label(label)
    if description:
        entity.set_description(description)
    if entity_id is not None:
        # As a Wikidata entity becomes one of a type, by a merge.
        entity.claim(model.DARQOS_TYPE, model.StringValue(type_id))
        model.KB(items.client).put(entity)
    else:
        items.create(type_id, entity)
    return items.get(entity.id)


def test_candidates_are_of_its_kind_by_name(items):
    plc = made(items, "Bloomsbury Publishing PLC")
    bloomsbury = made(items, "Bloomsbury Publishing", entity_id="Q568642", description="British publishing house")
    made(items, "Bloomsbury", type_id="darq.person")
    found = merging.candidates(items, plc, "Bloomsbury")
    assert [c.id for c in found] == ["Q568642"]
    assert found[0].describe() == "Bloomsbury Publishing — British publishing house (Q568642, Wikidata)"
    assert bloomsbury is not None


def test_an_untyped_wikidata_entity_is_offered_and_gains_the_type(items):
    """As one made minimally, for what a person's facts refer to, is."""
    local = made(items, "Pan Macmillan UK")
    bare = model.Item("Q2042203")
    bare.set_label("Pan Macmillan")
    model.KB(items.client).put(bare)
    [found] = merging.candidates(items, local, "Pan Macmillan")
    assert found.id == "Q2042203"
    plan = merging.plan(local, items.get("Q2042203"))
    items.merge(plan.old.id, plan.new.id)
    assert model.recorded_type(items.get("Q2042203")) == ORGANISATION


def test_a_candidate_without_a_description_says_how_linked_it_is(items):
    this = made(items, "Picador UK")
    other = made(items, "Picador")
    book = model.Item()
    book.set_label("Bliss")
    book.claim("P123", model.EntityValue(other.id))
    items.create("darq.entity", book)
    [found] = merging.candidates(items, this, "Picador")
    assert found.describe() == f"Picador — 1 link ({other.id})"


def test_whats_merged_isnt_offered(items):
    this = made(items, "Picador UK")
    old = made(items, "Picador")
    new = made(items, "Pan Macmillan")
    items.merge(old.id, new.id)
    assert [c.id for c in merging.candidates(items, this, "Picador")] == [new.id]


def test_this_goes_into_that():
    this, that = model.Item("a"), model.Item("b")
    plan = merging.plan(this, that)
    assert (plan.old.id, plan.new.id, plan.reversed) == ("a", "b", False)


def test_wikidatas_survives():
    this, that = model.Item("Q568642"), model.Item("b")
    plan = merging.plan(this, that)
    assert (plan.old.id, plan.new.id, plan.reversed) == ("b", "Q568642", True)
    assert "so that Wikidata's identifier, Q568642, stays" in plan.question()


def test_the_question_has_both_identifiers():
    this, that = model.Item("a"), model.Item("b")
    this.set_label("Bloomsbury Publishing PLC")
    that.set_label("Bloomsbury")
    question = merging.plan(this, that).question()
    assert question.startswith("Merge Bloomsbury Publishing PLC (a) into Bloomsbury (b)?")
    assert "can't be undone" in question


def typed(entity_id: str, type_id: str) -> model.Item:
    entity = model.Item(entity_id)
    entity.claim(model.DARQOS_TYPE, model.StringValue(type_id))
    return entity


@pytest.mark.parametrize("this, that, why", [
    (model.Item("a"), model.Item("a"), "itself"),
    (typed("a", "darq.person"), typed("b", ORGANISATION), "different kinds"),
    (model.Item("Q1"), model.Item("Q2"), "Wikidata says they're different"),
])
def test_what_cant_be_merged(this, that, why):
    with pytest.raises(merging.MergeError, match=why):
        merging.plan(this, that)


def test_one_thats_merged_cant_be_again():
    gone = model.Item("a")
    gone.redirect = "c"
    with pytest.raises(merging.MergeError, match="already been merged"):
        merging.plan(gone, model.Item("b"))


################################################################
# The entity lens's M
################################################################

def lens_for(window: FakeWindow, items, asks="Bloomsbury", chooses: int | None = 0, confirms=True):
    """A window, with an entity lens doing what's asked at once, and
    what's recorded and asked."""
    used = python_types.SimpleNamespace(
        connect_kb=lambda: items, items=lambda _: items, run=lambda job: job(),
        ask_text=lambda question, text: asks, choose=lambda question, choices, title="": chooses,
        loop=python_types.SimpleNamespace(post=lambda job: job()), recorded=[], asked=[])
    used.confirm = lambda question: used.asked.append(question) or confirms
    used.record = lambda item_id, event: used.recorded.append((item_id, event))
    used.in_kb = lambda job, done: entity_lens.EntityLens.in_kb(typing.cast(typing.Any, used), job, done)
    window.lens = used
    return used


class MergingWindow(FakeWindow):
    merge = entity_lens.EntityWindow.merge
    merge_found = entity_lens.EntityWindow.merge_found
    merge_into = entity_lens.EntityWindow.merge_into
    merged = entity_lens.EntityWindow.merged


def test_m_merges_into_the_one_chosen(items):
    plc = made(items, "Bloomsbury Publishing PLC")
    bloomsbury = made(items, "Bloomsbury")
    window = MergingWindow(plc)
    lens = lens_for(window, items)
    window.merge()
    assert items.get(plc.id).id == bloomsbury.id
    assert "Bloomsbury Publishing PLC" in items.get(bloomsbury.id).get_aliases()
    assert lens.asked[0].startswith(f"Merge Bloomsbury Publishing PLC ({plc.id}) into Bloomsbury ({bloomsbury.id})")
    assert lens.recorded == [(f"{ORGANISATION}:{plc.id}", darq.history.MERGED),
                             (f"{ORGANISATION}:{bloomsbury.id}", darq.history.MERGED)]
    assert window.refreshed == 1 and window.said[-1] == f"Merged {plc.id} into {bloomsbury.id}"


def test_m_on_wikidatas_keeps_it(items):
    wikidata = made(items, "Bloomsbury Publishing", entity_id="Q568642")
    plc = made(items, "Bloomsbury Publishing PLC")
    window = MergingWindow(wikidata)
    lens_for(window, items, asks="Bloomsbury Publishing PLC")
    window.merge()
    assert items.get(plc.id).id == "Q568642"
    assert items.get("Q568642", follow=False).redirect is None


def test_not_confirmed_merges_nothing(items):
    plc = made(items, "Bloomsbury Publishing PLC")
    made(items, "Bloomsbury")
    window = MergingWindow(plc)
    lens = lens_for(window, items, confirms=False)
    window.merge()
    assert items.get(plc.id).id == plc.id and lens.recorded == []


def test_none_chosen_merges_nothing(items):
    plc = made(items, "Bloomsbury Publishing PLC")
    made(items, "Bloomsbury")
    window = MergingWindow(plc)
    lens = lens_for(window, items, chooses=None)
    window.merge()
    assert items.get(plc.id).id == plc.id and lens.asked == []


def test_nothing_by_that_name_says_so(items):
    plc = made(items, "Bloomsbury Publishing PLC")
    window = MergingWindow(plc)
    lens_for(window, items, asks="Faber")
    window.merge()
    assert window.said[-1] == "There's nothing else of its kind by that name"


def test_one_that_cant_be_says_why(items):
    one = made(items, "Bloomsbury", entity_id="Q1")
    made(items, "Bloomsbury Publishing", entity_id="Q568642")
    window = MergingWindow(one)
    lens = lens_for(window, items, asks="Bloomsbury Publishing")
    window.merge()
    assert "Wikidata says they're different" in window.said[-1] and lens.asked == []
