# darqos
# Copyright (C) 2026 David Arnold

# The REPL: Python, with the darq API, a cell at a time, in a window, as
# a DarqOS process.  It's a tool (see tool.json), started from the
# Tools panel, or with Sys-s, which focuses its window if it's running:
# there's one session.  Its engine is darq.repl.  See doc/repl.rst.
#
# - Pygments syntax highlighting, of the input and the transcript, in the
#   DarqOS palette (darq.ui.style), as the lenses use it.
# - jedi completion against the live namespace: Tab, or after a '.'.
# - Results are numbered: $3 refers to cell 3's result.
# - On the left, a tree of what's bound, to browse, and edit: clicking a
#   name edits its source in the editor, and Ctrl+S saves it, changing
#   what exists in place (see darq.repl); Escape goes back to the cell.
#   What's not saved is kept as a draft, marked in the tree.
# - darq, and darq.terminal, as terminal, are ready,
#   and loop, its event loop, for terminal.watch.
#
# Keys: Enter runs a complete cell (else inserts a newline), Shift+Enter
# always inserts a newline, Ctrl+Enter always runs, Ctrl+A goes to the
# start of the line, as in Emacs, rather than selecting all, Up/Down on the first
# or last line step through history, Ctrl+L clears the transcript, Ctrl+Q
# quits, ending the session.
#
# Cells run on Qt's thread: a long one, or a slow call, holds the window
# till it's done.

import inspect
import pprint
import re
import sys
import typing

from pygments.lexers import PythonLexer
from pygments.token import Token, _TokenType
from PyQt6.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QIcon, QKeyEvent, QKeySequence, QPainter, QPalette, QPixmap, QShortcut,
                         QSyntaxHighlighter, QTextCharFormat, QTextCursor)
