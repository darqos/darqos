# darqos
# Copyright (C) 2026 David Arnold

# Editing for the entity lens: a form for an entity, in place of its
# page, in edit mode.
#
# Each change is committed as it's finished, with Enter, by leaving the
# field, or by choosing from a list: the form makes it as edits (see
# darq.kb), and hands them to its context, the window, which saves them
# and then has the form built again, from the entity as saved.  So the
# form keeps nothing of its own but what's not yet committed: a field
# being typed in, and the rows added for a new statement or qualifier,
# until they're given a value.
#
# Items and properties are chosen with a picker, which searches as it's
# typed in, through the context.

import re
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

import darq.kb as model
from darq.protocols.kb import Kind, Linked

import render
import times


Key = QtCore.Qt.Key

# The source of the statements made here, the operator's.
EDITOR = model.ENTITY_EDITOR

# Milliseconds after the last key typed in a picker before it searches.
SEARCH_DELAY = 250

# Most entities a picker lists.
MAX_CHOICES = 12

RANKS = [model.Rank.Normal, model.Rank.Preferred, model.Rank.Deprecated]

# A URL: its scheme, and the rest.
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S+")

STYLE = """
EntityForm { background-color: #323232; }
QLabel { color: #e0e0e0; }
QLabel[role="property"] { color: #9a9a9a; font-size: 15px; margin-top: 10px; }
QLabel[role="detail"] { color: #9a9a9a; font-size: 13px; }
QLabel[role="error"] { color: #f28b82; font-size: 13px; }
QLineEdit, QComboBox, QListWidget { color: #e0e0e0; background-color: #404040; border: 1px solid #505050;
                                    padding: 3px; }
QLineEdit[conflict="true"] { border: 1px solid #f28b82; }
QPushButton { color: #e0e0e0; background-color: #454545; border: none; padding: 3px 8px; }
QFrame[role="conflict"] { background-color: #5c3c3c; }
"""


class Context(typing.Protocol):
    """What a form edits for: the window."""

    chain: list[str]

    # The language labels, descriptions, aliases and text are edited in.
    language: str

    # Whether it's editing: the form's not built when it's not.
    editing: bool

    def entity(self) -> model.Entity:
        """Return the entity, as last read."""
        ...

    def name(self, entity_id: str) -> str:
        """Return an entity's label, or its identifier."""
        ...

    def datatype(self, property_id: str) -> model.Datatype | None:
        """Return a property's datatype, or None if it's not known."""
        ...

    def search(self, text: str, kind: Kind) -> list[Linked]:
        """Return the entities of a kind found by words of their labels."""
        ...

    def suggest(self) -> list[Linked]:
        """Return the properties to offer first."""
        ...

    def commit(self, edits: list[model.Edit]) -> bool:
        """Save edits to the entity: False if they couldn't be."""
        ...


def text_of(value: model.Value, context: Context) -> str:
    """Return a value as it's edited."""
    if isinstance(value, model.EntityValue):
        return context.name(value.entity_id)
    if isinstance(value, model.StringValue):
        return value.value
    if isinstance(value, model.TextValue):
        return render.best(value.text, [context.language, *context.chain]) or ""
    if isinstance(value, model.QuantityValue):
        return str(value.amount)
    return times.text(value)


def rank(choices: list[Linked], text: str) -> list[Linked]:
    """Return entities found, best first: those whose label is the text,
    then those it starts, then the rest, each by label."""
    folded = model.fold(text.strip())

    def key(link: Linked):
        label = model.fold(link.label)
        return (0 if label == folded else 1 if label.startswith(folded) else 2, label, link.entity_id)

    return sorted(choices, key=key)


