# DarqOS
# Copyright (C) 2020-2022 David Arnold

from typing import Optional


class Object:
    """
    Objects in Darq represent a relatively complex grouping of state and
    behaviour, at the level of eg. a text document, an image, a CAD
    model, etc.  They are roughly analogous to an application's save
    file in Windows or macOS.

    An Object has a unique identifier, some internal state that is
    optionally persisted to the Storage service, and a link to a Type
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
        self._id = None
        self._type_id = None
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
        self._id = None
        self._type_id = type_id
        return


def create_object(type_id: str, is_transient: bool = False) \
        -> Optional[ObjectProxy]:
    """Create a new object.

    :param type_id: Identifier for the new object's Type, as registered with
    the Type Service.
    :param is_transient: if True, do not persist the Object's state.
    :returns: Proxy for newly-created Object."""

    # Check type_id is legit or error.
    # Have type implementation create new instance.
    # If not transient, persist newly-created instance.
    # Create proxy for instance and return to caller.
    return None


def activate_object(object_id: str) \
        -> Optional[ObjectProxy]:
    """Activate an existing object.

    If the specified Object identifier is unknown, return an error.

    :object_id: Object's unique identifier."""
    pass



