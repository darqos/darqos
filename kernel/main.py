#! /usr/bin/env python
# darqos
# Copyright (C) 2022-2024 David Arnold

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
import os
import platform
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


class PseudoKernel(darq.Service, SocketListener, TimerListener):
    """IPC message router."""

    def __init__(self):
        """Constructor."""

        # Use the callbacks model for I/O.
        darq.init_callbacks(darq.SelectEventLoop(), self)
        super().__init__()

        self.host_os = ''
        self.host_os_version = ''
        self.python_version = ''
        self.cpu = ''
        self.device = ''

        self.services = []
        self.types = []
        self.tools = []
        self.lenses = []

        self.is_active: bool = True

        # Map of socket to IPC client.
        self.clients: typing.Dict[socket.socket: IPCClient] = {}

        # Map of file descriptor to IPC client.
        self.fds: typing.Dict[int, IPCClient] = {}

        # Host platform.
        self.detect_platform()

        # Listening socket.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setblocking(False)
        self.socket.bind(('0.0.0.0', TCP_PORT))
        self.socket.listen()
        darq.loop().add_socket(self.socket, self)

        logging.info(f"Using {self.host_os} {self.host_os_version}")
        logging.info(f"On a {self.device} ({self.cpu})")
        logging.info(f"Running Python v{self.python_version}")
        logging.info("")
        logging.info(f"Listening for IPC sessions on {TCP_PORT}")

        # Complete.
        logging.info("Initialized.")
        return

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

            darq.loop().add_socket(client_socket, self)

            logging.info(f"Socket [{client_socket.fileno()}] "
                         f"connected from {client_addr}")
            return

        client = self.clients.get(sock)
        if client is None:
            logging.error(f"got read callback from unexpected "
                          f"socket {sock.fileno()}.  Closing socket.")

            darq.loop().cancel_socket(sock)
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

        logging.info("Entering main loop.")
        darq.loop().run()
        return

        # FIXME: remove below, once darq loop is capable

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
                        logging.warning(f"No client for socket.")   # FIXME: log socket details
                        continue
                    client.on_readable(s)

            for s in ready_write:
                if s == self.socket:
                    logging.debug("Listening socket returned writeable")
                else:
                    client = self.clients.get(s)
                    if not client:
                        logging.warning(f"No client for socket")  # FIXME: log socket details
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
                logging.debug(f"{name} closed port {port}")

        # Remove client.
        sock = client.get_socket()
        if sock in self.clients:
            del self.clients[sock]

        darq.loop().cancel_socket(sock)
        sock.close()

        logging.info(f"{name} disconnected.")
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
            logging.warning(f"register_port() error: "
                            f"port already registered: {port}")
            return False

        self.fds[port] = client
        return True

    def deregister_port(self, port: int, client: IPCClient):
        """Delete the association between a port and a client (socket)."""
        if port not in self.fds:
            logging.warning(f"deregister_port() error: "
                            f"port not registered: {port}")
            return False

        registered_client = self.fds[port]
        if registered_client != client:
            logging.warning(f"deregister_port() error: "
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
            logging.warning(f"Failed to deliver message. "
                            f"No such port: {message.destination}")
            return

        buf = message.encode()
        dest_client.send_data(buf)
        return

    def handle_reboot(self, client: IPCClient, message: Reboot):
        logging.info(f"System rebooting.")

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

        logging.info(f"Starting bootstrap")

        # FIXME: what needs to be done here?

        logging.info(f"Completed bootstrap")
        return

    def schedule_boot(self):
        """Schedule the boot process via the event loop."""
        darq.loop().add_deferred(self.do_boot)
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
                logging.error("Ephemeral port overflow; request failed.")
                self.send_open_port_response(client,
                                             request.request_id,
                                             1,  # FIXME: better errno
                                             request.requested_port)
                return

        client.add_port(port)
        self.register_port(port, client)

        logging.info(f"{client.name()} new_port "
                     f"requested {request.requested_port}, "
                     f"assigned {port}.")

        logging.info(f"{client.name()} open_port({port}) succeeded")
        self.send_open_port_response(client, request.request_id, 0, port)
        return

    def handle_close_port_request(self,
                                  client: IPCClient,
                                  request: ClosePortRequest):
        """Handle request to close port."""

        port = request.port

        if port not in self.fds:
            logging.warning(f"close_port({port}) failed: bad port")
            self.send_close_port_response(client, port, False)
            return

        client.remove_port(port)
        self.deregister_port(port, client)

        logging.info(f"{client.name()} close_port({port}) succeeded")
        self.send_close_port_response(client, request.request_id, 0, port)
        return

    def handle_send_message(self,
                            source: IPCClient,
                            message: SendMessage):
        """Handle request to send message from connected client.

        :param source: Client session that received this message.
        :param message: Received message."""

        logging.info(f"send_message: from {message.source}, "
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
        logging.info(f"open_port response: "
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
        logging.info(f"close_port response: "
                     f"request_id={request_id}, result={result}, port={port}")
        return

    def dispatch(self, client: IPCClient):
        """Process data received from clients.

        :param client: Client connection that received data

        Handle received message, if buffer contains one."""

        # See if we have a header yet.
        buffer = client.get_buffer()
        if buffer.length() < 8:
            logging.debug(f"{client.name()} "
                          f"Queued data ({buffer.length()} bytes) too s"
                          f"mall for header (8 bytes).")
            return

        message_type = Message.decode_type(buffer.peek(8))
        message_length = Message.decode_length(buffer.peek(8))

        if message_type == 0 or message_length > buffer.length():
            # Added more bytes, but total available doesn't yet constitute
            # a message.  This should only really happen in testing.
            logging.debug(f"{client.name()} "
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
            logging.warning(f"{client.name()} Received message with "
                            f"unexpected type code [{message_type}] "
                            "Ignoring message.")
        return

    def detect_platform(self):
        """Detect host platform."""

        # Host OS (macOS, Linux, Darq)
        system = platform.system()
        if system == 'Darwin':
            self.host_os = 'macOS'
        elif system == 'Linux':
            self.host_os = 'Linux'
        else:
            self.host_os = 'Unknown'

        # OS Version
        if system == 'Darwin':
            mac_ver = platform.mac_ver()
            self.host_os_version = mac_ver[0]
        elif system == 'Linux':
            linux_ver = platform.freedesktop_os_release()
            self.host_os_version = linux_ver['PRETTY_NAME']
        else:
            self.host_os_version = 'Unknown'

        # CPU architecture.
        mach = platform.machine()
        if system == 'Darwin':
            self.cpu = mach
        elif system == 'Linux':
            self.cpu = mach
        else:
            self.cpu = 'Unknown'

        # Python version.
        self.python_version = platform.python_version()

        # Hardware device.
        if system == 'Darwin':
            f = os.popen("system_profiler SPHardwareDataType")
            s = f.read()
            f.close()

            model_name = 'Unknown'
            model_id = 'Unknown'

            for line in s.split('\n'):
                if ':' not in line:
                    continue

                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()

                if key == 'Model Name':
                    model_name = value
                elif key == 'Model Identifier':
                    model_id = value

            # FIXME: needs the "MacBook Pro Retina (late 2014)" here.
            self.device = f"{model_name} ({model_id})"

        if system == 'Linux':
            # FIXME: otherwise, dmidecode has some info?

            f = open('/proc/cpuinfo')
            s = f.read()
            f.close()

            model_name = 'Unknown'

            for line in s.split('\n'):
                if ':' not in line:
                    continue

                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()

                if key == 'Model':
                    model_name = value

            self.device = f"{model_name}"

        return

################################################################

if __name__ == "__main__":
    if os.getenv("INVOCATION_ID") is not None:
        # Running under systemd
        logging.basicConfig(stream=sys.stdout,
                            format='p-Kernel %(levelname)8s %(message)s',
                            level=logging.DEBUG)
    else:
        # Likely being run manually
        logging.basicConfig(stream=sys.stderr,
                            format='%(asctime)s p-Kernel %(levelname)8s %(message)s',
                            level=logging.DEBUG)

    logging.info("Starting p-kernel.")

    # Create p-Kernel.
    service = PseudoKernel()

    # Queue up the boot process.
    service.schedule_boot()

    # Run the event loop.
    service.run()

    logging.info("Exiting p-Kernel.")
    sys.exit(0)
