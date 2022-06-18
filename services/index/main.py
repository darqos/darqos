# DarqOS
# Copyright (C) 2022 David Arnold


import darq


class IndexService(darq.Service):

    def __init__(self):
        super().__init__()
        return

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "index"

