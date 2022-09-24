# darqos
# Copyright (C) 2022 David Arnold

"""Messages used for communication with the p-kernel.  These are roughly
equivalent to system calls."""

class Message:
    """Common header for all messages to/from the Message Service."""

    def __init__(self):
        """Constructor."""

        # Message version.
        self.version = UInt8(0)

        # Length of header in bytes.
        self.header_length = UInt8(0)

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
        self.version = base.version
        self.header_length = base.header_length
        self.type = base.type
        self._hpad0 = 0
        self.length = base.length

    def set_version(self, version: int):
        self.version = UInt8(version)

    def set_header_length(self, length: int):
        self.header_length = UInt8(length)

    def set_type(self, type_code: int):
        self.type = UInt8(type_code)

    def set_length(self, length: int):
        self.length = UInt32(length)


class OpenPortRequest(Message):
    def __init__(self):
        super().__init__()
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

