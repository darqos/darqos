# darqos
# Copyright (C) 2018-2026 David Arnold

# Text lens: a window for using a public.utf8-plain-text item, in the
# Terminal context.
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  It edits the
# item through the Text protocol (lib/didl/text.didl), sending each
# change as it's made, so it holds nothing unsaved: the implementation
# saves it.  It exits when its window is closed.
#
# Its thread is Qt's: the lens port is served on Qt's event loop (see
# darq.Server.attach), and its calls to the item and History are
# synchronous, on queued reply ports.
#
# Editing keys follow Emacs (see BINDINGS, and F1 for help).

import logging
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QAction, QKeyEvent, QMouseEvent, QTextCursor
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QPlainTextEdit, QVBoxLayout, QWidget

import darq
import darq.history
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.protocols.text import Status as TextStatus
from darq.protocols.text import Text


# History source for the events it records.
SOURCE = "text-lens"

# Seconds to wait for the item's replies.
TIMEOUT = 10.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier
Move = QTextCursor.MoveOperation

# Editing keys: (Ctrl, Alt, key) to a LensEdit method, and its help.
# Alt is Emacs's Meta.  C-x starts a two-key command (see PREFIXED).
BINDINGS: dict[tuple[bool, bool, Key], tuple[str, str]] = {
    (True, False, Key.Key_F): ("forward_char", "Forward a character"),
    (True, False, Key.Key_B): ("backward_char", "Back a character"),
    (True, False, Key.Key_N): ("next_line", "Next line"),
    (True, False, Key.Key_P): ("previous_line", "Previous line"),
    (True, False, Key.Key_A): ("line_start", "Start of line"),
    (True, False, Key.Key_E): ("line_end", "End of line"),
    (False, True, Key.Key_F): ("forward_word", "Forward a word"),
    (False, True, Key.Key_B): ("backward_word", "Back a word"),
    (False, True, Key.Key_Less): ("text_start", "Start of text"),
    (False, True, Key.Key_Greater): ("text_end", "End of text"),
    (True, False, Key.Key_V): ("page_down", "Down a page"),
    (False, True, Key.Key_V): ("page_up", "Up a page"),
    (True, False, Key.Key_D): ("delete_char", "Delete a character"),
    (False, True, Key.Key_D): ("kill_word", "Kill to the end of the word"),
    (False, True, Key.Key_U): ("upcase_word", "Upper-case to the end of the word"),
    (False, True, Key.Key_L): ("downcase_word", "Lower-case to the end of the word"),
    (False, True, Key.Key_C): ("capitalize_word", "Capitalize to the end of the word"),
    (True, False, Key.Key_K): ("kill_line", "Kill to the end of the line"),
    (True, False, Key.Key_Space): ("set_mark", "Set the mark"),
    (True, False, Key.Key_G): ("cancel", "Cancel the mark"),
    (True, False, Key.Key_W): ("kill_region", "Kill the region"),
    (False, True, Key.Key_W): ("copy_region", "Copy the region"),
    (True, False, Key.Key_Y): ("yank", "Yank (paste) the last kill"),
    (True, False, Key.Key_Slash): ("undo", "Undo"),
    (True, False, Key.Key_Underscore): ("undo", "Undo"),
}

# Commands after C-x: (Ctrl, key) to a method, and its help.
PREFIXED: dict[tuple[bool, Key], tuple[str, str]] = {
    (True, Key.Key_Q): ("toggle_read_only", "Toggle read-only"),
    (True, Key.Key_C): ("close_window", "Close"),
}

# Other keys, with no modifiers.
PLAIN: dict[Key, tuple[str, str]] = {
    Key.Key_F1: ("toggle_help", "Show or hide this help"),
    Key.Key_F11: ("toggle_full_screen", "Full screen"),
}


