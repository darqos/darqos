# darqos
# Copyright (C) 2026 David Arnold

"""What DarqOS asks of the host Linux, for want of an API of its own:
the machine's name and network addresses, which the lock screen shows,
so the machine can be reached (see doc/terminal.rst); and updating
DarqOS, by asking darqos-update(8), which runs as root, through files in
the run directory (see doc/distribution.rst).

Everything here is the host's, and so to be replaced, as DarqOS gains
its own: keeping it in one place is how it's found."""

import json
import logging
import os
import pathlib
import socket
import subprocess
import typing


# Control exports
__all__ = ["addresses", "can_update", "describe", "name", "parse_addresses", "request_update", "update_result"]

# Seconds to wait for ip(8).
TIMEOUT = 2.0

# What's said when there's no address.
NO_NETWORK = "No network"

# Where the run directory is, if the p-kernel doesn't say.
DEFAULT_RUN_DIR = "/darq/run"

# What's there on a system that can update itself: the unit that starts
# darqos-update(8) when an update's requested.
UPDATER = pathlib.Path("/etc/systemd/system/darqos-update.path")


def name() -> str:
    """Return the machine's host name, as far as the host knows it."""
    return socket.gethostname()


def parse_addresses(output: str) -> list[str]:
    """Return the global addresses in ip(8)'s JSON: every IPv4 one, in
    order, or, if there are none, the IPv6 ones."""
    try:
        interfaces = json.loads(output) if output.strip() else []
    except ValueError:
        return []
    found: dict[str, list[str]] = {"inet": [], "inet6": []}
    for interface in interfaces if isinstance(interfaces, list) else []:
        for info in interface.get("addr_info", []) if isinstance(interface, dict) else []:
            family, address = info.get("family"), info.get("local")
            if family in found and info.get("scope") == "global" and address and address not in found[family]:
                found[family].append(address)
    return found["inet"] or found["inet6"]


def addresses(run: typing.Callable[..., typing.Any] = subprocess.run) -> list[str]:
    """Return the machine's global network addresses, from ip(8): IPv4, or
    else IPv6.  None, if there are none, or it can't say."""
    try:
        done = run(["ip", "-j", "addr", "show", "scope", "global"], capture_output=True, text=True,
                   timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        logging.warning(f"Can't ask ip for the addresses: {e!r}")
        return []
    if done.returncode != 0:
        logging.warning(f"ip failed, {done.returncode}: {done.stderr.strip()}")
        return []
    return parse_addresses(done.stdout)


def update_dir() -> pathlib.Path:
    """Return the directory updates are requested in: the run
    directory's update directory."""
    return pathlib.Path(os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)) / "update"


def can_update(updater: pathlib.Path = UPDATER) -> bool:
    """Return whether this system can update itself: it's installed, with
    darqos-update(8), rather than run from a source tree."""
    return updater.exists()


def request_update(tarball: bytes, signature: bytes, action: str, directory: pathlib.Path | None = None):
    """Ask darqos-update(8) to install a distribution, with its signature:
    action is "update", or "reinstall", for the same build.  The request's
    written last, whole, as darqos-update(8) starts when it's there.

    :raises OSError: if it can't be written."""
    directory = directory or update_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for file, data in (("darqos.tar.gz", tarball), ("darqos.tar.gz.sig", signature)):
        (directory / f"{file}.new").write_bytes(data)
        (directory / f"{file}.new").rename(directory / file)
    (directory / "request.new").write_text(f"action={action}\n")
    (directory / "request.new").rename(directory / "request")


def update_result(directory: pathlib.Path | None = None) -> dict[str, str] | None:
    """Return what the last update came to, as darqos-update(8) says, once:
    it's kept, as result.shown, but not returned again.  None, if there's
    nothing new."""
    directory = directory or update_dir()
    try:
        text = (directory / "result").read_text()
        (directory / "result").rename(directory / "result.shown")
    except OSError:
        return None
    lines = (line.partition("=") for line in text.splitlines())
    return {name: value for name, _, value in lines}


def describe(host: str, found: list[str]) -> str:
    """Return a machine's name and addresses, as the lock screen shows
    them: "darqos · 192.168.86.172"."""
    return "  ·  ".join([part for part in (host, *(found or [NO_NETWORK])) if part])
