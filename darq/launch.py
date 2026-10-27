# darqos
# Copyright (C) 2026 David Arnold

# Creating and opening items, and starting lenses to use them, for the
# Terminal (see doc/types.rst).
#
# These are synchronous: starting a lens waits for its Ready, which can
# take a second or so for a Qt lens.

import time

from . import dipc
from .errors import DarqError, LaunchError, MessageDecodingError
from .identifier import split_item_id
from .protocols import lens as lens_protocol
from .protocols import type as type_protocol
from .system import close_port, connect, decode_port_destroyed, open_port, receive_message, spawn_process


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


def create_item(type_id: str) -> tuple[str, int]:
    """Create an item of a type.

    :returns: Its identifier, and a port for it, which the caller must
              close (or pass to a lens, with use_item).
    :raises LaunchError: if it couldn't be created."""
    response = _activate(type_id).create()
    if response.status != type_protocol.Status.OK or response.item is None:
        raise LaunchError(f"Can't create a {type_id} item: {response.status.name}")
    return response.item_id, response.item


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


def find_lens(type_id: str, context: str = "terminal") -> str:
    """Return the lens to use items of a type in a context: the first of
    those the Type service finds.

    :raises LaunchError: if there isn't one."""
    types = type_protocol.Type.Client(connect("darq.type"), timeout=TIMEOUT)
    response = types.find_lenses(type_id=type_id, context=context)
    if response.status != type_protocol.Status.OK or not response.lenses:
        raise LaunchError(f"No {context} lens for {type_id}")
    return response.lenses[0]


def start_lens(path: str, timeout: float = READY_TIMEOUT) -> int:
    """Spawn a lens, and wait for it to report Ready.

    :param path: The lens's directory.
    :returns: A handle for its port.
    :raises LaunchError: if it couldn't be spawned, or wasn't Ready in time."""
    bootstrap = open_port()
    try:
        try:
            spawn_process(path, bootstrap=bootstrap)
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
                return ready.lens
    finally:
        close_port(bootstrap)


def use_item(item_id: str, item: int | None = None, context: str = "terminal") -> int:
    """Start a lens to use an item.

    :param item_id: The item's identifier.
    :param item: A port for it, from create_item or open_item, which the
                 lens takes over; or None to open it.
    :param context: Context to use it in.
    :returns: A handle for the lens's port.
    :raises LaunchError: if the item couldn't be opened, or the lens
                         started, or it can't use the item."""
    type_id, _ = split_item_id(item_id)
    if item is None:
        item = open_item(item_id)
    try:
        path = find_lens(type_id, context)
        lens = start_lens(path)
    except LaunchError:
        _close_item(item)
        raise

    client = lens_protocol.Lens.Client(lens, timeout=TIMEOUT)
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


def _close_item(item: int):
    """Close an item that nothing took over, ignoring a failure."""
    try:
        type_protocol.Item.Client(item, timeout=TIMEOUT).close()
    except (DarqError, TimeoutError):
        pass
