#! /usr/bin/env python
# Copyright (C) 2021 David Arnold

# Security Service

from darq.os.base import Service

import base64
import sqlite3
import sys


class SecurityService(Service):
    """A simple security service."""

    def __init__(self, file: str = None):
        super().__init__("tcp://*:11003")

        if file is None:
            file = "security.sqlite"

        self.db = sqlite3.connect(file)
        cursor = self.db.cursor()
        cursor.execute("create table if not exists users ("
                       "name text not null primary key, "
                       "password text not null)")
        cursor.execute("create table if not exists groups ("
                       "name text not null primary key)")
        cursor.execute("create table if not exists memberships ("
                       "id int not null primary key, "
                       "username text not null, "
                       "groupname text not null, "
                       "FOREIGN KEY(username) REFERENCES users (name), "
                       "FOREIGN KEY(groupname) REFERENCES groups (name))")
        self.db.commit()

        self.context = None
        self.socket = None
        self.active = False

        return

    def add_user(self, user_name: str, password: str) -> bool:
        pass

    def update_password(self, user_name: str, old_password: str, new_password: str) -> bool:
        pass

    def add_group(self, group_name: str) -> bool:
        pass

    def add_user_to_group(self, user_name: str, group_name: str) -> bool:
        pass

    def remove_user_from_group(self, user_name: str, group_name: str) -> bool:
        pass

    def login(self, user_name: str, password: str) -> bytes:
        pass


    def handle_request(self, request: dict):
        """Handle requests."""

        method = request.get("method")
        if method == "add_user":
            self.set(request["key"], base64.b64decode(request["value"]))
            self.send_reply(request, result=True)
            return

        elif method == "add_group":
            self.update(request["key"], base64.b64decode(request["value"]))
            self.send_reply(request, result=True)
            return

        elif method == "update_password":
            rpc_result = self.exists(request["key"])
            self.send_reply(request, result=rpc_result)
            return

        elif method == "add_user_to_group":
            value = self.get(request["key"])
            self.send_reply(
                request,
                result=value is not None,
                value=base64.b64encode(value).decode() if value else '')
            return

        elif method == "remove_user_from_group":
            return

        elif method == "login":
            result = self.login(request["username"], request["password"])
            # FIXME: what's the type of the return value?
            return

        elif method == "delete":
            self.delete(request["key"])
            self.send_reply(request, result=True)
            return

        else:
            super().handle_request(request)
        return


if __name__ == "__main__":
    service = SecurityService()
    result = service.run()
    sys.exit(result)
