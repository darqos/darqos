# darqos
# Copyright (C) 2026 David Arnold

# Tests for distribution and install: mkdist, in a scratch git
# repository; bin/install, from a distribution, into a temporary root,
# skipping the steps that change the host (DARQOS_INSTALL_SKIP); and the
# backup, bin/darqos-backup.

import os
import pathlib
import shutil
import sqlite3
import stat
import subprocess
import tarfile

import pytest

from darq import version as darq_version


ROOT = pathlib.Path(__file__).parent.parent

# The install steps that change the host, or need root.
HOST_STEPS = "root stop packages user owner compositor venv units backup updates boot start"


def run(*command, env=None, cwd=None, check=True):
    """Run a command: with no release key, unless one's given, so that
    nothing's signed with this machine's own."""
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, cwd=cwd,
                            env={**os.environ, "DARQ_RELEASE_KEY": "", **(env or {})})
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def git(repo, *args):
    return run("git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args, cwd=repo)


@pytest.fixture
def repo(tmp_path):
    """A repository with mkdist, some code, and things that aren't
    distributed."""
    repo = tmp_path / "repo"
    for path, text in [("darq/__init__.py", "# darq\n"), ("services/storage/main.py", "# storage\n"),
                       ("bin/install", "#! /bin/sh\n"), ("man/install.8", ".TH INSTALL 8\n"),
                       ("requirements.txt", "PyQt6\n"), ("RELEASE_NOTES.md", "# Notes\n"), ("doc/index.rst", "Docs\n"),
                       ("keys/release_key.pub", "ssh-ed25519 AAAA test\n"),
                       ("RELEASE", "edition=2\nbuild=16\n"),
                       ("experiments/x.py", "# mine\n")]:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    shutil.copy(ROOT / "mkdist", repo / "mkdist")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "First")
    return repo


def tarball(directory: pathlib.Path) -> pathlib.Path:
    [path] = directory.glob("darqos-*ed-snap*.tar.gz")
    return path


def version_file(path: pathlib.Path) -> dict[str, str]:
    with tarfile.open(path) as tar:
        text = tar.extractfile("darq/VERSION").read().decode()  # type: ignore[union-attr]
    return dict(line.split("=", 1) for line in text.splitlines())


def test_mkdist(repo, tmp_path, release_key):
    out = tmp_path / "out"
    result = run(repo / "mkdist", out, cwd=repo, env={"DARQ_RELEASE_KEY": str(release_key[0])})
    assert result.stderr == ""
    with tarfile.open(tarball(out)) as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
    # The installed directories' tracked files, owned by root.
    assert sorted(members) == ["darq/RELEASE_NOTES.md", "darq/VERSION", "darq/bin/install", "darq/darq/__init__.py",
                               "darq/keys/release_key.pub", "darq/man/install.8", "darq/requirements.txt",
                               "darq/services/storage/main.py"]
    assert {(m.uid, m.gid) for m in members.values()} == {(0, 0)}
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    fields = version_file(tarball(out))
    assert fields["commit"] == head
    assert (fields["version"], fields["edition"], fields["build"]) == ("2.17", "2", "17")


def test_mkdist_names_the_edition_and_build(repo, tmp_path):
    out = tmp_path / "out"
    run(repo / "mkdist", out, cwd=repo)
    assert tarball(out).name.startswith("darqos-2ed-snap") and tarball(out).name.endswith("-b17.tar.gz")


def test_mkdist_counts_builds(repo, tmp_path):
    run(repo / "mkdist", tmp_path / "one", cwd=repo)
    assert (repo / "RELEASE").read_text() == "edition=2\nbuild=17\n"
    run(repo / "mkdist", tmp_path / "two", cwd=repo)
    assert (repo / "RELEASE").read_text() == "edition=2\nbuild=18\n"
    assert version_file(tarball(tmp_path / "two"))["version"] == "2.18"


