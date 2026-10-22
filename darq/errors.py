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
