# darqos
# Copyright (C) 2026 David Arnold

"""Writing to the KB from a lens or tool through the KB type
implementation, so that the Index is kept up to date (see
doc/kb-service.rst)."""

import darq
import darq.kb as model
from darq.protocols.kb import EntityWriter
from darq.protocols.type import Status, Type


# Control exports
__all__ = ["writer"]


# Seconds to wait for the services.
TIMEOUT = 60


def writer(kb: model.KB) -> model.KB:
    """Return a KB reading with a KB's client, and writing through the KB
    type implementation, on the calling thread.

    :raises darq.DarqError: if the implementation can't be had."""
    types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
    activated = types.activate(type_id=model.ENTITY_TYPE)
    if activated.status != Status.OK or activated.implementation is None:
        raise darq.DarqError(f"Can't reach the KB: {activated.status.name}")
    return model.KB(kb.client, EntityWriter.Client(activated.implementation, timeout=TIMEOUT))
