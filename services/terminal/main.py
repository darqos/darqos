#! /usr/bin/env python3
# darqos
# Copyright (C) 2020-2023 David Arnold

import sys
import typing
from urllib.parse import urlparse
from datetime import datetime

from PyQt6 import QtCore
from PyQt6.QtGui import QKeySequence, QIcon, QAction, QShortcut, QScreen
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QMenu, QHBoxLayout, QVBoxLayout, QLineEdit, QLabel, QToolButton, QToolBar, QTableWidget, QTableWidgetItem, QGroupBox, QMainWindow

#from darq.type.text import TextTypeView
from darq.services.history import History


class Type:
    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.impl: str = ""
        self.icon: str = ""
        return


class TypeCacheModel:
    def __init__(self):

        # Table of named types.
        self.types = {}

        # Sorted type name index.
        self.type_names = []

        # Most recently used types.
        self.used_types = []

        # Pinned types, in order.
        self.pinned_types: typing.List[str] = []
        return

    def add_type(self, type_: Type) -> None:
        """Add a type to the system."""

        if type_.name in self.types:
            raise KeyError(f"Type '{type_.name}' already exists.")

        # FIXME: Cache icon.

        self.types[type_.name] = type_
        self.type_names.append(type_.name)
        self.type_names.sort()
        return

    def remove_type(self, name: str) -> None:
        """Remove a type from the system."""

        if name not in self.types:
            raise KeyError(f"No such type '{name}'")

        if name in self.pinned_types:
            self.pinned_types.remove(name)

        if name in self.used_types:
            self.used_types.remove(name)

        del self.types[name]
        return

    def append_pinned_type(self, name: str) -> None:
        """Append a type to the pinned list."""

        t = self.types.get(name)
        if t is None:
            raise KeyError(f"No such type '{name}'")

        self.pinned_types.append(name)
        return

    def insert_pinned_type(self, index: int, name: str) -> None:
        """Insert a type into the pinned list."""

        t = self.types.get(name)
        if t is None:
            raise KeyError(f"No such type '{name}'")

        self.pinned_types.insert(index, name)
        return

    def unpin_type(self, name: str) -> None:
        """Remove a type from the pinned list."""

        if name not in self.pinned_types:
            raise KeyError(f"No such type '{name}'")

        if name in self.pinned_types:
            self.pinned_types.remove(name)

        return

    def pinned_count(self) -> int:
        """Return number of pinned types."""

        return len(self.pinned_types)

    def pinned_type(self, index: int) -> Type:
        """Return type at index in pinned type list."""

        if index < 0 or index >= len(self.pinned_types):
            raise IndexError(f"Pinned types index out of range: {index}")

        name = self.pinned_types[index]
        return self.types[name]

    def use_type(self, name: str) -> None:
        """Record usage of a type."""

        if name in self.used_types:
            self.used_types.remove(name)

        self.used_types.insert(0, name)
        return

    def get_types(self):
        # FIXME: do proper iterator stuff here
        res = []
        for name in self.type_names:
            res.append(self.types[name])
        return res


