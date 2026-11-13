# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Update DarqOS tool, tools/update: GitHub's releases, the
# two channels, what can be done with a version, and its window,
# offscreen, with what fetches and requests standing in.

import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import types

import pytest

from darq.ui import sources
from darq.version import Version

TOOL_PATH = pathlib.Path(__file__).parent.parent / "tools" / "update"


def load():
    sys.path.insert(0, str(TOOL_PATH))
    try:
        found = importlib.import_module("releases")
        spec = importlib.util.spec_from_file_location("update_tool", TOOL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
    finally:
        sys.path.remove(str(TOOL_PATH))
    return found, tool


releases, tool = load()

INSTALLED = Version("2.8", 2, 8)


def dist(version: str) -> bytes:
    edition, build = version.split(".")
    text = f"version={version}\nedition={edition}\nbuild={build}\n".encode()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("darq/VERSION")
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text))
    return data.getvalue()


def published(tag, pre_release=False, assets=True, draft=False):
    base = f"https://github.com/darqos/darqos/releases/download/{tag}"
    return {"tag_name": tag, "prerelease": pre_release, "draft": draft, "body": f"Notes for {tag}",
            "assets": [{"name": n, "browser_download_url": f"{base}/{n}"}
                       for n in ("darqos.tar.gz", "darqos.tar.gz.sig")] if assets else []}


LISTED = json.dumps([published("v3.0", pre_release=True), published("v2.10"), published("v2.9"),
                     published("v2.11", pre_release=True, draft=True), published("v2.4", pre_release=True,
                                                                                  assets=False),
                     published("edition-1")])


################################################################
# Releases
################################################################

def test_parsing_githubs_list():
    found = releases.parse(LISTED)
    assert [(r.version.version, r.pre_release) for r in found] == [("3.0", True), ("2.10", False), ("2.9", False)]
    assert found[1].dist.endswith("/v2.10/darqos.tar.gz") and found[1].signature.endswith("/v2.10/darqos.tar.gz.sig")
    assert found[1].notes == "Notes for v2.10"
    with pytest.raises(ValueError, match="isn't JSON"):
        releases.parse("<html>")
    with pytest.raises(ValueError, match="isn't a list"):
        releases.parse('{"message": "rate limited"}')


def test_the_two_channels():
    stable, pre_release = releases.channels(releases.parse(LISTED))
    assert stable is not None and stable.version.version == "2.10"
    assert pre_release is not None and pre_release.version.version == "3.0"
    assert releases.channels([]) == (None, None)


@pytest.mark.parametrize("offered, action", [
    (Version("2.9", 2, 9), "update"), (Version("3.0", 3, 0), "update"), (Version("2.8", 2, 8), "reinstall"),
    (Version("2.7", 2, 7), ""), (Version("1.40", 1, 40), "")])
def test_deciding(offered, action):
    assert releases.decide(INSTALLED, offered)[0] == action
    if not action:
        assert "go back by hand, with dui" in releases.decide(INSTALLED, offered)[1]


def test_a_distributions_version():
    assert releases.version_of(dist("2.9")) == Version("2.9", 2, 9)
    with pytest.raises(ValueError, match="isn't a DarqOS distribution"):
        releases.version_of(b"not a tarball")


################################################################
# Fetching
################################################################

class Http:
    def __init__(self, http_status=200, body=LISTED.encode()):
        self.response = types.SimpleNamespace(http_status=http_status, body=body)
        self.asked: list[str] = []

    def request(self, method, url, headers, body, max_size, id, listener):
        self.asked.append(url)
        return self.response


def test_fetching_the_releases():
    http = Http()
    assert [r.version.version for r in tool.fetch_releases(http)] == ["3.0", "2.10", "2.9"]
    assert http.asked == [releases.RELEASES]
    with pytest.raises(sources.SourceError, match="GitHub said 403"):
        tool.fetch_releases(Http(http_status=403))
    with pytest.raises(sources.SourceError, match="isn't running"):
        tool.fetch_releases(None)


def test_another_sources_signature_is_beside_it(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "read", lambda source: b"tarball:" + source.item_id.encode())
    fetched = []
    monkeypatch.setattr(sources, "fetch",
                        lambda url, http: fetched.append(url) or sources.Source(item_id="public.data:sig"))

    by_url = sources.Source(item_id="public.data:d", url="https://example.com/darqos.tar.gz")
    assert tool.from_source(by_url, None) == (b"tarball:public.data:d", b"tarball:public.data:sig")
    assert fetched == ["https://example.com/darqos.tar.gz.sig"]

    (tmp_path / "d.tar.gz.sig").write_bytes(b"signature")
    by_file = sources.Source(item_id="public.data:d", file=str(tmp_path / "d.tar.gz"))
    assert tool.from_source(by_file, None) == (b"tarball:public.data:d", b"signature")

    with pytest.raises(sources.SourceError, match="no signature beside it"):
        tool.from_source(sources.Source(item_id="public.data:d", file=str(tmp_path / "x.tar.gz")), None)
    with pytest.raises(sources.SourceError, match="can't be found"):
        tool.from_source(sources.Source(item_id="public.data:d"), None)


################################################################
# The window, offscreen
################################################################

class Loop:
    def post(self, function):
        function()


def inline(job, done):
    try:
        outcome = job()
    except Exception as e:
        outcome = e
    done(outcome)


@pytest.fixture
def window(qt_app, monkeypatch):
    made = tool.UpdateWindow(Loop(), INSTALLED, True)
    made.run = inline
    made.http = lambda: Http()
    made.requested = []
    made.request = lambda tarball, signature, action: made.requested.append((releases.version_of(tarball).version,
                                                                              signature, action))
    yield made
    made.close()


def test_the_window_shows_the_channels(window):
    window.look()
    (stable, stable_button), (pre, pre_button) = window.channels[False], window.channels[True]
    assert (stable.text(), stable_button.text(), stable_button.isEnabled()) == ("2.10", "Update", True)
    assert (pre.text(), pre_button.text(), pre_button.isEnabled()) == ("3.0", "Try it", True)


def test_updating_asks_and_requests(window, monkeypatch):
    asked = []
    monkeypatch.setattr(tool.dialogs, "confirm", lambda parent, title, question: asked.append(question) or True)
    monkeypatch.setattr(tool, "from_release", lambda release, http: (dist(release.version.version), b"sig"))
    window.look()
    window.choose_channel(True)
    assert window.requested == [("3.0", b"sig", "update")]
    assert tool.PRE_RELEASE_WARNING in asked[0] and "every window will close" in asked[0]
    assert window.status.text() == "Installing 3.0: DarqOS will restart in a moment."

    window.choose_channel(False)
    assert tool.PRE_RELEASE_WARNING not in asked[1]


def test_whats_older_or_declined_isnt_requested(window, monkeypatch):
    monkeypatch.setattr(tool.dialogs, "confirm", lambda parent, title, question: False)
    window.check((dist("2.7"), b"sig"), False)
    assert "2.7 is older than 2.8" in window.status.text()
    window.check((dist("2.8"), b"sig"), False)
    assert window.status.text() == "Not installed" and window.requested == []
    window.check((b"junk", b"sig"), False)
    assert "isn't a DarqOS distribution" in window.status.text()


def test_from_a_source_tree_it_cant(qt_app):
    made = tool.UpdateWindow(Loop(), INSTALLED, False)
    made.run = inline
    made.http = lambda: Http()
    made.look()
    assert "Only an installed system" in made.status.text() and not made.other.isEnabled()
    assert not made.channels[False][1].isEnabled()
    made.close()
