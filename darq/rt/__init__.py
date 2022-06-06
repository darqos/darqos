# darqos
# Copyright (C) 2022 David Arnold

# This is where the 'darq.rt' namespace is constructed.

from .history import Event, History as HistoryAPI
from .storage import Storage as StorageAPI
from .type import TypeServiceAPI as TypeAPI

# API instances in runtime library.
History = HistoryAPI.api()
Storage = StorageAPI.api()
Type = TypeAPI.api()
