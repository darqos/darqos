# darqos
# Copyright (C) 2022-2026 David Arnold


class DarqError(Exception):
    """Base class for all DarqOS Python Runtime exceptions."""
    pass


class MessageDecodingError(DarqError):
    """Failed to decode a message from the supplied buffer."""
    pass


class CannotAllocatePortError(DarqError):
    """The ephemeral port range is exhausted."""
    pass


class DuplicatePortError(DarqError):
    """The requested port number is already in use."""
    pass


class NonExistentPortError(DarqError):
    """The specified port does not exist."""
    pass


class PortNumberOutOfRange(DarqError):
    """The specified port number is out of range."""
    pass


class PortRightError(DarqError):
    """The handle doesn't hold the right needed: a receive right, to send from."""
    pass


class DeadPortError(DarqError):
    """The port has been destroyed."""
    pass


class TooManyHandlesError(DarqError):
    """A message can't transfer that many handles (see MAX_HANDLES)."""
    pass


class RemoteError(DarqError):
    """A service failed to handle a request: it sent an error reply.

    :ivar code: The reply's error code (see darq.dipc.ErrorCode).
    :ivar description: The service's description of the failure."""

    def __init__(self, code: int, description: str):
        super().__init__(f"error {code}: {description}")
        self.code = code
        self.description = description


class NoSuchChildError(DarqError):
    """The process isn't a child of the caller, or the caller has no children."""
    pass


class NotPermittedError(DarqError):
    """Only processes the p-kernel spawned (parent dpid 0) may do that."""
    pass


class ValueTooLargeError(DarqError):
    """The value is too long."""
    pass


class BootVectorFullError(DarqError):
    """The boot vector has no room for another name."""
    pass


class InvalidNameError(DarqError):
    """The name is empty, too long, or not valid UTF-8."""
    pass


class NameTakenError(DarqError):
    """Another port is already registered with the name."""
    pass


class ReservedNameError(DarqError):
    """The name is reserved for system services."""
    pass


class PortAlreadyNamedError(DarqError):
    """The port is already registered with another name."""
    pass


# FIXME
EXCEPTION_MAP: dict[int, DarqError] = {}

def get_exception(error_code:int) -> DarqError:
    return EXCEPTION_MAP.get(error_code, DarqError())



class ObjectIdentifierIllegalCodepointError(Exception):
    """Identifier string contains an illegal codepoint."""
    pass


class ObjectNotFoundError(Exception):
    """Identified object not found."""
    pass


class LocalPortNumberDoesNotExistError(Exception):
    """Specified port number doesn't exist."""
    pass
