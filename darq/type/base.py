# Copyright (C) 2020-2022 David Arnold

from typing import Optional


class Object:
    """Base class for types."""

    def __init__(self):
        return

    @staticmethod
    def new():
        """Create a new instance of this type."""
        return


class Type:

    def __init__(self):
        self.label = ""
        self.notes = ""
        return


class RelationshipType:

    def __init__(self):
        self.label = ""
        self.type_a = None
        self.type_b = None
        self.ab_label = ""
        self.ba_label = ""
        self.notes = ""
        return


class RelationshipInstance:

    def __init__(self):
        self.label = ""
        self.type = None
        self.party_a = None
        self.party_b = None
        self.notes = ""
        return


#-------------------------------------------------------------------------------
# API

def lookup_or_create_type(label: str, notes: str) -> Type:
    """Look up type, and create it if it doesn't exist."""

    # FIXME: look up type in type service.  If not found, register it.

    t = Type()
    t.label = label
    t.notes = notes

    # FIXME: register with type server
    return t


def get_type(label: str):
    """Look up a type with the specified label."""
    # FIXME: do lookup with type server
    return None


def new_relationship_type(label: str, type_a: Type, type_b: Type, notes: str):
    """Create a new relationship type."""

    rt = RelationshipType()
    rt.label = label
    rt.type_a = type_a
    rt.type_b = type_b
    rt.notes = notes
    return
