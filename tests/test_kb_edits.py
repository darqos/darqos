# darqos
# Copyright (C) 2026 David Arnold

# Tests for edits to entities, in darq.kb: the operations, made again to
# a later revision, and their conflicts; undoing them; and the
# operator's changes, keeping a source's statements its own, as a
# re-import (restate) does.

import pytest

import darq.kb as model


SOURCE = "book"
EDITOR = model.ENTITY_EDITOR
OWNED = frozenset({"label"})


def sourced(statement: model.Statement, source: str = SOURCE) -> model.Statement:
    return statement.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue(source)))


def person() -> model.Item:
    ada = model.Item("ada")
    ada.set_label("Ada")
    ada.claim("P551", model.EntityValue("home")).id = "home"
    sourced(ada.claim("P1329", model.StringValue("+61 7 3000 1234")).add_qualifier(
        "label", model.StringValue("work"))).id = "phone"
    ada.claim("P968", model.StringValue("mailto:ada@example.com")).id = "email"
    sourced(ada.statements[0])
    ada.revision = 3
    return ada


def get(entity: model.Entity, statement_id: str) -> model.Statement:
    statement = model.find_statement(entity, statement_id)
    assert statement is not None
    return statement


def values(entity: model.Entity, property_id: str) -> list[str]:
    return [str(model.encode_value(s.get_value())) for s in entity.get_statements(property_id)]


def test_in_order():
    def named(name, ordinal=None):
        s = model.Statement(model.Snak("P735", model.EntityValue(name)))
        if ordinal is not None:
            s.add_qualifier(model.SERIES_ORDINAL, model.StringValue(ordinal))
        return s

    statements = [named("x"), named("c", "10"), named("a", "2"), named("y"), named("b", " 9"), named("z", "?")]
    ordered = [s.get_value() for s in model.in_order(statements)]
    assert [v.entity_id for v in ordered if isinstance(v, model.EntityValue)] == ["a", "b", "c", "x", "y", "z"]


################################################################
# Operations
################################################################

def test_set_text():
    ada = person()
    edit = model.SetText("labels", "en", "Ada", "Ada Lovelace")
    edited = model.apply_edits(ada, [edit])
    assert edited.labels == {"en": "Ada Lovelace"} and ada.labels == {"en": "Ada"}
    assert edited.revision == 3

    # Made again, it does nothing; and it's undone by its inverse.
    assert model.apply_edits(edited, [edit]).labels == {"en": "Ada Lovelace"}
    assert model.apply_edits(edited, model.invert([edit])).labels == {"en": "Ada"}

    # Changed elsewhere, it conflicts, unless forced.
    ada.set_label("Augusta")
    with pytest.raises(model.EditConflict) as e:
        model.apply_edits(ada, [edit])
    assert e.value.edit is edit and edit.target() == "labels:en"
    assert model.apply_edits(ada, [edit], force=True).labels == {"en": "Ada Lovelace"}

    # None removes it.
    assert "en" not in model.apply_edits(ada, [model.SetText("labels", "en", "Augusta", None)]).labels


def test_set_aliases():
    ada = person()
    edited = model.apply_edits(ada, [model.SetAliases("en", [], ["Augusta", "Countess"])])
    assert edited.aliases == {"en": ["Augusta", "Countess"]}
    assert model.apply_edits(edited, [model.SetAliases("en", ["Augusta", "Countess"], [])]).aliases == {}
    with pytest.raises(model.EditConflict):
        model.apply_edits(edited, [model.SetAliases("en", ["Augusta"], ["A"])])


def test_statements_added_and_removed():
    ada = person()
    statement = model.Statement(model.Snak("P106", model.EntityValue("Q170790")))
    add = model.AddStatement(statement, 1)
    edited = model.apply_edits(ada, [add])
    assert [s.id for s in edited.statements] == ["home", statement.id, "phone", "email"]
    assert edited.statements[1].entity is edited
    assert model.document(model.apply_edits(edited, [add])) == model.document(edited)

    undone = model.apply_edits(edited, model.invert([add]))
    assert [s.id for s in undone.statements] == ["home", "phone", "email"]

    # Removing one that's gone does nothing; one that's changed conflicts.
    remove = model.RemoveStatement(ada.statements[2], 2)
    assert len(model.apply_edits(model.apply_edits(ada, [remove]), [remove]).statements) == 2
    changed = model.apply_edits(ada, [model.SetValue("email", model.StringValue("mailto:ada@example.com"),
                                                     model.StringValue("mailto:a@example.com"))])
    with pytest.raises(model.EditConflict):
        model.apply_edits(changed, [remove])
    assert len(model.apply_edits(changed, [remove], force=True).statements) == 2

    # Undoing a removal puts it back where it was.
    gone = model.apply_edits(ada, [model.RemoveStatement(ada.statements[1], 1)])
    back = model.apply_edits(gone, model.invert([model.RemoveStatement(ada.statements[1], 1)]))
    assert [s.id for s in back.statements] == ["home", "phone", "email"]


