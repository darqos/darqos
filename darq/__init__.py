# darqos
# Copyright (C) 2022 David Arnold

import os, uuid
from typing import Optional, Union

from darq.rt.object import ObjectIdentifier, ObjectProxy
from darq.rt.type import Type, TypeServiceAPI
from darq.rt.storage import Storage
from darq.rt.history import History

# Globals
#
# These values represent the basic runtime state of the application
# at the system level.  All applications have them.

# FIXME: make service APIs dynamic, such that they self-construct on use

_local_context_id: str = ''
_type_api: TypeServiceAPI = TypeServiceAPI.api()

# Storage API.
storage: Storage = Storage.api()

# History API.
history: History = History.api()


def system_reboot():
    """Restart the system.

    All active services, tools, lens, etc, are shutdown.  Once these
    shutdown processes are complete, the system will re-initialise,
    as if starting afresh.

    M0_NOTE:
    The boot process is managed by a Python function, which
    spawns all DarqOS entities as processes, managed by the boot process.
    A restart will kill all those processes, before spawning a new boot
    process and then exiting."""

    darq.os.reboot()


def system_shutdown():
    """Shyt down the system."""

    darq.os.shutdown()


def get_local_context() -> str:
    """Return the name of the local context."""

    # FIXME: it needs to be persistent, so should come from storage

    global _local_context_id
    context_path = os.path.expanduser("~/.darq-context-id")

    # Return cached value, if set.
    if _local_context_id:
        return _local_context_id

    # Load saved value, if it exists.
    if os.path.exists(context_path):
        f = open(context_path)
        s = f.read()
        f.close()

        _local_context_id = s.strip()
        return _local_context_id

    # Generate a unique context name, save it, and cache it.
    _local_context_id = uuid.uuid4().hex

    f = open(context_path, "w")
    f.write(_local_context_id + "\n")
    f.close()

    return _local_context_id


def create_type(type_id: str, name: str):
    """Create a new Type.

    :param type_id:
    :param name:

    The created Type is transient and inaccessible until registered with
    register_type(), which persists the information, and adds it to the
    runtime type registry."""

    return Type(type_id, name)


def register_type(typedef: Type):
    # FIXME: see darq.rt.type.TypeRegistry.register()
    return _type_api.register(typedef)


def deregister_type(type_id: str):
    return _type_api.deregister(type_id)


def get_type(type_id: str):
    """Look up a Type definition by its UTI.

    :param type_id: Uniform Type Identifier for the requested type."""

    return _type_api.get(type_id)


def create_object(type_id: str, is_transient: bool = False) -> ObjectProxy:
    """Create a new object.

    :param type_id: Identifier for the new object's Type, as registered with
    the Type Service.
    :param is_transient: if True, do not persist the Object's state.
    :returns: Proxy for newly-created Object."""

    # Check type_id is legit or error.
    # Have type implementation create new instance.
    # If not transient, persist newly-created instance.
    # Create proxy for instance and return to caller.
    pass


def lookup_object(object_id: Union[str, ObjectIdentifier]) -> ObjectProxy:
    """Obtain a proxy for an existing object.

    If the specified Object identifier is unknown, return an error.

    :object_id: Object's unique identifier."""

    # If object_id is a string, convert it to structured form.

    # Check context
    # If context is unknown, look it up
    # If context is known, but remote, lookup object_id remotely

    # Lookup type_id.  If unknown, raise an error: we can't deal with
    # this type of object.

    # Get endpoint for type implementation from type service.

    # Ask type implementation to activate the object, and return a
    # proxy.
    pass


