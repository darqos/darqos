# darqos
# Copyright (C) 2026 David Arnold

# User interface parts shared by the Terminal (services/terminal) and the
# development Terminal (tools/devterm): New's type list (darq.ui.types),
# History's event browser (darq.ui.history), and the launcher that
# creates and uses items without blocking them (darq.ui.launcher).
#
# Their widgets talk to the window holding them through a Host: the
# services it uses, its status line, and its launcher.

import logging
import os
import time
import typing

import darq
import darq.launch
from darq.protocols.history import History
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


class Services:
    """What the Terminals use: the Type, History and Security services,
    darq.launch, and shutting down and rebooting.  Tests replace them."""

    def __init__(self):
        self.types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
        self.history = History.Client(darq.connect("darq.history"), timeout=TIMEOUT)
        self.security = Security.Client(darq.connect("darq.security"), timeout=SECURITY_TIMEOUT)
        self.launch = darq.launch

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
