# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Text lens, lenses/text/main.py: its text sync, against a
# fake item; its window and keys, offscreen; its Lens handler; and then
# the whole system, started with darq.launch through a real p-kernel
# (see test_pkernel.py for the fixtures).

import importlib.util
import pathlib

import pytest

from darq.errors import DeadPortError
from darq.protocols.lens import Close, Status, UseRequest
from darq.protocols.text import GetResponse, ReplaceResponse
from darq.protocols.text import Status as TextStatus

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "text"
ITEM_ID = "public.utf8-plain-text:0123456789abcdef0123456789abcdef"


def load():
    spec = importlib.util.spec_from_file_location("text_lens", LENS_PATH / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lens = load()


class FakeText:
    """Stands in for a Text.Client: an item's text, and the calls made."""

    def __init__(self, text: str = ""):
        self.text = text
        self.version = 1
        self.replaces = []
        self.closed = False

        # Set to make the next replace conflict, or every call fail.
        self.conflict_with: str | None = None
        self.dead = False

    def check(self):
        if self.dead:
            raise DeadPortError("item is gone")

    def get(self):
        self.check()
        return GetResponse(text=self.text, version=self.version)

    def replace(self, start, end, text, expected):
        self.check()
        if self.conflict_with is not None:
            # Someone else changed it first.
            self.text, self.conflict_with = self.conflict_with, None
            self.version += 1
            return ReplaceResponse(version=self.version, status=TextStatus.CONFLICT)
        assert expected == self.version
        self.replaces.append((start, end, text))
        self.text = self.text[:start] + text + self.text[end:]
        self.version += 1
        return ReplaceResponse(version=self.version, status=TextStatus.OK)

    def close(self):
        self.check()
        self.closed = True


class FakeHistory:
    def __init__(self):
        self.events = []

    def add_event(self, item_id, event, source):
        self.events.append((item_id, event, source))


def make_sync(text: str = "", history=None) -> tuple:
    client = FakeText(text)
    return lens.TextSync(client, ITEM_ID, text, client.version, history), client


################################################################
# Helpers
################################################################

@pytest.mark.parametrize("old, new, expected", [
    ("", "", (0, 0, 0)),
    ("", "a", (0, 0, 1)),
    ("abc", "abc", (3, 3, 3)),
    ("abc", "abXc", (2, 2, 3)),
    ("abc", "ac", (1, 2, 1)),
    ("abc", "aXYc", (1, 2, 3)),
    ("aaa", "aa", (2, 3, 2)),
    ("abab", "ab", (2, 4, 2)),
    ("x😀y", "xy", (1, 2, 1)),
    ("hello", "", (0, 5, 0)),
])
def test_common_affixes(old, new, expected):
    start, old_end, new_end = lens.common_affixes(old, new)
    assert (start, old_end, new_end) == expected
    assert old[:start] + new[start:new_end] + old[old_end:] == new


def test_utf16_offsets():
    text = "a😀b"
    assert lens.utf16_length(text) == 4
    assert [lens.index_from_utf16(text, n) for n in range(5)] == [0, 1, 2, 2, 3]
    assert lens.index_from_utf16(text, 10) == 3


################################################################
# TextSync
################################################################

def test_change_sends_the_difference():
    history = FakeHistory()
    sync, client = make_sync("hello world", history)
    assert sync.change("hello world") == lens.TextSync.OK
    assert sync.change("hello, world") == lens.TextSync.OK
    assert sync.change("hello, world") == lens.TextSync.OK
    assert sync.change("hello, 😀") == lens.TextSync.OK
    assert sync.change("hello, 😀!") == lens.TextSync.OK
    assert client.replaces == [(5, 5, ","), (7, 12, "😀"), (8, 8, "!")]
    assert (sync.text, sync.version) == ("hello, 😀!", 4)
    assert history.events == [(ITEM_ID, "edited", "text-lens")] * 3


def test_change_conflict_reloads():
    sync, client = make_sync("abc")
    client.conflict_with = "other"
    assert sync.change("abcd") == lens.TextSync.CONFLICT
    assert (sync.text, sync.version) == ("other", 2)
    assert client.replaces == []


def test_change_failure():
    sync, client = make_sync("abc")
    client.dead = True
    assert sync.change("abcd") == lens.TextSync.FAILED
    assert not sync.available
    sync.close()
    assert not client.closed


def test_close():
    sync, client = make_sync()
    sync.close()
    sync.close()
    assert client.closed and not sync.available


def test_no_history():
    sync, _ = make_sync()
    sync.record("opened")


################################################################
# Window and keys, offscreen
################################################################

@pytest.fixture
def clipboard(qt_app):
    clipboard = qt_app.clipboard()
    clipboard.setText("")
    return clipboard


@pytest.fixture
def window(qt_app, clipboard):
    created = []

    def make(text: str = ""):
        sync, client = make_sync(text, FakeHistory())
        closed = []
        window = lens.TextWindow(sync, on_closed=lambda: closed.append(True))
        window.show()
        created.append(window)
        return window, client, closed

    yield make
    for window in created:
        window.on_closed = None
        window.close()


def key(window, k, ctrl=False, alt=False, text=""):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    modifiers = Qt.KeyboardModifier.NoModifier
    if ctrl:
        modifiers |= Qt.KeyboardModifier.ControlModifier
    if alt:
        modifiers |= Qt.KeyboardModifier.AltModifier
    window.edit.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, k, modifiers, text))


