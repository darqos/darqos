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


class Buffer:
    def __init__(self, buffer: bytes):
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
    def decode(buf: Buffer) -> typing.Optional['Message']:
        if buf.length() < Message.LENGTH:
            return None

        try:
            bits = struct.unpack(Message.FORMAT,
                                 buf.peek(Message.LENGTH))

            msg = Message()
            msg.length = bits[0]
            msg.version = bits[1]
            msg.type = bits[2]

            buf.consume(Message.LENGTH)
            return msg

        except:
            return None

    def init(self, base: 'Message'):
        self.length = base.length
        self.version = base.version
        self.type = base.type


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
        base = super().decode(buf)
        if not base:
            return None

        msg = SendMessage()
        super(msg).init(base)
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
    def __init__(self):
        super().__init__()


class Client:
    def __init__(self):
        self.socket = None
        self.ports = []
        self.send_buffer = b''
        self.recv_buffer = b''
        return

    def set_socket(self, sock: socket.socket):
        self.socket = sock

    def get_socket(self) -> socket.socket:
        return self.socket


class MessageService(Service):

    def __init__(self):
        super().__init__("tcp://*.11000")

        self.is_active = True
        self.clients = {}  # socket : Client

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
                    recv_buf = s.recv(65536)
                    if len(recv_buf) == 0:
                        del self.clients[s]
                        print("Closed session")
                        continue

                    client.send_buffer += recv_buf
                    print(f"Delivering {len(recv_buf)} bytes")
                    # Call parser

            for s in ready_write:
                if s == self.socket:
                    print("Listening socket returned writeable")
                else:
                    client = self.clients[s]
                    if len(client.send_buffer) > 0:
                        sent = s.send(client.send_buffer)
                        if sent < len(client.send_buffer):
                            client.send_buffer = client.send_buffer[sent:]
                        else:
                            client.send_buffer = b''


if __name__ == "__main__":
    service = MessageService()
    service.run()