class Picker(QWidget):
    """A line to type words of a label in, and a list of the entities of
    a kind found, to choose one from: with the arrow keys and Enter, or
    a click.

    Before anything's typed, it lists the suggestions, if any."""

    def __init__(self,
                 context: Context,
                 kind: Kind,
                 on_choose: typing.Callable[[Linked], None],
                 text: str = "",
                 placeholder: str = "",
                 suggestions: typing.Callable[[], list[Linked]] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self.kind = kind
        self.on_choose = on_choose
        self.suggestions = suggestions
        self.found: list[Linked] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.line = QLineEdit(text, self)
        self.line.setPlaceholderText(placeholder)
        self.line.textEdited.connect(self.typed)
        self.line.installEventFilter(self)
        layout.addWidget(self.line)
        self.choices = QListWidget(self)
        self.choices.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.choices.itemClicked.connect(self.clicked)
        self.choices.hide()
        layout.addWidget(self.choices)
        self.hint = QLabel(self)
        self.hint.setProperty("role", "error")
        self.hint.hide()
        layout.addWidget(self.hint)

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SEARCH_DELAY)
        self.timer.timeout.connect(self.search)

    def start(self):
        """Take the focus, and list the suggestions."""
        self.line.setFocus()
        if self.suggestions is not None and not self.line.text():
            self.show_choices(self.suggestions())

    def typed(self, text: str):
        self.hint.hide()
        self.timer.start()

    def search(self):
        text = self.line.text().strip()
        if not text:
            self.show_choices(self.suggestions() if self.suggestions is not None else [])
            return
        self.show_choices(rank(self.context.search(text, self.kind), text)[:MAX_CHOICES])

    def show_choices(self, found: list[Linked]):
        self.found = found
        self.choices.clear()
        for link in found:
            text = link.label or link.entity_id
            if link.description:
                text += f" — {link.description}"
            if link.item_id:
                text += f" ({render.kind_name(link.item_id)})"
            self.choices.addItem(QListWidgetItem(text))
        self.choices.setVisible(bool(found))
        if found:
            self.choices.setCurrentRow(0)
            self.choices.setFixedHeight(min(len(found), 6) * 24 + 6)

    def clicked(self, item: QListWidgetItem):
        self.choose(self.choices.row(item))

    def choose(self, row: int) -> bool:
        """Choose the entity listed in a row: False if there's none."""
        if self.timer.isActive():
            self.timer.stop()
            self.search()
            row = 0
        if not 0 <= row < len(self.found):
            return False
        link = self.found[row]
        self.choices.hide()
        self.line.setText(link.label or link.entity_id)
        self.on_choose(link)
        return True

    def accept(self) -> bool:
        """Choose the entity selected, if the text's been changed."""
        if not self.choose(self.choices.currentRow() if self.choices.isVisible() else -1):
            self.hint.setText("Choose one from the list")
            self.hint.show()
            return False
        return True

    def eventFilter(self, a0: QtCore.QObject | None, a1: QtCore.QEvent | None) -> bool:
        if a0 is self.line and a1 is not None and a1.type() == QtCore.QEvent.Type.KeyPress:
            event = typing.cast(QKeyEvent, a1)
            row = self.choices.currentRow()
            if event.key() == Key.Key_Down and self.choices.isVisible():
                self.choices.setCurrentRow(min(row + 1, self.choices.count() - 1))
                return True
            if event.key() == Key.Key_Up and self.choices.isVisible():
                self.choices.setCurrentRow(max(row - 1, 0))
                return True
            if event.key() in (Key.Key_Return, Key.Key_Enter):
                self.accept()
                return True
        return super().eventFilter(a0, a1)


