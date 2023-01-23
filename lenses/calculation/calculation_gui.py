#! /usr/bin/env python
# darqos
# Copyright (C) 2022 David Arnold


from typing import Optional

from PyQt6.QtWidgets import QApplication, QWidget, QTextEdit, QMenu, QVBoxLayout
from PyQt6 import QtCore, QtGui
# from PyQt6.QtGui import QColor, QKeySequence, QMouseEvent

# from darq.rt.storage import Storage
# from darq.rt.history import History, Events
# from darq.type.base import Object
from darq.runtime.lens import Lens

import sys
# import uuid


# The type of instances created by a calculator.  They're persistent,
# so you can come back to them in future, look at them, modify them, etc.

# The format of this item should basically reflect a record of the
# operations carried out when using the calculator, modified to make it
# reasonable to refer to previous results, etc, without using a "memory"
# slot.  $n would probably work?
#
# So the UI is basically a scrolling history tape.  Intermediate results
# are annotated with a $n identifier for subsequent reuse.  Up-arrow and
# down-arrow should probably scroll back through history.
#
# It'd be nice to add a little graphing function: almost a mini
# spreadsheet, since it'd need a table to drive a graph.

# The UI should probably look more like a notebook than a calculator:
# there's no point having virtual keys, when you have a keyboard!  So better
# to show a cursor, a current line, and maybe results with identifiers.
#
#  1 + 1
#  = 2  ($1)
#  0 / 2
#  = 5  ($2)
#  $2 * $1
#  = 10  ($3)
#  ost(pi)
# = -1.0  ($4)
#
# Really the question is whether to make that just plain text, or to try
# to write it on a canvas that gives richer display options.  Text would
# likely be enough to start.
#
# And then persistence: I think I can just save the whole thing as a text
# document?  Parsing it should be trivial.

# Things I'd like to add:
# - math quantities (pi, e, etc)
# - trig functions, etc.
# - units (the Unix database) (try pip 'pint'?)
# - Physical constants (avogadro's, etc, etc)
# - Physical quantities (mass of Earth, orbits of planets, speed of light, etc)
#   - Include a nice UI to look these up, with text descriptions, etc
#     - Should these be in the KB as WikiData-like entities?
# - graphing (matplotlib?)
# - maybe symbolic math, like sympy?


class KeyMapping:

    def __init__(self):
        self.shift = False
        self.control = False
        self.option = False
        self.command = False

        self.windows = False
        self.menu = False

        self.system = False
        self.application = False


class MyTextEdit(QTextEdit):
    """Intercept keystrokes, and do our own translation."""

    def event(self, event):
        et = event.type()

        if et == QtCore.QEvent.Type.KeyPress:
            print(f"KeyPress\n"
                  f"  key {hex(event.key())}\n"
                  f"  native mods {hex(event.nativeModifiers())}\n"
                  # not on macOS f"  native scan {hex(event.nativeScanCode())}\n"
                  f"  native virt {hex(event.nativeVirtualKey())}\n"
                  f"  text {event.text()}\n"
                  f"  count {event.count()}\n"
                  f"  Mods: {event.modifiers()}\n"
                  f"  Combo: {event.keyCombination()}")

            # Track modifier key-down events.

            return True

        elif et == QtCore.QEvent.Type.KeyRelease:
            print(f"KeyRelease\n  key = {hex(event.key())}")
            return True

        return super().event(event)


class CalculationView(QWidget, Lens):

    def __init__(self):
        super().__init__()

        self.flags = QtCore.Qt.WindowType(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setWindowFlags(self.flags)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)
        self.edit = MyTextEdit(self)
        self.layout.addWidget(self. edit)
        self.setLayout(self.layout)
        self.show()
        return


def main():

    app = QApplication(sys.argv)
    calc = CalculationView()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
