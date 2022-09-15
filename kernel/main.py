#! /usr/bin/env python
# DarqOS
# Copyright (C) 2022 David Arnold


import logging
import sys
from typing import MutableSequence

import darq
from darq.os.ipc import *


# TCP listening port for connection of IPC clients.
TCP_PORT = 11000

# Start of auto-allocated IPC port numbers.
EPHEMERAL_PORT_START = 16384

# End of auto-allocated IPC port numbers.
EPHEMERAL_PORT_MAX = 2 ** 32


class ServiceInterface:
    """Interface between client and service."""

    def deliver_message(self, message: DeliverMessage):

        pass

    def register_port(self, port: int, client: 'Client'):
        """Register a port for this client."""
        pass

    def deregister_port(self, port: int, client: 'Client'):
        """Deregister a port for this client."""
        pass

    def get_ephemeral_port(self) -> int:
        """Get a free ephemeral port."""
        pass


class Client:
    """Each connected TCP socket represents a client of the service.
    The data associated with each of these clients is kept in instances
    of this class.

    The recv_buffer is used to reassemble messages if they're fragmented
    across multiple calls to recv(), and also to process multiple messages
    if they're aggregated into a single recv() call's returned data.

    The send_buffer is used to queue data to be sent once the connection
    to the client has drained.  This could be problematic, since there's
    no back-pressure mechanism, but .. that's over-engineering for now."""

    def __init__(self, sock: socket.socket):
        """Constructor."""

        # TCP socket connected to client process.
        self.socket: socket.socket = sock

        # Outbound data queue.
        self.send_buffer: bytes = b''

        # Inbound data queue.
        self.recv_buffer: Buffer = Buffer()

        # Client's port number, allocated when handling the Login Request.
        self.ports: MutableSequence[int] = []
        return

    def name(self):
        """(Internal) Log message prefix."""
        return f'Port [{self.socket.fileno()}]'

    def get_socket(self) -> socket.socket:
        """Return the TCP socket connected to this client."""
        return self.socket

    def get_ports(self):
        return self.ports

    def process_received_data(self, buf: bytes, host: ServiceInterface):
        """Process received TCP data.

        :param buf: Byte buffer of received data.
        :param host: Reference to host service instance.

        Will append data to receive buffer, and if a complete
        message is available, dispatch it for handling."""

        # See if we have a header yet.
        self.recv_buffer.append(buf)
        header = Message.peek(self.recv_buffer)
        if header is None:
            # Added more bytes, but total available doesn't yet constitute
            # a message header.  This should only really happen in testing.
            logging.debug(f"IPC: {self.name()} Received data too small for header")
            return

        # Try to decode the entire message.  If it isn't all there,
        # just silently return.
        if header.type == MSG_OPEN_PORT_RQST:
            self.handle_open_port_request(host)

        elif header.type == MSG_REMOVE_PORT_RQST:
            self.handle_close_port_request()

        elif header.type == MSG_SEND_MESSAGE:
            self.handle_send_message(host)

        elif header.type == MSG_SEND_CHUNK:
            self.handle_send_chunk(host)

        elif header.type == MSG_DELIVER_MESSAGE:
            self.handle_deliver_message(host)

        elif header.type == MSG_DELIVER_CHUNK:
            self.handle_deliver_chunk(host)
        else:
            logging.warning(f"IPC: {self.name()} Received message with "
                            f"unexpected type code [{header.type}] "
                            "Ignoring message.")
        return

    def send_data(self, data: bytes):
        """Send a message to a connected client.

        :param data: Byte buffer of data to send."""

        # FIXME: append to send buffer, and write async

        while len(data) > 0:
            sent = self.socket.send(data)
            data = data[sent:]
        return

    def handle_open_port_request(self, host: ServiceInterface):
        """Handle request for new port."""
        request = OpenPortRequest.decode(self.recv_buffer)
        if not request:
            logging.warning(f"IPC: unable to decode open_port request")
            return

        port = request.requested_port

        # A requested port value of zero means to auto-assign a port number.
        if port == 0:
            port = host.get_ephemeral_port()
            if port <= 0:
                logging.error("IPC: Ephemeral port overflow; request failed.")
                self.send_open_port_response(False, request.requested_port)
                return

        self.ports.append(port)
        host.register_port(port, self)

        logging.info(f"IPC: {self.name()} new_port "
                     f"requested {request.requested_port}, "
                     f"assigned {port}.")

        logging.info(f"IPC: {self.name()} open_port({port}) succeeded")
        self.send_open_port_response(True, port)
        return

    def handle_close_port_request(self, host: ServiceInterface):
        """Handle request to close port."""

        request = ClosePortRequest.decode(self.recv_buffer)
        if not request:
            logging.warning(f"IPC: unable to decode close_port request")
            return

        port = request.port

        if port not in self.ports:
            logging.warning(f"IPC: close_port({port}) failed: bad port")
            self.send_close_port_response(False, port)
            return

        self.ports.remove(port)
        host.deregister_port(port, self)

        logging.info(f"IPC: {self.name()} close_port({port}) succeeded")
        self.send_close_port_response(True, port)
        return

    def handle_send_message(self, host: ServiceInterface):
        """Handle request to send message from connected client.

        :param host:
        :returns: None"""

        message = SendMessage.decode(self.recv_buffer)
        if not message:
            logging.debug("IPC: Received partial SendMessage")
            return

        logging.info(f"IPC: send_message: from {message.source}, "
                     f"to {message.destination}, "
                     f"len {message.payload_length}, "
                     f"[{message.payload.decode()}]")

        deliver = DeliverMessage()
        deliver.source = message.source
        deliver.destination = message.destination
        deliver.payload_length = message.payload_length
        deliver.payload = message.payload

        # Find destination
        host.deliver_message(deliver)

    def handle_deliver_message(self, host: ServiceInterface):
        message = DeliverMessage.decode(self.recv_buffer)
        if not message:
            logging.debug("IPC: Received partial DeliverMessage")
            return

        logging.info(f"IPC: deliver_message: "
                     f"from {message.source}, "
                     f"to {message.destination}, "
                     f"len {message.payload_length}")


    def handle_send_chunk(self, host: ServiceInterface):
        logging.info("send_chunk")

    def handle_deliver_chunk(self, host: ServiceInterface):
        logging.info("deliver_chunk")

    def send_open_port_response(self, result: bool, port: int):
        response = OpenPortResponse()
        response.result = result
        response.port = port
        buf = response.encode()
        self.send_data(buf)
        logging.info(f"IPC: new_port response: result={result}, port={port}")
        return

    def send_close_port_response(self, result: bool, port: int):
        response = ClosePortResponse()
        response.result = result
        response.port = port
        buf = response.encode()
        self.send_data(buf)
        logging.info(f"IPC: close_port response: port {port}")
        return

    def send_deliver_message(self, message: DeliverMessage):
        """Send delivery_message to client process."""

        buf = message.encode()
        self.send_data(buf)
        logging.info(f"deliver_message")
        return


