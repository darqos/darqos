# darqos
# Copyright (C) 2026 David Arnold

# Tests for the compositor's policy channel: compositor/policy.c,
# through policy-check.c, against the classes didlpygen generated from
# lib/didl/compositor.didl; and then the compositor itself, headless,
# through darq/compositor.py, if it's been built (see doc/terminal.rst).

import os
import pathlib
import re
import select
import shutil
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from darq.compositor import COMPOSITOR, Compositor, PolicyChannel
from darq.errors import ChannelClosedError, MessageDecodingError
from darq.protocols import compositor as protocol
from darq.protocols.compositor import (
    BindHotkey,
    CloseWindow,
    FocusWindow,
    HotkeyPressed,
    Lock,
    Modifier,
    RaiseWindow,
    Ready,
    SystemReleased,
    UnbindHotkey,
    Unlock,
    WindowAdded,
    WindowFocused,
    WindowRemoved,
    WindowRetitled,
)


SOURCE = pathlib.Path(__file__).parent.parent / "compositor"

# xkb keysyms.
XK_n = 0x6e
XK_Tab = 0xff09


################################################################
# policy.c
################################################################

@pytest.fixture(scope="module")
def policy_check(tmp_path_factory):
    """policy-check, built here, running: send it a line, get a line."""
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    program = tmp_path_factory.mktemp("policy") / "policy-check"
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(program),
                    str(SOURCE / "policy-check.c"), str(SOURCE / "policy.c")], check=True)
    process = subprocess.Popen([program], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def run(line: str) -> str:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(line + "\n")
        process.stdin.flush()
        return process.stdout.readline().strip()

    yield run
    process.communicate()


def encoded(policy_check, command: str):
    """Encode a Display message with policy.c, and decode it with Python."""
    data = bytes.fromhex(policy_check(command))
    message = protocol.Display.REQUESTS[int.from_bytes(data[6:8], "little")]()
    message.decode(data)
    assert message.encode()[0] == data
    return message


def hexed(text: bytes) -> str:
    return text.hex() or "-"


@pytest.mark.parametrize("command, message", [
    ("ready", Ready()),
    (f"hotkey {XK_Tab} {Modifier.SHIFT | Modifier.SUPER}", HotkeyPressed(keysym=XK_Tab, modifiers=9)),
    ("released", SystemReleased()),
    (f"added 7 {hexed(b'Notes')} {hexed(b'darq.text')}",
     WindowAdded(window=7, title="Notes", app_id="darq.text")),
    (f"added 8 - {hexed(b'x')}", WindowAdded(window=8, title="", app_id="x")),
    (f"added 9 {hexed(b'12345678')} -", WindowAdded(window=9, title="12345678", app_id="")),
    ("removed 4294967295", WindowRemoved(window=0xFFFFFFFF)),
    (f"retitled 3 {hexed('Ünïcode ✓ 🦉'.encode())}", WindowRetitled(window=3, title="Ünïcode ✓ 🦉")),
    ("focused 0", WindowFocused(window=0)),
])
def test_encode(policy_check, command, message):
    assert encoded(policy_check, command) == message


@pytest.mark.parametrize("raw, text", [
    (b"a\xffb", "a�b"),
    (b"\xc3", "�"),                        # Cut short.
    (b"\xc0\xaf", "��"),              # Overlong.
    (b"\xed\xa0\x80", "�" * 3),            # A surrogate.
    (b"\xf4\x90\x80\x80", "�" * 4),        # Beyond U+10FFFF.
    (b"\xf0\x9f\xa6\x89", "\U0001f989"),
])
def test_invalid_utf8_replaced(policy_check, raw, text):
    assert encoded(policy_check, f"retitled 1 {raw.hex()}").title == text


def test_long_strings_cut(policy_check):
    # At 1024 bytes, and not in the middle of a character.
    title = "a" * 1023 + "é"
    message = encoded(policy_check, f"added 1 {hexed(title.encode())} {hexed(b'b' * 2000)}")
    assert message.title == "a" * 1023
    assert message.app_id == "b" * 1024


@pytest.mark.parametrize("message, fields", [
    (BindHotkey(keysym=XK_Tab, modifiers=Modifier.SHIFT), (1, XK_Tab, 1, 0)),
    (UnbindHotkey(keysym=XK_n, modifiers=0), (2, XK_n, 0, 0)),
    (Lock(), (3, 0, 0, 0)),
    (Unlock(), (4, 0, 0, 0)),
    (FocusWindow(window=12), (5, 0, 0, 12)),
    (RaiseWindow(window=0xFFFFFFFF), (6, 0, 0, 0xFFFFFFFF)),
    (CloseWindow(window=1), (7, 0, 0, 1)),
])
def test_decode(policy_check, message, fields):
    data, handles = message.encode()
    assert handles == []
    assert policy_check(f"decode {data.hex()}") == " ".join(map(str, fields))


@pytest.mark.parametrize("data, error", [
    (b"", "too short for the header"),
    (Lock().encode()[0][:7], "too short for the header"),
    (Lock(txid=1).encode()[0], "txid isn't 0"),
    (b"\0\0\0\0\1\0\3\0", "flags aren't 0"),
    (b"\0\0\0\0\0\0\x08\0", "unknown method"),
    (b"\0\0\0\0\0\0\0\0", "unknown method"),
    (FocusWindow(window=1).encode()[0][:-1], "wrong length for its method"),
    (Lock().encode()[0] + b"\0", "wrong length for its method"),
])
def test_decode_bad(policy_check, data, error):
    assert policy_check(f"decode {hexed(data)}") == f"error: {error}"


def test_constants_match_didl():
    # policy.h's enums, against the IR's method numbers and Modifier.
    header = (SOURCE / "policy.h").read_text()
    constants = {name: int(value) for name, value in re.findall(r"\bPOLICY_(\w+) = (\d+),", header)}

    def name(cls) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).upper()

    methods = [*protocol.Display.REQUESTS.items(), *protocol.Policy.REQUESTS.items()]
    expected = {name(cls): number for number, cls in methods}
    expected |= {modifier.name: modifier.value for modifier in Modifier}
    assert len(expected) == len(methods) + len(Modifier)
    assert constants == expected


