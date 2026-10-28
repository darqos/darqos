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
import signal
import socket
import subprocess
import sys
import textwrap
import threading
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

# A Wayland client, with windows, which takes commands on stdin: "open
# <name>", "title <name> <title>", "hide <name>", "show <name>", and
# "quit".  A window is first titled with its name, and the app_id is
# darq-test.  It prints what happens to its windows, after their names:
# "exposed", "active <0|1>", "size <width> <height>", and "key <text>".
CLIENT = textwrap.dedent("""
    import sys
    from PyQt6.QtCore import QSocketNotifier
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QRasterWindow

    class Window(QRasterWindow):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.setTitle(name)
            self.resize(200, 100)
            self.activeChanged.connect(lambda: self.say("active", int(self.isActive())))

        def say(self, *words):
            print(self.name, *words, flush=True)

        def paintEvent(self, event):
            QPainter(self).fillRect(0, 0, self.width(), self.height(), QColor("teal"))

        def exposeEvent(self, event):
            super().exposeEvent(event)
            if self.isExposed():
                self.say("exposed")

        def resizeEvent(self, event):
            self.say("size", event.size().width(), event.size().height())

        def keyPressEvent(self, event):
            self.say("key", event.text())

        def closeEvent(self, event):
            app.quit()

    def command():
        line = sys.stdin.readline().split()
        if not line or line[0] == "quit":
            app.quit()
        elif line[0] == "open":
            windows[line[1]] = Window(line[1])
            windows[line[1]].show()
        elif line[0] == "title":
            windows[line[1]].setTitle(line[2])
        elif line[0] == "hide":
            windows[line[1]].hide()
        elif line[0] == "show":
            windows[line[1]].show()

    app = QGuiApplication(sys.argv)
    app.setDesktopFileName("darq-test")
    app.setQuitOnLastWindowClosed(False)
    windows = {}
    notifier = QSocketNotifier(0, QSocketNotifier.Type.Read)
    notifier.activated.connect(command)
    sys.exit(app.exec())
""")

# evdev keycodes, and a button.
KEY_TAB = 15
KEY_W = 17
KEY_T = 20
KEY_LEFTCTRL = 29
KEY_A = 30
KEY_LEFTSHIFT = 42
KEY_X = 45
KEY_B = 48
KEY_N = 49
KEY_RIGHT = 106
BTN_LEFT = 272

# The headless backend's output.
OUTPUT = (1280, 720)


class Client:
    """A Wayland client process, running CLIENT, and what it's printed."""

    def __init__(self, process: subprocess.Popen):
        self.process = process

        # Lines it's printed, and not yet expected.
        self.lines: list[str] = []
        self.printing = threading.Condition()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        assert self.process.stdout is not None
        with self.process.stdout:
            for line in self.process.stdout:
                with self.printing:
                    self.lines.append(line.strip())
                    self.printing.notify_all()

    def command(self, line: str):
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def open(self, name: str) -> "Window":
        """Open a window, and wait for it to be shown."""
        window = Window(self, name)
        self.command(f"open {name}")
        window.expect("exposed")
        return window

    def stop(self):
        self.process.kill()
        self.process.wait()
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.reader.join()


class Window:
    """One of a Client's windows."""

    def __init__(self, client: Client, name: str):
        self.client = client
        self.name = name
        self.process = client.process

    def command(self, verb: str, *args: str):
        self.client.command(" ".join([verb, self.name, *args]))

    def expect(self, line: str, timeout: float = 10):
        """Wait for it to print a line, if it hasn't already; the lines
        before it are kept, for later."""
        client = self.client
        line = f"{self.name} {line}"
        with client.printing:
            if not client.printing.wait_for(lambda: line in client.lines, timeout):
                pytest.fail(f"the client didn't print {line!r}, only {client.lines}")
            client.lines.remove(line)

    def printed(self, wait: float = 0.3) -> list[str]:
        """What it's printed, and not been expected, after a while."""
        time.sleep(wait)
        prefix = self.name + " "
        with self.client.printing:
            return [line.removeprefix(prefix) for line in self.client.lines if line.startswith(prefix)]


class Harness:
    """A compositor, and what the tests need of it."""

    def __init__(self, started: Compositor, env: dict[str, str]):
        self.started = started
        self.channel = started.channel
        self.process = started.process
        self.wayland = started.wayland

        # Its environment, for clients, and theirs, to clean up.
        self.env = env
        self.clients: list[Client] = []

    def stop(self) -> int:
        return self.started.stop()


def headless(runtime: pathlib.Path) -> dict[str, str]:
    """An environment for a headless compositor, and its clients."""
    if not COMPOSITOR.exists():
        pytest.skip("the compositor isn't built: see doc/terminal.rst")
    runtime.mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WAYLAND_", "QT_"))}
    return env | {"XDG_RUNTIME_DIR": str(runtime), "WLR_BACKENDS": "headless", "WLR_RENDERER": "pixman"}


