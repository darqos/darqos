#! /usr/bin/env python
# Copyright (C) 2022 David Arnold

from darq.os.base import Service
import logging
import select
import socket
import struct
import sys
import typing


# Message type codes.
MSG_ADD_PORT_RQST = 1
MSG_ADD_PORT_RESP = 2
MSG_REMOVE_PORT_RQST = 3
MSG_REMOVE_PORT_RESP = 4
MSG_SEND_MESSAGE = 5
MSG_SEND_CHUNK = 6
MSG_DELIVER_MESSAGE = 7
MSG_DELIVER_CHUNK = 8


class MessageDecodingError(Exception):
    """Failed to decode a message from the supplied buffer."""
    pass


class CannotAllocatePortError(Exception):
    """The ephemeral port range is exhausted."""
    pass


class Buffer:
    """Basic byte buffer wrapper."""

    def __init__(self, buffer: bytes = b''):
        """Constructor.

        :param buffer: Optional initial contents."""
        self.buffer = buffer

    def peek(self, length: int) -> bytes:
        """Return a copy of the start of the buffer.

        :param length: Number of bytes to return."""
        return self.buffer[:length]

    def consume(self, length: int) -> int:
        """Remove the start of the buffer.

        :param length: Number of bytes to remove."""
        self.buffer = self.buffer[length:]
        return len(self.buffer)

    def append(self, buffer: bytes) -> int:
        """Add more bytes to the end of the buffer.

        :param buffer: Bytes to add."""
        self.buffer += buffer
        return len(self.buffer)

    def raw(self) -> bytes:
        """Return reference to the internal assembly buffer."""
        return self.buffer

    def length(self) -> int:
        """Return count of bytes in the buffer."""
        return len(self.buffer)


class Message:
    """Common header for all messages to/from the Message Service."""

    # Header format.
    FORMAT = '!LBBxx'

    # Header length (in bytes).
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        """Constructor."""
        self.length = 0
        self.version = 0
        self.type = 0

    def encode(self) -> bytes:
        """Return a byte buffer encoding this message header."""
        buf = struct.pack(Message.FORMAT,
                          self.length,
                          self.version,
                          self.type)
        return buf

    @staticmethod
    def peek(buf: Buffer) -> typing.Optional['Message']:
        """Peek at the message header.

        :param buf: Byte buffer to decode the header from."""

        if buf.length() < Message.LENGTH:
            return None

        try:
            header = buf.peek(Message.LENGTH)
            bits = struct.unpack(Message.FORMAT, header)

            msg = Message()
            msg.length = bits[0]
            msg.version = bits[1]
            msg.type = bits[2]
            return msg

        except:
            return None

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['Message']:
        msg = Message.peek(buf)
        if msg:
            buf.consume(Message.LENGTH)
        return msg

    def init(self, base: 'Message'):
        self.length = base.length
        self.version = base.version
        self.type = base.type


