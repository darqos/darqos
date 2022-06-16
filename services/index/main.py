from .base import Service


class IndexService(Service):

    def __init__(self):
        super().__init__()
        return

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "index"

