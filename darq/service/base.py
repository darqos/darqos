#


class Service:

    def __init__(self):
        """Constructor."""
        return


class StorageService(Service):
    """A simple persistent key:value store.

    All system storage (aside from early bootstrap) should use this service.

    Large values should probably be memory-mapped and paged into working
    set as required, rather than being completely loaded all at once."""

    def __init__(self):
        super().__init__()
        return


class MetadataService(Service):

    def __init__(self):
        super().__init__()
        return


class IndexService(Service):

    def __init__(self):
        super().__init__()
        return


class