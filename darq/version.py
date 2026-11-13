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
__all__ = ["Version", "parse"]


class Version(typing.NamedTuple):
    """A version: as text, such as "2.17", and its edition and build, to
    compare.  Builds are counted within an edition, from 0, so a later
    one has a higher edition, or the same edition and a higher build:
    3.0 comes after 2.125."""

    version: str
    edition: int
    build: int

    def order(self) -> tuple[int, int]:
        """Return what it's ordered by: its edition, and then its build."""
        return self.edition, self.build


# What's known if neither file can be read.
UNKNOWN = Version("unknown", 0, 0)


def read_fields(path: pathlib.Path) -> dict[str, str]:
    """Return a file's name=value lines."""
    lines = (line.partition("=") for line in path.read_text(encoding="utf-8").splitlines())
    return {name.strip(): value.strip() for name, _, value in lines}


def parse(text: str) -> Version:
    """Return the version in a VERSION file's text.

    :raises ValueError: if it hasn't one."""
    lines = (line.partition("=") for line in text.splitlines())
    fields = {name.strip(): value.strip() for name, _, value in lines}
    try:
        return Version(fields["version"], int(fields["edition"]), int(fields["build"]))
    except KeyError as e:
        raise ValueError(f"no {e.args[0]}") from None


def find(root: str | pathlib.Path) -> Version:
    """Return the version of the system at a directory."""
    root = pathlib.Path(root)
    try:
        if (root / "VERSION").is_file():
            return parse((root / "VERSION").read_text(encoding="utf-8"))
        fields = read_fields(root / "RELEASE")
        edition, build = int(fields["edition"]), int(fields["build"])
        return Version(f"{edition}.{build}-dev", edition, build)
    except (OSError, KeyError, ValueError):
        return UNKNOWN
