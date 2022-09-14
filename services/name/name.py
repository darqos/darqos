#! /usr/bin/env python3
# darqos
# Copyright (C) 2022 David Arnold

class NameService(Service):

    def __init__(self):
        pass

    @staticmethod
    def get_name() -> str:
        """Return the service name."""
        return "name"

