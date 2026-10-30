# Development Terminal: an ordinary window for creating items, browsing
# History, and shutting down or rebooting.  It was written to try types
# and lenses before the compositor existed, and is kept alongside the
# real Terminal (services/terminal), sharing its New and History parts
# (darq.ui).
#
# The p-kernel starts it at boot, after the services, if
# DARQ_DEV_TERMINAL is set (see kernel/pkernel), so it's a system
# process, and may shut down and reboot.  It has:
#
# - New: the implemented types, from the Type service, to create an
#   item of, and use it in its lens.  Types with no Terminal lens are
#   shown greyed out.
# - History: events from the History service, between two times, and
#   filtered by type, event and source.  Double-click one to use its
#   item.
# - Tools: the installed tools, those under tools/ with a tool.json
#   manifest, such as the vCard import, to start.
# - System: Shutdown, Reboot, and Quit (just this window).
#
# Its calls are synchronous, on Qt's thread, but for creating and using
# items, which its Launcher does on other threads.

import json
import logging
import os
import pathlib

from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QTabWidget

import darq
from darq.loop import QtEventLoop
from darq.ui import Services, wait_for_display
from darq.ui.history import HistoryView
from darq.ui.launcher import Launcher
from darq.ui.types import TypeList


# History source for the events it records.
SOURCE = "devterm"

# Milliseconds between looking for types, while there are none: the
# type sync registers them at boot.
TYPES_RETRY = 2000

# Where the tools are, under DARQ_ROOT, as the type sync finds types.
DEFAULT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def find_tools(root: pathlib.Path) -> list[tuple[str, str]]:
    """Return the installed tools' names and directories, by name: those
    under tools/ with a tool.json manifest, {"name": ...}."""
    tools = []
    for manifest in sorted((root / "tools").glob("*/tool.json")):
        try:
            name = json.loads(manifest.read_text()).get("name") or manifest.parent.name
        except (OSError, ValueError, AttributeError) as e:
            logging.warning(f"Can't read {manifest}: {e}")
            continue
        tools.append((str(name), str(manifest.parent)))
    return sorted(tools)


class DevTerminal(QMainWindow):
    """The window: New and History tabs, and a System menu."""

    def __init__(self, services: Services):
        super().__init__()
        self.services = services
        self.loop = QtEventLoop()
        self.launcher = Launcher(self, SOURCE, self.loop.post)
        self.setWindowTitle("DarqOS Development Terminal")
        self.resize(1000, 700)

        self.factory = TypeList(self)
        self.history = HistoryView(self)
        tabs = QTabWidget()
        tabs.addTab(self.factory, "New")
        tabs.addTab(self.history, "History")
        self.setCentralWidget(tabs)

        menu_bar = self.menuBar()
        assert menu_bar is not None
        tools = menu_bar.addMenu("Tools")
        assert tools is not None
        for name, path in find_tools(pathlib.Path(os.environ.get("DARQ_ROOT", DEFAULT_ROOT))):
            tools.addAction(name, lambda name=name, path=path: self.start_tool(name, path))
        system = menu_bar.addMenu("System")
        assert system is not None
        system.addAction("Shutdown", self.services.shutdown)
        system.addAction("Reboot", self.services.reboot)
        system.addSeparator()
        system.addAction("Quit", self.close)

        self.status = QLabel()
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addWidget(self.status)

        self.retry = QtCore.QTimer(self)
        self.retry.setInterval(TYPES_RETRY)
        self.retry.timeout.connect(self.load_types)
        self.load_types()

    def load_types(self):
        """Load the types, trying again until the type sync has run."""
        if self.factory.refresh():
            self.retry.stop()
            self.history.set_types([entry.type_id for entry in self.factory.entries])
            self.show_status(f"{len(self.factory.entries)} implemented types")
        elif not self.retry.isActive():
            self.retry.start()

    def start_tool(self, name: str, path: str):
        """Start a tool, off the UI's thread: spawning waits for it to run."""
        self.show_status(f"Starting {name} ...")

        def started(dpid, error):
            self.show_status(f"Can't start {name}: {error}" if error else f"Started {name}")

        self.launcher.background(lambda: darq.spawn_process(path), started)

    def show_status(self, message: str):
        self.status.setText(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        self.show_status(error or f"Using {item_id}")


def darq_main():
    # With the Terminal, it's on the compositor's display, which may not
    # be there yet.
    if not wait_for_display():
        return 1
    app = QApplication([])
    window = DevTerminal(Services())
    window.show()
    app.exec()
    return 0
