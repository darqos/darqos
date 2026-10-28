# darqos
# Copyright (C) 2026 David Arnold

# The Terminal's full-screen windows, on every screen: a background, and
# the lock screen.  The compositor places them by their titles, in its
# background and lock layers, filling the screen each is shown on.
#
# Until the Security service is ported, Enter unlocks: there's no
# password yet.

import typing

from PyQt6 import QtCore
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QPaintEvent, QPainter, QScreen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


Key = QtCore.Qt.Key

# Keys that don't cancel the power prompt: modifiers, pressed on the way
# to another key.
MODIFIER_KEYS = {Key.Key_Shift, Key.Key_Control, Key.Key_Alt, Key.Key_Meta, Key.Key_Super_L,
                 Key.Key_Super_R, Key.Key_Hyper_L, Key.Key_Hyper_R, Key.Key_AltGr, Key.Key_CapsLock}

LOCKED_TEXT = "Locked\n\nPress Enter to unlock"
POWER_TEXT = "s: Shut down    b: Reboot    any other key: Cancel"


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
    """A screen's lock screen.  Enter asks to unlock; while it's showing
    the power prompt (see Screens.power_prompt), s and b ask to shut down
    or reboot, and any other key cancels it."""

    TITLE = "darq:lock"
    COLOUR = QColor("#101214")

    def __init__(self, screen: QScreen, unlock: typing.Callable[[], None],
                 power: typing.Callable[[str], None]):
        super().__init__()
        self.setWindowTitle(self.TITLE)
        self.setScreen(screen)
        self.unlock = unlock
        self.power = power
        self.prompting = False

        self.label = QLabel(LOCKED_TEXT)
        self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color: #c0c4c8; font-size: 24px")
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)

    def paintEvent(self, a0: QPaintEvent | None):
        QPainter(self).fillRect(self.rect(), self.COLOUR)

    def set_prompting(self, prompting: bool):
        self.prompting = prompting
        self.label.setText(POWER_TEXT if prompting else LOCKED_TEXT)

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None:
            return
        if self.prompting:
            choice = power_choice(a0)
            if choice is None:
                return
            self.power(choice)
        elif a0.key() in (Key.Key_Return, Key.Key_Enter):
            self.unlock()


class Screens:
    """A background and a lock window for each screen, as they come and
    go, with the lock windows shown while it's locked."""

    def __init__(self, app: QGuiApplication, unlock: typing.Callable[[], None],
                 power: typing.Callable[[str], None]):
        self.app = app
        self.unlock = unlock
        self.power = power
        self.locked = True
        self.windows: dict[QScreen, tuple[Background, LockWindow]] = {}

    def start(self):
        for screen in self.app.screens():
            self.add(screen)
        self.app.screenAdded.connect(self.add)
        self.app.screenRemoved.connect(self.remove)

    def add(self, screen: QScreen):
        background = Background(screen)
        lock = LockWindow(screen, self.unlock, self.on_power)
        self.windows[screen] = (background, lock)
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

    def lock(self):
        self.locked = True
        for lock in self.locks():
            lock.set_prompting(False)
            lock.showFullScreen()

    def unlock_screens(self):
        self.locked = False
        for lock in self.locks():
            lock.set_prompting(False)
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