def type_text(window, text):
    from PyQt6.QtCore import Qt
    for c in text:
        key(window, Qt.Key.Key_A, text=c)


def position(window) -> int:
    return window.edit.textCursor().position()


def test_loading_sends_nothing(window):
    w, client, _ = window("first line\nsecond")
    assert w.document_text() == "first line\nsecond"
    assert client.replaces == []


def test_typing_sends_each_change(window):
    from PyQt6.QtCore import Qt
    w, client, _ = window("")
    type_text(w, "hi")
    key(w, Qt.Key.Key_Return, text="\r")
    type_text(w, "😀")
    key(w, Qt.Key.Key_Backspace, text="\b")
    assert client.replaces == [(0, 0, "h"), (1, 1, "i"), (2, 2, "\n"), (3, 3, "😀"), (3, 4, "")]
    assert client.text == w.sync.text == "hi\n"


def test_movement_keys(window):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, _, _ = window("one two\nthree")
    key(w, K.Key_E, ctrl=True)
    assert position(w) == 7
    key(w, K.Key_B, ctrl=True)
    key(w, K.Key_A, ctrl=True)
    assert position(w) == 0
    key(w, K.Key_F, alt=True)
    assert position(w) == 3
    key(w, K.Key_F, alt=True)
    assert position(w) == 7
    key(w, K.Key_B, alt=True)
    assert position(w) == 4
    key(w, K.Key_N, ctrl=True)
    assert w.edit.textCursor().blockNumber() == 1
    key(w, K.Key_P, ctrl=True)
    assert w.edit.textCursor().blockNumber() == 0
    key(w, K.Key_Greater, alt=True)
    assert position(w) == 13
    key(w, K.Key_Less, alt=True)
    assert position(w) == 0
    key(w, K.Key_F, ctrl=True)
    assert position(w) == 1


def test_kill_line_and_yank(window, clipboard):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, client, _ = window("one two\nthree")
    key(w, K.Key_F, alt=True)
    key(w, K.Key_K, ctrl=True)
    assert (client.text, clipboard.text()) == ("one\nthree", " two")

    # At the end of a line, it kills the line break.
    key(w, K.Key_K, ctrl=True)
    assert (client.text, clipboard.text()) == ("onethree", "\n")

    key(w, K.Key_Y, ctrl=True)
    assert client.text == "one\nthree"


def test_region(window, clipboard):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, client, _ = window("abcdef")
    key(w, K.Key_F, ctrl=True)
    key(w, K.Key_Space, ctrl=True)
    key(w, K.Key_F, ctrl=True)
    key(w, K.Key_F, ctrl=True)
    assert w.edit.textCursor().selectedText() == "bc"
    key(w, K.Key_W, alt=True)
    assert (client.text, clipboard.text()) == ("abcdef", "bc")
    assert not w.edit.textCursor().hasSelection()

    # The cursor's left after the region.
    key(w, K.Key_Space, ctrl=True)
    key(w, K.Key_E, ctrl=True)
    key(w, K.Key_W, ctrl=True)
    assert (client.text, clipboard.text()) == ("abc", "def")

    # Typing ends the region.
    key(w, K.Key_Space, ctrl=True)
    type_text(w, "x")
    key(w, K.Key_B, ctrl=True)
    assert not w.edit.textCursor().hasSelection()


def test_delete_and_kill_word(window, clipboard):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, client, _ = window("one two")
    key(w, K.Key_D, ctrl=True)
    assert client.text == "ne two"
    key(w, K.Key_D, alt=True)
    assert (client.text, clipboard.text()) == (" two", "ne")
    key(w, K.Key_D, alt=True)
    assert (client.text, clipboard.text()) == ("", " two")