def common_affixes(old: str, new: str) -> tuple[int, int, int]:
    """Return where two strings differ: the length of their common
    prefix, and the ends, in each, of the parts that differ.

    So new is old with old[start:old_end] replaced by
    new[start:new_end]."""
    limit = min(len(old), len(new))
    start = 0
    while start < limit and old[start] == new[start]:
        start += 1
    suffix = 0
    while suffix < limit - start and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return start, len(old) - suffix, len(new) - suffix


def capitalize(text: str) -> str:
    """Return text with its first letter or digit in title case, and
    the rest in lower case, as Emacs's M-c does to a word."""
    for index, c in enumerate(text):
        if c.isalnum():
            return text[:index] + c.title() + text[index + 1:].lower()
    return text


def utf16_length(text: str) -> int:
    """Return the length of a string in UTF-16 code units, as Qt counts."""
    return len(text) + sum(1 for c in text if c > "￿")


def index_from_utf16(text: str, offset: int) -> int:
    """Return the index of a code point in a string, from its UTF-16 offset."""
    units = 0
    for index, c in enumerate(text):
        if units >= offset:
            return index
        units += 2 if c > "￿" else 1
    return len(text)


class TextSync:
    """Keeps an item's text in step with the editor's.

    It holds the text as the item has it, and its version, and turns
    each change to the editor's text into a Replace.  It uses no Qt, so
    it can be tested without a display."""

    # Results of change().
    OK = "ok"
    CONFLICT = "conflict"
    FAILED = "failed"

    def __init__(self, client: Text.Client, item_id: str, text: str, version: int,
                 history: History.Client | None = None):
        self.client = client
        self.item_id = item_id
        self.text = text
        self.version = version
        self.history = history

        # False once the item has stopped answering.
        self.available = True

    def change(self, new: str) -> str:
        """Send a change to the item, given the editor's new text.

        :returns: OK if it's made; CONFLICT if the item had been changed
                  by another client, in which case the text has been
                  reloaded; FAILED if the item doesn't answer."""
        if new == self.text:
            return self.OK
        start, old_end, new_end = common_affixes(self.text, new)
        try:
            response = self.client.replace(start=start, end=old_end, text=new[start:new_end],
                                           expected=self.version)
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't change {self.item_id}: {e!r}")
            self.available = False
            return self.FAILED

        if response.status != TextStatus.OK:
            logging.info(f"Change to {self.item_id} refused: {response.status.name}: reloading")
            return self.CONFLICT if self.reload() else self.FAILED

        self.text, self.version = new, response.version
        self.record(darq.history.EDITED)
        return self.OK

    def reload(self) -> bool:
        """Get the item's text again.

        :returns: False if it doesn't answer."""
        try:
            response = self.client.get()
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't get {self.item_id}: {e!r}")
            self.available = False
            return False
        self.text, self.version = response.text, response.version
        return True

    def record(self, event: str):
        """Record a History event about the item, if History is running."""
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=self.item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {self.item_id}: {e!r}")

    def close(self):
        """Close the item, once finished with it."""
        if not self.available:
            return
        self.available = False
        try:
            self.client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close {self.item_id}: {e!r}")


