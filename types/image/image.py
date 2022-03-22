# Copyright (C) 2021 David Arnold

import sys
import typing
import uuid

from darq.type.base import Object
from darq.rt.storage import Storage
from darq.rt.history import History, Event

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QGridLayout
from PyQt5 import QtCore


class Image(Object):

    def __init__(self):
        """Constructor."""
        super().__init__()
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
        return tuple(-1, -1)

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





class ImageView(QWidget):

    def __init__(self, url: str = None):
        """Constructor."""

        self.storage = Storage.api()
        self.history = History.api()

        # Load content.
        if url is not None:
            self.url = url  # FIXME: parsing?  volumes?  etc

            if self.storage.exists(self.url):
                buf = self.storage.get(self.url)
                self.image = Image.from_bytes(buf)
                self.history.add_event(self.url, Event.VIEWED)
            else:
                self.storage.set(self.url, b'')
                self.image = Image.new()
                self.history.add_event(self.url, Event.CREATED)
        else:
            self.url = str(uuid.uuid4())
            self.storage.set(self.url, b'')
            self.image = Image.new()
            self.history.add_event(self.url, Event.CREATED)

        super().__init__()

        # Draw a basic viewer/editor for an image.
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
    book = ImageView(url)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
