# darqos
# Copyright (C) 2026 David Arnold

# Entity lens: a window showing a Knowledge Base item, darq.entity or
# any of its kinds, in the Terminal context, and editing it.
#
# It's spawned for one item, with a bootstrap port it reports Ready on,
# and then asked to Use the item (see lib/didl/lens.didl).  It reads the
# item through the EntityItem protocol (lib/didl/kb.didl): the entity,
# the entities it refers to, and those that link to it, shown as HTML
# (see render.py).  It exits when its window is closed.
#
# Following a link shows the linked item in the same window, like a web
# browser, with Back to return; Ctrl+click, or the link's context menu,
# opens it in a new window, with its own lens.  Each item shown is
# watched, so the view keeps up with changes, and recorded in History
# as opened.
#
# In edit mode (E), the page is a form (see edit.py), and each change is
# saved as it's made, as edits (see darq.kb): applied to the entity as
# last read, and written whole with PutEntity.  If it's been changed
# since, it's read again and the edits made again, unless what they
# change has been changed too: then that's shown, to keep mine or take
# theirs.  A source's statements are kept its own (see
# darq.kb.change_value).  Undo and redo make edits too.  Items are
# recorded in History as edited, once each time edit mode's entered.
#
# Its thread is Qt's: the lens port, and the ports it watches items on,
# are served on Qt's event loop (see darq.Server.attach), and its calls
# to items and History are synchronous, on queued reply ports.

import logging
import os
import pathlib
import sys
import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                             QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

import darq
import darq.history
import darq.kb as model
import darq.launch
from darq.loop import QtEventLoop
from darq.protocols.history import History
from darq.protocols.kb import Changed, EntityItem, Kind, Linked, Listener, Role
from darq.protocols.kb import Status as KBStatus
from darq.protocols.lens import Close, Lens, Ready, Status, UseRequest, UseResponse
from darq.ui.launcher import Launcher

# Its own modules are beside it.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import edit  # noqa: E402
import render  # noqa: E402


# History source for the events it records.
SOURCE = "entity-lens"

# Seconds to wait for items' replies.
TIMEOUT = 10.0

# Seconds after starting to wait for a Use, before giving up and exiting.
USE_TIMEOUT = 60.0

# Most items listed as linking to the one shown.
MAX_LINKED_FROM = 200

# How many of those are asked for: the KB's limit, so "and N more" is
# right for up to that many.
LINKED_FROM_BATCH = 1000

# Most entities asked for when searching, to choose from.
MAX_FOUND = 50

# Times edits are made again, when the entity's changed as they're saved.
SAVE_ATTEMPTS = 3

Key = QtCore.Qt.Key
Modifier = QtCore.Qt.KeyboardModifier

HELP = """Alt+Left, Backspace   Back
Ctrl+click            Open a link in a new window
E                     Edit, or finish editing
D                     Show or hide deprecated statements
F5                    Refresh
F1                    Help
Ctrl+W                Close

Editing: each change is saved as it's made
Enter, Tab            Finish a field
Up, Down              Choose from a list
Esc                   Put a field back, or finish editing
Ctrl+Z, Ctrl+Shift+Z  Undo, Redo"""


class Opener(typing.Protocol):
    """Opens items, and starts lenses for them: darq.launch, and a
    Launcher, or fakes in tests."""

    def open(self, item_id: str) -> int:
        """Return a port for an item: raises DarqError if it can't."""
        ...

    def use(self, item_id: str) -> None:
        """Use an item, with its own lens, without blocking."""
        ...


class Shown:
    """The item being shown: its port, a client for it, and the port it's
    watched on; and, as last read, its entity, the entities it refers to,
    and those that link to it."""

    def __init__(self, item_id: str, port: int, client: EntityItem.Client):
        self.item_id = item_id
        self.port = port
        self.client = client
        self.listener: int | None = None
        self.entity: model.Entity = model.Item()
        self.linked: dict[str, Linked] = {}
        self.linked_from: list[Linked] = []