def test_value_rank_and_qualifiers():
    ada = person()
    old, new = model.StringValue("mailto:ada@example.com"), model.StringValue("mailto:a@example.com")
    end = model.Snak(model.END_TIME, model.TimeValue(2024, 6, 30))
    later = model.Snak(model.END_TIME, model.TimeValue(2025, precision=model.TimePrecision.Years))
    edits = [model.SetValue("email", old, new),
             model.SetRank("email", model.Rank.Normal, model.Rank.Preferred),
             model.SetQualifier("email", None, end)]
    edited = model.apply_edits(ada, edits)
    email = model.find_statement(edited, "email")
    assert email is not None and email.rank == model.Rank.Preferred
    assert values(edited, "P968") == [str(model.encode_value(new))]
    assert [q.property_id for q in email.qualifiers] == [model.END_TIME]
    assert all(e.target() == "statement:email" for e in edits)

    # Made again, they do nothing; undone, it's as it was.
    assert model.document(model.apply_edits(edited, edits)) == model.document(edited)
    assert model.document(model.apply_edits(edited, model.invert(edits))) == model.document(ada)

    # A qualifier changed, then removed.
    changed = model.apply_edits(edited, [model.SetQualifier("email", end, later)])
    time = get(changed, "email").qualifiers[0].value
    assert isinstance(time, model.TimeValue) and time.year == 2025
    removed = model.apply_edits(changed, [model.SetQualifier("email", later, None)])
    assert get(removed, "email").qualifiers == []

    # Changes to what's been changed elsewhere conflict.
    for edit in [model.SetValue("email", model.StringValue("x:y"), old),
                 model.SetRank("email", model.Rank.Deprecated, model.Rank.Normal),
                 model.SetQualifier("email", later, end)]:
        with pytest.raises(model.EditConflict):
            model.apply_edits(removed, [edit])

    # A statement that's gone can't be changed, even by force.
    gone = model.apply_edits(ada, [model.RemoveStatement(ada.statements[2])])
    with pytest.raises(model.EditConflict):
        model.apply_edits(gone, edits, force=True)


def test_edits_are_all_or_none():
    ada = person()
    edits = [model.SetText("labels", "en", "Ada", "Ada L"), model.SetRank("nothing", model.Rank.Normal,
                                                                          model.Rank.Preferred)]
    with pytest.raises(model.EditConflict):
        model.apply_edits(ada, edits)
    assert ada.labels == {"en": "Ada"}


def test_rebase():
    # Edits made to one revision are made again to a later one, changed
    # elsewhere in other ways.
    ada = person()
    mine = model.change_value(ada, "email", model.StringValue("mailto:a@example.com"), EDITOR)
    theirs = model.apply_edits(ada, [model.SetText("labels", "en", "Ada", "Ada Lovelace"),
                                     model.SetRank("phone", model.Rank.Normal, model.Rank.Preferred)])
    theirs.revision = 4
    rebased = model.apply_edits(theirs, mine)
    assert rebased.labels == {"en": "Ada Lovelace"} and rebased.revision == 4
    assert values(rebased, "P968") == [str(model.encode_value(model.StringValue("mailto:a@example.com")))]
    assert get(rebased, "phone").rank == model.Rank.Preferred


################################################################
# The operator's changes
################################################################

def reasons(statement: model.Statement) -> list[str]:
    return [q.value.entity_id for q in statement.qualifiers
            if q.property_id == model.REASON_FOR_DEPRECATED_RANK and isinstance(q.value, model.EntityValue)]


def test_operators_statements_are_changed_in_place():
    ada = person()
    new = model.StringValue("mailto:a@example.com")
    [edit] = model.change_value(ada, "email", new, EDITOR)
    assert isinstance(edit, model.SetValue)
    assert model.change_value(ada, "email", model.StringValue("mailto:ada@example.com"), EDITOR) == []
    [edit] = model.change_qualifier(ada, "email", None, model.Snak("label", model.StringValue("home")), EDITOR)
    assert isinstance(edit, model.SetQualifier)
    [edit] = model.remove_statement(ada, "email")
    assert isinstance(edit, model.RemoveStatement) and edit.index == 2
    [edit] = model.add_statement(ada, "P106", model.EntityValue("Q170790"), EDITOR)
    assert isinstance(edit, model.AddStatement) and model.is_sourced(edit.statement, EDITOR)
    assert model.is_operators(edit.statement)


def test_a_sources_statement_is_replaced():
    ada = person()
    edited = model.apply_edits(ada, model.change_value(ada, "phone", model.StringValue("+61 7 3000 9999"), EDITOR))
    old, new = edited.get_statements("P1329")
    assert old.id == "phone" and old.rank == model.Rank.Deprecated and reasons(old) == [model.REPLACED]
    assert model.is_sourced(old, SOURCE)
    assert new.rank == model.Rank.Normal and model.is_sourced(new, EDITOR) and len(new.references) == 1
    assert [q.property_id for q in new.qualifiers] == ["label"]

    # Replacing the replacement changes it, as it's the operator's, keeping
    # its reference.
    [edit] = model.change_value(edited, new.id, model.StringValue("+61 7 3000 8888"), EDITOR)
    assert isinstance(edit, model.SetValue)
    assert model.is_sourced(get(model.apply_edits(edited, [edit]), new.id), EDITOR)


