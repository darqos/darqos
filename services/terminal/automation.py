# darqos
# Copyright (C) 2026 David Arnold

# The Terminal's protocol, darq.terminal (lib/didl/terminal.didl): what a
# script, or the REPL, can ask of it, to do what the operator does with
# its panels and hotkeys.  See doc/terminal.rst, "Automation".
#
# It's answered on the Terminal's Qt thread, as its panels are: what
# would block, such as starting a lens, or asking one what it's
# showing, is done off it, by a launcher, and answered later, with a
# Deferred.

import dataclasses
import logging
import typing

import darq
import darq.items
import darq.packages
from darq.protocols.compositor import CloseWindow, FocusWindow, RaiseWindow
from darq.protocols.compositor import SetGeometry as SetCompositorGeometry
from darq.protocols.lens import Lens
from darq.protocols.terminal import (
    CloseRequest, CloseResponse, CreateItemRequest, CreateItemResponse, FocusRequest, FocusResponse,
    RunItemRequest, RunItemResponse,
    ImportFileRequest, ImportFileResponse, ListOutputsRequest, ListOutputsResponse, ListToolsRequest,
    ListToolsResponse, ListWindowsRequest, ListWindowsResponse, LockRequest, LockResponse, NotifyRequest,
    NotifyResponse, OpenEntityRequest, OpenEntityResponse, OpenItemRequest, OpenItemResponse, OpenUrlRequest,
    OpenUrlResponse, Output, Rect, RunToolRequest, RunToolResponse, SetGeometryRequest, SetGeometryResponse,
    StateRequest, StateResponse, Status, Terminal, Tool, WatchRequest, WatchResponse, Window,
)
from darq.ui.launcher import Launcher, start_thread
from darq.ui.tools import find_tools


# Control exports
__all__ = ["NAME", "TerminalService", "lens_shows"]

# The name it's registered as.
NAME = "darq.terminal"

# What a lens's Lens port's name starts with (see darq.ui.lens), and
# seconds to wait for it to say what it's showing.
LENS_NAME_PREFIX = "lens."
LENS_TIMEOUT = 5.0

# Seconds to wait for an item's lens's window, from the request.
OPEN_TIMEOUT = 120.0

# What the source is, when a request doesn't say.
DEFAULT_SOURCE = "script"


def lens_shows(name: str, timeout: float = LENS_TIMEOUT) -> tuple[str, bool]:
    """Ask a lens, by its Lens port's name, what it's showing: its item,
    and whether that's an entity.  It blocks: it's called off Qt's thread.

    :raises darq.DarqError, TimeoutError: if it can't be asked."""
    handle = darq.lookup_name(name, blocking=False)
    if handle is None:
        raise darq.DarqError(f"{name} isn't there")
    try:
        shown = Lens.Client(handle, timeout=timeout).shown()
    finally:
        darq.close_port(handle)
    return shown.item_id, shown.entity


class Host(typing.Protocol):
    """What the service needs of the Terminal: its state, and what it
    does (see services/terminal/main.py)."""

    services: typing.Any
    channel: typing.Any
    loop: typing.Any
    launcher: Launcher
    locked: bool
    focused: int
    windows: dict[int, str]
    app_ids: dict[int, str]
    recent: list[int]
    geometry: dict[int, Rect]
    outputs: dict[str, Rect]
    tools: typing.Any
    root: typing.Any

    # The ports of those watching, which events are sent to; and the
    # packages running as programs.
    watchers: set[int]
    running_items: set[str]

    def run_item(self, item_id: str) -> str: ...

    def lock(self) -> None: ...
    def say(self, message: str) -> None: ...


@dataclasses.dataclass
class Opening:
    """An item being opened for a request: answered once its lens's window
    is shown, or it fails."""

    deferred: darq.Deferred
    respond: typing.Callable[[str, int, Status, str], typing.Any]
    item_id: str = ""
    dpid: int = 0

    def answer(self, window: int = 0, status: Status = Status.OK, message: str = ""):
        try:
            self.deferred.reply(self.respond(self.item_id, window, status, message))
        except RuntimeError:
            # It's been answered already: it timed out.
            logging.info(f"Opening {self.item_id or 'an item'} finished too late: {status.name} {message}")


