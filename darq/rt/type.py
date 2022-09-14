#! /usr/bin/env python3
# darqos
# Copyright (C) 2022 David Arnold


from typing import Dict, Iterable, List, Optional

from darq.os.service import ServiceAPI

import zmq


# Default URL for Type service.
DEFAULT_URL = "tcp://localhost:11006"

# Message field name for method name string.
METHOD = "method"

# Message field name for result boolean.
RESULT = "result"


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


class TypeTool:
    def __init__(self):
        # User-visible name for this tool, usually like "TextTool".
        self.name: str = ""

        # Type supported by this Lens.
        self.type: str = ""

        # Actions supported for this Lens.
        self.actions: List[str] = []

        # Storage identifier for the Lens implementation.
        self.impl: str = ""
        return


class TypeServiceAPI(ServiceAPI):
    """Interface to the type system.

    A Type Implementation is the code that implements the behaviour of
    a type (or sometimes, types).  The state of type instances is kept
    separately: often in the Storage service.

    Using this service, type implementations are made available to the
    system.  Once activated, code can interact with the Type Implementation
    to perform Actions on Object (or Instances) of its type(s).

    For example, a Text object's identifier is returned from the Index
    service.  The object's type is extracted from the identifier, and
    the appropriate Type Implementation is activated.

    A request for a specific Action, eg. "view", is sent to the Type
    Implementation, along with the object's identifier.  It will return
    an endpoint via which the object is accessible.

    https://en.wikipedia.org/wiki/Uniform_Type_Identifier

    """

    @staticmethod
    def api() -> "TypeServiceAPI":
        return TypeServiceAPI()

    def __init__(self, url: str = None):
        """Constructor.

        :param url: Optional service identifier."""

        super().__init__(url if url is not None else DEFAULT_URL)
        return

    def register(self, type_id: str, desc: str, impl: str, icon: str) -> bool:
        """Register the factory implementation for a type.

        :param type_id: String UTI for this type.
        :param desc: String user-visible description of this type.
        :param impl: String URI for the type implementation's executable.
        :param icon: String URI for the type's icon."""

        request = {
            METHOD: "register",
            "id": type_id,
            "description": desc,
            "implementation_uri": impl,
            "icon_uri": icon
        }

        response = self.rpc(request)
        # FIXME: add error message.
        return response[RESULT]

    def deregister(self, type_id: str) -> bool:
        """Deregister a type implementation.

        :param type_id: String UTI for type to be deregistered."""

        request = {
            METHOD: "deregister",
            "id": type_id
        }

        response = self.rpc(request)
        # FIXME: add error message.
        return response[RESULT]

    def get(self, type_id: str):
        pass

    def register_tool(self):
        pass

    def deregister_tool(self):
        pass

    def perform_action(self, object_id, action, context):
        # FIXME: this is the heart of it: do something to an object
        # FIXME: get type, find tool for action, do action.
        pass

    def activate(self, type_id: str):
        # FIXME: old now.
        """Ensure the type is running, and return its URL."""

        request = {
            METHOD: "activate",
            "type_id": type_id
        }

        response = self.rpc(request)
        result = response[RESULT]
        if result:
            url = response["url"]
            return url

        # FIXME: add error message.
        return None


# a Neme should be the base object locator thing.
# needs embedded type and location.
# so
#    type:access_scheme:location
# eg.
#    text:storage:FF52FF02-D66C-4767-9F47-7E9EA7E31FD8
# or
#    person:kb:C5556C4D-5E80-4FD9-9290-843544F4B83D
#
# How doe standard URLs fit in here?
# What format is 'type' (and 'access_scheme')?

