#

from typing import Optional

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QMenu, QShortcut, qApp, QVBoxLayout
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent

from darq.rt.storage import Storage
from darq.rt.history import History, Event

import sys
import uuid


class Object:
    """Base class for types."""

    def __init__(self):
        return

    @staticmethod
    def new():
        """Create a new instance of this type."""
        return


class Text(Object):
    """This is a basic text class.  It does not support multiple
    fonts, styles, images, etc.

    So, at one level, this could just be a basic array of Unicode
    code points.  With simple accessors, perhaps a cursor position?
    Helper functions like, line skipping, word-by-word navigation,
    etc.

    Can it support bidi text?  Would that require some metadata to
    identify either a language (with implicit left-to-right/right-to-left
    semantics) or an explicit layout tag?

    What about Asian docs with top-to-bottom layout?

    Does it need to have explicit line structure?  Or is an ASCII CRLF
    sufficient?

    I think it makes sense to store the document as an ordered collection
    of lines, each of which is an ordered collection of words.  I think
    that's universal?  Apparently not: eg, Chinese and Japanese segment
    sentences, Vietnamese segments syllables, European languages
    segment words.  Unicode tech report 14 deals with line break, and
    TR 29 with character and word breaks.

    What about documents with mixed languages?  Especially those with two
    (or more) different presentation styles: eg. right-to-left quotes in
    a left-to-right document, or mixed horizontal and vertical layout?

    I think it's probably a good idea to put that sort of thing into a
    more complex document type?


    These concerns should be addressed for both presentation (read) and
    also editing (write) of such documents.  Editing should include
    features like undo/redo which also impact on the chosen structure.

    The API will need to be careful to distinguish between bytes (if it
    actually exposes them?), code points, graphemes, etc.  There's going
    to be a lot of Unicode algorithm work here.
    """

    def __init__(self):
        """Create a new Text instance."""
        super().__init__()
        self.point = 0
        self.mark = None
        self.text = ''
        return

    @staticmethod
    def new():
        return Text()

    @staticmethod
    def load(oid: str):
        """Load a Text object from storage."""
        # get bytes from storage
        text = Text.from_bytes()
        return text

    @staticmethod
    def from_bytes(buffer: bytes):
        """Create a Text object from serialized bytes.

        :param buffer: Byte buffer."""
        text = Text()
        text.text = buffer.decode()
        return text

    def to_bytes(self) -> bytes:
        """Return a serialized form of the Text object."""
        return self.text.encode()

    @staticmethod
    def type():
        return "text"

    def set_point(self, position: int) -> int:
        """Set the point to an absolute position.

        :param position: Offset from start of text, in characters
        :returns: new position

        The point is always addressed by its position, which is counted
        in User Perceived Characters (aka grapheme clusters).  This means
        it's not bytes, and it's not codepoints: a codepoint, followed
        by a combining character codepoint, such that there's a single
        character (grapheme cluster) perceived by the user, is considered
        indivisible and singular by the point.

        As an example, LATIN SMALL LETTER E WITH ACUTE (é), can be
        represented as a single pre-combined character U+00e9, or as
        U+0065, u+0301 being a sequence of LATIN SMALL LETTER E and
        COMBINING ACUTE ACCENT."""
        self.point = position
        return self.point

    def get_point(self) -> int:
        """Returns the current position of the point."""
        return self.point

    def set_mark(self, position: int = None):
        """Set the mark to an obsolute position.

        :param position: Offset from start of text, in characters."""
        self.mark = position
        return

    def get_mark(self) -> Optional[int]:
        """Return the current position of the mark.

        :returns:  Offset of mark from start of text, or None if not set."""
        return self.mark

    def exchange_point_and_mark(self):
        """Set the point from the mark, and the mark from the point."""
        tmp = self.mark
        self.mark = self.point
        self.point = tmp
        return

    def shift_point_backward(self, positions: int = 1) -> int:
        """Shift the point towards the start of the text.

        :param positions: Number of characters to move, default is one
        :returns: new position

        Moves the point one character (grapheme cluster) towards the
        beginning of the text."""
        self.point -= positions
        return self.point

    def shift_point_forward(self, positions: int = 1) -> int:
        """Shift the point towards the end of the text.

        :param positions: Number of characters to move, default is one
        :returns: new point position

        Moves the point one character (grapheme cluster) towards the
        end of the text."""

        self.point += positions
        return self.point

    def insert_before_point(self, text: str) -> None:

        return

    def delete_region(self, start: Optional[int], end: Optional[int]) -> None:
        """Delete a region of text.

        :param start: Position of start, or start of document if None.
        :param end: Position of end, or end of document if None.

        If both @p start and @p end are set, delete all characters
        between the two positions, and set the point between the
        preceding and following characters.

        If not set, @p start will default to the start of the Text, and
        @p end will default to the end of the Text.  Thus, if neither is
        set, delete all the text.
        """

        if start is None:
            start = 0

        if end is None:
            end = len(
                self.text)  ## FIXME: needs to be normalized Unicode length

        del self.text[start:end]
        return

    def delete_and_extract_region(self, start: int, end: int) -> "Text":

        if start is None:
            start = 0

        if end is None:
            end = len(
                self.text)  ## FIXME: needs to be normalized Unicode length

        text = Text()
        text.text = self.text[start:end]
        del self.text[start:end]
        return text

    def delete_forward_char(self, count: int = 1) -> None:
        """Deletes characters at point.

        :param count: Number of characters to delete"""
        del self.text[self.point:self.point + count]
        return

    def delete_backward_char(self, count: int = 1) -> None:
        del self.text[self.point - count: self.point] # FIXME: off by 1?
        return

    def delete_horizonal_space(self) -> None:
        """Delete all whitespace around point."""
        return

    def just_one_space(self):
        """Replace all whitespace around point with a single space."""
        return

    def delete_blank_lines(self) -> None:
        """Delete blanks lines around pooint.

        If point is on a blank line with one or more blank lines before
        or after it, then all but one of them are deleted.  If point is
        on an isolated blank line, this it is deleted.  If point is on a
        non-blank line, delete all blank lines immediately following it."""
        return

    def delete_trailing_whitespace(self, start: int = None, end: int = None) -> None:
        """Delete trailing whitespace in region.

        :param start:
        :param end:
        :returns:

        If the mark is inactive, or @p end is @c None, delete empty
        trailing lines at the end of the text also."""
        return

    def transpose_character(self):
        """Exchange the characters immediately before and after point."""
        tmp = self.text[self.point]
        self.text[self.point] = self.text[self.point - 1]
        self.text[self.point] = tmp
        return

    def mark_word(self):
        return

    def mark_paragraph(self):
        return

    def mark_whole_buffer(self):
        self.point = 0
        self.mark = len(self.text)  # FIXME: need to be normalized unicode length
        return


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

        action = m.exec_(self.mapToGlobal(event.pos()))
        if action == close_action:
            self.parent.close()


########################################################################

class TextTypeView(QWidget):
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
                self.history.add_event(self.url, Event.VIEWED)
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
        self.flags = QtCore.Qt.WindowFlags(QtCore.Qt.FramelessWindowHint)
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
            self.history.add_event(self.url, Events.MODIFIED)

        super().close()
        return


def main():
    url = None
    if len(sys.argv) > 1:
        url = sys.argv[1]

    app = QApplication(sys.argv)
    text = TextTypeView(url)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()