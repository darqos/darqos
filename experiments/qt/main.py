#!/usr/bin/python

"""
ZetCode PyQt5 tutorial

In this example, we create a simple
window in PyQt5.

Author: Jan Bodnar
Website: zetcode.com
"""

import sys
import PyQt5
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QTextEdit, QMenu, QShortcut, qApp
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent


class DarqTextEdit(QTextEdit):
    def __init__(self, parent, *args):
        super().__init__(*args)
        self.parent = parent

        self.setStyleSheet("QTextEdit { background-color: #323232; border-style: none; font-size: 18px; }")
        self.setAcceptRichText(True)
        c = QColor()
        c.setRgb(123, 187, 236, 255)
        self.setTextColor(c)
        return

    def contextMenuEvent(self, event):
        m = self.createStandardContextMenu()
        m.addSeparator()
        close_action = m.addAction("Close")

        action = m.exec_(self.mapToGlobal(event.pos()))
        if action == close_action:
            self.parent.close()


class TextTypeView(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        # Window placement.
        self._drag_start = None

        self.resize(1024, 768)
        self.move(300, 300)
        self.setWindowTitle('New')

        # Remove title bar, traffic lights, etc.
        flags = QtCore.Qt.WindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowFlags(flags)

        # Draw text view.
        e = DarqTextEdit(self, self)
        e.resize(924, 668)
        e.move(50, 50)

        # Install mouse event handler.

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
        m.addAction("Other")
        m.addAction("Stuff")
        m.addAction("Goes")
        m.addAction("Here")
        m.addSeparator()
        quit_action = m.addAction("Close")

        action = m.exec_(self.mapToGlobal(event.pos()))
        if action == quit_action:
            self.hide()
        return


class ObjectFactory(QWidget):
    def __init__(self, *args):
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



class ObjectSelector(QWidget):
    def __init__(self, *args):
        super().__init__(*args)
        self.init_ui()
        return

    def init_ui(self):
        return


class UI(QWidget):
    def __init__(self, *args):
        super().__init__(*args)

        self.factory = ObjectFactory()
        self.factory_shortcut = QShortcut(QKeySequence("Ctrl+n"), self)
        self.factory_shortcut.activated.connect(self.on_factory)

        self.selector = ObjectSelector()
        self.selector_shortcut = QShortcut(QKeySequence("Ctrl+s"), self)
        self.selector_shortcut.activated.connect(self.on_selector)

        flags = QtCore.Qt.WindowFlags(QtCore.Qt.WindowStaysOnBottomHint)
        self.setWindowFlags(flags)

        self.showFullScreen()
        return

    def contextMenuEvent(self, event):
        m = QMenu()
        m.addSeparator()
        quit_action = m.addAction("Shutdown")

        action = m.exec_(self.mapToGlobal(event.pos()))
        if action == quit_action:
            qApp.quit()

    def on_factory(self, *args):
        print("Factory: " + str(args))

        if self.factory.isVisible():
            self.factory.hide()
        else:
            self.factory.show()
        return

    def on_selector(self, *args):
        print("Selector: " + str(args))

        if self.selector.isVisible():
            self.selector.hide()
        else:
            self.selector.show()
        return


def main():

    app = QApplication(sys.argv)
    ui = UI()
    #x = TextTypeView(app)
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