from PyQt6.QtWidgets import (QApplication, QCompleter, QHBoxLayout, QLabel, QMainWindow, QMenu, QPlainTextEdit,
                             QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from darq.loop import QtEventLoop
from darq.repl import CLASS, FUNCTION, MODULE, PROPERTY, VARIABLE, Engine, Result, Results, Target, kind_of
from darq.ui import style
from darq.ui.style import CONTROLS, SCROLL_BARS

# Its windows' Wayland app_id, by which the Terminal finds its window,
# for Sys-s.
APP_ID = "darq.repl"

INDENT = "    "


# ----------------------------------------------------------------------
# Highlighting

DOLLAR = Token.Name.Variable.Magic


def lex_spans(text: str) -> list[tuple[int, int, _TokenType]]:
    """Pygments token spans: (start, end, token type)."""
    spans = []
    after_dollar = False
    for i, ttype, value in PythonLexer().get_tokens_unprocessed(text):
        if value == "$":
            ttype, after_dollar = DOLLAR, True
        elif after_dollar and ttype in Token.Number:
            after_dollar = False
            digits = re.match(r"\d+", value)
            if digits:
                spans.append((i, i + digits.end(), DOLLAR))
                i += digits.end()
                value = value[digits.end():]
        else:
            after_dollar = False
        if value:
            spans.append((i, i + len(value), ttype))
    return spans


# Token colours, from the Darq palette.  A token type without an entry
# takes its nearest ancestor's: (colour, bold, italic).
TOKEN_COLOURS: dict[_TokenType, tuple[str, bool, bool]] = {
    Token: (style.TEXT, False, False),
    Token.Keyword: (style.ACCENT, False, False),
    Token.Keyword.Constant: (style.TEXT_STRONG, False, False),
    Token.String: (style.LINK, False, False),
    Token.Comment: (style.FAINT, False, True),
    Token.Number: (style.TEXT_STRONG, False, False),
    Token.Error: (style.ERROR, False, False),
    Token.Generic.Prompt: (style.TEXT_DIM, False, False),
    Token.Generic.Output: (style.TEXT_SOFT, False, False),
    Token.Generic.Error: (style.ERROR, False, False),
    Token.Generic.Traceback: (style.ERROR, False, False),
    DOLLAR: (style.ACCENT, True, False),
}



def token_colour(ttype: _TokenType) -> tuple[str, bool, bool]:
    """A token type's (colour, bold, italic), from its nearest ancestor."""
    t: _TokenType | None = ttype
    while t is not None:
        if t in TOKEN_COLOURS:
            return TOKEN_COLOURS[t]
        t = t.parent
    return TOKEN_COLOURS[Token]


FONT = "font-family: monospace; font-size: 15px;"

# The window, after the lenses' CONTROLS: the transcript on the
# background, and the editor a field, where you type.
STYLESHEET = CONTROLS + f"""
Window, QSplitter {{ background-color: {style.SURFACE}; }}
QSplitter::handle {{ background-color: {style.BORDER}; }}
QPlainTextEdit {{ color: {style.TEXT}; border: none; padding: 6px; {FONT}
                  selection-background-color: {style.SELECTION};
                  selection-color: {style.TEXT_STRONG}; }}
Transcript {{ background-color: {style.BACKGROUND}; }}
Editor {{ background-color: {style.FIELD}; }}
Editor[readOnly="true"] {{ background-color: {style.RAISED}; }}
QTreeWidget {{ color: {style.TEXT}; background-color: {style.BACKGROUND}; border: none; {FONT}
               selection-background-color: {style.SELECTION}; selection-color: {style.TEXT_STRONG}; }}
QHeaderView::section {{ color: {style.TEXT_DIM}; background-color: {style.SURFACE}; border: none;
                        padding: 2px 6px; }}
#Header {{ background-color: {style.RAISED}; }}
#Header QLabel {{ color: {style.TEXT_SOFT}; }}
#Header QToolButton {{ color: {style.TEXT}; background: transparent; border: none; padding: 0 6px; }}
"""

# The completion popup's a top-level window, so it doesn't inherit the
# window's stylesheet: it's styled like a QMenu.
POPUP = f"""
QListView {{ color: {style.TEXT}; background-color: {style.RAISED};
             border: 1px solid {style.BORDER}; {FONT}
             selection-background-color: {style.SELECTION};
             selection-color: {style.TEXT_STRONG}; }}
""" + SCROLL_BARS


class Theme:
    """Token types, as Qt text formats."""

    def __init__(self) -> None:
        self._formats: dict[_TokenType, QTextCharFormat] = {}

    def format(self, ttype: _TokenType) -> QTextCharFormat:
        fmt = self._formats.get(ttype)
        if fmt is None:
            colour, bold, italic = token_colour(ttype)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            if bold:
                fmt.setFontWeight(700)
            fmt.setFontItalic(italic)
            self._formats[ttype] = fmt
        return fmt

    def apply(self, widget: QPlainTextEdit) -> None:
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText,
                         QColor(style.TEXT_DIM))
        widget.setPalette(palette)


class Highlighter(QSyntaxHighlighter):
    """Highlights a whole document, lexed as one.

    Lexing the whole text gets multi-line strings right.  A block's
    state is 1 if a token runs on past its end, so changing that
    rehighlights the following blocks."""

    def __init__(self, widget: QPlainTextEdit, theme: Theme) -> None:
        super().__init__(widget.document())
        self.theme = theme
        self._text: str | None = None
        self._spans: list[tuple[int, int, _TokenType]] = []

    def highlightBlock(self, text: str | None) -> None:
        doc = self.document()
        if doc is None or text is None:
            return
        full = doc.toPlainText()
        if full != self._text:
            self._text = full
            self._spans = lex_spans(full)
        start = self.currentBlock().position()
        end = start + len(text)
        state = 0
        for s, e, ttype in self._spans:
            if e <= start or s >= end:
                continue
            a, b = max(s, start), min(e, end)
            self.setFormat(a - start, b - a, self.theme.format(ttype))
            if e > end + 1:
                state = 1
        self.setCurrentBlockState(state)


# ----------------------------------------------------------------------
# Widgets

