# darqos
# Copyright (C) 2026 David Arnold

# Tests for REPL sessions (darq.sessions): the default session, which
# Sys-s opens, recorded in the KB, on the REPL settings entity.

import darq.items
import darq.kb as model
import darq.packages as packages
import darq.sessions as sessions
import darq.vocabulary

from test_kb_type import kb, kb_service, ports  # noqa: F401


def made(kb, n: int, name: str = "session") -> str:  # noqa: F811
    """A package's entity, as its type makes it."""
    entity_id = f"{n}" * 32
    darq.items.create(kb, packages.TYPE_ID, entity_id, name)
    return model.item_id(entity_id, packages.TYPE_ID)


def test_the_default_session(kb):  # noqa: F811
    darq.vocabulary.setup(kb)
    assert sessions.default_session(kb) is None
    first, second = made(kb, 1), made(kb, 2, "other")
    sessions.make_default(kb, first)
    assert sessions.default_session(kb) == first

    # There's only ever one: making another the default replaces it.
    sessions.make_default(kb, second)
    assert sessions.default_session(kb) == second
    settings = kb.get(sessions.SETTINGS)
    assert settings is not None and len(settings.get_statements(sessions.DEFAULT_SESSION)) == 1

    # Gone, there's none.
    kb.destroy("2" * 32)
    assert sessions.default_session(kb) is None


def test_one_is_made_if_theres_none(kb):  # noqa: F811
    darq.vocabulary.setup(kb)
    created: list[tuple[bytes, str]] = []

    def create(package: bytes, name: str) -> str:
        created.append((package, name))
        return made(kb, 3, name)

    item_id = sessions.find_or_make_default(kb, create)
    assert item_id == model.item_id("3" * 32, packages.TYPE_ID)
    assert [name for _, name in created] == ["session"] and packages.modules(created[0][0]) == {"__init__.py": ""}
    assert sessions.default_session(kb) == item_id
    assert sessions.find_or_make_default(kb, create) == item_id and len(created) == 1
