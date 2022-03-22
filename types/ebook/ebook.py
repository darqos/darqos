
import sys
import uuid

from darq.type.base import Object
from darq.rt.storage import Storage
from darq.rt.history import History, Events

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QGridLayout
from PyQt5 import QtCore


class Book(Object):

    def __init__(self):
        """Constructor."""
        super().__init__()
        return

    @staticmethod
    def new():
        return Book()

    @staticmethod
    def from_bytes(buffer: bytes) -> "Book":
        book = Book()
        #FIXME
        return book


class BookView(QWidget):

    def __init__(self, url: str = None):
        """Constructor."""

        self.storage = Storage.api()
        self.history = History.api()

        # Load content.
        if url is not None:
            self.url = url  # FIXME: parsing?  volumes?  etc

            if self.storage.exists(self.url):
                buf = self.storage.get(self.url)
                self.text = Book.from_bytes(buf)
                self.history.add_event(self.url, Events.VIEWED)
            else:
                self.storage.set(self.url, b'')
                self.text = Book.new()
                self.history.add_event(self.url, Events.CREATED)
        else:
            self.url = str(uuid.uuid4())
            self.storage.set(self.url, b'')
            self.text = Book.new()
            self.history.add_event(self.url, Events.CREATED)

        super().__init__()

        # Draw a basic viewer/editor for book info.
        self.resize(1024, 768)
        self.move(300, 300)

        flags = QtCore.Qt.WindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowFlags(flags)

        layout = QGridLayout()
        layout.setContentsMargins(50, 50, 50, 50)

        return


def main():
    url = None
    if len(sys.argv) > 1:
        url = sys.argv[1]

    app = QApplication(sys.argv)
    book = BookView(url)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