class ObjectFactory(QWidget):
    """Enables creation of new type instances."""

    def __init__(self, *args):
        """Constructor."""
        super().__init__(*args)

        # Cache.
        self.types = TypeCacheModel()
        self.init_types()

        # Set size and position.
        screen = QScreen.availableGeometry(QApplication.primaryScreen())
        self.resize(int(screen.width() * 0.7), int(screen.height() * 0.7))
        self.move(int(screen.width() * 0.15), int(screen.height() * 0.15))

        # Set window type.
        flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.WindowStaysOnTopHint |
                                     QtCore.Qt.WindowType.FramelessWindowHint)
        self.setWindowFlags(flags)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)

        # Pinned types.
        self.hotbar = QToolBar()
        self.hotbar.setStyleSheet("QToolBar { }")
        for index in range(self.types.pinned_count()):
            t = self.types.pinned_type(index)
            url = urlparse(t.icon)
            icon = QIcon(url.path)
            action = QAction(icon, t.name, QtCore.QCoreApplication.instance())
            action.triggered.connect(self.on_create)

            self.hotbar.addAction(action)
        self.layout.addWidget(self.hotbar)

        # Search bar.
        self.omnibox = QHBoxLayout()
        self.omnibox.setContentsMargins(20, 20, 20, 20)
        self.omnitext = QLineEdit()
        self.omnitext.setStyleSheet("QLineEdit { font-size: 20px; padding: 12px; border: none; border-radius: 10px; }")
        self.omnitext.setFrame(False)  # Doesn't seem to do anything'
        #self.omnitext.setAttribute(QtCore.Qt.WA_MacShowFocusRect, False)
        self.omnitext.setPlaceholderText("Search ...")
        self.omnitext.setClearButtonEnabled(True)
        self.omnitext.setTextMargins(20, 20, 20, 20)
        self.omnibox.addWidget(self.omnitext)
        self.layout.addLayout(self.omnibox)

        # Unfiltered types, MRU order (?)
        self.object_table = QVBoxLayout()
        self.object_table.setContentsMargins(20, 20, 20, 20)
        for t in self.types.get_types():
            print(t.name, t.icon)
            row = QLabel(t.name)
            self.object_table.addWidget(row)
        self.layout.addLayout(self.object_table)
        self.layout.setStretch(2, 100)

        self.setLayout(self.layout)
        self.hide()
        return

    def init_types(self):
        """Initialize types collection."""

        # List of known types, with index by name, by last use, and by
        # keyword from their description.  Each type has a name, icon, and
        # a description.
        #
        # There's also an ordered list of "pinned" types.

        text = Type()
        text.name = "Text"
        text.description = "Unformatted Unicode document"
        text.impl = "file:///Users/d/work/personal/darqos/darq/types/text.py"
        text.icon = "file:///Users/d/work/personal/darqos/darq/icons/txt.png"
        self.types.add_type(text)
        self.types.append_pinned_type("Text")

        book = Type()
        book.name = "Book Details"
        book.description = "Catalog data for a book"
        book.impl = ""
        book.icon = "file:///Users/d/work/personal/darqos/darq/icons/book.png"
        self.types.add_type(book)
        self.types.append_pinned_type("Book Details")

        person = Type()
        person.name = "Person"
        person.description = "A human being"
        person.impl = ""
        person.icon = "file:///Users/d/work/personal/darqos/darq/icons/person.png"
        self.types.add_type(person)
        return

    def on_create(self):
        o = TextTypeView()
        self.hide()
        return


