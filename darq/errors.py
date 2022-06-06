# darqos
# Copyright (C) 2022 David Arnold


class ObjectIdentifierIllegalCodepointError(Exception):
    """Identifier string contains an illegal codepoint."""
    pass


class ObjectNotFoundError(Exception):
    """Identified object not found."""
    pass