class EntityWindow(QWidget):
    """A frameless window showing an item, with Back, and a status line.

    Drag its margin to move it."""

    def __init__(self, lens: "EntityLens", on_closed: typing.Callable[[], None] | None = None):
        super().__init__()
        self.lens = lens
        self.on_closed = on_closed
        self.chain = render.languages(os.environ.get("LANG", "") or "en")

        # The item shown, and the items to go back to, with where they
        # were scrolled to.
        self.shown: Shown | None = None
        self.back: list[tuple[str, int]] = []

        # The item identifiers the page's links lead to, by number.
        self.targets: list[str] = []

        # Whether deprecated statements are shown on the page.
        self.show_deprecated = False

        # Editing: whether it is, the language it's in, the edits to undo
        # and redo, and whether the item's been recorded as edited since
        # edit mode was entered.
        self.editing = False
        self.language = self.chain[0]
        self.undo_stack: list[list[model.Edit]] = []
        self.redo_stack: list[list[model.Edit]] = []
        self.recorded_edit = False

        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("EntityWindow { background-color: #323232; }"
                           "QLabel { color: #e0e0e0; }"
                           "QPushButton { color: #e0e0e0; background-color: #454545; border: none;"
                           " padding: 6px 12px; }"
                           "QPushButton:disabled { color: #707070; }"
                           "QTextBrowser { background-color: #323232; border: none; }")
        self.resize(900, 700)

        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 20)
        header = QHBoxLayout()
        self.back_button = QPushButton("◀ Back", self)
        self.back_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.back_button.clicked.connect(self.go_back)
        header.addWidget(self.back_button)
        self.edit_button = QPushButton("Edit", self)
        self.edit_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.edit_button.clicked.connect(self.toggle_edit)
        header.addWidget(self.edit_button)
        titles = QVBoxLayout()
        self.title = QLabel(self)
        self.title.setStyleSheet("QLabel { font-size: 22px; }")
        self.item_label = QLabel(self)
        self.item_label.setStyleSheet("QLabel { color: #9a9a9a; font-size: 12px; }")
        self.item_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.title)
        titles.addWidget(self.item_label)
        header.addLayout(titles, 1)
        self.kind = QLabel(self)
        self.kind.setStyleSheet("QLabel { color: #9a9a9a; font-size: 15px; }")
        header.addWidget(self.kind, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self.on_anchor)
        self.browser.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self.on_context_menu)
        self.form = edit.EntityForm(self)
        self.form_area = QScrollArea(self)
        self.form_area.setWidgetResizable(True)
        self.form_area.setStyleSheet("QScrollArea { background-color: #323232; border: none; }")
        self.form_area.setWidget(self.form)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.form_area)
        layout.addWidget(self.stack, 1)

        self.status = QLabel(self)
        self.status.setStyleSheet("QLabel { color: #9a9a9a; font-size: 14px; }")
        layout.addWidget(self.status)
        self.setLayout(layout)

        self.help = QLabel(HELP, self)
        self.help.setStyleSheet("QLabel { background-color: rgba(20, 20, 20, 230); color: #e0e0e0;"
                                " font-family: monospace; font-size: 15px; padding: 24px; }")
        self.help.hide()
        self.update_back()

    # Showing items

    def show_item(self, item_id: str, port: int) -> bool:
        """Show an item, from a port for it, in place of any shown.

        :returns: False if it can't be read: then nothing's changed, and
                  the port's been closed."""
        shown = Shown(item_id, port, self.lens.client(port))
        page = self.page(shown)
        if page is None:
            self.close_item(shown)
            return False

        if self.shown is not None:
            self.leave()
        self.shown = shown
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.display(page, 0)
        self.watch(shown)
        self.lens.record(item_id, darq.history.OPENED)
        logging.info(f"Showing {item_id}")
        return True

    def read(self, shown: Shown, links: bool = True) -> bool:
        """Read an item's entity, and those it refers to, and, with links,
        those that link to it: False if it can't be read."""
        try:
            response = shown.client.get_entity(languages=self.chain)
            if response.status != KBStatus.OK:
                self.show_status(f"Can't read {shown.item_id}: {response.status.name}")
                return False
            linked_from = shown.linked_from
            if links:
                linked_from = shown.client.get_links(property_id="", role=Role.MAIN, languages=self.chain,
                                                     max=LINKED_FROM_BATCH, after="").linked
            entity = model.entity_from_wire(response.entity)
        except (darq.DarqError, TimeoutError, ValueError) as e:
            logging.error(f"Can't read {shown.item_id}: {e!r}")
            self.show_status(f"Can't read {shown.item_id}: {e}")
            return False
        shown.entity = entity
        shown.linked.update((link.entity_id, link) for link in response.linked)
        shown.linked_from = linked_from
        return True

    def page(self, shown: Shown) -> tuple[str, str, list[str]] | None:
        """Read an item, and return its title, its page, and its links'
        targets, or None if it can't be read."""
        if not self.read(shown):
            return None
        return self.render_page(shown)

    def render_page(self, shown: Shown) -> tuple[str, str, list[str]]:
        """Return an item's title, page, and links' targets, as last read."""
        entity = shown.entity
        page = render.Page(self.chain, {link.entity_id: render.Link(link.item_id, link.label)
                                        for link in shown.linked.values()})
        linked_from = [render.Link(link.item_id, link.label) for link in shown.linked_from if link.item_id]
        note = None
        _, requested = darq.split_item_id(shown.item_id)
        if entity.id != requested:
            note = f"{requested} was merged into this."
        html = page.render(entity, linked_from[:MAX_LINKED_FROM], max(0, len(linked_from) - MAX_LINKED_FROM),
                           note, self.show_deprecated)
        title = render.best(entity.labels, self.chain) or entity.id
        return title, html, page.targets

    def display(self, page: tuple[str, str, list[str]], scroll: int):
        """Show a page, scrolled to a position."""
        title, html, self.targets = page
        assert self.shown is not None
        self.title.setText(title)
        self.setWindowTitle(title)
        self.item_label.setText(self.shown.item_id)
        self.kind.setText(render.kind_name(self.shown.item_id))
        self.browser.setHtml(html)
        bar = self.browser.verticalScrollBar()
        if bar is not None:
            bar.setValue(scroll)
        self.update_back()

    def scroll_position(self) -> int:
        bar = self.browser.verticalScrollBar()
        return bar.value() if bar is not None else 0

    def refresh(self):
        """Read the item shown again, keeping the scroll position."""
        if self.shown is None:
            return
        if self.editing:
            if self.read(self.shown, links=False):
                self.title.setText(render.best(self.shown.entity.labels, self.chain) or self.shown.entity.id)
                self.form.rebuild()
            return
        page = self.page(self.shown)
        if page is not None:
            self.display(page, self.scroll_position())

    def changed(self, entity_id: str, revision: int = 0):
        """The KB says an entity's changed, to a revision: it's the one
        shown.  When editing, it's not shown till a field being typed in
        is finished with."""
        if self.shown is None or (revision and revision == self.shown.entity.revision):
            return
        if self.editing and self.form.dirty():
            if self.read(self.shown, links=False):
                self.show_status("Changed elsewhere: shown once this field's finished")
            return
        self.refresh()

    def toggle_deprecated(self):
        self.show_deprecated = not self.show_deprecated
        if self.shown is not None and not self.editing:
            self.display(self.render_page(self.shown), self.scroll_position())

    # Editing

    def toggle_edit(self):
        if self.editing:
            self.finish_editing()
        else:
            self.start_editing()

    def start_editing(self):
        """Show the item as a form, read afresh."""
        if self.shown is None or self.editing or not self.read(self.shown, links=False):
            return
        entity = self.shown.entity
        base = self.chain[0].split("-")[0]
        self.language = next((lang for lang in self.chain if lang in entity.labels), base)
        self.editing = True
        self.recorded_edit = False
        self.form.reset()
        self.form.build()
        self.stack.setCurrentWidget(self.form_area)
        self.edit_button.setText("Done")
        self.show_status("Editing: each change is saved as it's made")

    def finish_editing(self, refresh: bool = True):
        """Show the item's page again, committing the field being typed in,
        and read afresh, unless it's not to be."""
        if not self.editing:
            return
        focused = QApplication.focusWidget()
        if focused is not None and self.form.isAncestorOf(focused):
            focused.clearFocus()
        self.editing = False
        self.form.reset()
        self.stack.setCurrentWidget(self.browser)
        self.edit_button.setText("Edit")
        self.show_status("")
        if refresh:
            self.refresh()

    # As the form's context

    def entity(self) -> model.Entity:
        assert self.shown is not None
        return self.shown.entity

    def name(self, entity_id: str) -> str:
        link = self.shown.linked.get(entity_id) if self.shown is not None else None
        return link.label if link is not None and link.label else entity_id

    def datatype(self, property_id: str) -> model.Datatype | None:
        link = self.shown.linked.get(property_id) if self.shown is not None else None
        return model.datatype_from_wire(link.datatype) if link is not None else None

    def found(self, links: list[Linked]) -> list[Linked]:
        """Remember entities found, to name them, and know properties'
        datatypes."""
        if self.shown is not None:
            self.shown.linked.update((link.entity_id, link) for link in links)
        return links

    def search(self, text: str, kind: Kind) -> list[Linked]:
        assert self.shown is not None
        try:
            response = self.shown.client.find_entities(text=text, languages=self.chain, kind=kind, class_id="",
                                                       max=MAX_FOUND, after="")
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't search: {e}")
            return []
        if response.status != KBStatus.OK:
            self.show_status(f"Can't search: {response.status.name}")
            return []
        return self.found(response.found)

    def suggest(self) -> list[Linked]:
        assert self.shown is not None
        try:
            response = self.shown.client.suggest_properties(languages=self.chain, max=MAX_FOUND)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't suggest properties: {e}")
            return []
        return self.found(response.found) if response.status == KBStatus.OK else []

    def commit(self, edits: list[model.Edit], force: bool = False, undoing: bool | None = None) -> bool:
        """Save edits to the item, made to it as last read, or, if it's
        changed since, as it is: False if they can't be.

        :param force: Make them over others' changes.
        :param undoing: True if they undo edits, False if they redo them,
                        None if they're new."""
        shown = self.shown
        if shown is None:
            return False
        for _ in range(SAVE_ATTEMPTS):
            try:
                entity = model.apply_edits(shown.entity, edits, force)
            except model.EditConflict as e:
                self.conflict(edits, e, force)
                return False
            try:
                response = shown.client.put_entity(entity=model.entity_to_wire(entity))
            except (darq.DarqError, TimeoutError) as e:
                logging.error(f"Can't save {shown.item_id}: {e!r}")
                self.show_status(f"Can't save: {e}")
                return False
            if response.status == KBStatus.CONFLICT:
                if not self.read(shown, links=False):
                    return False
                continue
            if response.status != KBStatus.OK:
                why = "a value doesn't fit its property" if response.status == KBStatus.INVALID_STATEMENT \
                    else response.status.name
                self.show_status(f"Can't save: {why}")
                return False
            self.saved(edits, undoing)
            return True
        self.show_status("Can't save: it keeps being changed")
        return False

    def saved(self, edits: list[model.Edit], undoing: bool | None):
        """Edits have been saved: read the item as it is, and keep them to
        undo or redo."""
        assert self.shown is not None
        if undoing is None:
            self.undo_stack.append(edits)
            self.redo_stack.clear()
        elif undoing:
            self.redo_stack.append(model.invert(edits))
        else:
            self.undo_stack.append(edits)
        if not self.recorded_edit:
            self.lens.record(self.shown.item_id, darq.history.EDITED)
            self.recorded_edit = True
        self.form.conflict = None
        self.read(self.shown, links=False)
        self.title.setText(render.best(self.shown.entity.labels, self.chain) or self.shown.entity.id)
        self.show_status(self.note(edits))

    def note(self, edits: list[model.Edit]) -> str:
        """Return a note on edits that kept a source's statement its own."""
        for e in edits:
            if isinstance(e, model.SetQualifier) and e.old is None and e.new is not None \
                    and e.new.property_id == model.REASON_FOR_DEPRECATED_RANK \
                    and isinstance(e.new.value, model.EntityValue):
                statement = model.find_statement(self.entity(), e.statement_id)
                sources = [self.name(a.value.entity_id) for r in (statement.references if statement else [])
                           for a in r.snaks if isinstance(a.value, model.EntityValue)]
                source = ", ".join(sources) or "a source"
                if e.new.value.entity_id == model.REPLACED:
                    return f"Stated in {source}: kept, deprecated, and replaced with yours"
                return f"Stated in {source}: kept, deprecated, and hidden"
        return "Saved"

    def conflict(self, edits: list[model.Edit], error: model.EditConflict, forced: bool):
        """Show that edits can't be made, as what they change has been
        changed elsewhere."""
        target = error.edit.target()
        what = self.describe(target)
        if forced:
            self.form.show_conflict(f"{what} has been removed elsewhere: your change can't be made.", target,
                                    None)
        else:
            self.form.show_conflict(f"{what} has been changed elsewhere, as you changed it.", target,
                                    lambda: self.keep_mine(edits))
        self.show_status("")

    def keep_mine(self, edits: list[model.Edit]):
        self.form.conflict = None
        self.commit(edits, force=True)
        self.form.rebuild()

    def describe(self, target: str) -> str:
        """Return what an edit's target is, to show."""
        kind, _, rest = target.partition(":")
        if kind == "statement":
            statement = model.find_statement(self.entity(), rest)
            return f"A {self.name(statement.get_property_id())} statement" if statement else "A statement"
        return {"labels": "The label", "descriptions": "The description", "aliases": "The aliases"}.get(
            kind, target)

    def undo(self):
        self.step(self.undo_stack, True)

    def redo(self):
        self.step(self.redo_stack, False)

    def step(self, stack: list[list[model.Edit]], undoing: bool):
        if not self.editing or not stack:
            return
        edits = stack.pop()
        if not self.commit(model.invert(edits) if undoing else edits, undoing=undoing):
            stack.append(edits)
        self.form.rebuild()

    def watch(self, shown: Shown):
        """Have changes to an item reported, to refresh it."""
        listener = darq.open_port()
        self.lens.listen(listener, self)
        try:
            response = shown.client.watch_entity(listener=listener)
            if response.status != KBStatus.OK:
                raise darq.DarqError(response.status.name)
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't watch {shown.item_id}: {e!r}")
            self.lens.unlisten(listener)
            return
        shown.listener = listener

    def leave(self):
        """Stop showing, and close, the item shown."""
        if self.editing:
            self.finish_editing(refresh=False)
        shown, self.shown = self.shown, None
        if shown is not None:
            self.close_item(shown)

    def close_item(self, shown: Shown):
        if shown.listener is not None:
            # The KB stops watching when the port's destroyed.
            self.lens.unlisten(shown.listener)
            shown.listener = None
        try:
            shown.client.close()
        except (darq.DarqError, TimeoutError) as e:
            logging.warning(f"Can't close {shown.item_id}: {e!r}")
        darq.close_port(shown.port)

    # Following links

    def follow(self, item_id: str, new_window: bool = False):
        """Show a linked item: in this window, after the one shown, or in a
        new one."""
        if new_window:
            self.lens.opener.use(item_id)
            self.show_status(f"Opening {item_id} in a new window ...")
            return

        try:
            port = self.lens.opener.open(item_id)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't open {item_id}: {e}")
            return
        previous = (self.shown.item_id, self.scroll_position()) if self.shown is not None else None
        if self.show_item(item_id, port) and previous is not None:
            self.back.append(previous)
            self.update_back()
            self.show_status("")

    def go_back(self):
        """Show the item shown before, as it was scrolled."""
        if not self.back:
            return
        item_id, scroll = self.back[-1]
        try:
            port = self.lens.opener.open(item_id)
        except (darq.DarqError, TimeoutError) as e:
            self.show_status(f"Can't go back to {item_id}: {e}")
            return
        if self.show_item(item_id, port):
            self.back.pop()
            bar = self.browser.verticalScrollBar()
            if bar is not None:
                bar.setValue(scroll)
            self.update_back()
            self.show_status("")

    def update_back(self):
        self.back_button.setEnabled(bool(self.back))

    def target(self, href: str) -> str | None:
        """Return the item a link leads to, from its anchor."""
        if not href.startswith("#") or not href[1:].isdigit():
            return None
        n = int(href[1:])
        return self.targets[n] if n < len(self.targets) else None

    def on_anchor(self, url: QtCore.QUrl):
        item_id = self.target("#" + url.fragment())
        if item_id is not None:
            new_window = bool(QApplication.keyboardModifiers() & Modifier.ControlModifier)
            self.follow(item_id, new_window)

    def on_context_menu(self, pos: QtCore.QPoint):
        menu = QMenu(self)
        item_id = self.target(self.browser.anchorAt(pos))
        if item_id is not None:
            menu.addAction("Open", lambda: self.follow(item_id))
            menu.addAction("Open in New Window", lambda: self.follow(item_id, new_window=True))
            menu.addSeparator()
        back = menu.addAction("Back", self.go_back)
        if back is not None:
            back.setEnabled(bool(self.back))
        menu.addAction("Edit", self.start_editing)
        deprecated = menu.addAction("Show Deprecated", self.toggle_deprecated)
        if deprecated is not None:
            deprecated.setCheckable(True)
            deprecated.setChecked(self.show_deprecated)
        menu.addAction("Refresh", self.refresh)
        menu.addAction("Full screen", self.toggle_full_screen)
        menu.addAction("Help", self.toggle_help)
        menu.addSeparator()
        menu.addAction("Close", self.close)
        viewport = self.browser.viewport()
        menu.exec(viewport.mapToGlobal(pos) if viewport is not None else pos)

    # The window

    def show_status(self, message: str):
        self.status.setText(message)

    def toggle_help(self):
        if self.help.isVisible():
            self.help.hide()
        else:
            self.help.adjustSize()
            self.help.move((self.width() - self.help.width()) // 2, (self.height() - self.help.height()) // 2)
            self.help.show()
            self.help.raise_()

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None:
            return
        key, mods = a0.key(), a0.modifiers()
        control = bool(mods & Modifier.ControlModifier)
        if key == Key.Key_Escape and self.help.isVisible():
            self.help.hide()
        elif self.editing:
            if key == Key.Key_Escape:
                if not self.form.revert_focused():
                    self.finish_editing()
            elif key == Key.Key_Z and control:
                self.redo() if mods & Modifier.ShiftModifier else self.undo()
            elif key == Key.Key_E and not control and not self.form.focused():
                self.finish_editing()
            elif key == Key.Key_F1:
                self.toggle_help()
            elif key == Key.Key_W and control:
                self.close()
            else:
                super().keyPressEvent(a0)
        elif key == Key.Key_E and not control:
            self.start_editing()
        elif key == Key.Key_D and not control:
            self.toggle_deprecated()
        elif (key == Key.Key_Left and mods & Modifier.AltModifier) or key == Key.Key_Backspace:
            self.go_back()
        elif key == Key.Key_F5:
            self.refresh()
        elif key == Key.Key_F1:
            self.toggle_help()
        elif key == Key.Key_W and control:
            self.close()
        else:
            super().keyPressEvent(a0)

    def mousePressEvent(self, a0: QMouseEvent | None):
        # Dragging the margin moves the window: the compositor does it,
        # since a Wayland client can't place itself.
        handle = self.windowHandle()
        if a0 is not None and a0.button() == QtCore.Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def closeEvent(self, a0):
        self.leave()
        super().closeEvent(a0)
        if self.on_closed is not None:
            on_closed, self.on_closed = self.on_closed, None
            on_closed()


class ChangeListener(Listener.Handler):
    """Refreshes a window when the KB says its item's changed."""

    def __init__(self, window: EntityWindow):
        self.window = window

    def changed(self, request: Changed, source: int) -> None:
        self.window.changed(request.entity_id, request.revision)


class LaunchOpener:
    """Opens items with darq.launch, and starts lenses with a Launcher."""

    def __init__(self, launcher: Launcher):
        self.launcher = launcher

    def open(self, item_id: str) -> int:
        return darq.launch.open_item(item_id)

    def use(self, item_id: str):
        self.launcher.use(item_id)


class LensServices:
    """What a Launcher uses: darq.launch, and History."""

    def __init__(self, history: History.Client | None):
        self.launch = darq.launch
        self.history = history


class EntityLens(Lens.Handler):
    """Answers the Lens protocol: uses one item, in a window, and is the
    Launcher's host, for items opened in new windows."""

    def __init__(self, loop: QtEventLoop, history: History.Client | None, opener: Opener | None = None):
        self.loop = loop
        self.history = history
        self.window: EntityWindow | None = None
        self.services = typing.cast(typing.Any, LensServices(history))
        self.launcher = Launcher(self, SOURCE, loop.post)
        self.opener: Opener = opener if opener is not None else LaunchOpener(self.launcher)

    def use(self, request: UseRequest, source: int) -> UseResponse:
        if self.window is not None:
            return UseResponse(status=Status.BUSY)
        if request.item is None:
            return UseResponse(status=Status.FAILED)

        window = EntityWindow(self, on_closed=self.finished)
        if not window.show_item(request.item_id, request.item):
            window.on_closed = None
            window.deleteLater()
            return UseResponse(status=Status.FAILED)
        self.window = window
        window.show()
        logging.info(f"Using {request.item_id}")
        return UseResponse(status=Status.OK)

    def close(self, request: Close, source: int) -> None:
        if self.window is not None:
            self.window.close()
        else:
            self.finished()

    def client(self, port: int) -> EntityItem.Client:
        """Return a client for an item's port."""
        return EntityItem.Client(port, timeout=TIMEOUT)

    def listen(self, port: int, window: EntityWindow):
        """Serve the Listener protocol on a port, for a window."""
        assert self.server is not None
        self.server.add(port, ChangeListener(window))

    def unlisten(self, port: int):
        assert self.server is not None
        self.server.remove(port)
        darq.close_port(port)

    def record(self, item_id: str, event: str):
        """Record a History event about an item, if History is running."""
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")

    # As the Launcher's host

    def show_status(self, message: str):
        if self.window is not None:
            self.window.show_status(message)

    def launched(self, context: object, item_id: str | None, error: str | None):
        self.show_status(error or "")

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

    lens = EntityLens(loop, history)
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
