# darqos
# Copyright (C) 2026 David Arnold

# Tests for darqos-update(8): installing an update requested from within
# DarqOS, as root would, with its paths in a temporary directory, and
# commands standing in for the backup and dui(8).

import io
import pathlib
import subprocess
import tarfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
UPDATE = ROOT / "bin" / "darqos-update"


def run(*command, **options):
    return subprocess.run([str(c) for c in command], capture_output=True, text=True, **options)


@pytest.fixture
def key(tmp_path):
    """A release key, made for the test, and the allowed signers that name
    it, as install(8) writes them."""
    made = tmp_path / "release_key"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", made, check=True)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "allowed_signers").write_text(
        f'darqos-release namespaces="darqos-release" {(tmp_path / "release_key.pub").read_text()}')
    return made


def dist(version: str) -> bytes:
    """A distribution: a tarball with a VERSION file, as mkdist makes."""
    edition, build = version.split(".")
    text = f"version={version}\nedition={edition}\nbuild={build}\ncommit=abc\ndate=2026-11-13\n".encode()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        info = tarfile.TarInfo("darq/VERSION")
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text))
    return data.getvalue()


class System:
    """An installed system, its requests, and what the update did."""

    def __init__(self, root: pathlib.Path, key: pathlib.Path):
        self.root = root
        self.key = key
        self.darq = root / "darq"
        self.requests = self.darq / "run" / "update"
        self.requests.mkdir(parents=True)
        (self.darq / "VERSION").write_text("version=2.8\nedition=2\nbuild=8\n")
        self.installed = root / "installed"
        self.dui = root / "dui"
        self.dui.write_text(f'#! /bin/sh\necho "$@" >> {self.installed}\n')
        self.dui.chmod(0o755)

    def request(self, version: str, action: str = "update", signed_with: pathlib.Path | None = None):
        tarball = self.requests / "darqos.tar.gz"
        tarball.write_bytes(dist(version))
        signer = signed_with or self.key
        run("ssh-keygen", "-q", "-Y", "sign", "-f", signer, "-n", "darqos-release", tarball, check=True)
        (self.requests / "request").write_text(f"action={action}\n")

    def update(self, backup: str = "true") -> subprocess.CompletedProcess:
        return run("bash", UPDATE, env={"PATH": "/usr/bin:/bin", "DARQ": str(self.darq),
                                         "CONF_DIR": str(self.key.parent / "etc"),
                                         "LOG_DIR": str(self.root / "log"),
                                         "DARQOS_UPDATE_STAGING": str(self.root / "staging"),
                                         "DARQOS_BACKUP": backup, "DARQOS_DUI": str(self.dui)})

    def result(self) -> dict[str, str]:
        lines = (line.partition("=") for line in (self.requests / "result").read_text().splitlines())
        return {name: value for name, _, value in lines}

    def ran_dui(self) -> list[str]:
        return self.installed.read_text().splitlines() if self.installed.exists() else []


@pytest.fixture
def system(tmp_path, key):
    return System(tmp_path, key)


def test_nothing_requested_does_nothing(system):
    assert system.update().returncode == 0
    assert not (system.requests / "result").exists()


def test_a_newer_signed_build_is_installed(system):
    system.request("2.9")
    done = system.update()
    assert done.returncode == 0, done.stdout + done.stderr
    result = system.result()
    assert (result["outcome"], result["from"], result["to"]) == ("ok", "2.8", "2.9")
    assert result["message"] == "updated from 2.8 to 2.9"
    assert pathlib.Path(result["log"]).is_file()
    assert system.ran_dui() == [f"--update {system.root / 'staging' / 'darqos.tar.gz'}"]
    # The request's gone, with what it brought.
    assert sorted(p.name for p in system.requests.iterdir()) == ["result"]


def test_a_new_edition_is_newer(system):
    (system.darq / "VERSION").write_text("version=2.125\nedition=2\nbuild=125\n")
    system.request("3.0")
    assert system.update().returncode == 0
    assert system.result()["outcome"] == "ok"


@pytest.mark.parametrize("version, action, why", [
    ("2.7", "update", "2.7 is older than 2.8: go back by hand, with dui"),
    ("1.40", "reinstall", "1.40 is older than 2.8: go back by hand, with dui"),
    ("2.8", "update", "2.8 is what's installed"),
    ("2.9", "downgrade", "the request is for nothing it knows: downgrade"),
])
def test_what_isnt_newer_is_refused(system, version, action, why):
    system.request(version, action)
    assert system.update().returncode == 1
    assert (system.result()["outcome"], system.result()["message"]) == ("failed", why)
    assert system.ran_dui() == []


def test_the_same_build_can_be_reinstalled(system):
    system.request("2.8", "reinstall")
    assert system.update().returncode == 0
    assert system.result()["message"] == "reinstalled 2.8"


def test_another_keys_signature_is_refused(system, tmp_path):
    other = tmp_path / "other_key"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", other, check=True)
    system.request("2.9", signed_with=other)
    assert system.update().returncode == 1
    assert system.result()["message"] == "its signature isn't the release key's"
    assert system.ran_dui() == []


def test_an_unsigned_one_is_refused(system):
    system.request("2.9")
    (system.requests / "darqos.tar.gz.sig").unlink()
    assert system.update().returncode == 1
    assert system.result()["message"] == "the request has no distribution, or no signature"


def test_with_no_installed_key_its_refused(system):
    system.request("2.9")
    (system.key.parent / "etc" / "allowed_signers").unlink()
    assert system.update().returncode == 1
    assert system.result()["message"] == "no release key is installed: install by hand, with dui"


def test_a_failed_backup_installs_nothing(system):
    system.request("2.9")
    assert system.update(backup="false").returncode == 1
    assert system.result()["message"] == "the backup failed, so nothing was installed"
    assert system.ran_dui() == []


def test_a_failed_install_says_so(system):
    system.request("2.9")
    system.dui.write_text("#! /bin/sh\nexit 3\n")
    assert system.update().returncode == 1
    assert system.result()["message"] == "installing 2.9 failed"


def test_what_isnt_a_distribution_is_refused(system):
    system.request("2.9")
    tarball = system.requests / "darqos.tar.gz"
    tarball.write_bytes(b"not a tarball")
    (system.requests / "darqos.tar.gz.sig").unlink()
    run("ssh-keygen", "-q", "-Y", "sign", "-f", system.key, "-n", "darqos-release", tarball, check=True)
    assert system.update().returncode == 1
    assert system.result()["message"] == "it isn't a DarqOS distribution"
