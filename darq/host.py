# darqos
# Copyright (C) 2026 David Arnold

"""What DarqOS asks of the host Linux, for want of an API of its own:
for now, the machine's name and network addresses, which the lock screen
shows, so the machine can be reached (see doc/terminal.rst).

Everything here is the host's, and so to be replaced, as DarqOS gains
its own: keeping it in one place is how it's found."""

import json
import logging
import socket
import subprocess
import typing


# Control exports
__all__ = ["addresses", "describe", "name", "parse_addresses"]

# Seconds to wait for ip(8).
TIMEOUT = 2.0

# What's said when there's no address.
NO_NETWORK = "No network"


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


def describe(host: str, found: list[str]) -> str:
    """Return a machine's name and addresses, as the lock screen shows
    them: "darqos · 192.168.86.172"."""
    return "  ·  ".join([part for part in (host, *(found or [NO_NETWORK])) if part])
