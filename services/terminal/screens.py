# darqos
# Copyright (C) 2026 David Arnold

# The Terminal's full-screen windows, on every screen: a background, and
# the lock screen.  The compositor places them by their titles, in its
# background and lock layers, filling the screen each is shown on.
#
# The lock screen asks for the password, or, on the first boot, for a new
# one, twice.  What's typed is passed to the Terminal, which asks the
# Security service: the screens just show what it says.

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QPaintEvent, QPainter, QScreen
from PyQt6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget


Key = QtCore.Qt.Key

# Keys that don't cancel the power prompt: modifiers, pressed on the way
# to another key.
MODIFIER_KEYS = {Key.Key_Shift, Key.Key_Control, Key.Key_Alt, Key.Key_Meta, Key.Key_Super_L,
                 Key.Key_Super_R, Key.Key_Hyper_L, Key.Key_Hyper_R, Key.Key_AltGr, Key.Key_CapsLock}

POWER_TEXT = "s: Shut down    b: Reboot    any other key: Cancel"

# What the lock screen asks for: nothing yet, the password, a new one (on
# the first boot), or nothing it can (the Security service can't be
# reached: Enter tries again).
WAITING = "waiting"
ENTER = "enter"
CHOOSE = "choose"
UNAVAILABLE = "unavailable"

HEADINGS = {
    WAITING: "Locked",
    ENTER: "Locked",
    CHOOSE: "Choose a password",
    UNAVAILABLE: "Locked",
}


# The power prompt's answers, by key.
POWER_KEYS: dict[int, str] = {Key.Key_S.value: "shutdown", Key.Key_B.value: "reboot"}


def is_modifier(event: QKeyEvent) -> bool:
    return event.key() in MODIFIER_KEYS


def power_choice(event: QKeyEvent) -> str | None:
    """The answer to the power prompt that a key gives: "shutdown",
    "reboot", "cancel", or None for a modifier, which isn't one."""
    if is_modifier(event):
        return None
    return POWER_KEYS.get(event.key(), "cancel")


class Background(QWidget):
    """A screen's background: plain, for now."""

    TITLE = "darq:background"
    COLOUR = QColor("#202428")

    def __init__(self, screen: QScreen):
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.setScreen(screen)

    def paintEvent(self, a0: QPaintEvent | None):
        QPainter(self).fillRect(self.rect(), self.COLOUR)


class LockWindow(QWidget):
    """A screen's lock screen: a heading, the password field (and a
    second, to confirm a new one), and a message line.

    While it's showing the power prompt (see Screens.power_prompt), the
    fields are disabled, so the keys come to it: s and b ask to shut
    down or reboot, and any other key cancels it."""

    TITLE = "darq:lock"
    COLOUR = QColor("#101214")
    STYLE = "color: #c0c4c8; font-size: 20px"

    def __init__(self, screen: QScreen, screens: "Screens"):
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.setScreen(screen)
        self.screens = screens
        self.prompting = False

        self.heading = QLabel()
        self.heading.setStyleSheet("color: #c0c4c8; font-size: 28px")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Password")
        self.password.returnPressed.connect(self.password_entered)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setPlaceholderText("Again")
        self.confirm.returnPressed.connect(self.submit)
        self.message = QLabel()
        self.message.setStyleSheet(self.STYLE)
        self.message.setWordWrap(True)

        column = QVBoxLayout()
        column.addStretch()
        for widget in (self.heading, self.password, self.confirm, self.message):
            widget.setFixedWidth(400)
            column.addWidget(widget, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
        column.addStretch()
        self.setLayout(column)

    def paintEvent(self, a0: QPaintEvent | None):
        QPainter(self).fillRect(self.rect(), self.COLOUR)

    def update_state(self):
        """Show the screens' state: the mode, its message, and whether
        the fields can be used."""
        screens = self.screens
        self.heading.setText(HEADINGS[screens.mode])
        self.confirm.setVisible(screens.mode == CHOOSE)
        self.password.setVisible(screens.mode in (ENTER, CHOOSE))
        usable = not screens.busy and not self.prompting
        self.password.setEnabled(usable)
        self.confirm.setEnabled(usable)
        self.message.setText(POWER_TEXT if self.prompting else screens.message)
        if self.prompting:
            self.setFocus()
        elif usable and not self.confirm.hasFocus():
            self.password.setFocus()

    def clear(self):
        self.password.clear()
        self.confirm.clear()

    def password_entered(self):
        if self.screens.mode == CHOOSE:
            self.confirm.setFocus()
        else:
            self.submit()

    def submit(self):
        mode = self.screens.mode
        self.screens.submit(self.password.text(), self.confirm.text() if mode == CHOOSE else None)

    def set_prompting(self, prompting: bool):
        self.prompting = prompting
        self.update_state()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None:
            return
        if self.prompting:
            choice = power_choice(a0)
            if choice is not None:
                self.screens.on_power(choice)
        elif self.screens.mode in (UNAVAILABLE, WAITING) and a0.key() in (Key.Key_Return, Key.Key_Enter):
            self.screens.submit("", None)


class Screens:
    """A background and a lock window for each screen, as they come and
    go, with the lock windows shown while it's locked, and what they're
    asking for."""

    def __init__(self, app: QGuiApplication, submit: typing.Callable[[str, str | None], None],
                 power: typing.Callable[[str], None]):
        """Constructor.

        :param submit: Called with what's typed: the password, and, for a
                       new one, what was typed to confirm it (else None).
        :param power: Called with "shutdown" or "reboot"."""
        self.app = app
        self.submit = submit
        self.power = power
        self.locked = True
        self.mode = WAITING
        self.message = ""

        # While what's typed is being checked.
        self.busy = False
        self.windows: dict[QScreen, tuple[Background, LockWindow]] = {}

    def start(self):
        for screen in self.app.screens():
            self.add(screen)
        self.app.screenAdded.connect(self.add)
        self.app.screenRemoved.connect(self.remove)

    def add(self, screen: QScreen):
        background = Background(screen)
        lock = LockWindow(screen, self)
        self.windows[screen] = (background, lock)
        lock.update_state()
        background.showFullScreen()
        if self.locked:
            lock.showFullScreen()

    def remove(self, screen: QScreen):
        windows = self.windows.pop(screen, None)
        if windows is not None:
            for window in windows:
                window.close()
                window.deleteLater()

    def locks(self) -> list[LockWindow]:
        return [lock for _, lock in self.windows.values()]

    def show(self, mode: str | None = None, message: str = "", busy: bool = False, clear: bool = False):
        """Ask for something else, or say something, on every screen."""
        if mode is not None:
            self.mode = mode
        self.message = message
        self.busy = busy
        for lock in self.locks():
            if clear:
                lock.clear()
            lock.update_state()

    def lock(self):
        self.locked = True
        self.show(ENTER if self.mode == ENTER else self.mode, clear=True)
        for lock in self.locks():
            lock.prompting = False
            lock.update_state()
            lock.showFullScreen()

    def unlock_screens(self):
        self.locked = False
        self.show(message="", clear=True)
        for lock in self.locks():
            lock.prompting = False
            lock.hide()

    def power_prompt(self):
        """Ask, on the lock screens, whether to shut down or reboot."""
        for lock in self.locks():
            lock.set_prompting(True)

    def on_power(self, choice: str):
        for lock in self.locks():
            lock.set_prompting(False)
        if choice != "cancel":
            self.power(choice)
