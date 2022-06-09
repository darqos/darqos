# DarqOS
# Copyright (C) 2022 David Arnold


class EventLoopInterface:
    """Abstraction of event loop to work across uvloop for CLI and Qt
    for GUI applications."""

    def add_socket(self, sock, listener):
        pass

    def cancel_socket(self, sock):
        pass

    def add_timer(self, duration, listener) -> int:
        pass

    def cancel_timer(self, timer_id: int):
        pass


class Endpoint:
    def __init__(self):
        self.host = ''
        self.port = 0


class Node:

    def __init__(self):
        # oid : socket
        self.endpoints = {}

    def send(self, dest: str, msg: dict):
        pass

    def listen(self):
        pass

    def withdraw(self):
        pass

    def recv(self, oid: str, msg: dict):
        """Handle a received message."""
        pass

    def _recv_bytes(self, buf: bytes):
        """(Internal) Process bytes received from socket."""
        pass