def test_mkdist_starts_an_edition_at_0(repo, tmp_path):
    """A new edition's builds count from 0: 3.0 follows 2.16."""
    out = tmp_path / "out"
    run(repo / "mkdist", "--edition", "3", out, cwd=repo)
    fields = version_file(tarball(out))
    assert (fields["version"], fields["edition"], fields["build"]) == ("3.0", "3", "0")
    assert tarball(out).name.startswith("darqos-3ed-snap") and tarball(out).name.endswith("-b0.tar.gz")
    assert (repo / "RELEASE").read_text() == "edition=3\nbuild=0\n"
    run(repo / "mkdist", tmp_path / "next", cwd=repo)
    assert version_file(tarball(tmp_path / "next"))["version"] == "3.1"
    assert run(repo / "mkdist", "--edition", "three", cwd=repo, check=False).returncode == 2


def test_versions_are_ordered_by_edition_then_build():
    assert darq_version.parse("version=3.0\nedition=3\nbuild=0\n").order() > \
        darq_version.parse("version=2.125\nedition=2\nbuild=125\n").order()
    with pytest.raises(ValueError, match="no build"):
        darq_version.parse("version=2.1\nedition=2\n")


@pytest.fixture
def release_key(tmp_path):
    """A release key, made for the test, and its allowed signers."""
    key = tmp_path / "keys" / "release_key"
    key.parent.mkdir()
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", key)
    signers = tmp_path / "allowed_signers"
    signers.write_text(f'darqos-release namespaces="darqos-release" {(key.parent / "release_key.pub").read_text()}')
    return key, signers


def test_mkdist_signs_with_the_release_key(repo, tmp_path, release_key):
    key, signers = release_key
    out = tmp_path / "out"
    result = run(repo / "mkdist", out, cwd=repo, env={"DARQ_RELEASE_KEY": str(key)})
    assert "signed)" in result.stdout
    dist = tarball(out)
    signature = dist.with_name(dist.name + ".sig")
    with open(dist, "rb") as data:
        verified = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", "darqos-release",
                                   "-n", "darqos-release", "-s", str(signature)], stdin=data, capture_output=True)
    assert verified.returncode == 0, verified.stderr


def test_mkdist_without_a_key_warns_and_cant_publish(repo, tmp_path):
    out = tmp_path / "out"
    result = run(repo / "mkdist", out, cwd=repo)
    assert "isn't signed" in result.stderr
    assert not list(out.glob("*.sig"))
    # Publishing needs a pushed commit, before anything's built.
    result = run(repo / "mkdist", "--publish", tmp_path / "other", cwd=repo, check=False)
    assert result.returncode == 1 and "isn't pushed" in result.stderr


def test_mkdist_keeps_the_number_if_it_fails(repo, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o500)
    try:
        assert run(repo / "mkdist", out, cwd=repo, check=False).returncode != 0
    finally:
        out.chmod(0o700)
    assert (repo / "RELEASE").read_text() == "edition=2\nbuild=16\n"


def test_mkdist_doesnt_call_a_new_build_a_change(repo, tmp_path, release_key):
    # RELEASE isn't distributed, so it doesn't make the next one dirty.
    signed = {"DARQ_RELEASE_KEY": str(release_key[0])}
    run(repo / "mkdist", tmp_path / "one", cwd=repo, env=signed)
    result = run(repo / "mkdist", tmp_path / "two", cwd=repo, env=signed)
    assert result.stderr == ""
    assert "RELEASE" not in [name.split("/")[-1] for name in tarfile.open(tarball(tmp_path / "two")).getnames()]


def test_mkdist_warns_of_changes(repo, tmp_path):
    (repo / "darq" / "__init__.py").write_text("# changed\n")
    (repo / "darq" / "new.py").write_text("# new\n")
    # Not distributed: no warning.
    (repo / "doc" / "index.rst").write_text("Changed\n")

    out = tmp_path / "out"
    result = run(repo / "mkdist", out, cwd=repo)
    assert "uncommitted changes" in result.stderr
    assert "darq/new.py" in result.stderr
    with tarfile.open(tarball(out)) as tar:
        assert "darq/darq/new.py" not in tar.getnames()
        assert tar.extractfile("darq/darq/__init__.py").read() == b"# changed\n"  # type: ignore[union-attr]
    assert version_file(tarball(out))["version"] == "2.17-dirty"


################################################################
# Install
################################################################

