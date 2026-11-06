# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.version: finding the version of a system, installed or
# in a source tree; and asking the p-kernel, through a real one (see
# test_pkernel.py for the fixtures).

import pathlib

import pytest

import darq
from darq import version

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


ROOT = pathlib.Path(__file__).parent.parent


def test_installed(tmp_path):
    (tmp_path / "VERSION").write_text("version=2.17\nedition=2\nbuild=17\ncommit=abc\ndate=2026-11-06\n")
    assert version.find(tmp_path) == darq.Version("2.17", 2, 17)


def test_installed_dirty(tmp_path):
    (tmp_path / "VERSION").write_text("version=2.17-dirty\nedition=2\nbuild=17\n")
    assert version.find(tmp_path).version == "2.17-dirty"


def test_source_tree(tmp_path):
    (tmp_path / "RELEASE").write_text("edition=2\nbuild=17\n")
    assert version.find(tmp_path) == darq.Version("2.17-dev", 2, 17)


def test_installed_is_preferred(tmp_path):
    (tmp_path / "RELEASE").write_text("edition=2\nbuild=18\n")
    (tmp_path / "VERSION").write_text("version=2.17\nedition=2\nbuild=17\n")
    assert version.find(tmp_path).build == 17


@pytest.mark.parametrize("name, text", [("RELEASE", ""), ("RELEASE", "edition=two\nbuild=1\n"),
                                        ("RELEASE", "edition=2\n"), ("VERSION", "old-style 2026-11-03\n")])
def test_unreadable(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    assert version.find(tmp_path) == version.UNKNOWN


def test_nothing_there(tmp_path):
    assert version.find(tmp_path) == version.UNKNOWN


def test_this_tree_has_a_version():
    found = version.find(ROOT)
    assert found.edition >= 2 and found.build >= 1 and found.version.endswith("-dev")


def test_get_version(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel
    expected = kernel.version
    client_path = write_app(tmp_path / "client", f"""
        import darq

        def darq_main():
            got = darq.get_version()
            if got != darq.Version({expected.version!r}, {expected.edition}, {expected.build}):
                return f"got {{got!r}}"

            # It's only asked for once.
            if darq.get_version() is not got:
                return "asked again"
            return 0
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
