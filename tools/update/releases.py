# darqos
# Copyright (C) 2026 David Arnold

"""DarqOS's releases, as GitHub publishes them (see
doc/distribution.rst): each build that's published is tagged with its
version, v<edition>.<build>, such as v2.9, and is a pre-release, until
it's declared stable, when it's GitHub's Latest.  Each has its
distribution, as darqos.tar.gz, with its signature, darqos.tar.gz.sig,
and the release notes of what it brought.

So there are two channels: stable, the newest stable release, and
pre-release, the newest pre-release, which can be of a newer edition.

It uses no Qt, and knows nothing of how they're fetched."""

import dataclasses
import io
import json
import re
import tarfile

import darq.version
from darq.version import Version


# Control exports
__all__ = ["RELEASES", "Release", "channels", "decide", "parse", "version_of"]

# GitHub's list of the releases, newest first.
RELEASES = "https://api.github.com/repos/darqos/darqos/releases"

# A release's tag: its version.
TAG = re.compile(r"v(\d+)\.(\d+)")

# What a release's distribution, and its signature, are called.
DIST = "darqos.tar.gz"
SIGNATURE = "darqos.tar.gz.sig"

# Actions, for darqos-update(8).
UPDATE = "update"
REINSTALL = "reinstall"


@dataclasses.dataclass(frozen=True)
class Release:
    """A published release: its version, whether it's a pre-release, where
    its distribution and signature are, and its notes."""

    version: Version
    pre_release: bool
    dist: str
    signature: str
    notes: str = ""


def parse(text: str) -> list[Release]:
    """Return the releases in GitHub's list: those tagged with a version,
    with a distribution and its signature.

    :raises ValueError: if it isn't GitHub's list."""
    try:
        listed = json.loads(text)
    except ValueError as e:
        raise ValueError(f"GitHub's list of releases isn't JSON: {e}") from None
    if not isinstance(listed, list):
        raise ValueError("GitHub's list of releases isn't a list")
    found = []
    for release in listed:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = TAG.fullmatch(str(release.get("tag_name", "")))
        assets = {a.get("name"): a.get("browser_download_url", "") for a in release.get("assets") or []
                  if isinstance(a, dict)}
        if tag is None or not assets.get(DIST) or not assets.get(SIGNATURE):
            continue
        edition, build = int(tag.group(1)), int(tag.group(2))
        found.append(Release(Version(f"{edition}.{build}", edition, build), bool(release.get("prerelease")),
                             assets[DIST], assets[SIGNATURE], str(release.get("body") or "")))
    return found


def channels(releases: list[Release]) -> tuple[Release | None, Release | None]:
    """Return the newest stable release, and the newest pre-release."""
    def newest(pre_release: bool) -> Release | None:
        matching = [r for r in releases if r.pre_release == pre_release]
        return max(matching, key=lambda r: r.version.order(), default=None)

    return newest(False), newest(True)


def version_of(tarball: bytes) -> Version:
    """Return the version a distribution says it is.

    :raises ValueError: if it isn't a distribution."""
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            member = tar.extractfile("darq/VERSION")
            if member is None:
                raise KeyError("darq/VERSION")
            return darq.version.parse(member.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, OSError, UnicodeDecodeError) as e:
        raise ValueError(f"It isn't a DarqOS distribution: {e}") from None


def decide(installed: Version, offered: Version) -> tuple[str, str]:
    """Return what can be done with a version that's offered, and what's
    said of it: update, if it's newer; reinstall, if it's the same; or
    nothing, if it's older, as going back's done by hand."""
    if offered.order() > installed.order():
        return UPDATE, f"Update to {offered.version}"
    if offered.order() == installed.order():
        return REINSTALL, f"Reinstall {offered.version}"
    return "", f"{offered.version} is older than {installed.version}: go back by hand, with dui"
