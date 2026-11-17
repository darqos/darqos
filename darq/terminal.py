# darqos
# Copyright (C) 2026 David Arnold

"""Asking the Terminal to do what the operator does: list, focus, move
and close windows, open and create items, and run tools, for a script,
or the REPL.  It's a client of the Terminal protocol, darq.terminal
(lib/didl/terminal.didl): see doc/terminal.rst, "Automation".

    import darq.terminal as terminal
    terminal.source = "tidy-windows"
    for window in terminal.windows():
        terminal.set_geometry(window.window, 0, 0)

Each function raises TerminalError if the Terminal says no, such as
LOCKED, while the screen's locked; and DarqError, or TimeoutError, if
it can't be asked.  What makes or opens items names source, as what did
it, in History."""

import typing

from .errors import DarqError
from .loop import EventLoopInterface
from .protocols import terminal as protocol
from .protocols.terminal import Output, Rect, Status, Tool, Watcher, Window
from .system import Server, connect, open_port


# Control exports
__all__ = [
    "NAME",
    "Output",
    "Rect",
    "Status",
    "TerminalError",
    "Tool",
    "Watcher",
    "Window",
    "close",
    "create_item",
    "focus",
    "import_file",
    "lock",
    "notify",
    "open_entity",
    "open_item",
    "open_url",
    "outputs",
    "run_item",
    "run_tool",
    "set_geometry",
    "source",
    "state",
    "tools",
    "watch",
    "windows",
]

# The name the Terminal's registered as.
NAME = "darq.terminal"

# Seconds to wait for an answer; and for an item's lens's window, which
# the Terminal waits for, for up to two minutes.
TIMEOUT = 10.0
OPEN_TIMEOUT = 150.0

# What the requests say they're from: set it to the script's name.
source = "script"


class TerminalError(DarqError):
    """The Terminal said no: its status, and why, if it said."""

    def __init__(self, what: str, status: Status, message: str = ""):
        super().__init__(f"{what}: {status.name}" + (f": {message}" if message else ""))
        self.status = status
        self.message = message


# The connection, made when it's first needed.
_handle: int | None = None


def _client(timeout: float = TIMEOUT) -> protocol.Terminal.Client:
    global _handle
    if _handle is None:
        _handle = connect(NAME)
    return protocol.Terminal.Client(_handle, timeout=timeout)


def _check(what: str, response: typing.Any) -> typing.Any:
    if response.status != Status.OK:
        raise TerminalError(what, response.status, getattr(response, "message", ""))
    return response


def state() -> protocol.StateResponse:
    """Return whether the screen's locked, the focused window, and the
    system's version."""
    return _client().state()


def lock():
    """Lock the screen."""
    _check("lock", _client().lock())


def notify(text: str):
    """Tell the operator something, in a notice."""
    _check("notify", _client().notify(text=text, source=source))


def windows() -> list[Window]:
    """Return the windows, most recently focused first: a lens's says the
    item it's showing."""
    return list(_check("windows", _client(OPEN_TIMEOUT).list_windows()).windows)


def outputs() -> list[Output]:
    """Return the outputs: the screens, by name."""
    return list(_check("outputs", _client().list_outputs()).outputs)


def focus(window: int):
    """Raise and focus a window."""
    _check(f"focus {window}", _client().focus(window=window))


def set_geometry(window: int, x: int, y: int, width: int = 0, height: int = 0):
    """Move a window, and resize it: a width or height of 0 keeps that as
    it is.  Its client may not take the size asked for."""
    geometry = Rect(x=x, y=y, width=width, height=height)
    _check(f"set {window}'s geometry", _client().set_geometry(window=window, geometry=geometry))


def close(window: int):
    """Ask a window's client to close it."""
    _check(f"close {window}", _client().close(window=window))


def open_item(item_id: str, lens_for: str = "") -> int:
    """Open an item, in its lens, or another type's, such as darq.thing,
    for its entity, and return the lens's window."""
    response = _client(OPEN_TIMEOUT).open_item(item_id=item_id, lens_for=lens_for, source=source)
    return _check(f"open {item_id}", response).window


def create_item(type_id: str) -> tuple[str, int]:
    """Create an item of a type, and open it: return it, and its window."""
    response = _check(f"create a {type_id}", _client(OPEN_TIMEOUT).create_item(type_id=type_id, source=source))
    return response.item_id, response.window


def import_file(path: str) -> tuple[str, int]:
    """Import a file on the host, as an item of the type its extension
    finds, and open it: return it, and its window."""
    response = _check(f"import {path}", _client(OPEN_TIMEOUT).import_file(path=path, source=source))
    return response.item_id, response.window


def open_url(url: str) -> tuple[str, int]:
    """Fetch, keep, and open what's at a URL: return its item, and its
    window."""
    response = _check(f"open {url}", _client(OPEN_TIMEOUT).open_url(url=url, source=source))
    return response.item_id, response.window


def open_entity(window: int) -> tuple[str, int]:
    """Open the entity of what a lens's window is showing: return the
    item, and the entity's window."""
    response = _check(f"open {window}'s entity", _client(OPEN_TIMEOUT).open_entity(window=window, source=source))
    return response.item_id, response.entity_window


def tools() -> list[Tool]:
    """Return the installed tools, and whether each is running."""
    return list(_check("tools", _client().list_tools()).tools)


def run_tool(name: str):
    """Run a tool: watchers hear when it's finished."""
    _check(f"run {name}", _client().run_tool(name=name, source=source))


def run_item(item_id: str):
    """Run a Python package's item, as a program: watchers hear when it's
    finished."""
    _check(f"run {item_id}", _client().run_item(item_id=item_id, source=source))


def watch(handler: Watcher.Handler, loop: EventLoopInterface) -> Server:
    """Hear what happens, with a handler, on an event loop: windows
    added, removed, changed and focused, outputs, locking, and tools
    finishing.  It's heard till the server's port is closed.

    :returns: The server answering it, which must be kept."""
    port = open_port()
    server = Server()
    server.add(port, handler)
    server.attach(loop)
    _check("watch", _client().watch(listener=port))
    return server
