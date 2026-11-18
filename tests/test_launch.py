# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.launch's open_client: an item's client, by its type's
# implementation's item protocol.

import types

import pytest

import darq
import darq.launch
from darq.errors import LaunchError
from darq.protocols import kb, pdf, python, text
from darq.protocols import type as type_protocol


@pytest.mark.parametrize("implementation, protocol", [
    ("/darq/types/text", text.Text),
    ("types/pdf", pdf.Pdf),
    ("/darq/types/python", python.Python),
    ("/darq/types/kb", kb.EntityItem),
])
def test_an_implementations_item_protocol(implementation, protocol):
    assert darq.launch.item_protocol(implementation) is protocol


@pytest.mark.parametrize("implementation", ["/darq/types/html", "/darq/types/person", "/somewhere/else"])
def test_one_with_none_is_refused(implementation):
    with pytest.raises(LaunchError, match="has no item protocol"):
        darq.launch.item_protocol(implementation)


@pytest.fixture
def described(monkeypatch):
    """The Type service, describing types as a test says, and items opened
    as ports by number."""
    implementations: dict[str, str] = {}

    class Types:
        def __init__(self, handle, timeout=None):
            pass

        def describe(self, type_id=""):
            if type_id not in implementations:
                return types.SimpleNamespace(status=type_protocol.Status.NOT_FOUND, implementation="")
            return types.SimpleNamespace(status=type_protocol.Status.OK, implementation=implementations[type_id])

    monkeypatch.setattr(darq.launch, "connect", lambda name: 1)
    monkeypatch.setattr(darq.launch.type_protocol.Type, "Client", Types)
    monkeypatch.setattr(darq.launch, "open_item", lambda item_id: 77)
    return implementations


def test_open_client_gives_the_types_client(described):
    described["public.utf8-plain-text"] = "/darq/types/text"
    item = darq.open_client("public.utf8-plain-text:" + "1" * 32, timeout=3)
    assert isinstance(item, text.Text.Client) and item.service == 77 and item.timeout == 3


def test_a_type_not_implemented_is_refused(described):
    described["public.html"] = ""
    for item_id in ("public.html:" + "1" * 32, "nothing.here:" + "1" * 32):
        with pytest.raises(LaunchError, match="isn't implemented"):
            darq.open_client(item_id)
