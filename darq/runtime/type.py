# darqos
# Copyright (C) 2022 David Arnold

from typing import Dict, Iterable, List


class Type:
    """Description of an object type.

    This description is based on Apple's Uniform Type Identifier, which
    is in turn derived primarily from MIME types."""

    def __init__(self, uti: str, name: str = ''):
        """Constructor."""

        # Uniform Type Identifier (UTI) for this type.
        self.uti: str = uti

        # Short, user-focused name for this type.
        self.name: str = name

        # Full textual description of the type.
        self.description: str = ""

        # Additional attributes of the type.  Key is a UTI, value depends
        # on the key.
        self.tags: Dict[str, str] = {}

        # List of hierarchical "parents" for this type.
        self.conforms_to: List[str] = []

        # Icon for objects of this type.
        self.icon: str = ""

        # Reference source for this type.
        self.url: str = ''

        # Storage identifier for the type implementation.
        self.actions: Dict[str, str] = {}
        return

    def get_uti(self) -> str:
        """Return the unique Uniform Type Identifier (UTI) for this type."""
        return self.uti

    def get_name(self) -> str:
        """Return the unique short name for this type."""
        return self.name

    def get_description(self) -> str:
        """Return a short description of the produced object type."""
        return self.description

    def get_tag(self, tag: str) -> str:
        """Return the value of the specified tag, or None."""
        return self.tags.get(tag)

    def get_conformed_types(self) -> Iterable[str]:
        """Return sequence of types to which this type conforms."""
        return self.conforms_to

    def get_icon(self) -> str:
        """Return identifier for type's default icon."""
        return self.icon

    def get_reference_url(self) -> str:
        """Return reference URL for this type."""
        return self.url

    def get_implementation(self, action: str) -> str:
        """Return the storage URL for the type implementation."""
        return self.actions.get(action)