################################################################
# The compositor
################################################################

# A Wayland client: a window with a title, and an app_id, which takes
# commands on stdin, and prints "exposed" once it's shown.
CLIENT = textwrap.dedent("""
    import sys
    from PyQt6.QtCore import QSocketNotifier
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QRasterWindow

    class Window(QRasterWindow):
        def paintEvent(self, event):
            QPainter(self).fillRect(0, 0, self.width(), self.height(), QColor("teal"))

        def exposeEvent(self, event):
            super().exposeEvent(event)
            if self.isExposed():
                print("exposed", flush=True)

    def command():
        line = sys.stdin.readline().split()
        if not line or line[0] == "quit":
            app.quit()
        elif line[0] == "title":
            window.setTitle(line[1])

    app = QGuiApplication(sys.argv)
    app.setDesktopFileName("darq-test")
    window = Window()
    window.setTitle(sys.argv[1])
    window.resize(200, 100)
    window.show()
    notifier = QSocketNotifier(0, QSocketNotifier.Type.Read)
    notifier.activated.connect(command)
    sys.exit(app.exec())
""")

# evdev keycodes.
KEY_TAB = 15
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_X = 45
KEY_N = 49


class Harness:
    """A compositor, and what the tests need of it."""

    def __init__(self, started: Compositor, env: dict[str, str]):
        self.started = started
        self.channel = started.channel
        self.process = started.process
        self.wayland = started.wayland

        # Its environment, for clients, and theirs, to clean up.
        self.env = env
        self.clients: list[subprocess.Popen] = []

    def stop(self) -> int:
        return self.started.stop()


@pytest.fixture
def compositor(tmp_path):
    """The compositor, headless, with the test keyboard."""
    if not COMPOSITOR.exists():
        pytest.skip("the compositor isn't built: see doc/terminal.rst")
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WAYLAND_", "QT_"))}
    env |= {"XDG_RUNTIME_DIR": str(runtime), "WLR_BACKENDS": "headless", "WLR_RENDERER": "pixman"}
    with open(tmp_path / "compositor.log", "w") as log:
        harness = Harness(Compositor.start(args=["-k"], env=env, stdin=subprocess.PIPE, stderr=log), env)
        yield harness

        for client in harness.clients:
            client.kill()
            client.communicate()
        harness.stop()
        assert harness.process.stdin is not None
        harness.process.stdin.close()


def receive(compositor, timeout: float = 10):
    """The next message from the compositor."""
    deadline = time.monotonic() + timeout
    while (message := compositor.channel.receive()) is None:
        left = deadline - time.monotonic()
        assert left > 0, "no message from the compositor"
        select.select([compositor.channel], [], [], left)
    return message


def nothing_more(compositor, wait: float = 0.3):
    select.select([compositor.channel], [], [], wait)
    assert compositor.channel.receive() is None


def settle():
    """Let the compositor read the policy channel: it's another socket
    from the test keyboard, so their order isn't kept."""
    time.sleep(0.2)


