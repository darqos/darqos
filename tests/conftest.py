# darqos
# Copyright (C) 2026 David Arnold

# Shared test setup.

import os

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
