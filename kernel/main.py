#! /usr/bin/env python
# darqos
# Copyright (C) 2022 David Arnold

# This file is the main executable of the network service that currently
# provides the functionality expected to ultimately reside in the darqos
# kernel.
#
# Access to this functionality is via the system IPC mechanism, also
# implemented by this service.  Consequently, every darqos process has a
# TCP connection to this service which it uses to perform pseudo-system
# calls (p-calls).
#
# Applications invoke p-calls by sending a message via the IPC system.
# The construction and transmission of those messages is implemented in
# the darq.kernel namespace, and the function wrappers for each p-call
# are exposed in the 'darq' namespace for concise access in user code.
#
# xxx

import logging
import sys

import darq
from darq.kernel.ipc import *
from darq.kernel.message import *

from ipc import IPCClient


# TCP listening port for connection of IPC clients.
TCP_PORT = 11000

# Start of auto-allocated IPC port numbers.
EPHEMERAL_PORT_START = 16384

# End of auto-allocated IPC port numbers.
EPHEMERAL_PORT_MAX = 2 ** 32

# Services.
#
# For now, this is the registry of system services, started on boot.
# Ideally, this might be outside the code, but, that can come later.

SERVICES = []


class PseudoKernel(darq.Service):
    """IPC message router."""

    def __init__(self):
        """Constructor."""
        super().__init__(SelectEventLoop(), 11000)

        self.is_active: bool = True
        self.clients: typing.Dict[socket.socket: IPCClient] = {}
        self.fds: typing.Dict[int, IPCClient] = {}

        # Listening socket.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setblocking(False)
        self.socket.bind(('0.0.0.0', 11000))
        self.socket.listen()

        # Protocol codec.
        # FIXME: move this somewhere to run at import time
        self.codec = Codec()
        self.codec.register(MSG_OPEN_PORT_RQST, OpenPortRequest)
        self.codec.register(MSG_OPEN_PORT_RESP, OpenPortResponse)
        self.codec.register(MSG_REMOVE_PORT_RQST, ClosePortRequest)
        self.codec.register(MSG_REMOVE_PORT_RESP, ClosePortResponse)
        self.codec.register(MSG_SEND_MESSAGE, SendMessage)
        self.codec.register(MSG_DELIVER_MESSAGE, DeliverMessage)

        # Event loop.

        # Complete.
        logging.info("IPC: service initialized.")

    @staticmethod
    def get_name() -> str:
        return "IPC"

    def run(self):
        """Main loop."""

        logging.info("IPC: Servicing requests.")
        while self.is_active:
            sl = [c.get_socket() for c in self.clients.values()]
            sl.append(self.socket)

            ready_read, ready_write, junk = select.select(sl, sl, [], 1.0)

            for s in ready_read:
                if s == self.socket:
                    new_client, _ = self.socket.accept()
                    client = IPCClient(self, new_client)
                    self.clients[new_client] = client

                    logging.info(f"IPC: Client socket [{s.fileno()}] connected")

                else:
                    client = self.clients[s]
                    try:
                        recv_buf = s.recv(65536)
                    except ConnectionResetError:
                        self.handle_disconnect(client)
                        continue

                    if len(recv_buf) == 0:
                        del self.clients[s]
                        self.handle_disconnect(client)
                        continue

                    logging.debug(f"IPC: Client [{client.name()}] "
                                  f"delivering {len(recv_buf)} bytes")
                    client.receive_data(recv_buf)

            for s in ready_write:
                if s == self.socket:
                    logging.debug("IPC: Listening socket returned writeable")
                else:
                    client = self.clients.get(s)
                    if not client:
                        continue
                    if len(client.send_buffer) > 0:
                        sent = s.send(client.send_buffer)
                        if sent < len(client.send_buffer):
                            client.send_buffer = client.send_buffer[sent:]
                        else:
                            client.send_buffer = b''
        return

    def handle_disconnect(self, client: IPCClient):
        """Handle disconnection of a client process.

        :param client: Client that has disconnected."""

        # Deregister ports.
        ports = client.get_ports()
        for port in ports:
            if port in self.fds:
                del self.fds[port]
                logging.debug(f"IPC: closed port {port}")

        # Remove client.
        sock = client.get_socket()
        if sock in self.clients:
            del self.clients[sock]

        logging.info(f"IPC: Client on socket [{sock.fileno()}] disconnected")
        return

    def get_ephemeral_port(self):
        """Allocate an ephemeral port.

        Note: this isn't thread-safe: if it's called again before the port
        number is used, it will return the same value."""

        # Ports are allocated at random, outside the WKP range.
        port = random.randint(EPHEMERAL_PORT_START, EPHEMERAL_PORT_MAX)
        while port in self.fds:
            port = random.randint(EPHEMERAL_PORT_START, EPHEMERAL_PORT_MAX)

        # FIXME: there's a race condition here, that the port could be
        # FIXME: reused before it's registered.
        return port

    def register_port(self, port: int, client: IPCClient):
        """Record the association between a port and a client (socket)."""
        if port in self.fds:
            logging.warning(f"IPC: register_port() error: "
                            f"port already registered: {port}")
            return False

        self.fds[port] = client
        return True

    def deregister_port(self, port: int, client: IPCClient):
        """Delete the association between a port and a client (socket)."""
        if port not in self.fds:
            logging.warning(f"IPC: deregister_port() error: "
                            f"port not registered: {port}")
            return False

        registered_client = self.fds[port]
        if registered_client != client:
            logging.warning(f"IPC: deregister_port() error: "
                            f"port ({port}, and client {client.name()}) "
                            f"doesn't match registered client "
                            f"{registered_client.name()}")
            return False

        del self.fds[port]
        return True

    def deliver_message(self, message: DeliverMessage):
        """Deliver a received message to its destination port's client."""
        dest_client = self.fds.get(message.destination)
        if not dest_client:
            logging.warning(f"IPC: Failed to deliver message. "
                            f"No such port: {message.destination}")
            return

        buf = self.codec.encode(message)
        dest_client.send_data(buf)
        return

    def boot(self):
        pass

    def shutdown(self):
        pass

    def handle_open_port_request(self,
                                 client: IPCClient,
                                 request: OpenPortRequest):
        """Handle request for new port."""
        port = request.requested_port

        # A requested port value of zero means to auto-assign a port number.
        if port == 0:
            port = self.get_ephemeral_port()
            if port <= 0:
                logging.error("IPC: Ephemeral port overflow; request failed.")
                self.send_open_port_response(client,
                                             request.requested_port,
                                             False)
                return

        client.add_port(port)
        self.register_port(port, client)

        logging.info(f"IPC: {client.name()} new_port "
                     f"requested {request.requested_port}, "
                     f"assigned {port}.")

        logging.info(f"IPC: {client.name()} open_port({port}) succeeded")
        self.send_open_port_response(client, port, True)
        return

    def handle_close_port_request(self,
                                  client: IPCClient,
                                  request: ClosePortRequest):
        """Handle request to close port."""

        port = request.port

        if port not in self.fds:
            logging.warning(f"IPC: close_port({port}) failed: bad port")
            self.send_close_port_response(client, port, False)
            return

        client.remove_port(port)
        self.deregister_port(port, client)

        logging.info(f"IPC: {client.name()} close_port({port}) succeeded")
        self.send_close_port_response(client, port, True)
        return

    def handle_send_message(self,
                            source: IPCClient,
                            message: SendMessage):
        """Handle request to send message from connected client.

        :param source: Client session that received this message.
        :param message: Received message."""

        logging.info(f"IPC: send_message: from {message.source}, "
                     f"to {message.destination}, "
                     f"[{message.payload}]")

        # Look up destination.
        destination = self.fds.get(message.destination)
        if destination is None:
            raise Exception("bad destination port in send")

        deliver = DeliverMessage()
        deliver.set_version(1)
        deliver.set_header_length(8)
        deliver.set_type(MSG_DELIVER_MESSAGE)
        deliver.set_length(8 + 20 + len(message.payload))

        deliver.source = message.source
        deliver.destination = message.destination
        deliver.payload = message.payload

        buf = self.codec.encode(deliver)
        destination.send_data(buf)
        logging.info(f"Sent deliver_message")
        return

    def handle_send_chunk(self,
                          client: IPCClient,
                          message: SendChunk):
        logging.info("send_chunk")

    def handle_deliver_chunk(self,
                             client: IPCClient,
                             message: DeliverChunk):
        logging.info("deliver_chunk")

    def send_open_port_response(self,
                                client: IPCClient,
                                port: UInt64,
                                result: bool):
        response = OpenPortResponse()
        response.port = port
        response.result = result
        buf = self.codec.encode(response)
        client.send_data(buf)
        logging.info(f"IPC: new_port response: result={result}, port={port}")
        return

    def send_close_port_response(self,
                                 client: IPCClient,
                                 port: UInt64,
                                 result: bool):
        response = ClosePortResponse()
        response.port = port
        response.result = result
        buf = self.codec.encode(response)
        client.send_data(buf)
        logging.info(f"IPC: close_port response: port {port}")
        return

    def dispatch(self, client):
        """Process data received from clients.

        :param client: Client connection that received data

        Handle received message, if buffer contains one."""

        # See if we have a header yet.
        try:
            message = self.codec.decode(client.get_buffer())
        except Exception as e:
            logging.warning(f"IPC: failed to decode received message: {e}")
            return

        if message is None:
            # Added more bytes, but total available doesn't yet constitute
            # a message.  This should only really happen in testing.
            logging.debug(f"IPC: {client.name()} "
                          f"Queued received data too small for message")
            return

        if message.type == MSG_OPEN_PORT_RQST:
            self.handle_open_port_request(client, message)

        elif message.type == MSG_REMOVE_PORT_RQST:
            self.handle_close_port_request(client, message)

        elif message.type == MSG_SEND_MESSAGE:
            self.handle_send_message(client, message)

        elif message.type == MSG_SEND_CHUNK:
            self.handle_send_chunk(client, message)

        else:
            logging.warning(f"IPC: {client.name()} Received message with "
                            f"unexpected type code [{message.type}] "
                            "Ignoring message.")
        return


################################################################

if __name__ == "__main__":
    # FIXME: currently, just log to stderr.
    logging.basicConfig(stream=sys.stderr,
                        format='%(asctime)s %(levelname)8s %(message)s',
                        level=logging.DEBUG)

    service = PseudoKernel()
    service.run()
