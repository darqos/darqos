#! /usr/bin/env python


# FIXME: it might be good to async and sync versions here?

# In the runtime init call, there could be an optional listener
# provided, which would then be the target for callbacks from
# the runtime.
#
# It should also expose the event loop's run/stop, socket, timer,
# and deferred functions via the 'darq' namespace, I think?  They're
# not really port of the "OS" as such, but they're definitely part
# of the runtime, which is what that namespace really is?
#
# If you choose to do the sync style, then you need to call the
# 'wait_event' function, which is more-or-less 'select' by a
# different name, and will run the event loop, an return when
# something of interest has happened.  Note that this will be
# port-level stuff for ports, not byte level like for sockets.
#
# But .. how will sockets get integrated?  I guess ... the listener
# class will need to deal with those events too?
#
# And, of course, there's Python asyncio to consider here too.  Gah.


import darq
import sys


class ApiTest(darq.EventListener):

    def __init__(self):

        # Initialise the runtime library.
        darq.init_callbacks(darq.SelectEventLoop(), self)

        self.port = 0
        self.result = 0
        return

    def start(self):

        # Request a port.
        darq.open_port(2918)

        # Enter the event loop.
        print("Entering event loop")
        darq.loop().run()
        print("Exited event loop")
        return self.result

    def on_open_port(self, port: int):
        print(f"on_open_port({port})")
        self.port = port

        darq.send_message(2918, 2918, b'ping')

    def on_send_message(self, port: int, request_id: int):
        print(f"on_send_message({port}, {request_id})")
        # Message is sent; wait for it to be delivered back to us.
        pass

    def on_message(self, source: int, destination: int, message: bytes):
        print(f"on_message({source}, {destination}")
        # Received the delivered message.  Good.  Now close the port.
        darq.close_port(2918)

    def on_close_port(self, port: int):
        print(f"on_close_port({port})")
        # Port is closed, so exit event loop.
        darq.loop().stop()

    def on_error(self, request: int, error: int, reason: str):
        print("on_error({request}, {error}, {reason})")
        self.result = error
        return


if __name__ == "__main__":
    app = ApiTest()
    result = app.start()
    sys.exit(result)
