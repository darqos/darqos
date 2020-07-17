#! /usr/bin/env python
# Copyright (C) 2020 David Arnold

import sys
import PyQt5
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QTextEdit, QMenu, QShortcut, QVBoxLayout, qApp

from darq.type.text import TextTypeView


class ObjectFactory(QWidget):
    """Enables creation of new type instances."""

    def __init__(self, *args):
        """Constructor."""
        super().__init__(*args)
        self.init_ui()
        return

    def init_ui(self):

        # Hide the default chrome.
        flags = QtCore.Qt.WindowFlags(QtCore.Qt.WindowStaysOnTopHint |
                                      QtCore.Qt.FramelessWindowHint)
        self.setWindowFlags(flags)

        # Set size and position.

        # Pinned types.
        # Recently used types.
        # Search bar.
        # Selected type icon.
        # Search text.
        # Go button.
        # Matching types list.

        # FIXME: and in the meantime ...
        b = QPushButton("Text", self)
        b.clicked.connect(self.on_create)

        return

    def on_create(self):
        o = TextTypeView()
        self.hide()
        return


class ObjectSelector(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        # Set size & position.
        screen = QtWidgets.QDesktopWidget().screenGeometry(0)
        self.resize(int(screen.width() * 0.7), int(screen.height() * 0.7))
        self.move(int(screen.width() * 0.15), int(screen.height() * 0.15))

        # Set window stype.
        flags = QtCore.Qt.WindowFlags(
            QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.FramelessWindowHint)
        self.setWindowFlags(flags)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)

        self.setLayout(self.layout)
        self.show()
        return


class UI(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        self.factory = ObjectFactory()
        self.factory_shortcut = QShortcut(QKeySequence("Ctrl+n"), self)
        self.factory_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self.factory_shortcut.activated.connect(self.on_factory)

        self.selector = ObjectSelector()
        self.selector_shortcut = QShortcut(QKeySequence("Ctrl+s"), self)
        self.selector_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self.selector_shortcut.activated.connect(self.on_selector)

        flags = QtCore.Qt.WindowFlags(QtCore.Qt.WindowStaysOnBottomHint)
        self.setWindowFlags(flags)

        self.showFullScreen()
        return

    def contextMenuEvent(self, event):
        m = QMenu()
        new_action = m.addAction("New ...")
        find_action = m.addAction("Find ...")
        m.addSeparator()
        logout_action = m.addAction("Logout")
        quit_action = m.addAction("Shutdown")

        action = m.exec_(self.mapToGlobal(event.pos()))
        if action == new_action:
            self.on_factory()
        elif action == find_action:
            self.on_selector()
        elif action == quit_action:
            qApp.quit()

    def on_factory(self, *args):
        """Display a panel enabling creation of new type instances."""
        print("Factory: " + str(args))
        if self.factory.isVisible():
            self.factory.hide()
        else:
            self.factory.show()
        return

    def on_selector(self, *args):
        """Display a panel enabling a search of existing objects."""
        print("Selector: " + str(args))
        if self.selector.isVisible():
            self.selector.hide()
        else:
            self.selector.show()
        return


def main():
    app = QApplication(sys.argv)
    ui = UI()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
