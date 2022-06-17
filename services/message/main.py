#! /usr/bin/env python
# Copyright (C) 2022 David Arnold

from darq.os.base import Service
import logging
import select
import socket
import struct
import typing

LOG = logging.getLogger()

MSG_LOGIN_RQST = 1
MSG_LOGIN_RESP = 2
MSG_ADD_PORT_RQST = 3
MSG_ADD_PORT_RESP = 4
MSG_REMOVE_PORT_RQST = 5
MSG_REMOVE_PORT_RESP = 6
MSG_SEND_MESSAGE = 7
MSG_SEND_CHUNK = 8
MSG_DELIVER_MESSAGE = 9
MSG_DELIVER_CHUNK = 10
MSG_LOGOUT_RQST = 11
MSG_LOGOUT_RESP = 12


class Buffer:
    def __init__(self, buffer: bytes = b''):
        self.buffer = buffer

    def peek(self, length: int) -> bytes:
        return self.buffer[:length]

    def consume(self, length: int) -> int:
        self.buffer = self.buffer[length:]
        return len(self.buffer)

    def append(self, buffer: bytes) -> int:
        self.buffer += buffer
        return len(self.buffer)

    def raw(self) -> bytes:
        return self.buffer

    def length(self) -> int:
        return len(self.buffer)


class Message:

    FORMAT = '!LBBxx'
    LENGTH = struct.calcsize(FORMAT)

    def __init__(self):
        self.length = 0
        self.version = 0
        self.type = 0

    def encode(self) -> bytes:
        buf = struct.pack(Message.FORMAT,
                          self.length,
                          self.version,
                          self.type)
        return buf

    @staticmethod
    def peek(buf: Buffer) -> typing.Optional['Message']:
        """Peek at the message header."""

        if buf.length() < Message.LENGTH:
            return None

        try:
            bits = struct.unpack(Message.FORMAT,
                                 buf.peek(Message.LENGTH))
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


class IFoo:
    def deliver_message(self, message: DeliverMessage):
        pass

    def register_port(self, port: int, client: 'Client'):
        pass


class Client:
    def __init__(self):
        self.socket = None
        self.send_buffer = b''
        self.recv_buffer = Buffer()

        self.port = 0
        self.ports = []
        return

    def set_socket(self, sock: socket.socket):
        """Save the TCP socket connected to this client.

        :param sock: Server-side socket for client connection."""
        self.socket = sock

    def get_socket(self) -> socket.socket:
        """Return the TCP socket connected to this client."""
        return self.socket

    def get_port(self):
        """Return the base port number for this client."""
        return self.port

    def process_received_data(self, buf: bytes, host: IFoo):
        """Process received TCP data.

        :param buf: Byte buffer of received data.

        Will append data to receive buffer, and if a complete
        message is available, dispatch it for handling."""

        # See if we have a header yet.
        self.recv_buffer.append(buf)
        header = Message.peek(self.recv_buffer)
        if header is None:
            # Added more bytes, but total available doesn't yet constitute
            # a message header.  This should only really happen in testing.
            print("Received data too small for header")
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
            print(f"Received message with unexpected type: {header.type}")

        return

    def send_data(self, data: bytes):
        """Send a message to a connected client.

        :param data: Byte buffer of data to send."""

        # FIXME: append to send buffer, and write async
        while len(data) > 0:
            sent = self.socket.send(data)
            data = data[sent:]
        return

    def handle_login_request(self, host: IFoo):

        login = LoginRequest.decode(self.recv_buffer)
        if not login:
            return

        print(f"handle_login_request(): requested port {login.requested_port}")
        if login.requested_port == 0:
            port = 42  # FIXME
        else:
            port = login.requested_port

        self.port = port
        host.register_port(port, self)

        self.send_login_response(port)
        return

    def handle_add_port_request(self, host: IFoo):
        print("handle_add_port_request()")
        return

    def handle_remove_port_request(self):
        print("handle_remove_port_request()")

    def handle_send_message(self, host: IFoo):
        message = SendMessage.decode(self.recv_buffer)
        if not message:
            print("Incomplete SendMessage")
            return

        print(f"handle_send_message(): from {message.source}, to {message.destination}, len {message.payload_length}, [{message.payload.decode()}]")

        deliver = DeliverMessage()
        deliver.source = message.source
        deliver.destination = message.destination
        deliver.payload_length = message.payload_length
        deliver.payload = message.payload

        # Find destination
        host.deliver_message(deliver)

    def handle_logout_request(self):
        print("handle_logout_request()")

    def send_login_response(self, port: int):
        response = LoginResponse()
        response.port = port
        buf = response.encode()
        self.send_data(buf)
        print(f"Sent login_response(): port = {port}")
        return

    def send_deliver_message(self, message: DeliverMessage):
        buf = message.encode()
        self.send_data(buf)
        print(f"SSent deliver_message")
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

        while self.is_active:
            l = [c.get_socket() for c in self.clients.values()]
            l.append(self.socket)

            ready_read, ready_write, junk = select.select(l, l, [], 1.0)

            for s in ready_read:
                if s == self.socket:
                    new_client, _ = self.socket.accept()
                    client = Client()
                    client.set_socket(new_client)
                    self.clients[new_client] = client

                    print("Accepted new client")

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

                    print(f"Delivering {len(recv_buf)} bytes")
                    client.process_received_data(recv_buf, self)

            for s in ready_write:
                if s == self.socket:
                    print("Listening socket returned writeable")
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

        print(f"Client [{port}] on socket [{sock.fileno()}] disconnected")
        return

    def register_port(self, port: int, client: Client):
        if port in self.ports:
            print(f"Error: port already registered: {port}")
            return False

        self.ports[port] = client
        return True

    def deliver_message(self, message: DeliverMessage):
        dest = self.ports.get(message.destination)
        if not dest:
            print(f"Failed to deliver message. No such port: {message.destination}")
            return

        dest.send_deliver_message(message)
        return


if __name__ == "__main__":
    service = MessageService()
    service.run()
