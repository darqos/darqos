# darqos
# Copyright (C) 2026 David Arnold

# Type catalogue sync: run at boot, after the Type service, to make sure
# every installed type and lens is in its catalogue.
#
# It registers, through the Type protocol:
# - the base catalogue, catalogue.json beside this file: descriptions
#   of the common types, with no implementations;
# - each installed type implementation's types, from the type.json
#   manifest in its directory, under types/;
# - each installed lens, from the lens.json manifest in its directory,
#   under lenses/.
#
# Registering replaces a type's description and implementation, so a
# changed manifest takes effect.  Nothing is removed: a type or lens
# that's no longer installed stays in the catalogue until it's
# deregistered.
#
# Manifest formats:
#
#   type.json: {"types": [{"id": "public.text", "name": "Text",
#                          "description": "...", "conforms_to": [...],
#                          "mime_types": [...], "extensions": [...],
#                          "icon": "icon.png", "implemented": true}]}
#
#   lens.json: {"types": ["public.text"], "context": "terminal"}
#
# Only "id" is required for a type.  The directory is the implementation
# of the types marked "implemented" (and the lens, for a lens.json), and
# relative icon paths are taken from it.  The base catalogue has the
# same format as type.json.

import json
import logging
import os
import pathlib
import typing

import darq
from darq.protocols.type import Description, Status, Type


# Root of the installation: /darq, or a development tree.
DEFAULT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

BASE_CATALOGUE = pathlib.Path(__file__).resolve().parent / "catalogue.json"


def manifests(root: pathlib.Path, kind: str, name: str) -> typing.Iterator[tuple[pathlib.Path, dict]]:
    """Yield each installed manifest of a kind, as (its directory, its contents).

    A manifest that can't be read is logged and skipped."""
    for path in sorted((root / kind).glob(f"*/{name}")):
        try:
            yield path.parent, json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as e:
            logging.error(f"Skipped {path}: {e}")


def description(entry: dict, directory: pathlib.Path | None) -> Description:
    """Return the Description for a type manifest's entry."""
    icon = entry.get("icon", "")
    if icon and directory is not None:
        icon = str(directory / icon)
    return Description(name=entry.get("name", ""), description=entry.get("description", ""),
                       conforms_to=list(entry.get("conforms_to", [])),
                       mime_types=list(entry.get("mime_types", [])),
                       extensions=list(entry.get("extensions", [])),
                       icon=icon)


def sync(client: Type.Client, root: pathlib.Path, base: pathlib.Path = BASE_CATALOGUE) -> int:
    """Register the base catalogue, and every installed type and lens.

    :returns: The number that failed to register."""
    failed = 0

    def register_types(data: dict, directory: pathlib.Path | None, origin: str):
        nonlocal failed
        for entry in data.get("types", []):
            implementation = str(directory) if directory is not None and entry.get("implemented") else ""
            status = client.register(type_id=entry["id"], description=description(entry, directory),
                                     implementation=implementation).status
            if status != Status.OK:
                logging.error(f"Can't register {entry['id']}, from {origin}: {status.name}")
                failed += 1

    try:
        register_types(json.loads(base.read_text("utf-8")), None, str(base))
    except (OSError, ValueError) as e:
        logging.error(f"Skipped base catalogue {base}: {e}")
        failed += 1

    for directory, data in manifests(root, "types", "type.json"):
        register_types(data, directory, str(directory))

    for directory, data in manifests(root, "lenses", "lens.json"):
        context = data.get("context", "terminal")
        for type_id in data.get("types", []):
            status = client.register_lens(lens=str(directory), type_id=type_id, context=context).status
            if status != Status.OK:
                logging.error(f"Can't register lens {directory} for {type_id}: {status.name}")
                failed += 1

    logging.info(f"Type catalogue synced from {root}" + (f", with {failed} failures" if failed else ""))
    return failed


def darq_main():
    root = pathlib.Path(os.environ.get("DARQ_ROOT", DEFAULT_ROOT))
    client = Type.Client(darq.connect("darq.type"), timeout=30)
    return 1 if sync(client, root) else 0
