# darqos
# Copyright (C) 2021-2022 David Arnold

import sys
import typing
import uuid

import darq

from darq.rt.object import Object
from darq.rt.storage import Storage
from darq.rt.history import History, Event

from PyQt6.QtWidgets import QApplication, QWidget, QTextEdit, QGridLayout
from PyQt6 import QtCore


class Image(Object):

    #
    def __init__(self):
        """Constructor."""
        super().__init__()

        self._format = None
        self._data = b''
        return

    @staticmethod
    def new():
        return Image()

    @staticmethod
    def from_bytes(buffer: bytes) -> "Image":
        image = Image()
        #FIXME
        return image

    def get_size() -> typing.Tuple[int, int]:
        return -1, -1

    def crop(self, left, top, right, bottom):
        return

    def scale(self, x, y):
        return

    def rotate(self, angle):
        return

    def flip(self, around_x_axis):
        return

    def set_brightness(self, brightness):
        return

    def set_contrast(self, contrast):
        return

    def get_format(self):
        return

    def get_pixel(self, x: int, y: int):
        return

    def get_data(self):
        return


class ImageView(QWidget):

    def __init__(self, url: str = None):
        """Constructor."""

        # Load content.
        if url is not None:
            self.url = url  # FIXME: parsing?  volumes?  etc

            if darq.storage.exists(self.url):
                buf = darq.storage.get(self.url)
                self.image = Image.from_bytes(buf)
                darq.history.add_event(self.url, Event.VIEWED)
            else:
                darq.storage.set(self.url, b'')
                self.image = Image.new()
                darq.history.add_event(self.url, Event.CREATED)
        else:
            self.url = str(uuid.uuid4())
            darq.storage.set(self.url, b'')
            self.image = Image.new()
            darq.history.add_event(self.url, Event.CREATED)

        super().__init__()

        # Draw a basic viewer/editor for an image.
        self.resize(1024, 768)
        self.move(300, 300)

        flags = QtCore.Qt.WindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowFlags(flags)

        layout = QGridLayout()
        layout.setContentsMargins(50, 50, 50, 50)

        return


class ImageTypeImplementation:

    # Should start, and serve a control interface.
    # Control interface should support:
    # - ping
    # - shutdown
    # - create
    # - view
    # - edit
    # - print
    # - copy (?)
    # It should use the standard RPC facilities from the base OS.
    #
    # Creating a new object should be a request to the type manager,
    # which will pass it on to the type implementation (this thing),
    # which will allocate the object and return the OID.
    #
    # I need to figure out the
    def __init__(self):
        pass


def main():
    url = None
    if len(sys.argv) > 1:
        url = sys.argv[1]

    app = QApplication(sys.argv)
    book = ImageView(url)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
