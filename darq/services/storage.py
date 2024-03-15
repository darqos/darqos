# darqos
# Copyright (C) 2022 David Arnold

from typing import Union

import darq
from darq.runtime.service import ServiceAPI

import base64

# The IPC mechanism used between the runtime library and the service
# instance should really be encapsulated as a class that can be used
# by the APIs, rather than being reimplemented for each service.  But
# for now, just hack it up and we'll factor it out later.


class StorageAPI(ServiceAPI):
    """Interface to storage system."""

    def __init__(self):
        """Constructor."""
        super().__init__(11001)
        return

    def set(self, key: str, value: Union[bytes, bytearray]):
        request = {"method": "set",
                   "xid": "xxx",
                   "key": key,
                   "value": base64.b64encode(value).decode()}
        reply = self.rpc(request)
        return reply["result"]

    def update(self, key: str, value: Union[bytes, bytearray]):
        request = {"method": "update",
                   "xid": "xxx",
                   "key": key,
                   "value": base64.b64encode(value).decode()}
        reply = self.rpc(request)
        return reply["result"]

    def exists(self, key: str) -> bool:
        request = {"method": "exists",
                   "xid": "xxx",
                   "key": key}
        reply = self.rpc(request)
        assert reply['method'] == "exists"
        assert "result" in reply

        return reply['result']

    def get(self, key: str) -> bytes:
        request = {"method": "get",
                   "xid": "xxx",
                   "key": key}
        reply = self.rpc(request)
        print(reply)

        value = base64.b64decode(reply["value"])
        return value

    def delete(self, key: str):
        request = {"method": "delete",
                   "xid": "xxx",
                   "key": key}
        reply = self.rpc(request)
        return reply["result"]


def api():
    """Return a client API for the Storage service."""
    return StorageAPI()


def test():
    s = StorageAPI.api()
    assert not s.exists("foo")
    s.set("foo", b"bar")
    assert s.exists("foo")
    assert s.get("foo") == b"bar"
    s.delete("foo")
    assert not s.exists("foo")


if __name__ == "__main__":
    test()
