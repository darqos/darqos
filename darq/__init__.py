# darqos
# Copyright (C) 2022-2026 David Arnold

"""The DarqOS API, for programs: services, types, lenses and tools.

What a program uses directly is darq's own: the system calls, and the
clients and servers built on them (darq.system's); item and type
identifiers (darq.identifier's); the operator's languages
(darq.language's); and an item's client, by its identifier
(open_client, darq.launch's).  The rest is reached by its module: exceptions are
darq.errors's, all derived from darq.errors.DarqError; event loops
darq.loop's, and Qt's darq.qtloop's, imported only by what has windows;
and versions darq.version's.  The library's other modules, such as
darq.kb and darq.items, are imported by name.

What the runtime, and DarqOS's own services, use, and programs don't, is
darq._runtime's."""

# The system calls, and services' clients and servers.
from .system import *

# Item and type identifiers, and names.
from .identifier import *

# The operator's languages.
from .language import language_chain, language_tag, languages

# An item's client, by its identifier.
from .launch import open_client

# Modules, reached by name.
from . import errors, loop, version


# Control exports: what's above, and nothing that turns up in darq
# because a module imports it, such as darq.dipc.
__all__ = [
    # darq.system
    "getpid", "getppid", "open_port", "open_port_cb", "close_port", "close_port_cb",
    "receive_message", "receive_any", "set_port_listener", "PortSet", "notify_on_death",
    "spawn_process", "spawn_process_cb", "get_bootstrap_port", "kill_process", "kill_process_cb",
    "exit", "wait", "ChildStatus", "register_name", "register_name_cb", "lookup_name",
    "lookup_name_cb", "get_boot_value", "get_version", "set_boot_value", "shutdown", "reboot",
    "ExitReason", "MAX_HANDLES", "MAX_REGISTERED_NAME_LENGTH", "MAX_BOOT_VALUES",
    "MAX_BOOT_VALUE_LENGTH", "Deferred", "Server", "Timer", "connect", "release_reply_port",
    "send", "serve",
    # darq.identifier
    "MAX_TYPE_ID_LENGTH", "MAX_ITEM_SPECIFIER_LENGTH", "MAX_ITEM_ID_LENGTH", "canonical_type_id",
    "is_valid_type_id", "canonical_item_id", "is_valid_item_id", "split_item_id", "make_item_id",
    "serialize_url", "MAX_NAME_LENGTH", "canonical_name",
    # darq.language
    "language_chain", "language_tag", "languages",
    # darq.launch
    "open_client",
    # Modules
    "errors", "loop", "version",
]
