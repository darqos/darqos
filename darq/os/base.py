#

import orjson
import os
import zmq


class Service:
    """Service base class."""

    def __init__(self, url: str):
        """Constructor."""

        # Endpoint URL.
        self.url: str = url

        # zmq context.
        self._context = None

        # zmq listening socket.
        self._socket = None

        # Active flag: if False, exist service loop and shut down.
        self.active: bool = False

        # Write PID to run file.
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        f = open(os.path.join(tmpdir, f"{self.get_name()}.pid"), "w")
        f.write(f"{os.getpid()}\n")
        f.close()
        return

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "service"

    def run(self):
        """Accept and dispatch service requests."""

        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        self._socket.bind(self.url)
        self.active = True

        # Receive and dispatch.
        while self.active:
            buf = self._socket.recv()
            message = orjson.loads(buf)
            self.handle_request(message)

        # Clean up.
        self._socket.close()
        self._socket = None

        self._context.destroy()
        self._context = None
        return

    def handle_request(self, request: dict):
        """Dispatch the requested method.

        :param request: Decoded request as dictionary."""

        method = request.get("method")
        if method == "shutdown":
            self.send_reply(request, result=True)
            self.active = False
        else:
            self.send_reply(request,
                            result=False,
                            description=f"Unknown method requested {method}")
        return

    def send_reply(self, request: dict, reply: dict = None, **kwargs):
        """Construct and send standard reply message.

        :param request: Request dictionary (for method and xid values)
        :param reply: Optional reply dictionary
        :param kwargs: Optional keyword parameters, added to reply

        Note that either or both of the reply dictionary and keyword
        arguments may be supplied.  If both, they'll be merged, and
        keyword arguments will override the dictionary."""

        if reply is None:
            reply = {}
        reply.update(kwargs)

        reply["method"] = request["method"]
        reply["xid"] = request["xid"]
        buf = orjson.dumps(reply)
        self._socket.send(buf)
        return


class ServiceAPI:
    """Base class for runtime service APIs."""

    def __init__(self, sid: str):
        self._sid = sid
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REQ)
        self._socket.connect(self._sid)

    def rpc(self, request: dict) -> dict:
        """Send a server request, and await a reply."""
        buf = orjson.dumps(request)
        self._socket.send(buf)

        bug = self._socket.recv()
        response = orjson.loads(buf)
        return response
