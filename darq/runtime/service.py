# DarqOS
# Copyright (C) 2022 David Arnold


import orjson
import os

from darq.os.ipc import EventLoopInterface, PortListener, open_port, close_port, send_message


class Service(PortListener):
    """Service base class."""

    def __init__(self, loop: EventLoopInterface, port: int = 0):
        """Constructor."""

        # Save event loop access handle.
        self.loop: EventLoopInterface = loop

        # Service port.
        self.port: int = port

        # Active flag: if False, exist service loop and shut down.
        self.active: bool = True

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

        # Register event loop.
        # FIXME

        # Open service port.
        open_port(self, self.port)

        # Receive and dispatch.
        while self.active:
            try:
                self.loop.run()

            except KeyboardInterrupt:
                print("Ignore C-c")

        # Clean up.
        close_port(self.port)
        return

    def handle_request(self, reply_port: int, request: dict):
        """Dispatch the requested method.

        :param reply_port: Port number for reply.
        :param request: Decoded request as dictionary."""

        method = request.get("method")
        if method == "shutdown":
            self.send_reply(request, result=True)
            self.active = False
        else:
            self.send_reply(reply_port,
                            request,
                            result=False,
                            description=f"Unknown method requested {method}")
        return

    def handle_shutdown(self):
        """Override this method to handle shutdown requests."""
        self.active = False
        return

    def send_reply(self, port: int, request: dict, reply: dict = None, **kwargs):
        """Construct and send standard reply message.

        :param port: Reply port
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

        send_message(self.port, port, buf)
        return

    def on_message(self, source: int, destination: int, buffer: bytes):
        """Handle a delivered message."""

        if destination == self.port:
            message = orjson.loads(buffer)
            self.handle_request(source, message)

        else:
            # FIXME: deal with unhandled dest port
            pass
        pass

    def on_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
        """Handle a delivered stream chunk."""

        # Streaming not used for base service.
        pass

    def on_error(self, port: int, error: int, reason: str):
        """Handle a reported communications error."""
        # FIXME
        pass


class ServiceAPI(PortListener):
    """Base class for runtime service APIs."""

    def __init__(self, port: int):
        self._service_port = port
        self._port = open_port(self)

    def rpc(self, request: dict) -> dict:
        """Send a server request, and await a reply."""
        buf = orjson.dumps(request)
        send_message(self._port, self._service_port, buf)

        buf = self._socket.recv()
        response = orjson.loads(buf)
        return response

    def on_message(self, source: int, destination: int, buffer: bytes):
        """Handle a delivered message."""

        if destination == self._port:
            message = orjson.loads(buffer)
            # FIXME: interrupt event loop
            self.handle_request(source, message)

        else:
            # FIXME: deal with unhandled dest port
            pass
        pass

    def on_chunk(self, source: int, destination: int, stream: int, offset: int, chunk: bytes):
        """Handle a delivered stream chunk."""

        # Streaming not used for base service.
        pass

    def on_error(self, port: int, error: int, reason: str):
        """Handle a reported communications error."""
        # FIXME
        pass