@pytest.mark.parametrize("text, expected", [
    ("", ""),
    ("hello", "Hello"),
    ("HELLO", "Hello"),
    ("  (wORLD", "  (World"),
    ("2nd", "2nd"),
    ("ǆemal", "ǅemal"),
    ("...", "..."),
])
def test_capitalize(text, expected):
    assert lens.capitalize(text) == expected


def test_word_case(window):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, client, _ = window("one two three four")
    key(w, K.Key_U, alt=True)
    assert (client.text, position(w)) == ("ONE two three four", 3)
    key(w, K.Key_C, alt=True)
    assert (client.text, position(w)) == ("ONE Two three four", 7)

    # From the middle of a word, to its end.
    key(w, K.Key_B, alt=True)
    key(w, K.Key_F, ctrl=True)
    key(w, K.Key_U, alt=True)
    assert (client.text, position(w)) == ("ONE TWO three four", 7)
    key(w, K.Key_L, alt=True)
    assert (client.text, position(w)) == ("ONE TWO three four", 13)
    key(w, K.Key_E, ctrl=True)
    key(w, K.Key_B, alt=True)
    key(w, K.Key_F, ctrl=True)
    key(w, K.Key_U, alt=True)
    assert (client.text, position(w)) == ("ONE TWO three fOUR", 18)

    # At the end, there's no word left.
    key(w, K.Key_L, alt=True)
    assert (client.text, position(w)) == ("ONE TWO three fOUR", 18)


def test_word_case_across_lines(window):
    from PyQt6.QtCore import Qt
    w, client, _ = window("one\ntwo")
    key(w, Qt.Key.Key_E, ctrl=True)
    key(w, Qt.Key.Key_C, alt=True)
    assert (client.text, position(w)) == ("one\nTwo", 7)


def test_word_case_read_only(window):
    from PyQt6.QtCore import Qt
    w, client, _ = window("one")
    w.set_read_only(True)
    key(w, Qt.Key.Key_U, alt=True)
    assert (w.edit.toPlainText(), position(w)) == ("one", 0)
    assert w.status.text() == "Read only"


def test_undo(window):
    from PyQt6.QtCore import Qt
    w, client, _ = window("abc")
    type_text(w, "x")
    key(w, Qt.Key.Key_Slash, ctrl=True)
    assert client.text == "abc"


def test_read_only(window):
    from PyQt6.QtCore import Qt
    K = Qt.Key
    w, client, _ = window("abc")
    key(w, K.Key_X, ctrl=True)
    key(w, K.Key_Q, ctrl=True)
    assert w.edit.isReadOnly()
    type_text(w, "x")
    key(w, K.Key_D, ctrl=True)
    key(w, K.Key_K, ctrl=True)
    key(w, K.Key_Y, ctrl=True)
    assert client.replaces == []
    assert w.status.text() == "Read only"

    # Movement still works.
    key(w, K.Key_E, ctrl=True)
    assert position(w) == 3

    key(w, K.Key_X, ctrl=True)
    key(w, K.Key_Q, ctrl=True)
    assert not w.edit.isReadOnly()
    type_text(w, "x")
    assert client.text == "abcx"


def test_unknown_prefixed_key(window):
    from PyQt6.QtCore import Qt
    w, client, _ = window("abc")
    key(w, Qt.Key.Key_X, ctrl=True)
    key(w, Qt.Key.Key_Z, ctrl=True)
    assert w.status.text() == "C-x: not a command"
    type_text(w, "x")
    assert client.text == "xabc"


def test_conflict_reloads(window):
    w, client, _ = window("abc")
    client.conflict_with = "other text"
    type_text(w, "x")
    assert w.document_text() == "other text"
    assert w.status.text() == "Changed elsewhere: reloaded"
    assert client.replaces == []

    # And editing carries on from the reloaded text.
    type_text(w, "y")
    assert client.text == w.document_text()


def test_failure_makes_it_read_only(window):
    w, client, _ = window("abc")
    client.dead = True
    type_text(w, "x")
    assert w.edit.isReadOnly()
    assert w.status.text() == "Read only: the item isn't answering"
    w.set_read_only(False)
    assert w.edit.isReadOnly()


def test_text_it_cannot_hold_is_read_only(window):
    w, _, _ = window("a b")
    assert w.edit.isReadOnly()
    assert "can't edit" in w.status.text()


def test_non_breaking_space_is_kept(window):
    w, client, _ = window("a b")
    assert not w.edit.isReadOnly()
    type_text(w, "x")
    assert client.text == "xa b"


