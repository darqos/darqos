# darqos
# Copyright (C) 2026 David Arnold

# Creating and opening items, and starting lenses to use them, for the
# Terminal (see doc/types.rst).
#
# These are synchronous: starting a lens waits for its Ready, which can
# take a second or so for a Qt lens.

import importlib
import pathlib
import time
import typing

from . import dipc
from .errors import DarqError, LaunchError, MessageDecodingError
from .identifier import split_item_id
from .protocols import lens as lens_protocol
from .protocols import type as type_protocol
from .system import close_port, connect, decode_port_destroyed, open_port, receive_message, spawn_process


# Data's type, which takes anything: as darq.items.DATA_TYPE.
DATA_TYPE = "public.data"

# Seconds to wait for a service's reply, and for a lens to report Ready.
TIMEOUT = 10.0
READY_TIMEOUT = 30.0


def _activate(type_id: str) -> type_protocol.Implementation.Client:
    """Return a client for a type's implementation, starting it if need be."""
    types = type_protocol.Type.Client(connect("darq.type"), timeout=TIMEOUT)
    response = types.activate(type_id=type_id)
    if response.status != type_protocol.Status.OK or response.implementation is None:
        raise LaunchError(f"Can't activate {type_id}: {response.status.name}")
    return type_protocol.Implementation.Client(response.implementation, timeout=TIMEOUT)


def create_item(type_id: str, context: type_protocol.Context | None = None) -> tuple[str, int]:
    """Create an item of a type: recorded in History as its context says
    (see doc/history.rst).

    :returns: Its identifier, and a port for it, which the caller must
              close (or pass to a lens, with use_item).
    :raises LaunchError: if it couldn't be created."""
    response = _activate(type_id).create(type_id=type_id, context=context)
    if response.status != type_protocol.Status.OK or response.item is None:
        raise LaunchError(f"Can't create a {type_id} item: {response.status.name}")
    return response.item_id, response.item


def create_item_from(type_id: str, data: bytes, name: str = "", media_type: str = "",
                     context: type_protocol.Context | None = None) -> tuple[str, int]:
    """Create an item of a type from data in the type's format, such as a
    PNG file's bytes.

    :param name: The name of the file it came from, if it did.
    :param media_type: The media type it came with, if it did.

    :returns: Its identifier, and a port for it, as create_item does.
    :raises LaunchError: if it couldn't be created: if the data isn't of
                         the type, nothing is."""
    response = _activate(type_id).create_from(type_id=type_id, data=data, name=name, media_type=media_type,
                                              context=context)
    if response.status != type_protocol.Status.OK or response.item is None:
        raise LaunchError(f"Can't create a {type_id} item from data: {response.status.name}")
    return response.item_id, response.item


def import_data(media_type: str, extension: str, data: bytes, name: str = "",
                context: type_protocol.Context | None = None) -> str | None:
    """Create an item from data that's come from somewhere else, of the
    first type for its media type, or else its extension, that takes it,
    or else as data, public.data, which takes anything (see
    doc/data-type.rst).

    :param extension: The extension of its name, without its dot.
    :param name: The name of the file it came from, if it did.
    :returns: The item's identifier, or None if it can't be made, as
              data, too, isn't implemented."""
    types = find_types(mime_type=media_type) if media_type else []
    if extension:
        types += find_types(extension=extension)
    for type_id in [*dict.fromkeys(types), DATA_TYPE]:
        try:
            item_id, port = create_item_from(type_id, data, name, media_type, context)
        except LaunchError:
            continue
        try:
            type_protocol.Item.Client(port, timeout=TIMEOUT).close()
        finally:
            close_port(port)
        return item_id
    return None


def export_item(item_id: str) -> tuple[bytes, str]:
    """Return an item's content as data in its type's format, and its
    MIME type.

    :raises LaunchError: if it couldn't be exported."""
    type_id, _ = split_item_id(item_id)
    response = _activate(type_id).export(item_id=item_id)
    if response.status != type_protocol.Status.OK:
        raise LaunchError(f"Can't export {item_id}: {response.status.name}")
    return response.data, response.mime_type


