#

from typing import Union

import base64
import orjson
import zmq

# The IPC mechanism used between the runtime library and the service
# instance should really be encapsulated as a class that can be used
# by the APIs, rather than being reimplemented for each service.  But
# for now, just hack it up and we'll factor it out later.


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
        request = {"method": "set",
                   "xid": "xxx",
                   "key": key,
                   "value": base64.b64encode(value).decode()}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        return reply["result"]

    def update(self, key: str, value: Union[bytes, bytearray]):
        request = {"method": "update",
                   "xid": "xxx",
                   "key": key,
                   "value": base64.b64encode(value).decode()}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        return reply["result"]

    def exists(self, key: str) -> bool:
        request = {"method": "exists",
                   "xid": "xxx",
                   "key": key}
        self.socket.send_json(request)

        reply = self.socket.recv_json()
        assert reply['method'] == "exists"
        assert "result" in reply

        return reply['result']

    def get(self, key: str) -> bytearray:
        request = {"method": "get",
                   "xid": "xxx",
                   "key": key}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        print(reply)

        value = base64.b64decode(reply["value"])
        return value

    def delete(self, key: str):
        request = {"method": "delete",
                   "xid": "xxx",
                   "key": key}
        buf = orjson.dumps(request)
        self.socket.send(buf)

        buf = self.socket.recv()
        reply = orjson.loads(buf)
        return reply["result"]


def test():
    s = Storage.api()
    assert not s.exists("foo")
    s.set("foo", b"bar")
    assert s.exists("foo")
    assert s.get("foo") == b"bar"
    s.delete("foo")
    assert not s.exists("foo")


if __name__ == "__main__":
    test()
