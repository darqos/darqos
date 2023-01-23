#! /usr/bin/env python
# darqos
# Copyright (C) 2022-2023 David Arnold

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
import random
import select
import subprocess
import sys

from dataclasses import dataclass

import darq
from darq.kernel.ipc import *
from darq.kernel.loop import SelectEventLoop, SocketListener, TimerListener

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
# This list is ORDERED.

@dataclass
class ServiceInfo:
    name: str
    path: str

@dataclass
class ServiceState:
    name: str
    process: subprocess.Popen


SERVICES = [
    #ServiceInfo("storage", "services/storage/main.py"),
    #ServiceInfo("security", "services/security/main.py"),
    #ServiceInfo("type", "services/type/main.py"),
    # ServiceInfo("name", "service/name/name.py"),
    #ServiceInfo("index", "services/index/main.py"),
    #ServiceInfo("history", "services/history/main.py"),
    #ServiceInfo("metadata", "services/metadata/main.py"),
    # KB / things
    #ServiceInfo("terminal", "services/terminal/main.py")
]


class PseudoKernel(darq.Service, SocketListener, TimerListener):
    """IPC message router."""

    def __init__(self):
        """Constructor."""
        super().__init__(SelectEventLoop(), 11000)

        self.services = []
        self.types = []
        self.tools = []
        self.lenses = []

        self.is_active: bool = True
        self.clients: typing.Dict[socket.socket: IPCClient] = {}
        self.fds: typing.Dict[int, IPCClient] = {}

        # Listening socket.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setblocking(False)
        self.socket.bind(('0.0.0.0', 11000))
        self.socket.listen()
        self.loop.add_socket(self.socket, self)

        # Event loop.

        # Complete.
        logging.info("IPC: service initialized.")

    @staticmethod
    def get_name() -> str:
        return "IPC"

    def on_readable(self, sock: socket.socket):
        """Handle client offer socket readable event."""
        if sock == self.socket:
            # This is the server's listening socket.
            client_socket, client_addr = self.socket.accept()
            client = IPCClient(self, client_socket)
            self.clients[client_socket] = client

            self.loop.add_socket(client_socket, self)

            logging.info(f"IPC: Socket [{client_socket.fileno()}] "
                         f"connected from {client_addr}")
            return

        client = self.clients.get(sock)
        if client is None:
            logging.error(f"IPC: got read callback from unexpected "
                          f"socket {sock.fileno()}.  Closing socket.")

            self.loop.cancel_socket(sock)
            sock.close()
            return

        client.on_readable(sock)
        return

    def on_writeable(self, sock: socket.socket):
        # Ignore writeable events on offer socket.
        pass

    def on_timeout(self, timer_id: int, expiry: float, actual_time: float):
        pass

    def run(self):
        """Main loop."""

        logging.info("IPC: Servicing requests.")
        self.loop.run()
        return

        while self.is_active:
            sl = [c.get_socket() for c in self.clients.values()]
            sl.append(self.socket)

            ready_read, ready_write, junk = select.select(sl, sl, [], 1.0)

            for s in ready_read:
                if s == self.socket:
                    self.on_readable(s)
                else:
                    client = self.clients.get(s)
                    if not client:
                        logging.warning(f"IPC: no client for socket")
                        continue
                    client.on_readable(s)

            for s in ready_write:
                if s == self.socket:
                    logging.debug("IPC: Listening socket returned writeable")
                else:
                    client = self.clients.get(s)
                    if not client:
                        logging.warning(f"IPC: no client for socket")
                        continue
                    client.on_writeable(s)
        return

    def handle_disconnect(self, client: IPCClient):
        """Handle disconnection of a client process.

        :param client: Client that has disconnected."""

        # Cache name, because we need to use it a few times.
        name = client.name()

        # Deregister ports.
        ports = client.get_ports()
        for port in ports:
            if port in self.fds:
                del self.fds[port]
                logging.debug(f"IPC: {name} closed port {port}")

        # Remove client.
        sock = client.get_socket()
        if sock in self.clients:
            del self.clients[sock]

        self.loop.cancel_socket(sock)
        sock.close()

        logging.info(f"IPC: {name} disconnected.")
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

        buf = message.encode()
        dest_client.send_data(buf)
        return

    def handle_reboot(self, client: IPCClient, message: Reboot):
        logging.info(f"System booting.")

        # Shutdown first.
        self.do_shutdown()

        # Start services.
        self.do_boot()

    def handle_shutdown(self, client: IPCClient, message: Shutdown):
        logging.info(f"System shutdown requested.")

        self.do_shutdown()

    def do_shutdown(self):
        # Walk list of tools, and kill them all.
        # Walk list of lenses, and kill them all.
        # Walk list of types, and kill them all.

        # Walk list of services, and kill them all.
        service_state = self.services.pop()
        while service_state:
            service_state.process.terminate()
            service_state = self.services.pop()

        return

    def do_boot(self):
        """Start system services."""
        # Start list of configured services.
        for service_info in SERVICES:
            logging.info(f"Starting {service_info.name} service.")
            p = subprocess.Popen(["python", service_info.path])
            s = ServiceState(name=service_info.name, process=p)
            self.services.append(s)

        logging.info(f"Services started.")
        return

    def schedule_boot(self):
        """Schedule the boot process via the event loop."""
        self.loop.add_deferred(self.do_boot)
        return

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
                                             request.request_id,
                                             1,  # FIXME: better errno
                                             request.requested_port)
                return

        client.add_port(port)
        self.register_port(port, client)

        logging.info(f"IPC: {client.name()} new_port "
                     f"requested {request.requested_port}, "
                     f"assigned {port}.")

        logging.info(f"IPC: {client.name()} open_port({port}) succeeded")
        self.send_open_port_response(client, request.request_id, 0, port)
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
        self.send_close_port_response(client, request.request_id, 0, port)
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
            raise Exception("bad destination port in send")  # FIXME: needs proper exception (and on_error)

        deliver = DeliverMessage()
        deliver.source = message.source
        deliver.destination = message.destination
        deliver.set_payload(message.payload)

        buf = deliver.encode()
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
                                request_id: int,
                                result: int,
                                port: UInt64):
        response = OpenPortResponse()
        response.request_id = request_id
        response.result = result
        response.port = port
        buf = response.encode()
        client.send_data(buf)
        logging.info(f"IPC: open_port response: "
                     f"request_id={request_id}, result={result}, port={port}")
        return

    def send_close_port_response(self,
                                 client: IPCClient,
                                 request_id: int,
                                 result: int,
                                 port: UInt64):
        response = ClosePortResponse()
        response.request_id = request_id
        response.result = result
        response.port = port
        buf = response.encode()
        client.send_data(buf)
        logging.info(f"IPC: close_port response: "
                     f"request_id={request_id}, result={result}, port={port}")
        return

    def dispatch(self, client: IPCClient):
        """Process data received from clients.

        :param client: Client connection that received data

        Handle received message, if buffer contains one."""

        # See if we have a header yet.
        buffer = client.get_buffer()
        if buffer.length() < 8:
            logging.debug(f"IPC: {client.name()} "
                          f"Queued data ({buffer.length()} bytes) too s"
                          f"mall for header (8 bytes).")
            return

        message_type = Message.decode_type(buffer.peek(8))
        message_length = Message.decode_length(buffer.peek(8))

        if message_type == 0 or message_length > buffer.length():
            # Added more bytes, but total available doesn't yet constitute
            # a message.  This should only really happen in testing.
            logging.debug(f"IPC: {client.name()} "
                          f"Queued data ({buffer.length()} bytes) "
                          f"too small for message {message_type} "
                          f"which expects {message_length} bytes.")
            return

        message_bytes = buffer.peek(message_length)
        buffer.consume(message_length)  # FIXME: merge these two!

        if message_type == MSG_OPEN_PORT_RQST:
            message = OpenPortRequest()
            message.decode(message_bytes)
            self.handle_open_port_request(client, message)

        elif message_type == MSG_CLOSE_PORT_RQST:
            message = ClosePortRequest()
            message.decode(message_bytes)
            self.handle_close_port_request(client, message)

        elif message_type == MSG_SEND_MESSAGE:
            message = SendMessage()
            message.decode(message_bytes)
            self.handle_send_message(client, message)

        elif message_type == MSG_REBOOT:
            message = Reboot()
            message.decode(message_bytes)
            self.handle_reboot(client, message)

        elif message_type == MSG_SHUTDOWN:
            message = Shutdown()
            message.decode(message_bytes)
            self.handle_shutdown(client, message)

        else:
            logging.warning(f"IPC: {client.name()} Received message with "
                            f"unexpected type code [{message_type}] "
                            "Ignoring message.")
        return


################################################################

if __name__ == "__main__":
    # FIXME: currently, just log to stderr.
    logging.basicConfig(stream=sys.stderr,
                        format='%(asctime)s p-Kernel %(levelname)8s %(message)s',
                        level=logging.DEBUG)

    logging.info(f"Starting p-kernel.")

    # Create p-Kernel.
    service = PseudoKernel()

    # Queue up the boot process.
    service.schedule_boot()

    # Run the event loop.
    service.run()

    logging.info(f"Exiting p-kernel.")
    sys.exit(0)
