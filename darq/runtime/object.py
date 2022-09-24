# DarqOS
# Copyright (C) 2020-2022 David Arnold

import os

from typing import Optional, Union

from darq.errors import *


# An Object reference needs to have the context, the local_id, and the
# type.
#
# To use the object, you need to resolve the reference.  That resolution
# process should result in an object proxy that you can use to access the
# methods of the Object's Type.
#
# So, within a context (ie. on a device), you need to look up the type
# and get an endpoint to access the object.  This will mean that there
# needs to be a context-wide registry of running type implementations,
# and an ability to activate one that isn't running.  This should likely
# use a well-known address.
#
# _Somewhere_ there needs to be a mapping between the context-local object
# identifier and whatever implementation-specific storage is used to
# persist that object's state.  That should probably be handled in the
# Type implementation, since it will know what sort of state is required.


class ObjectIdentifier:
    """Identifier for an object."""

    def __init__(self, object_id, type_id, context_id):
        """Constructor.

        :param object_id:
        :param type_id:
        :param context_id:
        """

        # Validate strings.
        if not self._validate(object_id):
            raise ObjectIdentifierIllegalCodepointError(object_id)

        if not self._validate(type_id):
            raise ObjectIdentifierIllegalCodepointError(type_id)

        if not self._validate(context_id):
            raise ObjectIdentifierIllegalCodepointError(context_id)

        # Locally-unique identifier for this object.
        self._object_id: str = object_id

        # Uniform Type identifier for this object.
        self._type_id: str = type_id

        # Context (eg. device) identifier.
        self._context_id: str = context_id
        return

    def from_string(self, object_id: str):
        """Parse identifier from string format.

        :param object_id:
        """
        return

    def to_string(self) -> str:
        """Return a string-format of this identifier."""
        return f'{self._context_id}:{self._type_id}:{self._object_id}'

    def is_local(self) -> bool:
        """Return True if the identifier's context is local."""
        # FIXME
        return True

    def get_id(self) -> str:
        """Return local identifier for the referenced object."""
        return self._object_id

    def get_type(self) -> str:
        """Return type identifier for the referenced object."""
        return self._type_id

    def get_context(self) -> str:
        """Return context identifier for the referenced object."""
        return self._context_id

    @staticmethod
    def _validate(s):
        """(Internal).  Validate string contents.

        Context and identifier strings must not contain a colon, control
        characters, whitespace, punctuation, etc.  This function checks
        for invalid codepoints."""

        # FIXME: implement this more thoroughly!
        return not (':' in s)


class Object:
    """
    Objects in Darq represent a relatively complex grouping of state and
    behaviour, at the level of eg. a text document, an image, a CAD
    model, etc.  They are roughly analogous to an application's save
    file in Windows or macOS.

    Objects exist within a Context.  An example context is a specific
    device.  A context's name must be resolvable such that a remote
    client can access the object.

    Each Object has a contet-unique identifier, some internal state that
    is optionally persisted to the Storage service, and a link to a Type
    implementation that provides its behaviour.

    External to the Object itself, are references to it in the History
    service, the Index Service, and the Metadata Service.  These
    references allow the object to be discovered or located by the user,

    An Object maybe be active or passive.  When active, the Object's
    state is at least partially resident in memory, and its Type's
    functions are able to be executed.  A Type implementation may share
    a process, have its own process, or many processes, as required.

    Daemon, command-line or GUI applications interact with the Object
    using an RPC mechanism, via an Object Proxy.  No more than one
    instance of an Object is ever active, although it might support many
    Lenses (views of the object, such as a GUI).  The Type implementation
    manages simultaneous access to the Object's state.

    """

    def __init__(self):
        """Constructor."""

        # Locally-unique identifier for this object.
        self._id: bytes = b''

        # Uniform Type identifier for this object.
        self._type_id: str = ''

        # FIXME: this should be more generic, to cope with eg. KB-based
        # FIXME: objects as well as storage-based objects.
        self._storage_id = None
        return

    @staticmethod
    def new():
        """Create a new instance of this type."""
        pass


class ObjectProxy:
    """
    Objects in Darq exist as the (shared) implementation of a Type
    plus the (unique) state data for the instance.

    All Objects have a unique identifier by which they are referenced.
    The OS tracks active Objects (by their identifier), creates or
    reifies them from Storage, persists or cleans them up which they
    are no longer active, and permits interaction with and between them.

    This interaction uses a proxy Object in the local process to
    represent the target Object.  The proxy identifies the Object and
    transparently implements communication with it within or between
    processes as required.
    """

    def __init__(self, type_id: str):
        """Constructor."""

        self._id = None
        self._type_id = type_id

        # Endpoint to access proxied object.
        self._socket = None
        return