def test_close_closes_the_item(window):
    from PyQt6.QtCore import Qt
    w, client, closed = window("abc")
    key(w, Qt.Key.Key_X, ctrl=True)
    key(w, Qt.Key.Key_C, ctrl=True)
    assert client.closed and closed == [True]


def test_help(window):
    from PyQt6.QtCore import Qt
    w, _, _ = window()
    key(w, Qt.Key.Key_F1)
    assert w.help.isVisible()
    assert "C-k" in w.help.text() and "C-x C-q" in w.help.text()
    key(w, Qt.Key.Key_F1)
    assert not w.help.isVisible()


################################################################
# Lens handler
################################################################

class FakeLoop:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture
def handler(qt_app, monkeypatch):
    items = {}

    class Clients:
        @staticmethod
        def Client(port, timeout=None):
            return items[port]

    monkeypatch.setattr(lens, "Text", Clients)
    loop = FakeLoop()
    history = FakeHistory()
    handler = lens.TextLens(loop, history)
    yield handler, items, loop, history
    if handler.window is not None:
        handler.window.on_closed = None
        handler.window.close()


def test_use(handler):
    h, items, loop, history = handler
    items[7] = FakeText("hello")
    assert h.use(UseRequest(item_id=ITEM_ID, item=7), 0).status == Status.OK
    assert h.window is not None and h.window.isVisible()
    assert h.window.document_text() == "hello"
    assert history.events == [(ITEM_ID, "opened", "text-lens")]

    # One item per lens.
    items[8] = FakeText()
    assert h.use(UseRequest(item_id=ITEM_ID, item=8), 0).status == Status.BUSY

    h.close(Close(), 0)
    assert items[7].closed and loop.stopped


def test_use_failures(handler):
    h, items, loop, _ = handler
    assert h.use(UseRequest(item_id=ITEM_ID, item=None), 0).status == Status.FAILED
    items[7] = FakeText()
    items[7].dead = True
    assert h.use(UseRequest(item_id=ITEM_ID, item=7), 0).status == Status.FAILED
    assert h.window is None and not loop.stopped


def test_close_before_use(handler):
    h, _, loop, _ = handler
    h.close(Close(), 0)
    assert loop.stopped


def test_expire(handler):
    h, items, loop, _ = handler
    items[7] = FakeText()
    h.use(UseRequest(item_id=ITEM_ID, item=7), 0)
    h.expire()
    assert not loop.stopped

    h.window.on_closed = None
    h.window.close()
    h.window = None
    h.expire()
    assert loop.stopped


################################################################
# System
################################################################

def test_text_lens(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # Plays the Terminal: creates an item, writes to it, and has the
    # lens use it.
    client_path = write_app(tmp_path / "client", """
        import time
        import darq
        import darq.launch
        from darq.protocols.history import History
        from darq.protocols.lens import Lens, Status as LensStatus
        from darq.protocols.text import Text
        from darq.protocols.type import Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)

            # Wait for the sync to register the implementation and lens.
            deadline = time.monotonic() + 30
            while not types.find_lenses(type_id="public.utf8-plain-text", context="terminal").lenses:
                if time.monotonic() > deadline:
                    return "no text lens registered"
                time.sleep(0.05)
            if "public.utf8-plain-text" not in types.list_types(implemented=True).type_ids:
                return "text type isn't listed as implemented"
            path = darq.launch.find_lens("public.utf8-plain-text")
            if not path.endswith("lenses/text"):
                return f"lens is {path}"

            item_id, port = darq.launch.create_item("public.utf8-plain-text")
            item = Text.Client(port, timeout=10)
            item.replace(start=0, end=0, text="Hello", expected=0)

            lens = Lens.Client(darq.launch.use_item(item_id, port), timeout=10)

            # It's opened, by the lens.
            history = History.Client(darq.connect("darq.history"), timeout=10)
            events = [(e.event, e.source) for e in history.get_events(item_ids=[item_id]).events]
            if events != [("opened", "text-lens")]:
                return f"events are {events!r}"

            # It uses one item.
            other_id, other = darq.launch.create_item("public.utf8-plain-text")
            if lens.use(item_id=other_id, item=other).status != LensStatus.BUSY:
                return "second use wasn't refused"
            Text.Client(other, timeout=10).close()

            lens.close()
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)

    # The lens exits once closed.
    [lens_process] = [p for p in kernel._process_table.values() if p._image_path == str(LENS_PATH)]
    run_kernel_until(kernel, lambda: lens_process.get_status() == zombie, "the lens to exit")
    assert (lens_process.get_exit_status(), lens_process.get_exit_error()) == (0, None)
