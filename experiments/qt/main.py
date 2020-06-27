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
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5 import QtCore


def main():

    app = QApplication(sys.argv)

    # Background
    w = QWidget()
    #w.resize(250, 150)
    #w.move(300, 300)
    w.showMaximized()
    w.setWindowTitle('darq')
    w.show()

    # Object selector.
    w_os = QWidget()
    w_os.resize(1024, 768)
    w_os.move(300, 300)
    w_os.setWindowTitle('New')

    # Remove title bar, traffic lights, etc.
    flags = QtCore.Qt.WindowFlags(
        QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
    w_os.setWindowFlags(flags)

    w_os.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
