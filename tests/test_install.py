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


ROOT = pathlib.Path(__file__).parent.parent

# The install steps that change the host, or need root.
HOST_STEPS = "root stop packages user owner compositor venv units backup boot start"


def run(*command, env=None, cwd=None, check=True):
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, cwd=cwd,
                            env=None if env is None else {**os.environ, **env})
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
                       ("requirements.txt", "PyQt6\n"), ("doc/index.rst", "Docs\n"),
                       ("experiments/x.py", "# mine\n")]:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    shutil.copy(ROOT / "mkdist", repo / "mkdist")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "First")
    return repo


def tarball(directory: pathlib.Path) -> pathlib.Path:
    [path] = directory.glob("darqos-1ed-snap*.tar.gz")
    return path


def test_mkdist(repo, tmp_path):
    out = tmp_path / "out"
    result = run(repo / "mkdist", out, cwd=repo)
    assert result.stderr == ""
    with tarfile.open(tarball(out)) as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        version = tar.extractfile("darq/VERSION").read().decode()  # type: ignore[union-attr]
    # The installed directories' tracked files, owned by root.
    assert sorted(members) == ["darq/VERSION", "darq/bin/install", "darq/darq/__init__.py", "darq/man/install.8",
                               "darq/requirements.txt", "darq/services/storage/main.py"]
    assert {(m.uid, m.gid) for m in members.values()} == {(0, 0)}
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    assert version.split()[0] == head


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
        assert tar.extractfile("darq/VERSION").read().decode().split()[0].endswith("-dirty")  # type: ignore


################################################################
# Install
################################################################

@pytest.fixture
def dist(tmp_path):
    """This tree's distribution, unpacked."""
    out = tmp_path / "out"
    run(ROOT / "mkdist", out, cwd=ROOT)
    with tarfile.open(tarball(out)) as tar:
        tar.extractall(tmp_path / "dist", filter="data")
    return tmp_path / "dist" / "darq"


def install(dist, root, *args, check=True):
    return run(dist / "bin" / "install", *args, check=check,
               env={"DARQ": str(root), "LOG_DIR": str(root.parent / "log"), "DARQOS_INSTALL_SKIP": HOST_STEPS})


def test_install(dist, tmp_path):
    root = tmp_path / "darq"
    result = install(dist, root)
    assert "Ok: installed" in result.stdout

    for path in ["darq/__init__.py", "kernel/pkernel", "kernel/darqos-pkernel.service", "services/storage/main.py",
                 "types/text/main.py", "lenses/text/main.py", "tools/reindex/main.py", "compositor/compositor.c",
                 "lib/didl/kb.didl", "bin/didlc", "requirements.txt", "VERSION"]:
        assert (root / path).is_file(), path
    assert os.access(root / "kernel" / "pkernel", os.X_OK)
    # Manual pages, by section.
    assert (root / "man" / "man8" / "install.8").is_file()
    assert (root / "man" / "man1" / "didlc.1").is_file()
    # Only darq may read the run directory.
    assert stat.S_IMODE((root / "run").stat().st_mode) == 0o700
    # Logged.
    [log] = (tmp_path / "log").glob("install-*.log")
    assert "Ok: installed" in log.read_text()


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

    install(dist, root)
    assert not (root / "services" / "old").exists()
    assert not (root / "man" / "man8" / "gone.8").exists()
    assert (root / "compositor" / "build" / "darq-compositor").read_text() == "built\n"
    assert (root / "run" / "kb.sqlite").read_text() == "data\n"


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