class LaunchHost:
    """What a request's launcher reports to: rather than the Terminal's
    panels, the request."""

    def __init__(self, services: typing.Any, finished: typing.Callable[[Opening, str | None, str | None], None]):
        self.services = services
        self.finished = finished

    def show_status(self, message: str):
        logging.info(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        assert isinstance(context, Opening)
        self.finished(context, item_id, error)


class TerminalService(Terminal.Handler):
    """Answers the Terminal protocol, for the Terminal it's given."""

    def __init__(self, terminal: Host):
        self.terminal = terminal

        # Items being opened, waiting for their lenses' windows, by the
        # lenses' DPIDs.
        self.opening: dict[int, Opening] = {}

        # How requests' launches run, and come back to Qt's thread: tests
        # replace them.
        self.run: typing.Callable[[typing.Callable[[], None]], None] = start_thread
        self.post: typing.Callable[[typing.Callable[[], None]], None] = terminal.loop.post

    # The session

    def state(self, request: StateRequest, source: int) -> StateResponse:
        return StateResponse(locked=self.terminal.locked, focused=self.terminal.focused,
                             version=darq.get_version().version)

    def lock(self, request: LockRequest, source: int) -> LockResponse:
        if not self.terminal.locked:
            self.terminal.lock()
        return LockResponse(status=Status.OK)

    def notify(self, request: NotifyRequest, source: int) -> NotifyResponse:
        if self.terminal.locked:
            return NotifyResponse(status=Status.LOCKED)
        logging.info(f"Notice from {request.source or DEFAULT_SOURCE}")
        self.terminal.say(request.text)
        return NotifyResponse(status=Status.OK)

    # Windows

    def window(self, window: int, item_id: str = "", entity: bool = False) -> Window:
        """Return a window, as the protocol has it."""
        terminal = self.terminal
        return Window(window=window, title=terminal.windows.get(window, ""), app_id=terminal.app_ids.get(window, ""),
                      geometry=terminal.geometry.get(window, Rect()), focused=window == terminal.focused,
                      item_id=item_id, entity=entity)

    def list_windows(self, request: ListWindowsRequest, source: int) -> ListWindowsResponse | darq.Deferred:
        if self.terminal.locked:
            return ListWindowsResponse(status=Status.LOCKED)
        order = [w for w in self.terminal.recent if w in self.terminal.windows]
        order += [w for w in self.terminal.windows if w not in order]
        lenses = {w: self.terminal.app_ids.get(w, "") for w in order}
        lenses = {w: name for w, name in lenses.items() if name.startswith(LENS_NAME_PREFIX)}
        if not lenses:
            return ListWindowsResponse(windows=[self.window(w) for w in order], status=Status.OK)

        # Lenses are asked what they're showing, off Qt's thread.
        deferred = self.defer(timeout=LENS_TIMEOUT * (len(lenses) + 1))

        def ask() -> dict[int, tuple[str, bool]]:
            shows = {}
            for window, name in lenses.items():
                try:
                    shows[window] = lens_shows(name)
                except (darq.DarqError, TimeoutError) as e:
                    logging.info(f"Can't ask {name} what it's showing: {e}")
            return shows

        def asked(shows: dict[int, tuple[str, bool]] | None, error: Exception | None):
            shows = shows or {}
            windows = [self.window(w, *shows.get(w, ("", False))) for w in order if w in self.terminal.windows]
            try:
                deferred.reply(ListWindowsResponse(windows=windows, status=Status.OK))
            except RuntimeError:
                logging.info("Listing the windows took too long")

        self.terminal.launcher.background(ask, asked)
        return deferred

    def list_outputs(self, request: ListOutputsRequest, source: int) -> ListOutputsResponse:
        outputs = [Output(name=name, geometry=box) for name, box in sorted(self.terminal.outputs.items())]
        return ListOutputsResponse(outputs=outputs, status=Status.OK)

    def known(self, window: int) -> Status:
        """Return whether a window can be acted on."""
        if self.terminal.locked:
            return Status.LOCKED
        return Status.OK if window in self.terminal.windows else Status.NOT_FOUND

    def focus(self, request: FocusRequest, source: int) -> FocusResponse:
        status = self.known(request.window)
        if status == Status.OK:
            self.terminal.channel.send(RaiseWindow(window=request.window))
            self.terminal.channel.send(FocusWindow(window=request.window))
        return FocusResponse(status=status)

    def set_geometry(self, request: SetGeometryRequest, source: int) -> SetGeometryResponse:
        status = self.known(request.window)
        if status == Status.OK:
            box = request.geometry or Rect()
            self.terminal.channel.send(SetCompositorGeometry(window=request.window, x=box.x, y=box.y,
                                                             width=box.width, height=box.height))
        return SetGeometryResponse(status=status)

    def close(self, request: CloseRequest, source: int) -> CloseResponse:
        status = self.known(request.window)
        if status == Status.OK:
            self.terminal.channel.send(CloseWindow(window=request.window))
        return CloseResponse(status=status)

    def watch(self, request: WatchRequest, source: int) -> WatchResponse:
        if request.listener is None:
            return WatchResponse(status=Status.FAILED)
        self.terminal.watchers.add(request.listener)
        return WatchResponse(status=Status.OK)

    # Items

    def start(self, respond: typing.Callable[[str, int, Status, str], typing.Any], source: str,
              launch: typing.Callable[[Launcher, Opening], None]) -> darq.Deferred:
        """Start opening an item for a request, with its own launcher."""
        opening = Opening(self.defer(timeout=OPEN_TIMEOUT), respond)

        def started(dpid: int):
            opening.dpid = dpid

        host = LaunchHost(self.terminal.services, self.launched)
        launcher = Launcher(typing.cast(typing.Any, host), source or DEFAULT_SOURCE, self.post, run=self.run,
                            started=started)
        launch(launcher, opening)
        return opening.deferred

    def launched(self, opening: Opening, item_id: str | None, error: str | None):
        """A request's launch finished, on Qt's thread: if it started a
        lens, wait for its window, if it isn't shown already."""
        opening.item_id = item_id or opening.item_id
        if error is not None:
            opening.answer(status=Status.FAILED, message=error)
            return
        name = f"{LENS_NAME_PREFIX}{opening.dpid}"
        window = next((w for w, app_id in self.terminal.app_ids.items() if app_id == name), 0)
        if window or not opening.dpid:
            opening.answer(window)
        else:
            self.opening[opening.dpid] = opening

    def window_added(self, window: int, app_id: str):
        """A window's shown: answer the request whose lens it is."""
        if app_id.startswith(LENS_NAME_PREFIX):
            dpid = app_id.removeprefix(LENS_NAME_PREFIX)
            opening = self.opening.pop(int(dpid), None) if dpid.isdigit() else None
            if opening is not None:
                opening.answer(window)

    def open_item(self, request: OpenItemRequest, source: int) -> OpenItemResponse | darq.Deferred:
        if self.terminal.locked:
            return OpenItemResponse(status=Status.LOCKED)

        def respond(item_id: str, window: int, status: Status, message: str) -> OpenItemResponse:
            return OpenItemResponse(window=window, status=status, message=message)

        def launch(launcher: Launcher, opening: Opening):
            opening.item_id = request.item_id
            launcher.use(request.item_id, opening, lens_for=request.lens_for)

        return self.start(respond, request.source, launch)

    def create_item(self, request: CreateItemRequest, source: int) -> CreateItemResponse | darq.Deferred:
        if self.terminal.locked:
            return CreateItemResponse(status=Status.LOCKED)

        def respond(item_id: str, window: int, status: Status, message: str) -> CreateItemResponse:
            return CreateItemResponse(item_id=item_id, window=window, status=status, message=message)

        return self.start(respond, request.source,
                          lambda launcher, opening: launcher.create(request.type_id, request.type_id, opening))

    def import_file(self, request: ImportFileRequest, source: int) -> ImportFileResponse | darq.Deferred:
        if self.terminal.locked:
            return ImportFileResponse(status=Status.LOCKED)

        def respond(item_id: str, window: int, status: Status, message: str) -> ImportFileResponse:
            return ImportFileResponse(item_id=item_id, window=window, status=status, message=message)

        return self.start(respond, request.source,
                          lambda launcher, opening: launcher.create_from_file(request.path, opening))

    def open_url(self, request: OpenUrlRequest, source: int) -> OpenUrlResponse | darq.Deferred:
        if self.terminal.locked:
            return OpenUrlResponse(status=Status.LOCKED)

        def respond(item_id: str, window: int, status: Status, message: str) -> OpenUrlResponse:
            return OpenUrlResponse(item_id=item_id, window=window, status=status, message=message)

        return self.start(respond, request.source, lambda launcher, opening: launcher.fetch(request.url, opening))

    def open_entity(self, request: OpenEntityRequest, source: int) -> OpenEntityResponse | darq.Deferred:
        status = self.known(request.window)
        if status != Status.OK:
            return OpenEntityResponse(status=status)
        name = self.terminal.app_ids.get(request.window, "")
        if not name.startswith(LENS_NAME_PREFIX):
            return OpenEntityResponse(status=Status.NOT_FOUND, message="It isn't a lens's window")

        def respond(item_id: str, window: int, status: Status, message: str) -> OpenEntityResponse:
            return OpenEntityResponse(item_id=item_id, entity_window=window, status=status, message=message)

        def launch(launcher: Launcher, opening: Opening):
            def asked(result: tuple[str, bool] | None, error: Exception | None):
                if error is not None or result is None:
                    opening.answer(status=Status.FAILED, message=f"Can't ask the window what it's showing: {error}")
                    return
                item_id, entity = result
                opening.item_id = item_id
                if not item_id:
                    opening.answer(status=Status.NOT_FOUND, message="The window isn't showing an item")
                elif entity:
                    opening.answer(request.window, message="It's showing an entity already")
                else:
                    launcher.use(item_id, opening, lens_for=darq.items.BASE_TYPE)

            launcher.background(lambda: lens_shows(name), asked)

        return self.start(respond, request.source, launch)

    # Tools

    def list_tools(self, request: ListToolsRequest, source: int) -> ListToolsResponse:
        running = self.terminal.tools.tools.running
        tools = [Tool(name=t.name, description=t.description, running=t.name in running)
                 for t in find_tools(self.terminal.root)]
        return ListToolsResponse(tools=tools, status=Status.OK)

    def run_item(self, request: RunItemRequest, source: int) -> RunItemResponse:
        if self.terminal.locked:
            return RunItemResponse(status=Status.LOCKED)
        try:
            type_id, _ = darq.split_item_id(request.item_id)
        except darq.InvalidIdentifierError:
            return RunItemResponse(status=Status.NOT_FOUND, message=f"{request.item_id} isn't an item")
        if type_id != darq.packages.TYPE_ID:
            return RunItemResponse(status=Status.FAILED, message="Only a Python package can be run")
        if request.item_id in self.terminal.running_items:
            return RunItemResponse(status=Status.RUNNING)
        logging.info(f"{request.source or DEFAULT_SOURCE} runs {request.item_id}")
        why = self.terminal.run_item(request.item_id)
        return RunItemResponse(status=Status.FAILED if why else Status.OK, message=why)

    def run_tool(self, request: RunToolRequest, source: int) -> RunToolResponse:
        if self.terminal.locked:
            return RunToolResponse(status=Status.LOCKED)
        tool = next((t for t in find_tools(self.terminal.root) if t.name == request.name), None)
        if tool is None:
            return RunToolResponse(status=Status.NOT_FOUND)
        tools = self.terminal.tools.tools
        if tool.name in tools.running:
            return RunToolResponse(status=Status.RUNNING)
        logging.info(f"{request.source or DEFAULT_SOURCE} runs {tool.name}")
        tools.run(tool)
        return RunToolResponse(status=Status.OK)
