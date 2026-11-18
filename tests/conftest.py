# darqos
# Copyright (C) 2026 David Arnold

# Shared test setup.

import os
import pathlib

import pytest

# Qt runs without a display, here and in the processes a test p-kernel
# spawns, which inherit the environment.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qt_app():
    """The process' QApplication: Qt allows only one, for widgets or not."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class RecordedHistory:
    """Stands in for a History client: the events recorded through it, as
    (item_id, event, source, note)."""

    def __init__(self):
        self.events: list[tuple[str, str, str, str]] = []

    def add_event(self, item_id="", event="", source="", note=""):
        self.events.append((item_id, event, source, note))


@pytest.fixture(autouse=True)
def recorded(monkeypatch):
    """What type implementations record in History (see darq.history's
    Recorder), as there's no History service in a test without a
    p-kernel."""
    import darq.history

    history = RecordedHistory()
    monkeypatch.setattr(darq.history, "connect", lambda: history)
    return history


TERMINAL_PATH = pathlib.Path(__file__).parent.parent / "services" / "terminal"


def terminal_module(name: str):
    """Import one of the Terminal's modules (services/terminal), as
    pyloader does, with its directory on the path, as its modules import
    each other by name."""
    import importlib
    import sys
    sys.path.insert(0, str(TERMINAL_PATH))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(TERMINAL_PATH))
