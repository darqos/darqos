#! /usr/bin/env python3

from darq.os.base import TypeDefinition

import zmq

DEFAULT_URL = "tcp://localhost:11002"


class TypeServiceAPI:
    """Interface to the object / type system.

    The goal of this service is to spawn "application" processes, viewing
    or editing strongly typed objects.

    This service maintains a persistent collection of type definitions.
    Using this data, it is able to spawn a new type instance.

    https://en.wikipedia.org/wiki/Uniform_Type_Identifier

    """

    def __init__(self, url: str = None):
        """Constructor.

        :param url: Optional service locator."""

        self.url = url if url is not None else DEFAULT_URL

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(self.url)
        return

    def register(self, factory: str):
        """Register the factory implementation for a type.

        :param factory: Storage URL for factory implementation.

        The factory is queried to obtain the type identifier,
        description, icon, and other properties."""
        pass

    def deregister(self, typename:str):
        """Deregister the factory implementation for a type."""
        pass

    def create(self, typename: str):
        """Create a new instance of a type."""
        pass

    def open(self, object_id: str):
        """Open an existing instance of a type."""
        pass