def keys(compositor, *events: tuple[int, int]):
    assert compositor.process.stdin is not None
    compositor.process.stdin.write("".join(f"{code} {state}\n" for code, state in events).encode())
    compositor.process.stdin.flush()


def client(compositor, title: str, **env):
    """Start a Wayland client, and wait for its window to be shown."""
    process = subprocess.Popen(
        [sys.executable, "-c", CLIENT, title], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, env=compositor.env | {"QT_QPA_PLATFORM": "wayland", "QT_QPA_PLATFORMTHEME": "generic"} | env,
        pass_fds=[int(env["WAYLAND_SOCKET"])] if "WAYLAND_SOCKET" in env else [])
    compositor.clients.append(process)
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "exposed"
    return process


def test_hotkeys(compositor):
    assert receive(compositor) == Ready()
    channel = compositor.channel
    channel.send(BindHotkey(keysym=XK_n, modifiers=0))
    channel.send(BindHotkey(keysym=XK_Tab, modifiers=Modifier.SHIFT))
    settle()

    # Sys-n; Sys-Shift-Tab, by its first level's keysym; Sys-x isn't
    # bound.  Then System's release ends the chord.
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_N, 1), (KEY_N, 0),
         (KEY_LEFTSHIFT, 1), (KEY_TAB, 1), (KEY_TAB, 0), (KEY_LEFTSHIFT, 0),
         (KEY_X, 1), (KEY_X, 0), (KEY_LEFTCTRL, 0))
    assert receive(compositor) == HotkeyPressed(keysym=XK_n, modifiers=0)
    assert receive(compositor) == HotkeyPressed(keysym=XK_Tab, modifiers=Modifier.SHIFT)
    assert receive(compositor) == SystemReleased()

    # Only exact modifiers match, and unbound hotkeys go.  With no
    # hotkey pressed, System's release is nothing either.
    channel.send(UnbindHotkey(keysym=XK_n, modifiers=0))
    settle()
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_N, 1), (KEY_N, 0), (KEY_TAB, 1), (KEY_TAB, 0),
         (KEY_LEFTCTRL, 0))
    nothing_more(compositor)

    # Nor is a bad message from the Terminal: it's logged.
    channel.socket.send(b"\0\0\0\0\0\0\x63\0")
    nothing_more(compositor)
    assert compositor.process.poll() is None


def test_windows(compositor):
    assert receive(compositor) == Ready()

    # The Terminal's own window isn't in the window list.
    client(compositor, "darq:background", WAYLAND_SOCKET=str(compositor.wayland.fileno()))
    nothing_more(compositor)

    # Another client's is, and it's focused when it's mapped.
    app = client(compositor, "first", WAYLAND_DISPLAY="darq-0")
    added = receive(compositor)
    assert added == WindowAdded(window=added.window, title="first", app_id="darq-test")
    assert receive(compositor) == WindowFocused(window=added.window)

    assert app.stdin is not None
    app.stdin.write("title second\n")
    app.stdin.flush()
    assert receive(compositor) == WindowRetitled(window=added.window, title="second")

    # Unknown windows are ignored; a known one's client is asked to
    # close it.
    compositor.channel.send(RaiseWindow(window=added.window))
    compositor.channel.send(FocusWindow(window=added.window))
    compositor.channel.send(CloseWindow(window=added.window + 100))
    compositor.channel.send(CloseWindow(window=added.window))
    assert receive(compositor) == WindowRemoved(window=added.window)
    assert receive(compositor) == WindowFocused(window=0)
    assert app.wait(10) == 0


def test_exits_when_channel_closed(compositor):
    assert receive(compositor) == Ready()
    assert compositor.stop() == 0
    assert not (pathlib.Path(compositor.env["XDG_RUNTIME_DIR"]) / "darq-0").exists()


def test_channel_closed_by_compositor(compositor):
    assert receive(compositor) == Ready()
    compositor.process.terminate()
    assert compositor.process.wait(10) == 0
    with pytest.raises(ChannelClosedError):
        receive(compositor)


def test_bad_message_from_compositor():
    # Pretend to be the compositor, on a pair of our own.
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    channel = PolicyChannel(ours)
    theirs.send(b"\0\0\0\0\0\0\x63\0")
    with pytest.raises(MessageDecodingError, match="unknown method 99"):
        channel.receive()
    theirs.send(WindowFocused(window=1).encode()[0][:-1])
    with pytest.raises(MessageDecodingError):
        channel.receive()
    assert channel.receive() is None
    theirs.close()
    with pytest.raises(ChannelClosedError):
        channel.receive()
    channel.close()
