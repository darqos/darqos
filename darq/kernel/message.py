# darqos
# Copyright (C) 2022-2023 David Arnold

# Messages used for communication with the p-kernel.
# These are roughly equivalent to system calls.
#
# Note that these are different from message used to communicate between
# user processes and services, which _use_ the IPC-related p-kernel
# messages as transport.  This distinction would be a lot more clear if
# these messages were actual system calls, but that's the right mental
# model regardless.

from .types import UInt8, UInt16, UInt32, UInt64


# Message type codes.
MSG_OPEN_PORT_RQST = 1
MSG_OPEN_PORT_RESP = 2
MSG_REMOVE_PORT_RQST = 3
MSG_REMOVE_PORT_RESP = 4
MSG_SEND_MESSAGE = 5
MSG_SEND_CHUNK = 6
MSG_DELIVER_MESSAGE = 7
MSG_DELIVER_CHUNK = 8
MSG_REBOOT = 9
MSG_SHUTDOWN = 10


class Message:
    """Common header for all messages to/from the p-kernel."""

    def __init__(self):
        """Constructor."""

        # Message version.
        self.version = UInt8(0)

        # Length of header in bytes.
        self.header_length = UInt8(8)

        # Message type code.
        self.type = UInt8(0)

        # Padding.
        self._hpad0 = UInt8(0)

        # Message length, including header, in bytes.
        self.length = UInt32(0)

    def get_header_length(self) -> int:
        """Return length of this message's header."""
        return self.header_length

    def init(self, base: 'Message'):
        """Initialise this message from another.

        :param base: Message to copy."""
        self.version = base.version
        self.header_length = base.header_length
        self.type = base.type
        self._hpad0 = 0
        self.length = base.length

    def set_version(self, version: int):
        """Set the version number header field.

        :param version: Integer version number for message format."""
        self.version = UInt8(version)

    def set_header_length(self, length: int):
        """Set the header length field.

        :param length: Number of bytes in the header."""
        self.header_length = UInt8(length)

    def set_type(self, type_code: int):
        """Set the message type code.

        :param type_code: Integer message type code."""
        self.type = UInt8(type_code)

    def set_length(self, length: int):
        """Set the total message length.

        :param length: Total number of bytes in the message."""
        self.length = UInt32(length)


class OpenPortRequest(Message):
    def __init__(self):
        super().__init__()
        self.set_type(MSG_OPEN_PORT_RQST)
        self.set_length(8 + 8)
        self.requested_port: UInt64 = UInt64(0)


class OpenPortResponse(Message):

    def __init__(self):
        super().__init__()
        self.port: UInt64 = UInt64(0)
        self.result: bool = False


class ClosePortRequest(Message):
    def __init__(self):
        super().__init__()
        self.port: UInt64 = UInt64(0)


class ClosePortResponse(Message):
    def __init__(self):
        super().__init__()
        self.port: UInt64 = UInt64(0)


class SendMessage(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.payload = b''


class DeliverMessage(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.payload = b''


class SendChunk(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.offset: UInt64 = UInt64(0)
        self.payload = b''


class DeliverChunk(Message):
    def __init__(self):
        super().__init__()
        self.source: UInt64 = UInt64(0)
        self.destination: UInt64 = UInt64(0)
        self.offset: UInt64 = UInt64(0)
        self.payload = b''


class Reboot(Message):
    def __init__(self):
        super().__init__()


class Shutdown(Message):
    def __init__(self):
        super().__init__()
