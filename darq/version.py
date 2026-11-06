# darqos
# Copyright (C) 2026 David Arnold

"""What version of DarqOS this is (see doc/distribution.rst).

An installed system has a VERSION file, made by mkdist.  A source tree
has a RELEASE file, with the edition and the last build, and is shown as
being developed since it.  The p-kernel reads one when it starts, and
darq.get_version() asks the p-kernel."""

import pathlib
import typing


# Control exports
__all__ = ["Version"]


class Version(typing.NamedTuple):
    """A version: as text, such as "2.17", and its edition and build, to
    compare.  Builds are counted across editions, so a later one has a
    higher build, whatever its edition."""

    version: str
    edition: int
    build: int


# What's known if neither file can be read.
UNKNOWN = Version("unknown", 0, 0)


def read_fields(path: pathlib.Path) -> dict[str, str]:
    """Return a file's name=value lines."""
    lines = (line.partition("=") for line in path.read_text(encoding="utf-8").splitlines())
    return {name.strip(): value.strip() for name, _, value in lines}


def find(root: str | pathlib.Path) -> Version:
    """Return the version of the system at a directory."""
    root = pathlib.Path(root)
    try:
        if (root / "VERSION").is_file():
            fields = read_fields(root / "VERSION")
            return Version(fields["version"], int(fields["edition"]), int(fields["build"]))
        fields = read_fields(root / "RELEASE")
        edition, build = int(fields["edition"]), int(fields["build"])
        return Version(f"{edition}.{build}-dev", edition, build)
    except (OSError, KeyError, ValueError):
        return UNKNOWN
