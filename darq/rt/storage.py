#

from typing import Union

import struct
import zmq


class Storage:
    """Interface to storage system."""

    @staticmethod
    def api() -> "Storage":
        return Storage()

    def __init__(self):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect("tcp://localhost:11001")
        return

    def set(self, key: str, value: Union[bytes, bytearray]):
        tag_len = struct.pack("<H", len("set"))
        key_len = struct.pack("<L", len(key.encode()))
        value_len = struct.pack("<L", len(value))

        request = tag_len + b"set" + key_len + key.encode() + value_len + value
        self.socket.send(request)

        reply = self.socket.recv()
        return

    def update(self, key: str, value: Union[bytes, bytearray]):
        pass

    def exists(self, key: str) -> bool:
        tag_len = struct.pack("<H", len("exists"))
        key_len = struct.pack("<L", len(key.encode()))

        request = tag_len + b"exists" + key_len + key.encode()
        self.socket.send(request)

        reply = self.socket.recv()

    pass

    def get(self, key: str) -> bytearray:
        pass

    def delete(self, key: str):
        pass


#
def test():
    s = Storage.api()
    assert not s.exists("foo")
    s.set("foo", b"bar")
    assert s.exists("foo")
    assert s.get("foo") == b"bar"
    s.delete("foo")
    assert not s.exists("foo")


test()