@pytest.fixture
def compositor(tmp_path):
    """The compositor, headless, with the test keyboard and pointer."""
    env = headless(tmp_path / "run")
    with open(tmp_path / "compositor.log", "w") as log:
        harness = Harness(Compositor.start(args=["-k"], env=env, stdin=subprocess.PIPE, stderr=log), env)
        yield harness

        for client in harness.clients:
            client.stop()
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


def test_input(compositor, *lines: str):
    assert compositor.process.stdin is not None
    compositor.process.stdin.write("".join(line + "\n" for line in lines).encode())
    compositor.process.stdin.flush()


def keys(compositor, *events: tuple[int, int]):
    test_input(compositor, *(f"key {code} {state}" for code, state in events))


def move(compositor, x: int, y: int):
    test_input(compositor, f"move {x} {y}")


def click(compositor, x: int, y: int):
    move(compositor, x, y)
    test_input(compositor, f"button {BTN_LEFT} 1", f"button {BTN_LEFT} 0")


def client(compositor, name: str, terminal: bool = False) -> Window:
    """Start a Wayland client, with a window, and wait for it to be
    shown.  With terminal=True, it's the Terminal: on the Terminal's own
    connection, which only one process can use."""
    env = compositor.env | {"QT_QPA_PLATFORM": "wayland", "QT_QPA_PLATFORMTHEME": "generic"}
    fds = []
    if terminal:
        fds = [compositor.wayland.fileno()]
        env["WAYLAND_SOCKET"] = str(fds[0])
    else:
        env["WAYLAND_DISPLAY"] = "darq-0"
    process = subprocess.Popen([sys.executable, "-c", CLIENT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True, env=env, pass_fds=fds)
    started = Client(process)
    compositor.clients.append(started)
    return started.open(name)


def unlocked(compositor):
    """Wait for it to start, and unlock it."""
    assert receive(compositor) == Ready()
    compositor.channel.send(Unlock())
    settle()


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
    unlocked(compositor)

    # The Terminal's own window isn't in the window list.
    client(compositor, "darq:background", terminal=True)
    nothing_more(compositor)

    # Another client's is, and it's focused when it's mapped.
    app = client(compositor, "first")
    added = receive(compositor)
    assert added == WindowAdded(window=added.window, title="first", app_id="darq-test")
    assert receive(compositor) == WindowFocused(window=added.window)

    app.command("title", "second")
    assert receive(compositor) == WindowRetitled(window=added.window, title="second")

    # Unknown windows are ignored; a known one's client is asked to
    # close it.  The focus goes back to the background.
    compositor.channel.send(RaiseWindow(window=added.window))
    compositor.channel.send(FocusWindow(window=added.window))
    compositor.channel.send(CloseWindow(window=added.window + 100))
    compositor.channel.send(CloseWindow(window=added.window))
    assert receive(compositor) == WindowRemoved(window=added.window)
    assert receive(compositor) == WindowFocused(window=0)
    assert app.process.wait(10) == 0


def test_starts_locked(compositor):
    assert receive(compositor) == Ready()

    # The lock window fills the output, and has the keyboard; an
    # application window doesn't, while it's locked.
    lock = client(compositor, "darq:lock", terminal=True)
    lock.expect(f"size {OUTPUT[0]} {OUTPUT[1]}")
    lock.expect("active 1")
    app = client(compositor, "app")
    added = receive(compositor)
    assert isinstance(added, WindowAdded)
    nothing_more(compositor)
    compositor.channel.send(FocusWindow(window=added.window))
    click(compositor, OUTPUT[0] // 2, OUTPUT[1] // 2)
    keys(compositor, (KEY_A, 1), (KEY_A, 0))
    lock.expect("key a")
    assert not [line for line in app.printed() if line.startswith(("key", "active"))]
    nothing_more(compositor, 0)

    # Unlocked, with the lock window hidden, the application has it.
    compositor.channel.send(Unlock())
    lock.command("hide")
    assert receive(compositor) == WindowFocused(window=added.window)
    app.expect("active 1")
    keys(compositor, (KEY_A, 1), (KEY_A, 0))
    app.expect("key a")

    # Locked again, nothing has it until the lock window's back.
    compositor.channel.send(Lock())
    assert receive(compositor) == WindowFocused(window=0)
    app.expect("active 0")
    lock.command("show")
    lock.expect("active 1")
    keys(compositor, (KEY_A, 1), (KEY_A, 0))
    lock.expect("key a")
    assert not [line for line in app.printed() if line.startswith("key")]


def test_layers(compositor):
    unlocked(compositor)
    middle = (OUTPUT[0] // 2, OUTPUT[1] // 2)

    # A background, mapped after an application window, is below it,
    # fills the output, and doesn't take the focus.
    move(compositor, *middle)
    app = client(compositor, "app")
    added = receive(compositor)
    assert receive(compositor) == WindowFocused(window=added.window)
    app.expect("active 1")
    background = client(compositor, "darq:background", terminal=True)
    background.expect(f"size {OUTPUT[0]} {OUTPUT[1]}")
    nothing_more(compositor)

    # Focus follows the mouse, but not into the background: clicking
    # it does, though.
    move(compositor, 10, 10)
    nothing_more(compositor)
    click(compositor, 10, 10)
    assert receive(compositor) == WindowFocused(window=0)
    click(compositor, *middle)
    assert receive(compositor) == WindowFocused(window=added.window)

    # An overlay window, one of the Terminal's panels, is above the
    # application, and keeps the focus from the mouse until it's gone.
    panel = background.client.open("darq:panel")
    assert receive(compositor) == WindowFocused(window=0)
    panel.expect("active 1")
    app.expect("active 0")
    move(compositor, 10, 10)
    move(compositor, *middle)
    keys(compositor, (KEY_A, 1), (KEY_A, 0))
    panel.expect("key a")
    nothing_more(compositor)
    panel.command("hide")
    assert receive(compositor) == WindowFocused(window=added.window)
    app.expect("active 1")


def test_window_mode(compositor):
    unlocked(compositor)
    x, y = OUTPUT[0] // 2, OUTPUT[1] // 2

    # Two windows, 200 by 100, centred: b on top.
    move(compositor, x, y)
    client(compositor, "a")
    a = receive(compositor).window
    assert receive(compositor) == WindowFocused(window=a)
    b_client = client(compositor, "b")
    b = receive(compositor).window
    assert receive(compositor) == WindowFocused(window=b)

    # With Sys-w held, b moves with the mouse, 400 to the left.
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_W, 1))
    move(compositor, x - 400, y)
    keys(compositor, (KEY_W, 0), (KEY_LEFTCTRL, 0))
    move(compositor, x, y)
    assert receive(compositor) == WindowFocused(window=a)
    move(compositor, x - 400, y)
    assert receive(compositor) == WindowFocused(window=b)

    # b's right edge is 300 left of the middle: a step right reaches
    # 5 beyond it.
    move(compositor, x - 295, y)
    nothing_more(compositor)
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_W, 1), (KEY_RIGHT, 1), (KEY_RIGHT, 0),
         (KEY_W, 0), (KEY_LEFTCTRL, 0))
    move(compositor, x, y)
    assert receive(compositor) == WindowFocused(window=a)
    move(compositor, x - 295, y)
    assert receive(compositor) == WindowFocused(window=b)

    # Taller and broader.
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_W, 1), (KEY_T, 1), (KEY_T, 0), (KEY_B, 1), (KEY_B, 0),
         (KEY_W, 0), (KEY_LEFTCTRL, 0))
    b_client.expect("size 216 116")

    # A held arrow repeats: in 1.2 seconds, many more than the 5 steps
    # it'd take to reach 100 left of the middle.
    keys(compositor, (KEY_LEFTCTRL, 1), (KEY_W, 1), (KEY_RIGHT, 1))
    time.sleep(1.2)
    keys(compositor, (KEY_RIGHT, 0), (KEY_W, 0), (KEY_LEFTCTRL, 0))
    move(compositor, x + 50, y)
    assert receive(compositor) == WindowFocused(window=a)
    move(compositor, x - 100, y)
    assert receive(compositor) == WindowFocused(window=b)

    # Sys-w keys aren't hotkeys, or a client's.
    assert not [line for line in b_client.printed(0) if line.startswith("key")]
    nothing_more(compositor)


def test_dies_with_the_terminal(tmp_path):
    # A Terminal whose policy channel is held open by another process,
    # as if it were stuck: the compositor still exits when it dies.
    script = textwrap.dedent("""
        import select, subprocess, sys
        from darq.compositor import Compositor
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        c = Compositor.start(**quiet)
        select.select([c.channel], [], [], 10)
        assert c.channel.receive() is not None
        holder = subprocess.Popen(["sleep", "30"], pass_fds=[c.channel.fileno()], **quiet)
        print(c.process.pid, holder.pid, flush=True)
    """)
    env = headless(tmp_path / "run") | {"PYTHONPATH": str(SOURCE.parent)}
    holder = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=30)
    assert holder.returncode == 0, holder.stderr
    pid, sleeper = map(int, holder.stdout.split())
    try:
        deadline = time.monotonic() + 10
        while status(pid) != "Z":
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL)
                pytest.fail("the compositor didn't exit")
            time.sleep(0.05)
    finally:
        os.kill(sleeper, signal.SIGKILL)


def status(pid: int) -> str:
    """A process' state, from /proc: "Z" once it's gone, too."""
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0]
    except (FileNotFoundError, IndexError):
        return "Z"


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