def find_types(mime_type: str = "", extension: str = "") -> list[str]:
    """Return the implemented types for data with a MIME type, or a file
    name extension, best first (see the Type protocol's FindTypes)."""
    types = type_protocol.Type.Client(connect("darq.type"), timeout=TIMEOUT)
    response = types.find_types(mime_type=mime_type, extension=extension)
    if response.status != type_protocol.Status.OK:
        raise LaunchError(f"Can't find types: {response.status.name}")
    return response.type_ids


def open_item(item_id: str) -> int:
    """Open an item.

    :returns: A port for it, which the caller must close (or pass to a
              lens, with use_item).
    :raises LaunchError: if it couldn't be opened."""
    type_id, _ = split_item_id(item_id)
    response = _activate(type_id).open(item_id=item_id)
    if response.status != type_protocol.Status.OK or response.item is None:
        raise LaunchError(f"Can't open {item_id}: {response.status.name}")
    return response.item


def open_entity(item_id: str) -> int:
    """Open an item's entity, through the item's type implementation, as
    every item has an entity (see doc/item.rst).

    :returns: A port for it, serving the EntityItem protocol, which the
              caller must close (or pass to a lens, with use_item).
    :raises LaunchError: if it couldn't be opened."""
    type_id, _ = split_item_id(item_id)
    response = _activate(type_id).open_entity(item_id=item_id)
    if response.status != type_protocol.Status.OK or response.item is None:
        raise LaunchError(f"Can't open {item_id}'s entity: {response.status.name}")
    return response.item


def find_lens(type_id: str, context: str = "terminal") -> str:
    """Return the lens to use items of a type in a context: the first of
    those the Type service finds.

    :raises LaunchError: if there isn't one."""
    types = type_protocol.Type.Client(connect("darq.type"), timeout=TIMEOUT)
    response = types.find_lenses(type_id=type_id, context=context)
    if response.status != type_protocol.Status.OK or not response.lenses:
        raise LaunchError(f"No {context} lens for {type_id}")
    return response.lenses[0]


class StartedLens(typing.NamedTuple):
    """A lens that's been started: a handle for its Lens port, and its
    process's DPID, which names its windows (see darq.ui.lens.announce)."""

    port: int
    dpid: int


def start_lens(path: str, timeout: float = READY_TIMEOUT) -> StartedLens:
    """Spawn a lens, and wait for it to report Ready.

    :param path: The lens's directory.
    :returns: It.
    :raises LaunchError: if it couldn't be spawned, or wasn't Ready in time."""
    bootstrap = open_port()
    try:
        try:
            dpid = spawn_process(path, bootstrap=bootstrap)
        except DarqError as e:
            raise LaunchError(f"Can't start lens {path}: {e}") from e

        deadline = time.monotonic() + timeout
        while True:
            message = receive_message(bootstrap, timeout=max(0.0, deadline - time.monotonic()))
            if message is None:
                raise LaunchError(f"Lens {path} wasn't Ready within {timeout}s")
            source, payload, handles = message
            if decode_port_destroyed(source, payload) is not None:
                continue
            ready = lens_protocol.Ready()
            try:
                if dipc.decode_method(payload) != ready.METHOD:
                    continue
                ready.decode(payload, handles)
            except MessageDecodingError:
                continue
            if ready.lens is not None:
                return StartedLens(ready.lens, dpid)
    finally:
        close_port(bootstrap)


# Each type implementation's item protocol, by its directory's name: the
# protocol named for it, in darq.protocols' module of that name, as
# darq.protocols.text.Text, but for the Knowledge Base's, whose items
# are entities.
ITEM_PROTOCOLS = {"kb": "EntityItem"}


