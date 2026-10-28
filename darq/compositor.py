# darqos
# Copyright (C) 2026 David Arnold

# The Terminal's side of the compositor: starting it, and its policy
# channel (see doc/terminal.rst, and lib/didl/compositor.didl).
#
# The compositor isn't a DarqOS process: it's a Linux child of the
# Terminal, given two socket pairs' ends.  One is the Terminal's own
# Wayland connection (WAYLAND_SOCKET), which is how the compositor tells
# the Terminal's windows from other clients'.  The other is the policy
# channel, a SOCK_SEQPACKET pair carrying one DIPC-format message per
# packet, all one-way.

import os
import pathlib
import socket
import subprocess
import typing

from darq.dipc import Message, decode_method
from darq.errors import ChannelClosedError, MessageDecodingError
from darq.protocols import compositor as protocol


# Control exports
__all__ = [
    "COMPOSITOR",
    "Compositor",
    "PolicyChannel",
]


# The compositor, built in the source tree for now.
COMPOSITOR = pathlib.Path(__file__).parent.parent / "compositor" / "build" / "darq-compositor"

# Larger than any message the compositor sends (see compositor/policy.h).
MAX_MESSAGE = 4096


class PolicyChannel:
    """The Terminal's end of the policy channel.

    Its socket is non-blocking, so it can be watched by an event loop:
    receive() returns None when there's nothing to read."""

    def __init__(self, sock: socket.socket):
        """Constructor.

        :param sock: A SOCK_SEQPACKET socket."""
        self.socket = sock
        self.socket.setblocking(False)

    def fileno(self) -> int:
        return self.socket.fileno()

    def close(self) -> None:
        """Close the channel: the compositor exits."""
        self.socket.close()

    def send(self, message: Message) -> None:
        """Send a Policy message.

        The compositor reads each one promptly, so a full socket isn't
        waited for: it's a stuck compositor, and a BlockingIOError."""
        data, _ = message.encode()
        self.socket.send(data)

    def receive(self) -> Message | None:
        """Return the next Display message, or None if there isn't one.

        A message that can't be decoded is consumed, and raises
        MessageDecodingError; when the compositor has closed the
        channel, ChannelClosedError is raised."""
        try:
            data = self.socket.recv(MAX_MESSAGE + 1)
        except BlockingIOError:
            return None
        except ConnectionResetError:
            data = b""
        if not data:
            raise ChannelClosedError("the compositor closed the policy channel")
        if len(data) > MAX_MESSAGE:
            raise MessageDecodingError("message too long")
        cls = protocol.Display.REQUESTS.get(decode_method(data))
        if cls is None:
            raise MessageDecodingError(f"unknown method {decode_method(data)}")
        message = cls()
        message.decode(data)
        return message


class Compositor:
    """A running compositor, started by the Terminal."""

    def __init__(self, process: subprocess.Popen, channel: PolicyChannel,
                 wayland: socket.socket):
        self.process = process
        self.channel = channel

        # The Terminal's end of its Wayland connection: its descriptor
        # goes in WAYLAND_SOCKET, for Qt, which takes it over.
        self.wayland = wayland

    @classmethod
    def start(cls, path: os.PathLike | str = COMPOSITOR, args: typing.Sequence[str] = (),
              env: typing.Mapping[str, str] | None = None, **kwargs) -> "Compositor":
        """Start a compositor, with the Terminal's Wayland connection
        and the policy channel.

        :param args: More arguments for it.
        :param env: Its environment, if not this one.
        :param kwargs: For subprocess.Popen."""
        wayland, wayland_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        policy, policy_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        fds = (wayland_child.fileno(), policy_child.fileno())
        try:
            process = subprocess.Popen(
                [str(path), "-t", str(fds[0]), "-p", str(fds[1]), *args],
                env=env, pass_fds=fds, **kwargs)
        except BaseException:
            for sock in (wayland, policy):
                sock.close()
            raise
        finally:
            wayland_child.close()
            policy_child.close()
        return cls(process, PolicyChannel(policy), wayland)

    def stop(self, timeout: float = 5) -> int:
        """Close the policy channel, and wait for the compositor to
        exit; kill it if it doesn't.  Returns its exit status."""
        self.channel.close()
        self.wayland.close()
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()
