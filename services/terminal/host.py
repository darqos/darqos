# darqos
# Copyright (C) 2026 David Arnold

"""The Terminal's services, and its panels' host: the window holding a
panel's widget, with the services it uses."""

import typing

import darq
import darq.index
import darq.kb as model
import darq.launch
from darq.protocols.kb import KB
from darq.protocols.history import History
from darq.protocols.http import Http
from darq.protocols.security import Security
from darq.protocols.type import Type
from darq.ui import launcher


# Control exports
__all__ = ["Host", "Services"]


# Seconds to wait for services' replies, and for a password check, which
# the Security service delays after wrong ones (up to 30 seconds).
TIMEOUT = 10.0
SECURITY_TIMEOUT = 60.0

# Seconds to wait for a fetch.
FETCH_TIMEOUT = 600.0


class Services:
    """What the Terminal uses: the Type, History and Security services,
    darq.index's search, darq.launch, the Knowledge Base, for items'
    names, and shutting down and rebooting.
    Tests replace them."""

    def __init__(self):
        self.types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
        self.history = History.Client(darq.connect("darq.history"), timeout=TIMEOUT)
        self.security = Security.Client(darq.connect("darq.security"), timeout=SECURITY_TIMEOUT)
        self.index = darq.index
        self.launch = darq.launch
        self.kb_client: model.KB | None = None

    def http(self) -> Http.Client | None:
        """Return a client of the HTTP service, or None if it's not
        running.  It's looked for only when it's wanted: connecting waits
        for a service, and a Terminal mustn't wait at boot for one it can
        work without."""
        handle = darq.lookup_name("darq.http", blocking=False)
        return Http.Client(handle, timeout=FETCH_TIMEOUT) if handle is not None else None

    def kb(self) -> model.KB | None:
        """Return a client of the Knowledge Base, or None if it's not
        running: looked for only when it's wanted, as the HTTP service is,
        and kept."""
        if self.kb_client is None:
            handle = darq.lookup_name("darq.kb", blocking=False)
            if handle is not None:
                self.kb_client = model.KB(KB.Client(handle, timeout=TIMEOUT))
        return self.kb_client

    def shutdown(self):
        darq.shutdown()

    def reboot(self):
        darq.reboot()


class Host(launcher.Host, typing.Protocol):
    """The Terminal, holding a panel's widget: a launcher's host, with
    the Terminal's services."""

    services: Services
