# darqos
# Copyright (C) 2021-2026 David Arnold

# Security service: the password, registered as darq.security.
#
# It serves the Security protocol (lib/didl/security.didl), as described
# in doc/security.rst.  1st Edition has only a password, set on the first
# boot, and checked by the Terminal's lock screen.  It's kept as an
# scrypt hash, in a file in the run directory, readable only by its
# owner.
#
# Any process can ask, so guessing is slowed: password checks (and
# changes, which check the old one) are made one at a time, and after a
# few wrong ones in a row, each waits longer before it's made.

import base64
import collections
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import typing

import darq
from darq.dipc import ErrorCode
from darq.protocols.security import (
    CheckPasswordRequest,
    CheckPasswordResponse,
    IsPasswordSetRequest,
    IsPasswordSetResponse,
    Security,
    SetPasswordRequest,
    SetPasswordResponse,
    Status,
)


# Password file, in the run directory.
PASSWORD_FILE = "security.json"

# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"

# scrypt's parameters for new hashes: they're stored with each, so they
# can be raised later.  This takes about 0.1s, and 32 MiB.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# Longest password, in bytes, as UTF-8.
MAX_PASSWORD = 1024

# Checks made at once, while the password's wrong, before the next one
# waits: 1, 2, 4, ... seconds, up to the longest delay.
FREE_TRIES = 3
MAX_DELAY = 30.0


def hash_password(password: str, n: int | None = None, r: int | None = None, p: int | None = None,
                  salt: bytes | None = None) -> dict[str, typing.Any]:
    """Return a password's record: its scrypt hash, and how it was made
    (by default, with new parameters, and salt)."""
    n = SCRYPT_N if n is None else n
    r = SCRYPT_R if r is None else r
    p = SCRYPT_P if p is None else p
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                         maxmem=2 * 128 * r * n, dklen=KEY_BYTES)
    return {"version": 1, "scheme": "scrypt", "n": n, "r": r, "p": p,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(key).decode()}


def verify_password(password: str, record: dict[str, typing.Any]) -> bool:
    """Return True if a password matches its record."""
    if record.get("version") != 1 or record.get("scheme") != "scrypt":
        raise ValueError(f"unknown password record: version {record.get('version')!r}")
    expected = base64.b64decode(record["hash"])
    actual = hash_password(password, n=record["n"], r=record["r"], p=record["p"],
                           salt=base64.b64decode(record["salt"]))["hash"]
    return hmac.compare_digest(base64.b64decode(actual), expected)


class PasswordStore:
    """The password's record, in a file, readable only by its owner.

    A file that can't be read is taken as a password that's set, but
    never matches: it fails closed."""

    def __init__(self, path: str):
        self.path = path
        self.record: dict[str, typing.Any] | None = None
        self.broken = False
        try:
            with open(path) as f:
                self.record = json.load(f)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logging.error(f"Can't read the password file {path}: {e}: no password will match")
            self.broken = True

    def is_set(self) -> bool:
        return self.broken or self.record is not None

    def check(self, password: str) -> bool:
        if self.record is None:
            return False
        try:
            return verify_password(password, self.record)
        except (KeyError, TypeError, ValueError) as e:
            logging.error(f"Can't check the password: {e}")
            return False

    def set(self, password: str):
        """Save a new password, replacing the file atomically."""
        record = hash_password(password)
        temporary = self.path + ".new"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, json.dumps(record).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, self.path)
        self.record = record
        self.broken = False


class Backoff:
    """How long the next password check must wait, after wrong ones."""

    def __init__(self, clock: typing.Callable[[], float] = time.monotonic):
        self.clock = clock
        self.failures = 0
        self.ready_at = 0.0

    def wait(self) -> float:
        """Seconds until the next check may be made."""
        return max(0.0, self.ready_at - self.clock())

    def failed(self):
        self.failures += 1
        delay = 0.0 if self.failures < FREE_TRIES else min(MAX_DELAY, 2.0 ** (self.failures - FREE_TRIES))
        self.ready_at = self.clock() + delay

    def succeeded(self):
        self.failures = 0
        self.ready_at = 0.0


def valid_password(password: str) -> bool:
    return 0 < len(password.encode("utf-8")) <= MAX_PASSWORD


class SecurityService(Security.Handler):
    """The password: whether it's set, setting it, and checking it."""

    def __init__(self, store: PasswordStore, clock: typing.Callable[[], float] = time.monotonic):
        self.store = store
        self.backoff = Backoff(clock)

        # Checks waiting for the backoff, in order: each with its
        # deferred reply.  And the timer to make the next.
        self.waiting: collections.deque[tuple[darq.Deferred, typing.Callable[[], typing.Any]]] = \
            collections.deque()
        self.timer: darq.Timer | None = None

    def is_password_set(self, request: IsPasswordSetRequest, source: int) -> IsPasswordSetResponse:
        return IsPasswordSetResponse(set=self.store.is_set())

    def set_password(self, request: SetPasswordRequest, source: int):
        if not valid_password(request.new_password):
            return SetPasswordResponse(status=Status.INVALID)
        if not self.store.is_set():
            if request.old_password:
                return SetPasswordResponse(status=Status.NOT_SET)
            self.store.set(request.new_password)
            logging.info("Password set")
            return SetPasswordResponse(status=Status.OK)
        if not request.old_password:
            return SetPasswordResponse(status=Status.ALREADY_SET)

        def change() -> SetPasswordResponse:
            if not self.checked(request.old_password):
                return SetPasswordResponse(status=Status.WRONG_PASSWORD)
            self.store.set(request.new_password)
            logging.info("Password changed")
            return SetPasswordResponse(status=Status.OK)

        return self.when_allowed(change)

    def check_password(self, request: CheckPasswordRequest, source: int):
        if not self.store.is_set():
            return CheckPasswordResponse(status=Status.NOT_SET)
        return self.when_allowed(lambda: CheckPasswordResponse(
            status=Status.OK if self.checked(request.password) else Status.WRONG_PASSWORD))

    def checked(self, password: str) -> bool:
        """Check the password, counting it if it's wrong."""
        if self.store.check(password):
            self.backoff.succeeded()
            return True
        self.backoff.failed()
        logging.warning(f"Wrong password ({self.backoff.failures} in a row)")
        return False

    def when_allowed(self, check: typing.Callable[[], typing.Any]):
        """Make a check now, if it's allowed, and nothing's waiting; or
        defer its reply until it's made, in turn."""
        if not self.waiting and self.backoff.wait() == 0:
            return check()
        self.waiting.append((self.defer(), check))
        self.schedule()
        return self.waiting[-1][0]

    def schedule(self):
        if self.timer is None and self.waiting:
            assert self.server is not None
            self.timer = self.server.call_later(self.backoff.wait(), self.make_waiting)

    def make_waiting(self):
        """Make the waiting checks that are now allowed."""
        self.timer = None
        while self.waiting and self.backoff.wait() == 0:
            deferred, check = self.waiting.popleft()
            try:
                deferred.reply(check())
            except Exception as e:
                logging.exception("Password check failed")
                deferred.error(ErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        self.schedule()


def darq_main():
    run_dir = os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR)
    os.makedirs(run_dir, exist_ok=True)
    darq.serve("darq.security", SecurityService(PasswordStore(os.path.join(run_dir, PASSWORD_FILE))))
