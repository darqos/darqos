# DarqOS
# Copyright (C) 2018-2022 David Arnold

from typing import Optional

from PyQt6.QtWidgets import QApplication, QWidget, QTextEdit, QMenu, QVBoxLayout
from PyQt6 import QtCore
from PyQt6.QtGui import QColor, QKeySequence, QMouseEvent, QShortcut

from darq.rt.storage import Storage
from darq.rt.history import History, Event
from darq.lens import Lens

import sys
import uuid


########################################################################

class DarqTextEdit(QTextEdit):
    """TextEdit control with altered style and extended context menu."""

    def __init__(self, parent):
        """Constructor."""

        super().__init__()
        self.parent = parent

        self.setStyleSheet("QTextEdit {"
                           " background-color: #323232;"
                           " border-style: none;"
                           " font-size: 18px; "
                           "}")
        self.setAcceptRichText(True)
        c = QColor()
        c.setRgb(123, 187, 236, 255)
        self.setTextColor(c)
        return

    def contextMenuEvent(self, event):
        """Extend standard context menu."""

        m = self.createStandardContextMenu()
        m.addSeparator()
        close_action = m.addAction("Close")

        action = m.exec(self.mapToGlobal(event.pos()))
        if action == close_action:
            self.parent.close()


########################################################################

class GuiTextLens(QWidget, Lens):
    """GUI text type."""

    def __init__(self, url: str = None):
        """Constructor."""

        self.storage = Storage.api()
        self.history = History.api()

        # Load content.
        if url is not None:
            self.url = url  # FIXME: parsing?  volumes?  etc

            if self.storage.exists(self.url):
                buf = self.storage.get(self.url)
                self.text = Text.from_bytes(buf)
                self.history.add_event(self.url, Event.READ)
            else:
                self.storage.set(self.url, b'')
                self.text = Text.new()
                self.history.add_event(self.url, Event.CREATED)
        else:
            self.url = str(uuid.uuid4())
            self.storage.set(self.url, b'')
            self.text = Text.new()
            self.history.add_event(self.url, Event.CREATED)

        # Window placement.
        self._drag_start = None
        self._fullscreen = False

        # Create view.
        super().__init__()

        # Default size.
        self.resize(1024, 768)

        # Default position.
        self.move(300, 300)
        self.setWindowTitle('New')

        # Remove title bar, traffic lights, etc.
        self.flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setWindowFlags(self.flags)

        # Draw text view inside frame.
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)
        self.edit = DarqTextEdit(self)
        self.edit.append(self.text.text)
        self.layout.addWidget(self.edit)

        self.setLayout(self.layout)
        self.show()
        return

    def mousePressEvent(self, a0: QMouseEvent) -> None:
        print("Mouse Press Event: " + str(a0.pos()))
        self._drag_start = a0.pos()
        a0.ignore()
        return

    def mouseReleaseEvent(self, a0: QMouseEvent) -> None:
        self._move_window(a0.pos())
        a0.ignore()
        return

    def mouseDoubleClickEvent(self, a0: QMouseEvent) -> None:
        a0.ignore()
        return

    def mouseMoveEvent(self, a0: QMouseEvent) -> None:
        print("Mouse Move Event")
        print("event pos = " + str(a0.pos()))
        print("window pos = " + str(self.pos()))
        print("Event x & y = " + str(a0.x()) + " " + str(a0.y()))

        self._move_window(a0.pos())
        a0.ignore()
        return

    def _move_window(self, drag_pos):
        delta_x = drag_pos.x() - self._drag_start.x()
        delta_y = drag_pos.y() - self._drag_start.y()

        self.move(self.pos().x() + delta_x, self.pos().y() + delta_y)
        return

    def contextMenuEvent(self, event):
        m = QMenu()
        fullscreen_action = m.addAction("Full screen")
        m.addAction("Show metadata")
        m.addSeparator()
        quit_action = m.addAction("Close")

        action = m.exec(self.mapToGlobal(event.pos()))
        if action == quit_action:
            self.close()
        elif action == fullscreen_action:
            self.toggle_fullscreen()
        return

    def toggle_fullscreen(self):
        if self._fullscreen:
            self.showNormal()
            self._fullscreen = False
        else:
            self.showFullScreen()
            self._fullscreen = True
        return

    def close(self):
        if self.edit.document().isModified():
            text = self.edit.toPlainText()
            self.storage.update(self.url, text.encode())
            self.history.add_event(self.url, Event.MODIFIED)

        super().close()
        return


def main():
    url = None
    if len(sys.argv) > 1:
        url = sys.argv[1]

    app = QApplication(sys.argv)
    text = TextTypeView(url)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