class ObjectSelector(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        # Set size & position.
        screen = QScreen.availableGeometry(QApplication.primaryScreen())
        self.resize(int(screen.width() * 0.7), int(screen.height() * 0.7))
        self.move(int(screen.width() * 0.15), int(screen.height() * 0.15))

        # Set window type.
        flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.WindowStaysOnTopHint |
                                     QtCore.Qt.WindowType.FramelessWindowHint)
        self.setWindowFlags(flags)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)

        self.omnibox = QHBoxLayout()
        self.omnibox.setContentsMargins(20, 20, 20, 20)
        self.omnitext = QLineEdit()
        self.omnitext.setStyleSheet("QLineEdit { font-size: 20px; padding: 12px; border: none; border-radius: 10px; }")
        self.omnitext.setFrame(False)  # Doesn't seem to do anything'
        self.omnitext.setAttribute(QtCore.Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.omnitext.setPlaceholderText("Search ...")
        self.omnitext.setClearButtonEnabled(True)
        self.omnitext.setTextMargins(20, 20, 20, 20)
        self.omnibox.addWidget(self.omnitext)
        self.layout.addLayout(self.omnibox)

        # Here I'm going to start with the list of objects from
        # the history service.  This will need to be completely
        # rejigged in future.

        # So, a table ... date/time, event, type, details of object.
        #
        # The object details will be the tricky bit: objects don't
        # have to have a name or anything.  Perhaps it'd be good for
        # the type implementation to have a method to get a description
        # of the object in a type-specific way?

        self.object_table = QTableWidget(10, 4, self)
        self.object_table.setHorizontalHeaderLabels(("Date / Time", "Type", "Event", "Object"))
        self.object_table.verticalHeader().setVisible(False)
        self.object_table.setContentsMargins(20, 20, 20, 20)
        for r in range(10):
            for c in (0, 1, 2, 3):
                item = QTableWidgetItem()
                item.setText(["datetime", "type", "event", "description of object"][c])
                self.object_table.setItem(r, c, item)
        self.layout.addWidget(self.object_table)

        self.setLayout(self.layout)
        self.hide()

        self.history = History.api()
        return

    def show(self):

        # FIXME: needs a lot more work here ...
        # Populate history list
        self.object_table.clearContents()

        now = datetime.utcnow()
        history = self.history.get_events(now, 100, True)

        r = 0
        for event in history:
            for c in range(4):
                item = QTableWidgetItem()
                item.setText([event[0], "type", event[2], event[1]][c])
                self.object_table.setItem(r, c, item)
            r += 1

        super().show()
        return

    def hide(self):
        super().hide()
        return

    def toggle_visibility(self):
        """Show if hidden; hide if shown"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
        return


class Overlay(QMainWindow):
    def __init__(self, *args):
        super().__init__(*args)

        box = QWidget()
        self.setCentralWidget(box)

        hbox = QHBoxLayout()
        box.setLayout(hbox)
        hbox.addStretch(1)
        group_login = QWidget()
        hbox.addWidget(group_login)
        hbox.addStretch(1)

        vbox = QVBoxLayout()
        group_login.setLayout(vbox)
        vbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        username = QLineEdit()
        username.setPlaceholderText("username")
        vbox.addWidget(username)

        password = QLineEdit()
        password.setPlaceholderText("password")
        password.setEchoMode(QLineEdit.EchoMode.Password)
        vbox.addWidget(password)

        login = QPushButton("login", self)
        login.clicked.connect(self.on_login)
        login.setDefault(True)
        vbox.addWidget(login)

        # Tab order for mouse-less login.
        #self.setTabOrder(username, password)
        #self.setTabOrder(password, login)

        flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.WindowStaysOnTopHint |
                                     QtCore.Qt.WindowType.FramelessWindowHint)
        self.setWindowFlags(flags)
        self.showFullScreen()
        return

    def on_login(self):
        print("Login")


class UI(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        # Create new objects with Sys-n
        self.factory = ObjectFactory()
        self.factory_shortcut = QShortcut(QKeySequence("Ctrl+n"), self)
        self.factory_shortcut.setContext(
            QtCore.Qt.ShortcutContext.ApplicationShortcut)
        self.factory_shortcut.activated.connect(self.on_factory)

        # Select an existing object with Sys-s
        self.selector = ObjectSelector()
        self.selector_shortcut = QShortcut(QKeySequence("Ctrl+s"), self)
        self.selector_shortcut.setContext(
            QtCore.Qt.ShortcutContext.ApplicationShortcut)
        self.selector_shortcut.activated.connect(self.on_selector)

        # Background window.
        flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.WindowStaysOnTopHint |
                                     QtCore.Qt.WindowType.FramelessWindowHint)
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

        action = m.exec(self.mapToGlobal(event.pos()))
        if action == new_action:
            self.on_factory()
        elif action == find_action:
            self.on_selector()
        elif action == quit_action:
            QApplication.quit()

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
        self.selector.toggle_visibility()
        return

    def on_login(self):
        print("Login")


def main():
    app = QApplication(sys.argv)
    ui = UI()
    #login = Overlay()
    sys.exit(app.exec())


print("main")
if __name__ == "__main__":
    main()