class LensEdit(QPlainTextEdit):
    """The editor: a plain text widget, with Emacs keys.

    Killed text goes to the clipboard, which is the kill buffer."""

    def __init__(self, window: "TextWindow"):
        super().__init__(window)
        self.window_ = window
        self.setStyleSheet("QPlainTextEdit {"
                           " background-color: #323232;"
                           " color: #7bbbec;"
                           " border-style: none;"
                           " font-size: 18px;"
                           "}")

        # Movement extends the selection from the mark, while it's set.
        self.mark_active = False

        # Waiting for the second key of a C-x command.
        self.prefix = False

    def keyPressEvent(self, e: QKeyEvent | None):
        if e is None:
            return
        modifiers = e.modifiers()
        ctrl = bool(modifiers & Modifier.ControlModifier)
        alt = bool(modifiers & Modifier.AltModifier)
        key = Key(e.key())

        if self.prefix:
            self.prefix = False
            entry = PREFIXED.get((ctrl, key))
            if entry is None:
                self.window_.show_status("C-x: not a command")
            else:
                getattr(self, entry[0])()
            return
        if (ctrl, alt, key) == (True, False, Key.Key_X):
            self.prefix = True
            return

        entry = BINDINGS.get((ctrl, alt, key)) or (PLAIN.get(key) if not (ctrl or alt) else None)
        if entry is not None:
            getattr(self, entry[0])()
            return

        # Anything else is Qt's: typing, arrows, Backspace, and so on.
        # Typing ends the region.
        if e.text() and not ctrl and not alt:
            self.mark_active = False
        super().keyPressEvent(e)

    # Movement

    def move_cursor(self, operation: Move, count: int = 1):
        cursor = self.textCursor()
        mode = QTextCursor.MoveMode.KeepAnchor if self.mark_active else QTextCursor.MoveMode.MoveAnchor
        cursor.movePosition(operation, mode, count)
        self.setTextCursor(cursor)

    def forward_char(self):
        self.move_cursor(Move.NextCharacter)

    def backward_char(self):
        self.move_cursor(Move.PreviousCharacter)

    def next_line(self):
        self.move_cursor(Move.Down)

    def previous_line(self):
        self.move_cursor(Move.Up)

    def line_start(self):
        self.move_cursor(Move.StartOfBlock)

    def line_end(self):
        self.move_cursor(Move.EndOfBlock)

    def forward_word(self):
        # To the end of this word, or the next.
        position = self.textCursor().position()
        self.move_cursor(Move.EndOfWord)
        if self.textCursor().position() == position:
            self.move_cursor(Move.NextWord)
            self.move_cursor(Move.EndOfWord)

    def backward_word(self):
        self.move_cursor(Move.PreviousWord)

    def text_start(self):
        self.move_cursor(Move.Start)

    def text_end(self):
        self.move_cursor(Move.End)

    def page_lines(self) -> int:
        viewport = self.viewport()
        height = viewport.height() if viewport is not None else 0
        return max(1, height // max(1, self.fontMetrics().lineSpacing()) - 2)

    def page_down(self):
        self.move_cursor(Move.Down, self.page_lines())

    def page_up(self):
        self.move_cursor(Move.Up, self.page_lines())

    # Editing

    def editable(self) -> bool:
        """Return True if the text can be changed, else say why not."""
        if self.isReadOnly():
            self.window_.show_status("Read only")
            return False
        return True

    def delete_char(self):
        if self.editable():
            self.textCursor().deleteChar()

    def kill(self, cursor: QTextCursor):
        """Kill a cursor's selection, to the clipboard."""
        if not self.editable() or not cursor.hasSelection():
            return
        self.copy_selection(cursor)
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        self.mark_active = False

    def word_cursor(self) -> QTextCursor:
        """Return a cursor selecting from the cursor to the end of this
        word, or the next, as forward_word moves."""
        cursor = self.textCursor()
        cursor.clearSelection()
        position = cursor.position()
        cursor.movePosition(Move.EndOfWord, QTextCursor.MoveMode.KeepAnchor)
        if cursor.position() == position:
            cursor.movePosition(Move.NextWord, QTextCursor.MoveMode.KeepAnchor)
            cursor.movePosition(Move.EndOfWord, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def kill_word(self):
        self.kill(self.word_cursor())

    def convert_word(self, convert: typing.Callable[[str], str]):
        """Convert the text to the end of the word, leaving the cursor
        after it."""
        if not self.editable():
            return
        cursor = self.word_cursor()
        # The word may run on from a line break: keep it a line break.
        text = cursor.selectedText().replace("\u2029", "\n")
        new = convert(text)
        if new != text:
            cursor.insertText(new)
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.mark_active = False

    def upcase_word(self):
        self.convert_word(str.upper)

    def downcase_word(self):
        self.convert_word(str.lower)

    def capitalize_word(self):
        self.convert_word(capitalize)

    def kill_line(self):
        # To the end of the line, or the line break, if it's there.
        cursor = self.textCursor()
        cursor.clearSelection()
        operation = Move.NextCharacter if cursor.atBlockEnd() else Move.EndOfBlock
        cursor.movePosition(operation, QTextCursor.MoveMode.KeepAnchor)
        self.kill(cursor)

    def set_mark(self):
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.mark_active = True
        self.window_.show_status("Mark set")

    def cancel(self):
        self.prefix = False
        self.mark_active = False
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.window_.show_status("")

    def kill_region(self):
        self.kill(self.textCursor())

    def copy_selection(self, cursor: QTextCursor):
        # Qt separates paragraphs with U+2029: the clipboard wants "\n".
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(cursor.selectedText().replace(" ", "\n"))

    def copy_region(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            self.copy_selection(cursor)
        self.cancel()

    def yank(self):
        clipboard = QApplication.clipboard()
        if self.editable() and clipboard is not None:
            self.mark_active = False
            self.textCursor().insertText(clipboard.text())

    # Undo is QPlainTextEdit's own, which also reverses the region.

    # Window

    def toggle_read_only(self):
        self.window_.set_read_only(not self.isReadOnly())

    def close_window(self):
        self.window_.close()

    def toggle_help(self):
        self.window_.toggle_help()

    def toggle_full_screen(self):
        self.window_.toggle_full_screen()


def help_text() -> str:
    """The key bindings, for the help overlay."""
    def name(ctrl: bool, alt: bool, key: Key) -> str:
        label = {Key.Key_Space: "SPC", Key.Key_Less: "<", Key.Key_Greater: ">",
                 Key.Key_Slash: "/", Key.Key_Underscore: "_"}.get(key, key.name.removeprefix("Key_").lower())
        return ("C-" if ctrl else "") + ("M-" if alt else "") + label

    lines = [f"{name(*k):10} {help}" for k, (_, help) in BINDINGS.items() if k[2] != Key.Key_Underscore]
    lines += [f"{'C-x ' + name(ctrl, False, key):10} {help}" for (ctrl, key), (_, help) in PREFIXED.items()]
    lines += [f"{key.name.removeprefix('Key_'):10} {help}" for key, (_, help) in PLAIN.items()]
    return "\n".join(lines)


class TextWindow(QWidget):
    """A frameless window showing an item's text, with a status line.

    Drag its margin to move it."""

    def __init__(self, sync: TextSync, on_closed=None):
        super().__init__()
        self.sync = sync
        self.on_closed = on_closed

        # Set while the editor's text is changed here, not by typing.
        self._loading = False

        self.setWindowTitle(sync.item_id)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("TextWindow { background-color: #323232; }")
        self.resize(1024, 768)

        layout = QVBoxLayout()
        layout.setContentsMargins(50, 50, 50, 20)
        self.edit = LensEdit(self)
        layout.addWidget(self.edit)
        self.status = QLabel(self)
        self.status.setStyleSheet("QLabel { color: #9a9a9a; font-size: 14px; }")
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(help_text(), self)
        self.help.setStyleSheet("QLabel { background-color: rgba(20, 20, 20, 230); color: #e0e0e0;"
                                " font-family: monospace; font-size: 15px; padding: 24px; }")
        self.help.hide()

        document = self.edit.document()
        assert document is not None
        document.contentsChanged.connect(self.on_changed)
        self.load(sync.text)

    def document_text(self) -> str:
        """The editor's text, as the item would have it.

        Qt separates paragraphs with U+2029; its plain text would also
        replace non-breaking spaces, so the raw text is used instead."""
        document = self.edit.document()
        assert document is not None
        return document.toRawText().replace(" ", "\n")

    def load(self, text: str, cursor_index: int | None = None):
        """Show the item's text, keeping the cursor near where it was."""
        self._loading = True
        try:
            self.edit.setPlainText(text)
        finally:
            self._loading = False

        if cursor_index is not None:
            cursor = self.edit.textCursor()
            index = min(cursor_index, len(text))
            cursor.setPosition(utf16_length(text[:index]))
            self.edit.setTextCursor(cursor)

        if self.document_text() != text:
            # The editor can't hold it exactly (a U+2029, say): editing
            # it would change what it can't show.
            self.set_read_only(True, "Read only: the text has characters this lens can't edit")

    def on_changed(self):
        """Send a change to the item."""
        if self._loading or not self.sync.available:
            return
        result = self.sync.change(self.document_text())
        if result == TextSync.CONFLICT:
            index = index_from_utf16(self.document_text(), self.edit.textCursor().position())
            self.load(self.sync.text, index)
            self.show_status("Changed elsewhere: reloaded")
        elif result == TextSync.FAILED:
            self.set_read_only(True, "Read only: the item isn't answering")

    def set_read_only(self, read_only: bool, why: str | None = None):
        if not read_only and not self.sync.available:
            self.show_status("Read only: the item isn't answering")
            return
        self.edit.setReadOnly(read_only)
        self.show_status(why or ("Read only" if read_only else ""))

    def show_status(self, message: str):
        self.status.setText(message)

    def toggle_help(self):
        if self.help.isVisible():
            self.help.hide()
        else:
            self.help.adjustSize()
            self.help.move(max(0, (self.width() - self.help.width()) // 2),
                           max(0, (self.height() - self.help.height()) // 2))
            self.help.raise_()
            self.help.show()

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def mousePressEvent(self, a0: QMouseEvent | None):
        # Dragging the margin moves the window: the compositor does it,
        # since a Wayland client can't place itself.
        handle = self.windowHandle()
        if a0 is not None and a0.button() == QtCore.Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def contextMenuEvent(self, a0):
        if a0 is None:
            return
        menu = QMenu(self)
        full_screen = QAction("Full screen", menu)
        full_screen.setCheckable(True)
        full_screen.setChecked(self.isFullScreen())
        full_screen.triggered.connect(self.toggle_full_screen)
        read_only = QAction("Read only", menu)
        read_only.setCheckable(True)
        read_only.setChecked(self.edit.isReadOnly())
        read_only.triggered.connect(lambda checked: self.set_read_only(checked))
        menu.addAction(full_screen)
        menu.addAction(read_only)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        menu.exec(a0.globalPos())

    def closeEvent(self, a0):
        self.sync.close()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, None
            on_closed()


class TextLens(Lens.Handler):
    """Answers the Lens protocol: uses one item, in a window."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None):
        self.loop = loop
        self.history = history
        self.window: TextWindow | None = None

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        client = Text.Client(request.item, timeout=TIMEOUT)
        try:
            response = client.get()
        except (darq.DarqError, TimeoutError) as e:
            logging.error(f"Can't get {request.item_id}: {e!r}")
            return UseResponse(status=Status.FAILED)

        sync = TextSync(client, request.item_id, response.text, response.version, self.history)
        self.window = TextWindow(sync, on_closed=self.finished)
        self.window.show()
        sync.record(darq.history.OPENED)
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def expire(self):
        """Give up, if no Use has come."""
        if self.window is None:
            logging.error(f"Not asked to Use an item within {USE_TIMEOUT}s: exiting")
            self.finished()

    def finished(self):
        self.loop.stop()


def darq_main():
    bootstrap = darq.get_bootstrap_port()
    if bootstrap is None:
        logging.error("No bootstrap port: a lens is started for an item, by the Terminal")
        return 1

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    loop = QtEventLoop()

    # History is best effort: the lens works without it.
    handle = darq.lookup_name("darq.history", blocking=False)
    history = History.Client(handle) if handle is not None else None

    lens = TextLens(loop, history)
    port = darq.open_port()
    server = darq.Server()
    server.add(port, lens)
    server.attach(loop)
    server.call_later(USE_TIMEOUT, lens.expire)
    darq.send(bootstrap, Ready(lens=port))

    loop.run()
    loop.close()
    del app
    return 0