def test_a_statement_both_state_is_a_sources():
    ada = person()
    phone = get(ada, "phone")
    phone.add_reference(model.Reference().add(model.STATED_IN, model.EntityValue(EDITOR)))
    assert not model.is_operators(phone)
    edits = model.change_value(ada, "phone", model.StringValue("+61 7 3000 9999"), EDITOR)
    assert [type(e) for e in edits] == [model.SetRank, model.SetQualifier, model.AddStatement]
    assert [type(e) for e in model.remove_statement(ada, "phone")] == [model.SetRank, model.SetQualifier]

    # Without references, it's the operator's, as before editors.
    assert model.is_operators(get(ada, "email"))


def test_a_sources_statement_is_annotated_in_place():
    ada = person()
    start = model.Snak(model.START_TIME, model.TimeValue(2019, 3, precision=model.TimePrecision.Months))
    [edit] = model.change_qualifier(ada, "home", None, start, EDITOR)
    assert isinstance(edit, model.SetQualifier)
    [edit, *_] = model.change_rank(ada, "home", model.Rank.Preferred)
    assert isinstance(edit, model.SetRank)

    # Its other qualifiers are the source's: changing one replaces it.
    label = get(ada, "phone").qualifiers[0]
    edits = model.change_qualifier(ada, "phone", label, model.Snak("label", model.StringValue("mobile")), EDITOR)
    edited = model.apply_edits(ada, edits)
    old, new = edited.get_statements("P1329")
    assert old.rank == model.Rank.Deprecated and model.encode_value(old.qualifiers[0].value) == \
        model.encode_value(model.StringValue("work"))
    assert model.encode_value(new.qualifiers[0].value) == model.encode_value(model.StringValue("mobile"))


def test_a_sources_statement_is_deprecated_not_removed():
    ada = person()
    edits = model.remove_statement(ada, "phone")
    edited = model.apply_edits(ada, edits)
    [phone] = edited.get_statements("P1329")
    assert phone.rank == model.Rank.Deprecated and reasons(phone) == [model.REMOVED]
    assert model.remove_statement(edited, "phone") == []

    # Undoing puts it back; so does raising its rank, without the reason.
    assert model.document(model.apply_edits(edited, model.invert(edits))) == model.document(ada)
    raised = model.apply_edits(edited, model.change_rank(edited, "phone", model.Rank.Normal))
    [phone] = raised.get_statements("P1329")
    assert phone.rank == model.Rank.Normal and reasons(phone) == []


def test_changes_survive_a_reimport():
    # The source states what it did: the operator's changes are kept, and
    # nothing's stated again.
    ada = person()
    stated = [model.copy_statement(s) for s in ada.statements if s.references]
    for s in stated:
        s.references = []
    start = model.Snak(model.START_TIME, model.TimeValue(2019, 3, precision=model.TimePrecision.Months))
    edits = (model.change_qualifier(ada, "home", None, start, EDITOR)
             + model.change_value(ada, "phone", model.StringValue("+61 7 3000 9999"), EDITOR))
    edited = model.apply_edits(ada, edits)
    edited = model.apply_edits(edited, model.remove_statement(edited, "home"))
    before = model.document(edited)

    result = model.restate(edited, SOURCE, [model.copy_statement(s) for s in stated], OWNED)
    assert not result and model.document(edited) == before
    assert len(edited.get_statements("P1329")) == 2 and len(edited.get_statements("P551")) == 1
    [home] = edited.get_statements("P551")
    assert home.rank == model.Rank.Deprecated and model.is_sourced(home, SOURCE)

    # Once the source no longer states them, its deprecated statements
    # are kept, as the operator's.
    result = model.restate(edited, SOURCE, [], OWNED)
    assert len(result.kept) == 2 and not any(model.is_sourced(s, SOURCE) for s in edited.statements)


def test_a_source_agreeing_cites_itself():
    # A source stating what the operator has adds its reference; when it
    # no longer does, the editor's keeps it.
    ada = person()
    edited = model.apply_edits(ada, model.add_statement(ada, "P106", model.EntityValue("Q170790"), EDITOR))
    occupation = model.Statement(model.Snak("P106", model.EntityValue("Q170790")))
    stated = [model.copy_statement(s) for s in ada.statements if s.references] + [occupation]
    for s in stated:
        s.references = []
    model.restate(edited, SOURCE, stated, OWNED)
    [s] = edited.get_statements("P106")
    assert model.is_sourced(s, SOURCE) and model.is_sourced(s, EDITOR)

    model.restate(edited, SOURCE, stated[:-1], OWNED)
    [s] = edited.get_statements("P106")
    assert not model.is_sourced(s, SOURCE) and model.is_sourced(s, EDITOR)
