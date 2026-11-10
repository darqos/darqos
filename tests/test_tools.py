# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.ui.tools: finding the installed tools, running one, and
# saying how it finished (see doc/terminal.rst).

import json
import pathlib
import types
import typing

import pytest

import darq
from darq.ui import tools

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


def test_tools_are_found_by_their_manifests(tmp_path):
    for directory, manifest in (("reindex", {"name": "Reindex", "description": "Publish again."}),
                                ("vcard-import", {"name": "import vCards"}),
                                ("unnamed", {})):
        (tmp_path / "tools" / directory).mkdir(parents=True)
        (tmp_path / "tools" / directory / "tool.json").write_text(json.dumps(manifest))
    (tmp_path / "tools" / "broken").mkdir()
    (tmp_path / "tools" / "broken" / "tool.json").write_text("{not json")
    (tmp_path / "tools" / "not-a-tool").mkdir()
    found = tools.find_tools(tmp_path)
    assert [(t.name, t.description) for t in found] == [("import vCards", ""), ("Reindex", "Publish again."),
                                                       ("unnamed", "")]
    assert found[1].path == str(tmp_path / "tools" / "reindex")


def test_the_installed_tools_are_found():
    names = [tool.name for tool in tools.find_tools(pathlib.Path(__file__).parent.parent)]
    assert "Import vCards" in names and "Give Items Entities" in names


@pytest.mark.parametrize("finished, said", [
    (darq.ChildStatus(7, darq.ExitReason.EXITED, 0, None), "Reindex finished"),
    (darq.ChildStatus(7, darq.ExitReason.EXITED, 2, None), "Reindex failed: exit status 2"),
    (darq.ChildStatus(7, darq.ExitReason.EXITED, 1, "Traceback:\n  x\nKeyError: 'id'\n\n"),
     "Reindex failed: KeyError: 'id'"),
    (darq.ChildStatus(7, darq.ExitReason.KILLED, -1, None), "Reindex was stopped"),
    (darq.ChildStatus(7, darq.ExitReason.LOST, -1, None), "Reindex was lost"),
])
def test_how_a_tool_finished(finished, said):
    assert tools.outcome("Reindex", finished) == said


class Later:
    """A launcher whose jobs run when they're told to."""

    def __init__(self):
        self.jobs: list[typing.Callable[[], None]] = []

    def background(self, job, finished):
        self.jobs.append(lambda: finished(job(), None))


def test_running_a_tool_waits_for_it():
    launcher = Later()
    said: list[str] = []
    asked: list[str] = []
    tool = tools.Tool("Reindex", "", "/darq/tools/reindex")
    tools.run_tool(launcher, tool, said.append, spawn=lambda path: asked.append(path) or 9,
                   wait=lambda dpid: darq.ChildStatus(dpid, darq.ExitReason.EXITED, 0, None))
    assert said == []
    launcher.jobs.pop()()
    assert asked == ["/darq/tools/reindex"] and said == ["Reindex finished"]


def state(shown: tools.ToolList) -> str:
    """Return what's shown of the first tool's state."""
    cell = shown.table.item(0, 2)
    return cell.text() if cell is not None else ""


def test_a_tool_thats_running_isnt_started_again(qt_app, tmp_path):
    (tmp_path / "tools" / "reindex").mkdir(parents=True)
    (tmp_path / "tools" / "reindex" / "tool.json").write_text(json.dumps({"name": "Reindex"}))
    launcher = Later()
    statuses: list[str] = []
    finished: list[str] = []
    host = types.SimpleNamespace(launcher=launcher, show_status=statuses.append, tool_finished=finished.append)
    shown = tools.ToolList(typing.cast(typing.Any, host), tmp_path, spawn=lambda path: 9,
                           wait=lambda dpid: darq.ChildStatus(dpid, darq.ExitReason.EXITED, 0, None))
    shown.refresh()
    shown.run_selected()
    assert state(shown) == tools.RUNNING
    shown.run_selected()
    assert statuses == ["Started Reindex", "Reindex is still running"] and len(launcher.jobs) == 1

    launcher.jobs.pop()()
    assert finished == ["Reindex finished"] and state(shown) == ""
    shown.run_selected()
    assert len(launcher.jobs) == 1


def test_running_a_real_tool(live_pkernel, tmp_path):  # noqa: F811
    """Spawning a tool and waiting for it, off the main thread, as the
    Terminal does, with the p-kernel."""
    kernel = live_pkernel
    tool = write_app(tmp_path / "tool", """
        def darq_main():
            raise ValueError("no export")
        """)
    client_path = write_app(tmp_path / "client", f"""
        import threading
        import darq
        from darq.ui import tools

        def darq_main():
            said = []

            class Launcher:
                def background(self, job, finished):
                    result = {{}}

                    def run():
                        try:
                            result["value"] = job()
                        finally:
                            darq.release_reply_port()

                    thread = threading.Thread(target=run)
                    thread.start()
                    thread.join()
                    finished(result.get("value"), None)

            tools.run_tool(Launcher(), tools.Tool("Broken", "", {str(tool)!r}), said.append)
            return 0 if said == ["Broken failed: ValueError: no export"] else repr(said)
        """)
    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
