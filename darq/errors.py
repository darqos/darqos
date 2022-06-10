# darqos
# Copyright (C) 2022 David Arnold


class ObjectIdentifierIllegalCodepointError(Exception):
    """Identifier string contains an illegal codepoint."""
    pass


class ObjectNotFoundError(Exception):
    """Identified object not found."""
    pass


class LocalPortNumberDoesNotExistError(Exception):
    """Specified port number doesn't exist."""
    pass