class Transcript(QPlainTextEdit):
    """The read-only record of cells and their results."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setReadOnly(True)
        theme.apply(self)

    def _cursor(self) -> QTextCursor:
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        return c

    def _insert_code(self, c: QTextCursor, text: str, indent: str) -> None:
        for s, e, ttype in lex_spans(text):
            fmt = self.theme.format(ttype)
            for i, piece in enumerate(text[s:e].split("\n")):
                if i:
                    c.insertText("\n" + indent, self.theme.format(Token.Text))
                if piece:
                    c.insertText(piece, fmt)

    def _new_block(self, c: QTextCursor) -> None:
        if not self.document().isEmpty():   # type: ignore[union-attr]
            c.insertText("\n", self.theme.format(Token.Text))

    def add_text(self, text: str, ttype: _TokenType = Token.Generic.Output
                 ) -> None:
        c = self._cursor()
        self._new_block(c)
        c.insertText(text.rstrip("\n"), self.theme.format(ttype))
        self.ensureCursorVisible()

    def add_result(self, source: str, result: Result) -> None:
        c = self._cursor()
        self._new_block(c)
        prompt = f"[{result.number}] "
        c.insertText(prompt, self.theme.format(Token.Generic.Prompt))
        if result.edited:
            # A save: what it edited, then its source.
            c.insertText(f"# edit {result.edited}", self.theme.format(Token.Comment))
            c.insertText("\n" + " " * len(prompt), self.theme.format(Token.Text))
        self._insert_code(c, source.rstrip("\n"), " " * len(prompt))
        if result.note:
            self.add_text(result.note, Token.Comment)
        if result.stdout:
            self.add_text(result.stdout)
        if result.stderr:
            self.add_text(result.stderr, Token.Generic.Error)
        if result.error:
            self.add_text(result.error, Token.Generic.Traceback)
        if result.has_value:
            c = self._cursor()
            self._new_block(c)
            label = f"${result.number}"
            c.insertText(label, self.theme.format(DOLLAR))
            c.insertText(" = ", self.theme.format(Token.Operator))
            try:
                text = pprint.pformat(result.value, width=100)
            except Exception as e:
                text = f"<repr failed: {e!r}>"
            self._insert_code(c, text, " " * (len(label) + 3))
        self.ensureCursorVisible()


class Editor(QPlainTextEdit):
    """The input editor: highlighting, completion and history."""

    submitted = pyqtSignal(str)

    # In editing mode, editing a target, not a cell: Ctrl+S saves, and
    # Escape goes back to the cell.
    saved = pyqtSignal()
    escaped = pyqtSignal()

    def __init__(self, engine: Engine, theme: Theme) -> None:
        super().__init__()
        self.engine = engine
        theme.apply(self)
        self.highlighter = Highlighter(self, theme)
        self.setPlaceholderText(
            "Enter runs · Shift+Enter newline · Ctrl+Enter forces run · "
            "Tab completes · $n is cell n's result")
        self.setTabChangesFocus(False)

        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.activated.connect(self.insert_completion)
        popup = self.completer.popup()
        if popup is not None:
            popup.setStyleSheet(POPUP)
        self._prefix = 0

        self.history: list[str] = []
        self._hpos = 0
        self._draft = ""

        # Whether it's editing a target, rather than a cell.
        self.editing = False

    # Completion

    def _before_cursor(self) -> str:
        return self.toPlainText()[:self.textCursor().position()]

    def complete(self, auto: bool) -> None:
        popup = self.completer.popup()
        assert popup is not None
        names, prefix = self.engine.complete(self._before_cursor())
        if not names:
            popup.hide()
            return
        self._prefix = prefix
        if not auto and len(names) == 1:
            self.insert_completion(names[0])
            return
        self.model.setStringList(names)
        popup.setCurrentIndex(self.model.index(0, 0))
        rect = self.cursorRect()
        sb = popup.verticalScrollBar()
        rect.setWidth(popup.sizeHintForColumn(0)
                      + (sb.sizeHint().width() if sb else 0))
        self.completer.complete(rect)

    def insert_completion(self, name: str) -> None:
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.Left,
                       QTextCursor.MoveMode.KeepAnchor, self._prefix)
        c.insertText(name)
        self.setTextCursor(c)
        popup = self.completer.popup()
        if popup is not None:
            popup.hide()

    # History

    def _set_text(self, text: str, at_end: bool) -> None:
        self.setPlainText(text)
        c = self.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End if at_end
                       else QTextCursor.MoveOperation.Start)
        if not at_end:
            c.movePosition(QTextCursor.MoveOperation.EndOfLine)
        self.setTextCursor(c)

    def history_back(self) -> None:
        if self._hpos == 0:
            return
        if self._hpos == len(self.history):
            self._draft = self.toPlainText()
        self._hpos -= 1
        self._set_text(self.history[self._hpos], at_end=True)

    def history_forward(self) -> None:
        if self._hpos == len(self.history):
            return
        self._hpos += 1
        text = (self._draft if self._hpos == len(self.history)
                else self.history[self._hpos])
        self._set_text(text, at_end=False)

    # Editing

    def _should_run(self) -> bool:
        text = self.toPlainText()
        if not text.strip():
            return False
        lines = text.split("\n")
        if len(lines) > 1:
            if not self.textCursor().atEnd():
                return False
            if not lines[-1].strip():
                return True
            if lines[-1][:1].isspace():
                return False    # In a block: a blank line ends it.
        return self.engine.is_complete(text)

    def newline(self) -> None:
        line = self.textCursor().block().text()
        line = line[:self.textCursor().positionInBlock()]
        indent = line[:len(line) - len(line.lstrip())]
        if line.rstrip().endswith(":"):
            indent += INDENT
        self.insertPlainText("\n" + indent)

    def run(self) -> None:
        source = self.toPlainText().rstrip()
        if not source.strip():
            return
        if not self.history or self.history[-1] != source:
            self.history.append(source)
        self._hpos = len(self.history)
        self._draft = ""
        self.clear()
        self.submitted.emit(source)

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        if e is None:
            return
        K = Qt.Key
        key, mods = e.key(), e.modifiers()
        popup = self.completer.popup()
        popup_open = popup is not None and popup.isVisible()

        if popup_open and key in (K.Key_Return, K.Key_Enter, K.Key_Escape,
                                  K.Key_Tab, K.Key_Backtab):
            e.ignore()      # The completer handles these.
            return

        if self.editing:
            if key == K.Key_S and mods == Qt.KeyboardModifier.ControlModifier:
                self.saved.emit()
                return
            if key == K.Key_Escape:
                self.escaped.emit()
                return
            if key in (K.Key_Return, K.Key_Enter):
                if not self.isReadOnly():
                    self.newline()
                return

        if key in (K.Key_Return, K.Key_Enter):
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.run()
            elif (not mods & Qt.KeyboardModifier.ShiftModifier
                  and self._should_run()):
                self.run()
            else:
                self.newline()
            return

        # Ctrl+A goes to the start of the line, as in Emacs: the line as
        # typed, not as it's wrapped.
        if key == K.Key_A and mods == Qt.KeyboardModifier.ControlModifier:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            self.setTextCursor(cursor)
            return

        if key == K.Key_Tab and not mods:
            line = self.textCursor().block().text()
            line = line[:self.textCursor().positionInBlock()]
            if line.strip():
                self.complete(auto=False)
            else:
                self.insertPlainText(INDENT)
            return

        cursor = self.textCursor()
        doc = self.document()
        if key == K.Key_Up and not popup_open and cursor.blockNumber() == 0 and not self.editing:
            self.history_back()
            return
        if (key == K.Key_Down and not popup_open and doc is not None and not self.editing
                and cursor.blockNumber() == doc.blockCount() - 1):
            self.history_forward()
            return

        super().keyPressEvent(e)

        typed = e.text()
        if typed == ".":
            self.complete(auto=True)
        elif popup_open:
            if (typed and (typed.isalnum() or typed == "_")) \
                    or key == K.Key_Backspace:
                self.complete(auto=True)
            elif typed:
                assert popup is not None
                popup.hide()


# ----------------------------------------------------------------------
# The namespace tree

# Kinds' badges: a letter, and its colour.
BADGES = {
    MODULE: ("M", style.ACCENT),
    CLASS: ("C", style.LINK),
    FUNCTION: ("ƒ", style.TEXT_STRONG),
    PROPERTY: ("P", style.TEXT_SOFT),
    VARIABLE: ("v", style.TEXT_DIM),
}

# What marks a target with an unsaved draft.
DRAFT_MARK = " •"

# Where an item's target, if it has one, is kept.
TARGET_ROLE = Qt.ItemDataRole.UserRole


def label_of(target: Target) -> str:
    """A target, as it's named to the operator."""
    return target.label if target.name else f"a new member of {'.'.join(target.path[:-1])}"


