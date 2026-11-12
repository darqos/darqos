# darqos
# Copyright (C) 2026 David Arnold

"""A Slack export, as experiments/slack/extract.py writes it: a directory
of JSON, with the workspace's users, its conversations, a file of each
one's messages, and its files; read as it is, or from a zip of it, as an
item holds it.  It knows nothing of the KB."""

import dataclasses
import io
import json
import pathlib
import posixpath
import typing
import zipfile


# Messages that are things happening, rather than things said: they're
# left out for now (see doc/slack-import-tool.rst).
SYSTEM = frozenset({
    "app_conversation_join", "bot_add", "bot_remove", "channel_archive", "channel_convert_to_private", "channel_join",
    "channel_leave", "channel_name", "channel_posting_permissions", "channel_purpose", "channel_topic",
    "channel_unarchive", "group_archive", "group_join", "group_leave", "group_name", "group_purpose", "group_topic",
    "group_unarchive", "joiner_notification_for_inviter", "sh_room_created",
})


def microseconds(ts: str) -> int:
    """Return a Slack timestamp as microseconds since 1970."""
    seconds, _, fraction = ts.partition(".")
    return int(seconds) * 1_000_000 + int(fraction[:6].ljust(6, "0"))


def is_said(message: dict) -> bool:
    return message.get("subtype") not in SYSTEM


@dataclasses.dataclass
class File:
    """A file the export has: its name, its size, and its data, read when
    it's wanted."""

    name: str
    size: int
    read: typing.Callable[[], bytes]


class Directory:
    """An export as a directory, read as it is."""

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)

    def file(self, name: str) -> File | None:
        """Return a file by its path in the export, or None if it isn't
        there, or is outside it."""
        path = (self.path / name).resolve()
        if not path.is_relative_to(self.path.resolve()) or not path.is_file():
            return None
        return File(path.name, path.stat().st_size, path.read_bytes)


class Zipped:
    """An export as a zip of its directory, as an item holds it: its files
    are found under the directory that has users.json, if there's one.

    :raises ValueError: if it isn't a zip."""

    def __init__(self, data: bytes):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError(f"It isn't a zip: {e}") from None
        names = self.zip.namelist()
        users = min((n for n in names if posixpath.basename(n) == "users.json"), key=len, default="users.json")
        self.root = posixpath.dirname(users)

    def file(self, name: str) -> File | None:
        path = posixpath.normpath(posixpath.join(self.root, name))
        if path.startswith("..") or (self.root and not path.startswith(self.root + "/")):
            return None
        try:
            info = self.zip.getinfo(path)
        except KeyError:
            return None
        return File(posixpath.basename(path), info.file_size, lambda: self.zip.read(info))


class Export:
    """What an export holds, from a directory, or a zip of one.

    :raises OSError, ValueError: if it can't be read."""

    def __init__(self, source: str | pathlib.Path | Directory | Zipped):
        self.source = source if isinstance(source, (Directory, Zipped)) else Directory(source)
        self.users: dict[str, dict] = {u["id"]: u for u in self.read("users.json")}
        self.conversations: list[dict] = self.read("conversations.json")
        self.files: dict[str, dict] = {f["id"]: f for f in self.read("files.json")}

    def read(self, name: str) -> typing.Any:
        found = self.source.file(name)
        if found is None:
            raise ValueError(f"The export has no {name}")
        return json.loads(found.read().decode("utf-8"))

    @property
    def team_id(self) -> str:
        """Return the workspace's identifier."""
        return next((u["team_id"] for u in self.users.values() if u.get("team_id")), "")

    def messages(self, conversation: str) -> list[dict]:
        """Return what was said in a conversation, oldest first, with
        replies having their parents' times in thread_parent.

        A reply also sent to the channel is listed in both, and is one
        message, so it's kept as a reply.  A conversation Slack wouldn't
        give, which has no file, is empty."""
        if self.source.file(f"messages/{conversation}.json") is None:
            return []
        said: dict[str, dict] = {}
        for message in self.read(f"messages/{conversation}.json"):
            if is_said(message):
                said.setdefault(message["ts"], message)
            for reply in message.get("thread_replies", ()):
                if is_said(reply) and reply["ts"] != message["ts"]:
                    reply["thread_parent"] = message["ts"]
                    said[reply["ts"]] = reply
        return sorted(said.values(), key=lambda m: microseconds(m["ts"]))

    def file(self, record: dict) -> File | None:
        """Return a file's data, or None if it wasn't downloaded."""
        local = record.get("local_path")
        return self.source.file(local) if local else None
