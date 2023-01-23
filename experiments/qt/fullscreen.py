#! /usr/bin/env python

import sys

#from PyQt5.QtWidgets import QApplication, QWidget
#from PyQt5 import QtCore

#from PySide2.QtWidgets import QApplication, QWidget
#from PySide2 import QtCore

#from PyQt6.QtWidgets import QApplication, QWidget
#from PyQt6 import QtCore

from PySide6.QtWidgets import QApplication, QWidget
from PySide6 import QtCore

import qdarktheme


def main():

    app = QApplication(sys.argv)
    qdarktheme.setup_theme()

    w = QWidget()
    w.setWindowTitle("Test Window")

    flags = QtCore.Qt.WindowType.WindowStaysOnBottomHint
    w.setWindowFlags(flags)

    w.showFullScreen()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
