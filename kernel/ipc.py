# darqos
# Copyright (C) 2022 David Arnold

import logging
import socket
from typing import MutableSequence

from darq.kernel.types import UInt64
from darq.kernel.ipc import Buffer


class IPCClient:
    """Each connected TCP socket represents a client of the service.
    The data associated with each of these clients is kept in instances
    of this class.

    The recv_buffer is used to reassemble messages if they're fragmented
    across multiple calls to recv(), and also to process multiple messages
    if they're aggregated into a single recv() call's returned data.

    The send_buffer is used to queue data to be sent once the connection
    to the client has drained.  This could be problematic, since there's
    no back-pressure mechanism, but .. that's over-engineering for now."""

    def __init__(self, kernel, sock: socket.socket):
        """Constructor.

        :param kernel: Reference to owning p-kernel instance.
        :param: sock: Accepted socket for this client."""

        self.kernel = kernel

        # TCP socket connected to client process.
        self.socket: socket.socket = sock

        # Outbound data queue.
        self.send_buffer: bytes = b''

        # Inbound data queue.
        self.recv_buffer: Buffer = Buffer()

        # Client's port number, allocated when handling the Login Request.
        self.ports: MutableSequence[UInt64] = []
        return

    def name(self) -> str:
        """(Internal) Log message prefix."""
        return f'Socket [{self.socket.fileno()}]'

    def get_socket(self) -> socket.socket:
        """Return the TCP socket connected to this client."""
        return self.socket

    def add_port(self, port: UInt64):
        """Add a port to this client's list.

        :param port: Port number to add."""
        self.ports.append(port)

    def remove_port(self, port: UInt64):
        """Remove a port from this client's list.

        :param port: Port number to remove."""
        self.ports.remove(port)

    def get_ports(self) -> MutableSequence[UInt64]:
        """Return the list of ports registered for this client."""
        return self.ports

    def get_buffer(self) -> Buffer:
        """Return reference to the receive buffer."""
        return self.recv_buffer

    def receive_data(self, buf: bytes):
        """Process received TCP data.

        :param buf: Byte buffer of received data.

        Will append data to receive buffer, and inform kernel."""

        self.recv_buffer.append(buf)
        self.kernel.dispatch(self)
        return

    def send_data(self, data: bytes):
        """Send a message to a connected client.

        :param data: Byte buffer of data to send."""

        # FIXME: append to send buffer, and write async?

        self.socket.sendall(data)
        logging.info(f"Sent {len(data)} bytes to socket {self.socket.getpeername()}")
        return

    def on_readable(self, sock: socket.socket):
        """Handle data available to receive."""

        try:
            recv_buf = self.socket.recv(65536)
        except ConnectionResetError:
            logging.warning(f"IPC: {self.name()} connection reset.")
            self.kernel.handle_disconnect(self)
            return

        if len(recv_buf) == 0:
            logging.debug(f"IPC: {self.name()} connection closed by peer.")
            self.kernel.handle_disconnect(self)
            return

        self.recv_buffer.append(recv_buf)
        logging.debug(f"IPC: {self.name()} "
                      f"delivered {len(recv_buf)} bytes")

        self.kernel.dispatch(self)
        return

    def on_writeable(self, sock: socket.socket):
        """Handle socket ready for send."""

        if len(self.send_buffer) > 0:
            sent = self.socket.send(self.send_buffer)
            if sent < len(self.send_buffer):
                self.send_buffer = self.send_buffer[sent:]
            else:
                self.send_buffer = b''
        return

