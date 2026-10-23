# darqos
# Copyright (C) 2022-2026 David Arnold
#
# This is the complete programming API for DarqOS programs:
# services, types, lenses, tools, etc.  It includes the raw
# system calls, as well as userland library code to facilitate
# programming applications.
#
# Programs should `import darq` to import all DarqOS API elements.

import os
import uuid

from typing import Union

from darq.services.type import TypeServiceAPI
from darq.services.storage import StorageAPI
from darq.services.storage import api as storage_api
from darq.services.history import History

from darq.runtime.object import ObjectIdentifier, ObjectProxy
from darq.runtime.service import Service
from darq.runtime.service import ServiceAPI
from darq.runtime.tool import Tool
from darq.runtime.type import Type
from darq.runtime.util import Facility, Level, log

from .application import *
from .common import *
from .errors import *
from .loop import *
from .syscall import *


# Globals
#
# These values represent the basic runtime state of the application
# process at the system level.  All applications have them.

# FIXME: make service APIs dynamic, such that they self-construct on use

_local_context_id: str = ''

#_type_api: TypeServiceAPI = TypeServiceAPI.api()
_type_api = None

# Storage API.
#storage: Storage = Storage.api()
storage = None

# History API.
#history: History = History.api()
history = None


################################################################
# Kernel API -- System calls
################################################################

def get_state():
    global _darq_process_state
    return _darq_process_state