class ValueEditor(QWidget):
    """Edits a value of a datatype: an item or property with a Picker,
    others as text.  A value's committed when it's changed, and finished
    with, if it's valid; otherwise, why not is shown."""

    def __init__(self,
                 context: Context,
                 datatype: model.Datatype,
                 value: model.Value | None,
                 on_value: typing.Callable[[model.Value], typing.Any],
                 placeholder: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self.datatype = datatype
        self.value = value
        self.on_value = on_value
        self.initial = "" if value is None else text_of(value, context)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.picker: Picker | None = None
        if datatype in (model.Datatype.Item, model.Datatype.Property):
            kind = Kind.PROPERTY if datatype == model.Datatype.Property else Kind.ITEM
            self.picker = Picker(context, kind, self.chosen, self.initial, placeholder, parent=self)
            self.line = self.picker.line
            self.line.editingFinished.connect(self.left)
            layout.addWidget(self.picker)
        else:
            self.line = QLineEdit(self.initial, self)
            self.line.setPlaceholderText(placeholder)
            self.line.editingFinished.connect(self.finish)
            self.line.textEdited.connect(self.typed)
            layout.addWidget(self.line)
        self.hint = QLabel(self)
        self.hint.setProperty("role", "detail")
        self.hint.hide()
        layout.addWidget(self.hint)

    def dirty(self) -> bool:
        """Return whether it's been changed, and not committed."""
        return self.line.text() != self.initial

    def revert(self):
        self.line.setText(self.initial)
        self.hint.hide()
        if self.picker is not None:
            self.picker.choices.hide()
            self.picker.hint.hide()

    def typed(self, text: str):
        """Show how a time's read, as it's typed."""
        if self.datatype != model.Datatype.Time or not text.strip():
            self.hint.hide()
            return
        try:
            self.show_hint(f"= {render.format_time(times.parse(text))}", "detail")
        except ValueError as e:
            self.show_hint(str(e), "error")

    def show_hint(self, text: str, role: str):
        self.hint.setText(text)
        self.hint.setProperty("role", role)
        style = self.hint.style()
        if style is not None:
            style.polish(self.hint)
        self.hint.show()

    def finish(self):
        if not self.dirty():
            return
        try:
            value = self.parse(self.line.text())
        except ValueError as e:
            self.show_hint(str(e), "error")
            return
        self.initial = self.line.text()
        self.on_value(value)

    def left(self):
        """The picker's lost the focus, or Enter was pressed: if it's been
        changed, and nothing's chosen, it's put back."""
        if self.picker is not None and self.dirty() and not self.picker.choices.isVisible():
            self.revert()

    def chosen(self, link: Linked):
        self.initial = self.line.text()
        self.on_value(model.EntityValue(link.entity_id))

    def parse(self, text: str) -> model.Value:
        """Return the value some text gives, or raise ValueError."""
        text = text.strip()
        if not text:
            raise ValueError("Give a value, or remove it with ×")
        if self.datatype == model.Datatype.Time:
            return times.parse(text)
        if self.datatype == model.Datatype.Quantity:
            unit = self.value.unit if isinstance(self.value, model.QuantityValue) else None
            return model.QuantityValue(text, unit)
        if self.datatype == model.Datatype.Text:
            value = model.TextValue()
            if isinstance(self.value, model.TextValue):
                value.text = dict(self.value.text)
            value.set_text(text, self.context.language)
            return value
        if self.datatype == model.Datatype.Url and not URL.fullmatch(text):
            raise ValueError("A URL starts with its scheme, such as https: or mailto:")
        return model.StringValue(text)


def editor(context: Context, property_id: str, value: model.Value | None,
           on_value: typing.Callable[[model.Value], typing.Any], parent: QWidget) -> QWidget:
    """Return an editor for a property's value, or, if its datatype isn't
    known, the value as a label."""
    datatype = context.datatype(property_id)
    if datatype is None:
        label = QLabel("" if value is None else text_of(value, context), parent)
        label.setToolTip(f"{property_id} isn't known: its values can't be edited")
        return label
    return ValueEditor(context, datatype, value, on_value, "value", parent)


def small_button(text: str, tip: str, action: typing.Callable[[], typing.Any], parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setToolTip(tip)
    button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    button.clicked.connect(lambda: action())
    return button


class StatementRow(QWidget):
    """A statement: its value, rank, qualifiers and sources, and buttons
    to add qualifiers, or dates, and to remove it."""

    def __init__(self, form: "EntityForm", statement: model.Statement):
        super().__init__(form)
        self.form = form
        self.statement = statement
        context = form.context
        sid = statement.id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        top = QHBoxLayout()
        self.value = editor(context, statement.get_property_id(), statement.get_value(),
                            lambda v: form.commit(model.change_value(context.entity(), sid, v, EDITOR)), self)
        form.register(f"value:{sid}", self.value)
        top.addWidget(self.value, 1)
        self.rank = QComboBox(self)
        self.rank.addItems([r.name for r in RANKS])
        self.rank.setCurrentIndex(RANKS.index(statement.rank))
        self.rank.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.rank.currentIndexChanged.connect(
            lambda n: form.commit(model.change_rank(context.entity(), sid, RANKS[n])))
        top.addWidget(self.rank, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        top.addWidget(small_button("+ qualifier", "Add a qualifier", lambda: form.pick_qualifier(sid), self),
                      0, QtCore.Qt.AlignmentFlag.AlignTop)
        top.addWidget(small_button("dates", "Add start and end times", lambda: form.add_dates(sid), self),
                      0, QtCore.Qt.AlignmentFlag.AlignTop)
        top.addWidget(small_button("×", "Remove the statement", lambda: form.remove(sid), self),
                      0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        for n, q in enumerate(statement.qualifiers):
            layout.addLayout(self.qualifier(q.property_id, q, f"qualifier:{sid}:{n}"))
        for n, property_id in enumerate(form.new_qualifiers.get(sid, [])):
            layout.addLayout(self.qualifier(property_id, None, f"new-qualifier:{sid}:{n}", n))
        if sid in form.picking:
            picker = Picker(context, Kind.PROPERTY, lambda link: form.add_qualifier(sid, link),
                            placeholder="qualifier property", suggestions=context.suggest, parent=self)
            form.register(f"pick-qualifier:{sid}", picker.line)
            form.start_picker = picker
            layout.addWidget(picker)

        for r in statement.references:
            sources = "; ".join(f"{context.name(a.property_id)}: {text_of(a.value, context)}" for a in r.snaks)
            label = QLabel(f"    source: {sources}", self)
            label.setProperty("role", "detail")
            layout.addWidget(label)

    def qualifier(self, property_id: str, snak: model.Snak | None, key: str, pending: int = -1) -> QHBoxLayout:
        """Return a row for a qualifier, or a new one (snak None) of a
        property, the pending'th."""
        form, sid = self.form, self.statement.id
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)
        name = QLabel(form.context.name(property_id), self)
        name.setProperty("role", "detail")
        name.setMinimumWidth(140)
        row.addWidget(name, 0, QtCore.Qt.AlignmentFlag.AlignTop)

        def changed(value: model.Value):
            new = model.Snak(property_id, value)
            if snak is None:
                form.commit(model.change_qualifier(form.context.entity(), sid, None, new, EDITOR),
                            lambda: form.drop_qualifier(sid, pending))
            else:
                form.commit(model.change_qualifier(form.context.entity(), sid, snak, new, EDITOR))

        def remove():
            if snak is None:
                form.drop_qualifier(sid, pending)
                form.rebuild()
            else:
                form.commit(model.change_qualifier(form.context.entity(), sid, snak, None, EDITOR))

        value = editor(form.context, property_id, None if snak is None else snak.value, changed, self)
        form.register(key, value)
        row.addWidget(value, 1)
        row.addWidget(small_button("×", "Remove the qualifier", remove, self), 0,
                      QtCore.Qt.AlignmentFlag.AlignTop)
        return row


class EntityForm(QWidget):
    """An entity's form: its label, description and aliases, and its
    statements, by property, with a button to add one.  Its kind's
    statements, DarqOS type, aren't shown: they can't be changed."""

    def __init__(self, context: Context, parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self.setStyleSheet(STYLE)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 12, 0)
        self.body: QWidget | None = None

        # What's not yet committed: properties of new statements, and of
        # new qualifiers, by statement; and the statements a qualifier's
        # being picked for (or "" for a new statement).
        self.new_statements: list[str] = []
        self.new_qualifiers: dict[str, list[str]] = {}
        self.picking: set[str] = set()

        # The editors, by key, as last built, a picker to start, and the
        # editor to give the focus to, once it's built again.
        self.editors: dict[str, QWidget] = {}
        self.start_picker: Picker | None = None
        self.focus_next: str | None = None

        # Builds it again, once what asked for it's finished.
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.build)

        # A conflict to show: a message, its target, and what to do to
        # keep mine (or None, if that can't be done).
        self.conflict: tuple[str, str, typing.Callable[[], None] | None] | None = None

    def register(self, key: str, widget: QWidget):
        self.editors[key] = widget

    def dirty(self) -> bool:
        """Return whether an editor's been changed, and not committed."""
        return any(isinstance(e, (ValueEditor, TextField)) and e.dirty() for e in self.editors.values())

    def focused(self) -> str | None:
        """Return the key of the editor with the focus, if any."""
        widget = QApplication.focusWidget()
        while widget is not None:
            for key, editor_ in self.editors.items():
                if editor_ is widget:
                    return key
            widget = widget.parentWidget()
        return None

    def revert_focused(self) -> bool:
        """Put back the editor with the focus, if it's been changed:
        False if there's none."""
        key = self.focused()
        widget = self.editors.get(key) if key is not None else None
        if isinstance(widget, (ValueEditor, TextField)) and widget.dirty():
            widget.revert()
            return True
        return False

    def reset(self):
        """Forget what's not been committed."""
        self.new_statements.clear()
        self.new_qualifiers.clear()
        self.picking.clear()
        self.conflict = None
        self.focus_next = None
        self.timer.stop()

    # Building

    def rebuild(self, focus: str | None = None):
        """Build it again, from the entity, later, as it's often asked for
        by an editor that's then destroyed; and give an editor the focus."""
        if focus is not None:
            self.focus_next = focus
        self.timer.start()

    def build(self):
        """Build it from the entity, keeping the focus where it was."""
        self.timer.stop()
        if not self.context.editing:
            return
        focus, self.focus_next = self.focus_next or self.focused(), None
        if self.body is not None:
            for widget in self.body.findChildren(QWidget):
                widget.blockSignals(True)
            self.body.hide()
            self.body.deleteLater()
        self.editors = {}
        self.start_picker = None

        context = self.context
        entity = context.entity()
        language = context.language
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        if self.conflict is not None:
            layout.addWidget(self.conflict_panel(body))

        def text_edit(field: str, title: str):
            old = getattr(entity, field).get(language)

            def changed(text: str):
                self.commit([model.SetText(field, language, old, text or None)])

            row = QHBoxLayout()
            label = QLabel(title, body)
            label.setMinimumWidth(140)
            row.addWidget(label)
            edit = TextField(old or "", changed, body)
            self.register(f"{field}:{language}", edit)
            row.addWidget(edit, 1)
            layout.addLayout(row)

        text_edit("labels", "Label")
        text_edit("descriptions", "Description")
        aliases = entity.aliases.get(language, [])
        row = QHBoxLayout()
        label = QLabel("Also known as", body)
        label.setMinimumWidth(140)
        row.addWidget(label)
        field = TextField(", ".join(aliases), lambda text: self.commit(
            [model.SetAliases(language, aliases, [a.strip() for a in text.split(",") if a.strip()])]), body)
        field.setPlaceholderText("aliases, separated by commas")
        self.register(f"aliases:{language}", field)
        row.addWidget(field, 1)
        layout.addLayout(row)
        note = QLabel(f"In {language}", body)
        note.setProperty("role", "detail")
        layout.addWidget(note)

        order: list[str] = []
        for s in entity.statements:
            if s.get_property_id() not in order and s.get_property_id() != model.DARQOS_TYPE:
                order.append(s.get_property_id())
        for property_id in order:
            heading = QLabel(context.name(property_id), body)
            heading.setProperty("role", "property")
            layout.addWidget(heading)
            for s in model.in_order(entity.get_statements(property_id)):
                row_ = StatementRow(self, s)
                if self.conflict is not None and self.conflict[1] == f"statement:{s.id}":
                    row_.setStyleSheet("StatementRow { border-left: 3px solid #f28b82; }")
                layout.addWidget(row_)

        for n, property_id in enumerate(self.new_statements):
            heading = QLabel(context.name(property_id), body)
            heading.setProperty("role", "property")
            layout.addWidget(heading)
            new = editor(context, property_id, None, lambda v, p=property_id, n=n: self.commit(
                model.add_statement(context.entity(), p, v, EDITOR), lambda: self.drop_statement(n)), body)
            self.register(f"new-statement:{n}", new)
            layout.addWidget(new)

        if "" in self.picking:
            picker = Picker(context, Kind.PROPERTY, self.add_statement, placeholder="property",
                            suggestions=self.statement_suggestions, parent=body)
            self.register("pick-statement", picker.line)
            self.start_picker = picker
            layout.addWidget(picker)
        else:
            add = QPushButton("+ statement", body)
            add.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            add.clicked.connect(self.pick_statement)
            layout.addWidget(add, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        self.layout_.addWidget(body)
        self.body = body
        if self.start_picker is not None:
            self.start_picker.start()
        elif focus is not None:
            self.focus(focus)

    def conflict_panel(self, parent: QWidget) -> QFrame:
        assert self.conflict is not None
        message, _, keep_mine = self.conflict
        panel = QFrame(parent)
        panel.setProperty("role", "conflict")
        layout = QHBoxLayout(panel)
        label = QLabel(message, panel)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        if keep_mine is not None:
            layout.addWidget(small_button("Keep mine", "Make my change over theirs", keep_mine, panel))
        layout.addWidget(small_button("Take theirs", "Drop my change", self.take_theirs, panel))
        return panel

    # Changes

    def commit(self, edits: list[model.Edit], then: typing.Callable[[], None] | None = None) -> bool:
        """Commit edits, then do something if they were, and build it
        again."""
        if not edits:
            return True
        ok = self.context.commit(edits)
        if ok and then is not None:
            then()
        self.rebuild()
        return ok

    def show_conflict(self, message: str, target: str, keep_mine: typing.Callable[[], None] | None):
        self.conflict = (message, target, keep_mine)
        self.rebuild()

    def take_theirs(self):
        self.conflict = None
        self.rebuild()

    def remove(self, statement_id: str):
        self.commit(model.remove_statement(self.context.entity(), statement_id))

    def statement_suggestions(self) -> list[Linked]:
        """The properties to offer for a new statement: those the entity
        has, not the annotations, which are qualifiers."""
        return [link for link in self.context.suggest() if link.entity_id not in model.ANNOTATIONS]

    def pick_statement(self):
        self.picking.add("")
        self.rebuild()

    def add_statement(self, link: Linked):
        self.picking.discard("")
        self.new_statements.append(link.entity_id)
        self.rebuild(f"new-statement:{len(self.new_statements) - 1}")

    def drop_statement(self, n: int):
        if 0 <= n < len(self.new_statements):
            del self.new_statements[n]

    def pick_qualifier(self, statement_id: str):
        self.picking.add(statement_id)
        self.rebuild()

    def add_qualifier(self, statement_id: str, link: Linked):
        self.picking.discard(statement_id)
        pending = self.new_qualifiers.setdefault(statement_id, [])
        pending.append(link.entity_id)
        self.rebuild(f"new-qualifier:{statement_id}:{len(pending) - 1}")

    def add_dates(self, statement_id: str):
        """Add new start and end time qualifiers, for those it hasn't."""
        statement = model.find_statement(self.context.entity(), statement_id)
        if statement is None:
            return
        have = {q.property_id for q in statement.qualifiers}
        if any(self.context.datatype(p) is None for p in (model.START_TIME, model.END_TIME)):
            # They're among the suggestions, which are remembered.
            self.context.suggest()
        pending = self.new_qualifiers.setdefault(statement_id, [])
        for property_id in (model.START_TIME, model.END_TIME):
            if property_id not in have and property_id not in pending:
                pending.append(property_id)
        self.rebuild(f"new-qualifier:{statement_id}:0" if pending else None)

    def drop_qualifier(self, statement_id: str, n: int):
        pending = self.new_qualifiers.get(statement_id, [])
        if 0 <= n < len(pending):
            del pending[n]

    def focus(self, key: str):
        widget = self.editors.get(key)
        if isinstance(widget, ValueEditor):
            widget.line.setFocus()
        elif widget is not None:
            widget.setFocus()


class TextField(QLineEdit):
    """A line of text, committed when it's changed and finished with."""

    def __init__(self, text: str, on_text: typing.Callable[[str], typing.Any], parent: QWidget | None = None):
        super().__init__(text, parent)
        self.initial = text
        self.on_text = on_text
        self.editingFinished.connect(self.finish)

    def dirty(self) -> bool:
        return self.text() != self.initial

    def revert(self):
        self.setText(self.initial)

    def finish(self):
        if self.dirty():
            self.initial = self.text()
            self.on_text(self.text().strip())
