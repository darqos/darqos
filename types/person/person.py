# DarqOS
# Copyright (C) 2018-2022 David Arnold

from typing import Optional

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QTextEdit, QMenu, QShortcut, qApp, QVBoxLayout
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent

from darq.rt.storage import Storage
from darq.rt.history import History, Event
from darq.rt.object import Object

import sys
import uuid


class Person(Object):
    """This is a UI for person objects.

    It is most closely related to an Address Book or Contacts application,
    but it doesn't support display of groups or organisations, because
    they are different types in Darq.

    Unlike a typical address book, in Darq, the history of changes is
    remembered, and things have dates.  So, for instance, if you live at
    one address for a while, and then move to a new location, both these
    addresses will persist in the system, with start and end dates making
    it apparent which is current.

    Additionally, and also unlike most address books, the entries are not
    independent, but record information about the linkages from the
    subject person to other entities: locations (addresses), companies
    (employment roles), other people (families, assistants, etc).

    All person data is stored using the system's Knowledge Base service,
    which provides a means to store information about entities, including
    links between them, in a generic and extensible way.

    A Person may have the following pre-configured attribute types:
    - Names, including titles and honorifics
    - Contact details: phone numbers, IM identifiers, emails, etc
    - Various types of locations: work, homes, etc
    - Relationships: spouse, children, parents, etc
    - Dates: birthday, marriage anniversaries, deaths
    - Employment records
    - Financial accounts: bank account numbers, PayPal IDs, etc
    - Educational history
    - etc

    This application is used to create, view, and edit person records.
    One or more related services are responsible for synchronising
    information about people with the KB service.  They deal with:
    - CardDAV (eg. Google Contacts, Apple Contacts)
    - Outlook Contacts
    - LinkedIn
    - Facebook
    - Ancestry.com
    - General GEDCOM genealogical data
    - etc

    See also the related applications for Organisations, Locations, and
    Groups.

    When this application is started, it requires an identifier for a
    Person object.  That identifier must be resolved (via the system
    runtime) to a specific entity within a specific Knowledgebase
    Service instance, accessed via a specific protocol.

    In the short term, this should be a TCP host:port and identifier,
    and accessible using Cap'n'Proto over TCP.

    Actions this lens should support are: new, view, edit, print, etc.

    """

    def __init__(self):
        """Create a new Person instance."""
        super().__init__()

        self._id = None
        self._full_name = ""
        self._birth_timestamp = None
        self._death_timestamp = None
        self._emails = []

        return

    @staticmethod
    def new():
        return Person()

    @staticmethod
    def load(oid: str):
        """Load a Person object from storage."""
        # get bytes from storage
        person = Person.from_bytes()
        return person

    @staticmethod
    def from_bytes(buffer: bytes):
        """Create a Person object from serialized bytes.

        :param buffer: Byte buffer."""
        person = Person()
        #person.text = buffer.decode()
        return person

    def to_bytes(self) -> bytes:
        """Return a serialized form of the Text object."""
        return b''
        #return self.text.encode()

    @staticmethod
    def type():
        return "person"


########################################################################

class PersonTypeView(QWidget):
    """GUI person viewer."""

    def __init__(self, url: str = None):
        """Constructor."""

        # Initiate connection to runtime services.
        # FIXME: self.kb = KB.api()
        # FIXME: self.history = History.api()
        # FIXME: self.index = Index.api()

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
            # Generate identifier for new object.
            self.url = str(uuid.uuid4())

            # Create placeholder in KB.
            self.person = Person.new()
            #self.storage.set(self.url, b'')

            # Record creation event.
            #self.history.add_event(self.url, Event.CREATED)

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
        self.flags = QtCore.Qt.WindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowFlags(self.flags)

        # Draw text view inside frame.
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(50, 50, 50, 50)

        self.full_name = QLabel()
        self.full_name.setText("Full Name")
        self.layout.addWidget(self.full_name)

        self.setLayout(self.layout)
        self.show()

    def mousePressEvent(self, a0: QMouseEvent) -> None:
        print("Mouse Press Event: " + str(a0.pos()))
        self._drag_start = a0.pos()
        a0.ignore()

    def mouseReleaseEvent(self, a0: QMouseEvent) -> None:
        self._move_window(a0.pos())
        a0.ignore()

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

        action = m.exec_(self.mapToGlobal(event.pos()))
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
    ui = PersonTypeView(url)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