class LoginRequest(Message):

    FORMAT = '!Q'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.requested_port = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack('!Q', self.requested_port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['LoginRequest']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = LoginRequest()
        msg.init(base)
        try:
            bits = struct.unpack(LoginRequest.FORMAT,
                                 buf.peek(LoginRequest.LENGTH))
            msg.requested_port = bits[0]
            buf.consume(LoginRequest.LENGTH)
            return msg

        except:
            return None


class LoginResponse(Message):

    FORMAT = '!Q'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.port = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(LoginResponse.FORMAT, self.port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['LoginResponse']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = LoginResponse()
        super(msg).init(base)
        try:
            bits = struct.unpack(LoginResponse.FORMAT,
                                 buf.peek(LoginResponse.LENGTH))

            msg.port = bits[0]

            buf.consume(LoginResponse.LENGTH)
            return msg

        except:
            return None


class AddPortRequest(Message):

    FORMAT = '!Q'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.requested_port = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack('!Q', self.requested_port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['AddPortRequest']:
        base = super().decode(buf)
        if not base:
            return None

        msg = AddPortRequest()
        super(msg).init(base)
        try:
            bits = struct.unpack(AddPortRequest.FORMAT,
                                 buf.peek(AddPortRequest.LENGTH))

            msg.requested_port = bits[0]

            buf.consume(AddPortRequest.LENGTH)
            return msg

        except:
            return None


class AddPortResponse(Message):

    FORMAT = '!Q'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.port = 0

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(AddPortResponse.FORMAT, self.port)
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['AddPortResponse']:
        base = super().decode(buf)
        if not base:
            return None

        msg = AddPortResponse()
        super(msg).init(base)
        try:
            bits = struct.unpack(AddPortResponse.FORMAT,
                                 buf.peek(AddPortResponse.LENGTH))

            msg.port = bits[0]

            buf.consume(AddPortResponse.LENGTH)
            return msg

        except:
            return None


class SendMessage(Message):

    FORMAT = '!QQL'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.source = 0
        self.destination = 0
        self.payload_length = 0
        self.payload = b''

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(SendMessage.FORMAT,
                           self.source,
                           self.destination,
                           len(self.payload))
        buf += self.payload
        buf += '\0' * (4 - (len(self.payload) % 4))
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['SendMessage']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = SendMessage()
        msg.init(base)
        try:
            bits = struct.unpack(SendMessage.FORMAT,
                                 buf.peek(SendMessage.LENGTH))
            msg.source = bits[0]
            msg.destination = bits[1]
            msg.payload_length = bits[2]
            buf.consume(SendMessage.LENGTH)
            msg.payload = buf.peek(msg.payload_length)
            buf.consume(msg.payload_length)
            return msg

        except:
            return None


class DeliverMessage(Message):

    FORMAT = '!QQL'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        super().__init__()
        self.source = 0
        self.destination = 0
        self.payload_length = 0
        self.payload = b''

    def encode(self) -> bytes:
        buf = super().encode()
        buf += struct.pack(DeliverMessage.FORMAT,
                           self.source,
                           self.destination,
                           len(self.payload))
        buf += self.payload
        buf += b'\0' * (4 - (len(self.payload) % 4))
        return buf

    @staticmethod
    def decode(buf: Buffer) -> typing.Optional['DeliverMessage']:
        base = Message.decode(buf)
        if not base:
            return None

        msg = DeliverMessage()
        msg.init(base)
        try:
            bits = struct.unpack(DeliverMessage.FORMAT,
                                 buf.peek(DeliverMessage.LENGTH))
            msg.source = bits[0]
            msg.destination = bits[1]
            msg.payload_length = bits[2]
            buf.consume(SendMessage.LENGTH)
            msg.payload = buf.peek(msg.payload_length)
            buf.consume(msg.payload_length)
            return msg

        except:
            return None


class ServiceInterface:
    def deliver_message(self, message: DeliverMessage):
        pass

    def register_port(self, port: int, client: 'Client'):
        pass

    def get_ephemeral_port(self) -> int:
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
        self.port: int = 0
        return

    def _id(self):
        """(Internal) Log message prefix."""
        port = f'{self.port if self.port else "-"}'
        return f'Port [{self.socket.fileno()} / {port}]'

    def get_socket(self) -> socket.socket:
        """Return the TCP socket connected to this client."""
        return self.socket

    def get_port(self) -> int:
        """Return the base port number for this client."""
        return self.port

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
            logging.debug(f"{self._id()} Received data too small for header")
            return

        # Try to decode the entire message.  If it isn't all there,
        # just silently return.
        if header.type == MSG_LOGIN_RQST:
            self.handle_login_request(host)

        elif header.type == MSG_ADD_PORT_RQST:
            self.handle_add_port_request(host)

        elif header.type == MSG_REMOVE_PORT_RQST:
            self.handle_remove_port_request()

        elif header.type == MSG_SEND_MESSAGE:
            self.handle_send_message(host)

        elif header.type == MSG_LOGOUT_RQST:
            self.handle_logout_request()

        else:
            logging.warning(f"{self._id()} Received message with "
                            f"unexpected type code [{header.type}]")
        return

    def send_data(self, data: bytes):
        """Send a message to a connected client.

        :param data: Byte buffer of data to send."""

        # FIXME: append to send buffer, and write async
        while len(data) > 0:
            sent = self.socket.send(data)
            data = data[sent:]
        return

    def handle_login_request(self, host: ServiceInterface):

        login = LoginRequest.decode(self.recv_buffer)
        if not login:
            return

        if login.requested_port == 0:
            # FIXME: handle error here, and return NAK
            port = host.get_ephemeral_port()
        else:
            port = login.requested_port

        self.port = port
        host.register_port(port, self)

        logging.info(f"{self._id()} Login "
                     f"requested port {login.requested_port}, "
                     f"assigned port {port}.")

        self.send_login_response(port)
        return

    def handle_add_port_request(self, host: ServiceInterface):
        logging.info("handle_add_port_request()")
        return

    def handle_remove_port_request(self):
        logging.info("handle_remove_port_request()")

    def handle_send_message(self, host: ServiceInterface):
        message = SendMessage.decode(self.recv_buffer)
        if not message:
            logging.debug("Incomplete SendMessage")
            return

        logging.info(f"handle_send_message(): from {message.source}, to {message.destination}, len {message.payload_length}, [{message.payload.decode()}]")

        deliver = DeliverMessage()
        deliver.source = message.source
        deliver.destination = message.destination
        deliver.payload_length = message.payload_length
        deliver.payload = message.payload

        # Find destination
        host.deliver_message(deliver)

    def handle_logout_request(self):
        logging.info("handle_logout_request()")

    def send_login_response(self, port: int):
        response = LoginResponse()
        response.port = port
        buf = response.encode()
        self.send_data(buf)
        logging.info(f"Sent login_response(): port = {port}")
        return

    def send_deliver_message(self, message: DeliverMessage):
        buf = message.encode()
        self.send_data(buf)
        logging.info(f"SSent deliver_message")
        return


class MessageService(Service):

    def __init__(self):
        super().__init__("tcp://*.11000")

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

    @staticmethod
    def get_name() -> str:
        return "message"

    def run(self):
        """Main loop."""

        logging.info("Servicing requests.")
        while self.is_active:
            l = [c.get_socket() for c in self.clients.values()]
            l.append(self.socket)

            ready_read, ready_write, junk = select.select(l, l, [], 1.0)

            for s in ready_read:
                if s == self.socket:
                    new_client, _ = self.socket.accept()
                    client = Client(new_client)
                    self.clients[new_client] = client

                    logging.info(f"Client socket [{s.fileno()}] connected")

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

                    logging.debug(f"Client port [{client.get_port()}] delivering {len(recv_buf)} bytes")
                    client.process_received_data(recv_buf, self)

            for s in ready_write:
                if s == self.socket:
                    logging.debug("Listening socket returned writeable")
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
        port = client.get_port()
        if port in self.ports:
            del self.ports[port]

        # Remove client.
        sock = client.get_socket()
        if sock in self.clients:
            del self.clients[sock]

        logging.info(f"Client [{port}] on socket [{sock.fileno()}] disconnected")
        return

    def get_ephemeral_port(self):
        """Allocate an ephemeral port.

        Note: this isn't thread-safe: if it's called again before the port
        number is used, it will return the same value."""

        # Start ephemeral ports at 60,000.
        p = 60000
        while p in self.ports:
            p += 1
            if p > 65535:
                logging.error("Ephemeral port overflow!")
                raise CannotAllocatePortError()
        return p

    def register_port(self, port: int, client: Client):
        if port in self.ports:
            logging.warning(f"Error: port already registered: {port}")
            return False

        self.ports[port] = client
        return True

    def deliver_message(self, message: DeliverMessage):
        dest = self.ports.get(message.destination)
        if not dest:
            logging.warning(f"Failed to deliver message. No such port: {message.destination}")
            return

        dest.send_deliver_message(message)
        return


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr,
                        format='%(asctime)s %(levelname)8s %(message)s',
                        level=logging.DEBUG)

    service = MessageService()
    service.run()