@pytest.fixture
def dist(tmp_path):
    """This tree's distribution, unpacked."""
    # A copy of RELEASE, so testing doesn't use up this tree's numbers.
    out = tmp_path / "out"
    release = tmp_path / "RELEASE"
    shutil.copy(ROOT / "RELEASE", release)
    run(ROOT / "mkdist", out, cwd=ROOT, env={"DARQ_RELEASE": str(release)})
    with tarfile.open(tarball(out)) as tar:
        tar.extractall(tmp_path / "dist", filter="data")
    return tmp_path / "dist" / "darq"


def install(dist, root, *args, check=True):
    return run(dist / "bin" / "install", *args, check=check,
               env={"DARQ": str(root), "LOG_DIR": str(root.parent / "log"), "DARQOS_INSTALL_SKIP": HOST_STEPS,
                    "CONF_DIR": str(root.parent / "etc")})


def test_install(dist, tmp_path):
    # A distribution made with a umask that lets the group write, and
    # unpacked as root, which keeps its modes.
    for path in dist.rglob("*"):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWGRP | stat.S_IWOTH)
    root = tmp_path / "darq"
    result = install(dist, root)
    assert "Ok: installed" in result.stdout
    assert f"installing version {darq_version.find(root).version} from" in result.stdout
    assert darq_version.find(root).build > 0

    for path in ["darq/__init__.py", "kernel/pkernel", "kernel/darqos-pkernel.service", "services/storage/main.py",
                 "types/text/main.py", "lenses/text/main.py", "tools/reindex/main.py", "compositor/compositor.c",
                 "lib/didl/kb.didl", "bin/didlc", "requirements.txt", "VERSION"]:
        assert (root / path).is_file(), path
    assert os.access(root / "kernel" / "pkernel", os.X_OK)
    # Writable only by its owner, whatever the distribution's modes.
    writable = [str(p.relative_to(root)) for p in root.rglob("*")
                if p.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH)]
    assert writable == []
    # Manual pages, by section.
    assert (root / "man" / "man8" / "install.8").is_file()
    assert (root / "man" / "man1" / "didlc.1").is_file()
    # Only darq may read the run directory.
    assert stat.S_IMODE((root / "run").stat().st_mode) == 0o700
    # Logged.
    [log] = (tmp_path / "log").glob("install-*.log")
    assert "Ok: installed" in log.read_text()


def test_a_manual_install_trusts_its_release_key(dist, tmp_path):
    """The distribution's key is installed by hand, first or after; an
    update, which darqos-update(8) checked with the installed key, leaves
    it as it is."""
    (dist / "keys").mkdir(exist_ok=True)
    (dist / "keys" / "release_key.pub").write_text("ssh-ed25519 AAAAfirst release\n")
    root = tmp_path / "darq"
    signers = tmp_path / "etc" / "allowed_signers"
    assert "Ok: installed the release key" in install(dist, root).stdout
    assert signers.read_text() == 'darqos-release namespaces="darqos-release" ssh-ed25519 AAAAfirst release\n'
    assert (root / "run" / "update").is_dir()

    (dist / "keys" / "release_key.pub").write_text("ssh-ed25519 AAAAsecond release\n")
    assert "release key" not in install(dist, root, "--update").stdout
    assert "AAAAfirst" in signers.read_text()
    install(dist, root)
    assert "AAAAsecond" in signers.read_text()

    (dist / "keys" / "release_key.pub").unlink()
    assert "Warning: no release key" in install(dist, root).stdout
    assert "AAAAsecond" in signers.read_text()


def test_the_release_notes_are_installed(dist, tmp_path):
    """What's changed is installed beside the code, as /darq/RELEASE_NOTES.md."""
    (dist / "RELEASE_NOTES.md").write_text("# DarqOS Release Notes\n")
    root = tmp_path / "darq"
    install(dist, root)
    assert (root / "RELEASE_NOTES.md").read_text() == "# DarqOS Release Notes\n"


