# darqos
# Copyright (C) 2026 David Arnold

# Update DarqOS: a window for updating the system, from within it (see
# doc/distribution.rst).  It's a tool (see tool.json), started from the
# Terminal's Tools panel, Sys-t.
#
# It lists GitHub's releases (releases.py): the newest stable release,
# and the newest pre-release, with what's installed.  Choosing one fetches
# its distribution and signature, as items, with the HTTP service, and
# reads the version it says it is: if it's newer, it can be updated to,
# and if it's the same, reinstalled.  Or a distribution can come from
# another source (darq.ui.sources), with its signature beside it: at its
# URL, with .sig, or beside the local file it was made from.
#
# Once it's confirmed, the distribution and signature are left for
# darqos-update(8), which runs as root: it checks the signature with the
# installed release key, backs up, installs it, and starts DarqOS again,
# and the Terminal says how it went.  Only an installed system can update
# itself: not one run from a source tree.

import logging
import pathlib
import sys
import typing

from PyQt6.QtWidgets import QApplication, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import darq
import darq.host
from darq.loop import QtEventLoop
from darq.protocols.http import Header
from darq.ui import dialogs, sources
from darq.version import Version

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import releases  # noqa: E402


# History's source of what it brings in, and the items it reads: tarballs.
SOURCE = "update"
ACCEPTS = sources.accepting(["application/gzip", "application/x-gzip", "application/x-gtar"], ["tar.gz", "gz"])

# Most bytes of GitHub's list of releases.
MAX_LIST = 4 << 20

# What's said before installing a pre-release.
PRE_RELEASE_WARNING = ("Pre-releases are for trying out: don't install one on a system whose data you can't lose.  "
                       "The backup taken before it is the way back.")