class IPCService(darq.Service):
    """IPC message router."""

    def __init__(self):
        """Constructor."""
        super().__init__(SelectEventLoop(), 11000)

        self.is_active: bool = True
        self.clients: typing.Dict[socket.socket : Client] = {}
        self.ports: typing.Dict[int, Client] = {}

        # Listening socket.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setblocking(False)
        self.socket.bind(('0.0.0.0', 11000))
        self.socket.listen()

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
            l = [c.get_socket() for c in self.clients.values()]
            l.append(self.socket)

            ready_read, ready_write, junk = select.select(l, l, [], 1.0)

            for s in ready_read:
                if s == self.socket:
                    new_client, _ = self.socket.accept()
                    client = Client(new_client)
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
                    client.process_received_data(recv_buf, self)

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

    def handle_disconnect(self, client: Client):
        # Deregister port.
        ports = client.get_ports()
        for port in ports:
            if port in self.ports:
                del self.ports[port]
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

        # Start ephemeral ports at EPHEMERAL_PORT_START
        p = EPHEMERAL_PORT_START
        while p in self.ports:
            p += 1
            if p >= EPHEMERAL_PORT_MAX:
                return -1
        return p

    def register_port(self, port: int, client: Client):
        if port in self.ports:
            logging.warning(f"IPC: Error: port already registered: {port}")
            return False

        self.ports[port] = client
        return True

    def deregister_port(self, port: int, client: Client):
        if port not in self.ports:
            logging.warning(f"IPC: Error: port not registered: {port}")
            return False

        registered_client = self.ports[port]
        if registered_client != client:
            logging.warning(f"IPC: deregister_port({port}, {client.name()}) "
                            f"doesn't match registered client "
                            f"{registered_client.name()}")
            return False

        del self.ports[port]
        return True

    def deliver_message(self, message: DeliverMessage):
        """Deliver a received message to its destination port's client."""
        dest_client = self.ports.get(message.destination)
        if not dest_client:
            logging.warning(f"IPC: Failed to deliver message. "
                            f"No such port: {message.destination}")
            return

        dest_client.send_deliver_message(message)
        return


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr,
                        format='%(asctime)s %(levelname)8s %(message)s',
                        level=logging.DEBUG)

    service = IPCService()
    service.run()