def badge(kind: str) -> QIcon:
    """A kind's badge: its letter, on its colour."""
    letter, colour = BADGES[kind]
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colour))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 16, 16, 3, 3)
    painter.setPen(QColor(style.BACKGROUND))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(11)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return QIcon(pixmap)


class NamespaceTree(QTreeWidget):
    """What's bound, where: the session's names, the results, the
    built-ins, and the drafts, each read only when it's expanded."""

    chosen = pyqtSignal(object)

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setHeaderLabels(["Name", "Type"])
        self.setColumnWidth(0, 170)
        self.setIndentation(14)
        self.private = False
        self.drafts: dict[tuple[int, str], tuple[Target, str]] = {}
        self.icons = {kind: badge(kind) for kind in BADGES}
        self.itemExpanded.connect(self.read_members)
        self.itemClicked.connect(self.choose)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)

        # Asked of the window, for the context menu: tests replace them.
        self.actions_for: typing.Callable[[Target], list[tuple[str, typing.Callable[[], None]]]] = \
            lambda target: []
        self.refresh()

    def tops(self) -> list[tuple[str, Target]]:
        session = self.engine.session()
        return [("Session", session),
                ("Results", Target(self.engine.namespace, "_out", ("Results",))),
                ("Built-ins", self.engine.builtins())]

    def refresh(self) -> None:
        """Read it all again, keeping what's expanded, and selected."""
        expanded = {self.path_of(item) for item in self.walk() if item.isExpanded()}
        if not expanded:
            expanded = {("Session",)}
        selected = {self.path_of(item) for item in self.selectedItems()}
        self.clear()
        if self.drafts:
            drafts = QTreeWidgetItem(self, ["Drafts", ""])
            for target, _ in sorted(self.drafts.values(), key=lambda d: d[0].label):
                item = QTreeWidgetItem(drafts, [label_of(target) + DRAFT_MARK, ""])
                item.setData(0, TARGET_ROLE, target)
            drafts.setExpanded(True)
        for name, target in self.tops():
            item = QTreeWidgetItem(self, [name, ""])
            item.setData(0, TARGET_ROLE, target)
            QTreeWidgetItem(item, [""])
        for item in list(self.walk()):
            self.restore(item, expanded, selected)

    def restore(self, item: QTreeWidgetItem, expanded: set, selected: set) -> None:
        path = self.path_of(item)
        if path in selected:
            item.setSelected(True)
        if path in expanded:
            item.setExpanded(True)
            for i in range(item.childCount()):
                child = item.child(i)
                if child is not None:
                    self.restore(child, expanded, selected)

    def walk(self) -> typing.Iterator[QTreeWidgetItem]:
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def path_of(self, item: QTreeWidgetItem | None) -> tuple[str, ...]:
        names = []
        while item is not None:
            names.append(item.text(0).removesuffix(DRAFT_MARK))
            item = item.parent()
        return tuple(reversed(names))

    def read_members(self, item: QTreeWidgetItem) -> None:
        """Read what's in it, the first time it's expanded: till then, it
        has a blank child, so it can be."""
        placeholder = item.child(0) if item.childCount() == 1 else None
        if placeholder is None or placeholder.data(0, TARGET_ROLE) is not None or placeholder.text(0):
            return
        item.takeChild(0)
        target = item.data(0, TARGET_ROLE)
        try:
            members = self.engine.members(target, self.private)
        except Exception as e:
            QTreeWidgetItem(item, [f"Can't read it: {e}", ""])
            return
        for binding in members:
            child = QTreeWidgetItem(item, [self.name_of(binding.target), binding.type_name])
            child.setIcon(0, self.icons[binding.kind])
            child.setData(0, TARGET_ROLE, binding.target)
            child.setForeground(1, QColor(style.TEXT_DIM))
            if binding.expandable:
                QTreeWidgetItem(child, [""])

    def name_of(self, target: Target) -> str:
        return target.name + (DRAFT_MARK if target.key() in self.drafts else "")

    def choose(self, item: QTreeWidgetItem, column: int) -> None:
        target = item.data(0, TARGET_ROLE)
        if isinstance(target, Target) and target.name:
            self.chosen.emit(target)

    def menu(self, point) -> None:
        item = self.itemAt(point)
        menu = QMenu(self)
        target = item.data(0, TARGET_ROLE) if item is not None else None
        if isinstance(target, Target):
            for text, action in self.actions_for(target):
                menu.addAction(text, action)
            menu.addSeparator()
        private = menu.addAction("Show private names")
        assert private is not None
        private.setCheckable(True)
        private.setChecked(self.private)
        private.toggled.connect(self.show_private)
        menu.exec(self.mapToGlobal(point))

    def show_private(self, shown: bool) -> None:
        self.private = shown
        self.refresh()


