# darqos
# Copyright (C) 2026 David Arnold

"""A Slack export, as experiments/slack/extract.py writes it: a directory
of JSON, with the workspace's users, its conversations, a file of each
one's messages, and its files.  It knows nothing of the KB."""

import json
import pathlib
import typing


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


class Export:
    """What an export holds.

    :raises OSError, ValueError: if it can't be read."""

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self.users: dict[str, dict] = {u["id"]: u for u in self.read("users.json")}
        self.conversations: list[dict] = self.read("conversations.json")
        self.files: dict[str, dict] = {f["id"]: f for f in self.read("files.json")}

    def read(self, name: str) -> typing.Any:
        with open(self.path / name, encoding="utf-8") as f:
            return json.load(f)

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
        path = self.path / "messages" / f"{conversation}.json"
        if not path.exists():
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

    def file_path(self, record: dict) -> pathlib.Path | None:
        """Return where a file's data is, or None if it wasn't downloaded."""
        local = record.get("local_path")
        if not local:
            return None
        path = (self.path / local).resolve()
        return path if path.is_relative_to(self.path.resolve()) and path.is_file() else None