def fetch_releases(http: typing.Any) -> list[releases.Release]:
    """Return GitHub's releases.

    :raises sources.SourceError: if they can't be had."""
    if http is None:
        raise sources.SourceError("The HTTP service isn't running")
    try:
        response = http.request(method="GET", url=releases.RELEASES,
                                headers=[Header(name="Accept", value="application/vnd.github+json")],
                                body=b"", max_size=MAX_LIST, id=0, listener=None)
    except (darq.DarqError, TimeoutError) as e:
        raise sources.SourceError(f"Can't reach GitHub: {e}") from None
    if response.http_status != 200:
        raise sources.SourceError(f"GitHub said {response.http_status}")
    try:
        return releases.parse(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise sources.SourceError(str(e)) from None


def fetch(url: str, http: typing.Any) -> bytes:
    """Return what's at a URL, kept as an item."""
    return sources.read(sources.fetch(url, http))


def from_release(release: releases.Release, http: typing.Any) -> tuple[bytes, bytes]:
    """Return a release's distribution and signature."""
    return fetch(release.dist, http), fetch(release.signature, http)


def from_source(source: sources.Source, http: typing.Any) -> tuple[bytes, bytes]:
    """Return a distribution from another source, and its signature,
    beside it: at its URL, or beside the local file it was made from.

    :raises sources.SourceError: if either can't be had."""
    tarball = sources.read(source)
    if source.url:
        return tarball, fetch(f"{source.url}.sig", http)
    if source.file:
        try:
            return tarball, pathlib.Path(f"{source.file}.sig").read_bytes()
        except OSError:
            raise sources.SourceError(f"There's no signature beside it: {source.file}.sig") from None
    raise sources.SourceError("Its signature can't be found: choose it by its URL, or as a local file")


class UpdateWindow(QWidget):
    """What's installed, what can be updated to, and doing it."""

    def __init__(self, loop: typing.Any, installed: Version, can_update: bool):
        super().__init__()
        self.installed = installed
        self.can_update = can_update
        self.run = sources.runner(loop)
        self.setWindowTitle("Update DarqOS")
        self.resize(720, 320)

        # Clients of the HTTP service, and what writes a request: tests
        # replace them.
        self.http: typing.Callable[[], typing.Any] = sources.SourceChooser.connect_http
        self.request: typing.Callable[[bytes, bytes, str], None] = darq.host.request_update

        layout = QVBoxLayout()
        grid = QGridLayout()
        grid.addWidget(QLabel("Installed:"), 0, 0)
        grid.addWidget(QLabel(installed.version), 0, 1)
        self.channels: dict[bool, tuple[QLabel, QPushButton]] = {}
        for row, (pre_release, title) in enumerate(((False, "Stable:"), (True, "Pre-release:")), 1):
            shown, button = QLabel("Looking ..."), QPushButton("Update", self)
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, p=pre_release: self.choose_channel(p))
            grid.addWidget(QLabel(title), row, 0)
            grid.addWidget(shown, row, 1)
            grid.addWidget(button, row, 2)
            self.channels[pre_release] = (shown, button)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.source = sources.SourceChooser(self, SOURCE, ACCEPTS, self.run, "Distributions (*.tar.gz);;All files (*)")
        row = QHBoxLayout()
        row.addWidget(self.source, 1)
        self.other = QPushButton("Install it", self)
        self.other.clicked.connect(self.choose_other)
        row.addWidget(self.other)
        layout.addLayout(row)

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.setLayout(layout)

        # The releases offered, by whether they're pre-releases.
        self.offered: dict[bool, releases.Release | None] = {False: None, True: None}
        if not can_update:
            self.status.setText("Only an installed system can update itself: this one's run from a source tree.")
            self.other.setEnabled(False)

    def look(self):
        """Find what's been released."""
        self.run(lambda: fetch_releases(self.http()), self.found)

    def found(self, outcome: typing.Any):
        if isinstance(outcome, Exception):
            for shown, _ in self.channels.values():
                shown.setText("Can't say")
            self.status.setText(str(outcome))
            return
        stable, pre_release = releases.channels(outcome)
        for is_pre, release in ((False, stable), (True, pre_release)):
            self.offered[is_pre] = release
            shown, button = self.channels[is_pre]
            if release is None:
                shown.setText("None published")
                button.setEnabled(False)
                continue
            action, said = releases.decide(self.installed, release.version)
            shown.setText(release.version.version if action else said)
            button.setText({releases.UPDATE: "Try it" if is_pre else "Update",
                            releases.REINSTALL: "Reinstall"}.get(action, "Update"))
            button.setEnabled(bool(action) and self.can_update)

    def choose_channel(self, pre_release: bool):
        release = self.offered[pre_release]
        if release is not None:
            self.status.setText(f"Fetching {release.version.version} ...")
            self.run(lambda: from_release(release, self.http()), lambda got: self.check(got, pre_release))

    def choose_other(self):
        source = self.source.source
        if source is None:
            self.status.setText("Choose where the distribution comes from.")
            return
        if source.path:
            self.status.setText("A distribution's a file, not a directory.")
            return
        self.status.setText(f"Reading {source.name} ...")
        self.run(lambda: from_source(source, self.http()), lambda got: self.check(got, False))

    def check(self, got: typing.Any, pre_release: bool):
        """Check what was fetched is something to install, ask, and ask
        darqos-update(8) to install it."""
        if isinstance(got, Exception):
            self.status.setText(str(got))
            return
        tarball, signature = got
        try:
            offered = releases.version_of(tarball)
        except ValueError as e:
            self.status.setText(str(e))
            return
        action, said = releases.decide(self.installed, offered)
        if not action:
            self.status.setText(said)
            return
        question = f"{said}?\n\nDarqOS will back up, install it, and restart: every window will close."
        if pre_release:
            question += f"\n\n{PRE_RELEASE_WARNING}"
        if not dialogs.confirm(self, "Update DarqOS", question):
            self.status.setText("Not installed")
            return

        def requested(outcome: typing.Any):
            if isinstance(outcome, Exception):
                self.status.setText(f"Can't ask for it to be installed: {outcome}")
            else:
                self.status.setText(f"Installing {offered.version}: DarqOS will restart in a moment.")

        self.run(lambda: self.request(tarball, signature, action), requested)


def darq_main():
    app = QApplication([])
    loop = QtEventLoop()
    window = UpdateWindow(loop, darq.get_version(), darq.host.can_update())
    window.show()
    window.look()
    app.exec()
    logging.info("Update DarqOS closed")
    return 0
