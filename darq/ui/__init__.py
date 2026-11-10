# darqos
# Copyright (C) 2026 David Arnold

# User interface parts of the Terminal (services/terminal): New's type
# list (darq.ui.types), History's event browser (darq.ui.history), Search
# (darq.ui.search), Tools (darq.ui.tools), and the launcher that creates
# and uses items without blocking them (darq.ui.launcher); and, for the
# lenses, their shared look (darq.ui.lens).
#
# Their widgets talk to the window holding them through a Host: the
# services it uses, its status line, and its launcher.

import logging
import os
import time
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

if typing.TYPE_CHECKING:
    from .launcher import Launcher


# Control exports
__all__ = [
    "Host",
    "Services",
    "wait_for_display",
]


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


class Host(typing.Protocol):
    """The window holding a shared widget."""

    services: Services
    launcher: "Launcher"

    def show_status(self, message: str) -> None:
        """Show a message, such as progress or an error, in its status
        line."""
        ...

    def launched(self, context: object, item_id: str | None, error: str | None) -> None:
        """A launch has finished: see Launcher."""
        ...


def wait_for_display(timeout: float = 10.0) -> bool:
    """Wait for the compositor's socket, if this process is to use it.

    The p-kernel starts GUI processes with WAYLAND_DISPLAY=darq-0, the
    socket of the compositor the Terminal starts, which may not be
    listening yet at boot: Qt exits if it can't connect.

    :returns: False if it's still not there."""
    display = os.environ.get("WAYLAND_DISPLAY", "")
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if display != "darq-0" or not runtime:
        return True
    path = os.path.join(runtime, display)
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            logging.error(f"No Wayland display at {path}")
            return False
        time.sleep(0.1)
    return True