def test_update(dist, tmp_path):
    root = tmp_path / "darq"
    install(dist, root)
    # What's gone from the distribution goes; the compositor's build and
    # the run directory's files are kept.
    (root / "services" / "old").mkdir()
    (root / "services" / "old" / "main.py").write_text("# old\n")
    (root / "compositor" / "build").mkdir()
    (root / "compositor" / "build" / "darq-compositor").write_text("built\n")
    (root / "run" / "kb.sqlite").write_text("data\n")
    (root / "man" / "man8" / "gone.8").write_text("gone\n")
    # Made with a looser umask, as a login session's can be.
    (root / "run" / "kb.sqlite").chmod(0o664)

    install(dist, root)
    assert stat.S_IMODE((root / "run" / "kb.sqlite").stat().st_mode) == 0o600
    assert not (root / "services" / "old").exists()
    assert not (root / "man" / "man8" / "gone.8").exists()
    assert (root / "compositor" / "build" / "darq-compositor").read_text() == "built\n"
    assert (root / "run" / "kb.sqlite").read_text() == "data\n"


def test_update_takes_a_gone_directorys_cache_with_it(dist, tmp_path):
    """A directory that's gone from the distribution goes, though it has
    a compiled cache, which isn't copied: a directory that's still there
    keeps its cache."""
    root = tmp_path / "darq"
    install(dist, root)
    (root / "tools" / "old" / "__pycache__").mkdir(parents=True)
    (root / "tools" / "old" / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"compiled")
    kept = next(d for d in (root / "services").iterdir() if d.is_dir()) / "__pycache__" / "kept.pyc"
    kept.parent.mkdir(exist_ok=True)
    kept.write_bytes(b"compiled")

    install(dist, root)
    assert not (root / "tools" / "old").exists()
    assert kept.read_bytes() == b"compiled"


def test_install_in_place(dist, tmp_path):
    # "/darq/bin/install --boot": run from the installation, it copies
    # nothing, and does the rest.
    root = tmp_path / "darq"
    install(dist, root)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    shutil.rmtree(root / "run")

    result = run(root / "bin" / "install", "--boot",
                 env={"DARQ": str(root), "LOG_DIR": str(tmp_path / "log"), "DARQOS_INSTALL_SKIP": HOST_STEPS})
    assert "installing in place" in result.stdout and "Ok: installed" in result.stdout
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before
    assert (root / "man" / "man8" / "install.8").is_file()


def test_install_usage(dist, tmp_path):
    result = install(dist, tmp_path / "darq", "--bogus", check=False)
    assert result.returncode == 2
    assert "Usage" in result.stderr


################################################################
# Backup
################################################################

@pytest.fixture
def backup(tmp_path):
    """A run directory with a database being written, and the backup's
    configuration, keeping two snapshots, copied to a destination."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    db = sqlite3.connect(run_dir / "kb.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (x)")
    db.execute("INSERT INTO t VALUES ('kept')")
    db.commit()
    (run_dir / "bootvec.json").write_text("{}\n")

    snapshots = tmp_path / "snapshots"
    conf = tmp_path / "backup.conf"
    conf.write_text(f"RUN_DIR={run_dir}\nSNAPSHOTS={snapshots}\nKEEP=2\nBACKUP_DEST={tmp_path / 'dest'}\n")
    yield snapshots, conf
    db.close()


def test_backup(tmp_path, backup):
    snapshots, conf = backup
    # Older snapshots, and one left by a backup that failed.
    for name in ["20200101-000000", "20200102-000000", ".partial-20200103-000000"]:
        (snapshots / name).mkdir(parents=True)

    result = run(ROOT / "bin" / "darqos-backup", env={"DARQOS_BACKUP_CONF": str(conf)})
    assert "Ok: removed snapshot 20200101-000000" in result.stdout

    names = sorted(p.name for p in snapshots.iterdir())
    assert names[0] == "20200102-000000" and len(names) == 2
    latest = snapshots / names[1]
    assert sorted(p.name for p in latest.iterdir()) == ["bootvec.json", "kb.sqlite"]
    copy = sqlite3.connect(latest / "kb.sqlite")
    try:
        assert copy.execute("SELECT x FROM t").fetchall() == [("kept",)]
    finally:
        copy.close()
    assert stat.S_IMODE(snapshots.stat().st_mode) == 0o700

    # Copied to the destination, as kept.
    assert sorted(p.name for p in (tmp_path / "dest").iterdir()) == names
