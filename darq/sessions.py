# darqos
# Copyright (C) 2026 David Arnold

"""REPL sessions: each a darq.python item, opened in the REPL, its lens
(see doc/repl.rst); and the default session, which Sys-s opens.

The default's recorded in the Knowledge Base, as a statement on a fixed
entity, *REPL settings*: its *default session* is the session's entity.
So there's only ever one, and it's found again whatever it's been
renamed to.  If there's none, or it's gone, Sys-s makes a session, and
makes it the default.  When there are users, it'll be each user's."""

import typing

import darq.kb as model
from darq.kb import EntityValue, Item, Property, Snak, Statement
from . import packages
from .identifier import split_item_id


# Control exports
__all__ = ["DEFAULT_NAME", "DEFAULT_SESSION", "SETTINGS", "default_session", "find_or_make_default", "make_default",
           "vocabulary"]

# The entity whose statements are the REPL's settings, and its property
# for the default session, an item: fixed, as the rest of the vocabulary
# is (see darq.vocabulary).
SETTINGS = "8ef33b7f-293f-4108-9ca7-e74241a40cd1"
DEFAULT_SESSION = "57c4d555-99fb-4980-ac77-f01a768ff14b"

# What a default session's called, when it's made.
DEFAULT_NAME = "session"


def vocabulary() -> list[model.Entity]:
    """The REPL settings entity, and its default session property."""
    settings = Item(SETTINGS)
    settings.set_label("REPL settings")
    settings.set_description("What the REPL's set to: its default session")
    default = Property(model.Datatype.Item, DEFAULT_SESSION)
    default.set_label("default session")
    default.set_description("The REPL session Sys-s opens")
    return [settings, default]


def default_session(kb: model.KB) -> str | None:
    """Return the default session's item, or None if there's none, or
    it's gone."""
    settings = kb.get(SETTINGS, follow=False)
    if settings is None:
        return None
    for statement in settings.get_statements(DEFAULT_SESSION):
        value = statement.get_value()
        if not isinstance(value, EntityValue):
            continue
        entity = kb.get(value.entity_id)
        if entity is not None and not entity.destroyed and model.recorded_type(entity) == packages.TYPE_ID:
            return model.item_id(entity.id, packages.TYPE_ID)
    return None


def make_default(kb: model.KB, item_id: str):
    """Make a session the default: the one Sys-s opens."""
    _, entity_id = split_item_id(item_id)
    kb.state(SETTINGS, model.DARQOS, [Statement(Snak(DEFAULT_SESSION, EntityValue(entity_id)))])


def find_or_make_default(kb: model.KB, create: typing.Callable[[bytes, str], str]) -> str:
    """Return the default session's item, making a session, as the
    default, if there's none.

    :param create: Makes a package item from its zip and name, and
                   returns it: darq.launch's, through its type."""
    found = default_session(kb)
    if found is not None:
        return found
    item_id = create(packages.make({}), DEFAULT_NAME)
    make_default(kb, item_id)
    return item_id