def item_protocol(implementation: str) -> typing.Any:
    """Return the item protocol of a type implementation, by its path.

    :raises LaunchError: if it has none."""
    name = pathlib.PurePath(implementation).name
    try:
        module = importlib.import_module(f"darq.protocols.{name}")
    except ImportError:
        raise LaunchError(f"{name} has no item protocol") from None
    protocol = getattr(module, ITEM_PROTOCOLS.get(name, name.capitalize()), None)
    if protocol is None or not hasattr(protocol, "Client"):
        raise LaunchError(f"{name} has no item protocol")
    return protocol


def open_client(item_id: str, timeout: float | None = TIMEOUT) -> typing.Any:
    """Open an item, and return a client of its type's item protocol: as
    darq.protocols.text.Text.Client, for a text.  It's darq.open_client.

        item = darq.open_client("public.utf8-plain-text:6f11...")
        item.get().text
        item.close()

    Its close(), the protocol's Close, closes the item when the caller's
    finished with it, as for open_item's port.

    :param timeout: Seconds the client waits for each reply.
    :raises LaunchError: if its type isn't implemented, or has no item
                         protocol, or the item can't be opened."""
    type_id, _ = split_item_id(item_id)
    types = type_protocol.Type.Client(connect("darq.type"), timeout=TIMEOUT)
    described = types.describe(type_id=type_id)
    if described.status != type_protocol.Status.OK or not described.implementation:
        raise LaunchError(f"{type_id} isn't implemented")
    protocol = item_protocol(described.implementation)
    return protocol.Client(open_item(item_id), timeout=timeout)

def use_item(item_id: str, item: int | None = None, context: str = "terminal", lens_for: str = "") -> StartedLens:
    """Start a lens to use an item.

    :param item_id: The item's identifier.
    :param item: A port for it, from create_item or open_item, which the
                 lens takes over; or None to open it.
    :param context: Context to use it in.
    :param lens_for: A type to use the lens of, instead of the item's own.
    :returns: The lens.
    :raises LaunchError: if the item couldn't be opened, or the lens
                         started, or it can't use the item."""
    type_id, _ = split_item_id(item_id)
    if item is None:
        item = open_item(item_id)
    try:
        path = find_lens(lens_for or type_id, context)
        lens = start_lens(path)
    except LaunchError:
        _close_item(item)
        raise

    client = lens_protocol.Lens.Client(lens.port, timeout=TIMEOUT)
    try:
        status = client.use(item_id=item_id, item=item).status
    except (DarqError, TimeoutError) as e:
        status = e
    if status != lens_protocol.Status.OK:
        # It didn't take over the item, and has nothing to do.
        _close_item(item)
        try:
            client.close()
        except DarqError:
            pass
        reason = status.name if isinstance(status, lens_protocol.Status) else repr(status)
        raise LaunchError(f"Lens {path} can't use {item_id}: {reason}")
    return lens


# What a program kept as an item is: a Python package (see
# darq.packages).
PACKAGE_TYPE = "darq.python"


def run_item(item_id: str) -> int:
    """Run a Python package's item, as a program: spawn a process, which
    imports its main module, and calls its darq_main(), as a directory's
    would be.

    :returns: Its DPID.
    :raises LaunchError: if it isn't a package, or can't be spawned."""
    type_id, _ = split_item_id(item_id)
    if type_id != PACKAGE_TYPE:
        raise LaunchError(f"{item_id} isn't a Python package: only a package can be run")
    try:
        return spawn_process(item_id)
    except DarqError as e:
        raise LaunchError(f"Can't run {item_id}: {e}") from e


def destroy_item(item_id: str, context: type_protocol.Context | None = None):
    """Destroy an item.

    :raises LaunchError: if it couldn't be."""
    type_id, _ = split_item_id(item_id)
    response = _activate(type_id).destroy(item_id=item_id, context=context)
    if response.status != type_protocol.Status.OK:
        raise LaunchError(f"Can't destroy {item_id}: {response.status.name}")


def _close_item(item: int):
    """Close an item that nothing took over, ignoring a failure."""
    try:
        type_protocol.Item.Client(item, timeout=TIMEOUT).close()
    except (DarqError, TimeoutError):
        pass
