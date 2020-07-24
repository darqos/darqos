
from typing import Optional

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QMenu, QShortcut, qApp, QVBoxLayout
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent

from darq.rt.storage import Storage
from darq.rt.history import History, Events
from darq.type.base import Object

import sys
import uuid

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
# there's no point have virtual keys, when you have a keyboard!  So better
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


class Calculation(Object):
    pass


class CalculationView(QWidget):
    pass
