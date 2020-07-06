#

import orjson
import zmq


class Service:
    """Service base class."""

    def __init__(self, url: str):
        """Constructor."""

        # Endpoint URL.
        self.url: str = url

        # zmq context.
        self.context = None

        # zmq listening socket.
        self.socket = None

        # Active flag: if False, exist service loop and shut down.
        self.active: bool = False
        return

    def run(self):
        """Accept and dispatch service requests."""

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(self.url)
        self.active = True

        # Receive and dispatch.
        while self.active:
            buf = self.socket.recv()
            message = orjson.loads(buf)
            self.handle_request(message)

        # Clean up.
        self.socket.close()
        self.socket = None

        self.context.destroy()
        self.context = None
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
        self.socket.send(buf)
        return


class TypeDefinition:
    """Description of an object type."""

    def __init__(self):
        self.uti = 'public.base'
        self.name = ''
        self.description = ''
        self.implementation = ''
        return

    def get_uti(self) -> str:
        """Return the unique Uniform Type Identifier (UTI) for this type."""
        return self.uti

    def get_name(self) -> str:
        """Return the unique short name for this type."""
        return self.name

    def get_description(self) -> str:
        """Return a short description of the produced object type."""
        return self.description

    def get_implementation(self) -> str:
        """Return the storage URL for the type implementation."""
        return self.implementation

    @staticmethod
    def from_dict(d):
        """Create a type description from a dictionary."""
        td = TypeDefinition()
        td.uti = d.get("uti")
        td.name = d.get("name")
        td.description = d.get("description")
        td.implementation = d.get("implementation")
        return td