class Window(QMainWindow):
    def __init__(self, engine: Engine | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Python REPL")
        self.resize(1160, 700)
        self.setStyleSheet(STYLESHEET)
        theme = Theme()
        self.engine = engine or Engine()
        self.transcript = Transcript(theme)
        self.editor = Editor(self.engine, theme)
        self.editor.submitted.connect(self.on_submitted)
        self.editor.saved.connect(self.save)
        self.editor.escaped.connect(self.back_to_cell)

        # What the editor's editing: a target, or, if it's None, a cell;
        # the source it was given, to tell a draft; the cell's draft,
        # while it's elsewhere; and whether it's a class whole, to read.
        self.target: Target | None = None
        self.loaded = ""
        self.cell_draft = ""
        self.reading = False

        # Which target is edited, and how to go back.
        self.header = QWidget()
        self.header.setObjectName("Header")
        bar = QHBoxLayout(self.header)
        bar.setContentsMargins(8, 2, 2, 2)
        self.heading = QLabel()
        bar.addWidget(self.heading, 1)
        close = QToolButton()
        close.setText("×")
        close.setToolTip("Back to the cell (Escape)")
        close.clicked.connect(self.back_to_cell)
        bar.addWidget(close)
        self.header.hide()
        below = QWidget()
        column = QVBoxLayout(below)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.header)
        column.addWidget(self.editor)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.transcript)
        right.addWidget(below)
        right.setSizes([520, 180])

        self.transcript.add_text(f"Python {sys.version.split()[0]}", Token.Comment)
        self.transcript.add_text(self.engine.start(), Token.Comment)
        self.tree = NamespaceTree(self.engine)
        self.tree.chosen.connect(self.edit)
        self.tree.actions_for = self.actions_for
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right)
        splitter.setSizes([260, 900])
        self.setCentralWidget(splitter)

        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.transcript.clear)
        self.editor.setFocus()

    def on_submitted(self, source: str) -> None:
        self.transcript.add_result(source, self.engine.run(source))
        self.tree.refresh()

    # Editing a target

    def edit(self, target: Target, text: str | None = None, reading: bool = False) -> None:
        """Edit a target, or, for a library's, or a class whole, read
        it: with its draft, if it has one."""
        self.stash()
        if text is None:
            source = self.engine.source_of(target)
            text, editable, why = source.text, source.editable, source.why
        else:
            editable, why = not reading, "The whole class is read: edit its header and members"
        self.target = target
        self.reading = reading
        self.loaded = text
        draft = self.tree.drafts.get(target.key())
        self.editor.editing = True
        self.editor.setReadOnly(not editable)
        self.editor.setPlainText(draft[1] if draft is not None and editable else text)
        verb = "Editing" if editable else "Viewing"
        self.heading.setText(f"{verb}: {label_of(target)}" + ("" if editable else f"  ({why})"))
        self.header.show()
        self.editor.setFocus()

    def stash(self) -> None:
        """Keep what's in the editor: the cell's, or a target's draft."""
        if self.target is None:
            self.cell_draft = self.editor.toPlainText()
            return
        if self.editor.isReadOnly() or self.reading:
            return
        text = self.editor.toPlainText()
        if text != self.loaded:
            self.tree.drafts[self.target.key()] = (self.target, text)
        else:
            self.tree.drafts.pop(self.target.key(), None)

    def back_to_cell(self) -> None:
        if self.target is None:
            return
        self.stash()
        self.target = None
        self.reading = False
        self.editor.editing = False
        self.editor.setReadOnly(False)
        self.editor.setPlainText(self.cell_draft)
        self.header.hide()
        self.tree.refresh()
        self.editor.setFocus()

    def save(self) -> None:
        """Save the target, as a cell: it's shown, and the draft's gone."""
        target = self.target
        if target is None or self.editor.isReadOnly() or self.reading:
            return
        text = self.editor.toPlainText()
        self.done(text, self.engine.save(target, text))
        if self.transcript_error is None:
            self.tree.drafts.pop(target.key(), None)
            if target.name:
                self.loaded = self.engine.source_of(target).text
                self.editor.setPlainText(self.loaded)
            else:
                self.editor.setPlainText("")
                self.loaded = ""
                self.back_to_cell()
        self.tree.refresh()

    def done(self, text: str, result: Result) -> None:
        self.transcript.add_result(text, result)
        self.transcript_error = result.error

    transcript_error: str | None = None

    # The tree's context menu

    def actions_for(self, target: Target) -> list[tuple[str, typing.Callable[[], None]]]:
        """What can be done to a target: a class defined in a cell can
        have a member added, be rebuilt, or be read whole; what's bound in
        the session, or a cell's class, or an object, can be deleted."""
        actions: list[tuple[str, typing.Callable[[], None]]] = []
        value = self.engine.lookup(target.container, target.name)
        if kind_of(value) == CLASS and target.name:
            if id(value) in self.engine.headers:
                path = (*target.path, "")
                actions.append(("Add member", lambda: self.edit(Target(value, "", path),
                                                                "def name(self):\n    pass\n")))
                actions.append(("Rebuild", lambda: self.run_action(self.engine.rebuild(target),
                                                                   f"# rebuild {target.label}\n")))
            actions.append(("View whole class", lambda: self.edit(target, self.engine.whole_class(target),
                                                                  reading=True)))
        container = target.container
        deletable = (self.engine.is_session(container) or id(container) in self.engine.headers
                     or not (inspect.isclass(container) or inspect.ismodule(container)
                             or isinstance(container, (dict, Results))))
        if target.name and deletable and not target.name.startswith("$"):
            actions.append(("Delete", lambda: self.run_action(self.engine.delete(target), f"del {target.label}\n")))
        return actions

    def run_action(self, result: Result, text: str) -> None:
        self.done(text, result)
        self.tree.refresh()


def darq_main():
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication([])
    loop = QtEventLoop()
    engine = Engine()
    engine.namespace["loop"] = loop
    window = Window(engine)
    window.show()
    result = app.exec()
    loop.close()
    return result
