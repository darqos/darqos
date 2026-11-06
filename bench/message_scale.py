# darqos
# Copyright (C) 2026 David Arnold
"""Measures one Knowledge Base entity per message, on real messages.

Reads a Slack export, as experiments/slack/extract.py writes it, and
makes the entities doc/message-type.rst designs: a conversation for each
of its conversations, a sender for each person or bot, and a message
for each message and thread reply, leaving out system messages.  It runs
the service's own code, KnowledgeBase and Store from services/kb, in
this process, as bench/kb_scale.py does.

    venv313/bin/python bench/message_scale.py \\
        --export experiments/slack/data --dir /var/tmp/message-scale

Each shape is tried in a database of its own:

    full            Every statement has a reference, stated in the
                    workspace, as a source's statements do now.
    entity-source   One stated-in statement on each message, instead
                    of a reference on each statement.
    lean            As entity-source, without instance of: the DarqOS
                    type alone records the kind.

It reports each one's size, by table, and the speed of writing it and of
reading it as a lens would.  Results go to message-scale.csv in the
directory, and to standard output: they're only numbers, so nothing of
the messages' content leaves the database, which it leaves in the
directory, readable only by its owner.
"""

import argparse
import csv
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import time
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import kb_scale  # noqa: E402

import darq.kb as model  # noqa: E402
import darq.message  # noqa: E402
from darq.kb import (Datatype, EntityValue, Item, Reference, Statement, StringValue, TextValue,  # noqa: E402
                     TimeValue)

kb_module = kb_scale.kb_module
store_module = kb_scale.store_module

# Messages that are things happening, rather than things said: they're
# left out for now (see doc/slack-import-tool.rst).
SYSTEM = {"app_conversation_join", "bot_add", "bot_remove", "channel_archive", "channel_join", "channel_leave",
          "channel_name", "channel_purpose", "channel_topic", "channel_unarchive", "group_join", "group_leave",
          "group_name", "group_purpose", "group_topic", "joiner_notification_for_inviter", "sh_room_created"}

# Language of a body: a service doesn't say.
UNDETERMINED = "und"

SHAPES = ("full", "entity-source", "lean")

# Messages a lens reads at a time, and identifiers in a batch of a walk.
PAGE = 50
BATCH = 1000

# Entities written in a transaction.
TRANSACTION = 200


def microseconds(ts: str) -> int:
    """Return a Slack timestamp as microseconds since 1970."""
    seconds, _, fraction = ts.partition(".")
    return int(seconds) * 1_000_000 + int(fraction[:6].ljust(6, "0"))


def message_id(conversation: str, ts: str) -> str:
    return darq.message.message_id(f"slack:{conversation}:{ts}", microseconds(ts))


def message_time(ts: str) -> TimeValue:
    return darq.message.time_value(microseconds(ts))


def body_of(message: dict) -> str:
    """Return a message's text, with its attachments', since a bot's
    often has only those.  It isn't converted, which takes about the same
    room."""
    parts = [message.get("text") or ""]
    for attachment in message.get("attachments", ()):
        parts.append(attachment.get("text") or attachment.get("fallback") or "")
    return "\n".join(part for part in parts if part)


def is_said(message: dict) -> bool:
    return message.get("subtype") not in SYSTEM


def read_conversation(path: pathlib.Path) -> Iterator[dict]:
    """Yield a conversation's messages and replies, with their parents'
    times, in thread_parent.  A reply also sent to the channel is listed
    in both, and is one message: it's kept as a reply."""
    messages: Dict[str, dict] = {}
    for message in json.loads(path.read_text()):
        if is_said(message):
            messages.setdefault(message["ts"], message)
        for reply in message.get("thread_replies", ()):
            if is_said(reply):
                reply["thread_parent"] = message["ts"]
                messages[reply["ts"]] = reply
    yield from sorted(messages.values(), key=lambda m: (float(m["ts"]), "thread_parent" in m))


class Vocabulary:
    """The properties and classes a message needs."""

    def __init__(self, kb):
        def p(label: str, datatype: Datatype, prop_id: Optional[str] = None) -> str:
            return kb.add_property(label, datatype, prop_id).id

        self.instance_of = p("instance of", Datatype.Item, model.INSTANCE_OF)
        self.darqos_type = p("DarqOS type", Datatype.String, model.DARQOS_TYPE)
        self.stated_in = p("stated in", Datatype.Item, model.STATED_IN)
        self.sender = p("sender", Datatype.Item)
        self.conversation = p("conversation", Datatype.Item)
        self.time = p("point in time", Datatype.Time, model.POINT_IN_TIME)
        self.body = p("body", Datatype.Text)
        self.reply_to = p("in response to", Datatype.Item)
        self.mentions = p("mentions", Datatype.Item)
        self.reaction = p("reaction", Datatype.String)
        self.reacted_by = p("reacted by", Datatype.Item)
        self.edited = p("last edited", Datatype.Time)
        self.attachment = p("attachment", Datatype.String)
        self.file_name = p("file name", Datatype.String)
        self.media_type = p("media type", Datatype.String)
        self.message_class = kb.add_item("instant message").id
        self.conversation_class = kb.add_item("conversation").id
        self.workspace = kb.add_item("A Slack workspace").id


class Build:
    """Makes a shape's entities from an export."""

    def __init__(self, kb, shape: str, senders: Dict[str, str]):
        self.kb = kb
        self.shape = shape
        self.v = Vocabulary(kb)
        self.senders = senders
        self.people: Dict[str, str] = {}
        self.conversations: Dict[str, str] = {}

    def state(self, item: Item, property_id: str, value) -> Statement:
        statement = item.claim(property_id, value)
        if self.shape == "full":
            statement.add_reference(Reference().add(self.v.stated_in, EntityValue(self.v.workspace)))
        return statement

    def sender(self, key: str) -> str:
        """Return the entity for a person or bot, by their identifier."""
        if key not in self.people:
            self.people[key] = self.kb.add_item(key).id
        return self.people[key]

    def conversation(self, key: str) -> str:
        if key not in self.conversations:
            item = self.kb.add_item(key)
            item.claim(self.v.instance_of, EntityValue(self.v.conversation_class))
            self.conversations[key] = item.id
        return self.conversations[key]

    def make(self, conversation: str, message: dict) -> Item:
        v = self.v
        item = Item(message_id(conversation, message["ts"]))
        if self.shape != "lean":
            self.state(item, v.instance_of, EntityValue(v.message_class))
        self.state(item, v.darqos_type, StringValue("darq.message"))
        if self.shape != "full":
            item.claim(v.stated_in, EntityValue(v.workspace))

        key = message.get("user") or message.get("bot_id")
        if key:
            self.state(item, v.sender, EntityValue(self.sender(key)))
        self.state(item, v.conversation, EntityValue(self.conversation(conversation)))
        self.state(item, v.time, message_time(message["ts"]))
        body = body_of(message)
        if body:
            self.state(item, v.body, TextValue(body, UNDETERMINED))
        parent = message.get("thread_parent")
        if parent:
            self.state(item, v.reply_to, EntityValue(message_id(conversation, parent)))
        edited = message.get("edited")
        if edited:
            self.state(item, v.edited, message_time(edited["ts"]))
        for block in message.get("blocks", ()):
            for user in mentioned(block):
                self.state(item, v.mentions, EntityValue(self.sender(user)))
        for reaction in message.get("reactions", ()):
            statement = self.state(item, v.reaction, StringValue(reaction["name"]))
            for user in reaction.get("users", ()):
                statement.add_qualifier(v.reacted_by, EntityValue(self.sender(user)))
        for attached in message.get("files", ()):
            statement = self.state(item, v.attachment, StringValue(f"com.adobe.pdf:{attached['id'].lower():0>32}"))
            statement.add_qualifier(v.file_name, StringValue(attached.get("name") or "file"))
            statement.add_qualifier(v.media_type, StringValue(attached.get("mimetype") or "application/octet-stream"))
        return item


def mentioned(value) -> Iterator[str]:
    """Yield the users mentioned in a block."""
    if isinstance(value, dict):
        if value.get("type") == "user" and "user_id" in value:
            yield value["user_id"]
        for inner in value.values():
            yield from mentioned(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from mentioned(inner)


def time_key(entity, property_id: str) -> Tuple[int, ...]:
    """Return an entity's time, to sort by."""
    t = entity.get_statements(property_id)[0].get_value()
    return (t.year, t.month, t.day, t.hour, t.minute, t.second, t.nanosecond)


def index_size(directory: pathlib.Path, export: pathlib.Path) -> Tuple[int, int]:
    """Return the size of the Index's table, with every body in it, and
    the number of bodies: its cost is the same for every shape."""
    path = directory / "index.sqlite"
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("create virtual table entries using fts5 (names, body, tokenize = 'unicode61 remove_diacritics 2')")
    count = 0
    for conversation in sorted((export / "messages").glob("*.json")):
        for message in read_conversation(conversation):
            db.execute("insert into entries (names, body) values ('', ?)", (body_of(message),))
            count += 1
    db.commit()
    db.execute("insert into entries (entries) values ('optimize')")
    db.commit()
    db.execute("vacuum")
    db.close()
    return path.stat().st_size, count


class Run:
    """One shape, in its own database."""

    def __init__(self, directory: pathlib.Path, shape: str, results: "csv._writer"):  # pyright: ignore
        self.shape = shape
        self.results = results
        self.path = str(directory / f"{shape}.sqlite")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass
        self.store = store_module.Store(self.path)
        self.kb = kb_module.KnowledgeBase(self.store)
        with self.kb.transaction():
            self.build = Build(self.kb, shape, {})
        self.count = 0
        self.text = 0

    def record(self, metric: str, times: Sequence[float] = (), value: object = ""):
        row: List[object] = [self.shape, metric, len(times)]
        shown = ""
        if times:
            ms = sorted(t * 1000 for t in times)
            p50 = statistics.median(ms)
            p99 = ms[min(len(ms) - 1, int(len(ms) * 0.99))]
            row += [f"{p50:.3f}", f"{p99:.3f}"]
            shown = f"p50 {p50:9.3f} ms  p99 {p99:9.3f} ms"
        else:
            row += ["", ""]
        row.append(value)
        if value != "":
            shown += f"  {value}"
        self.results.writerow(row)
        print(f"{self.shape:<14} {metric:<32} {shown}", flush=True)

    def write(self, export: pathlib.Path):
        """Write every message, in batches, checking each statement as
        the service does, and forgetting them, as a bounded cache would."""
        started = time.perf_counter()
        times: List[float] = []
        for path in sorted((export / "messages").glob("*.json")):
            messages = list(read_conversation(path))
            for start in range(0, len(messages), TRANSACTION):
                begun = time.perf_counter()
                made = []
                for message in messages[start:start + TRANSACTION]:
                    entity = self.build.make(path.stem, message)
                    self.kb.register(entity)
                    for statement in entity.statements:
                        self.kb.check(statement)
                    made.append(entity)
                    self.count += 1
                    self.text += len(body_of(message).encode())
                self.kb.commit()
                times.append(time.perf_counter() - begun)
                for entity in made:
                    self.kb._evict(entity)
        elapsed = time.perf_counter() - started
        self.record("messages", value=self.count)
        self.record("text bytes", value=self.text)
        self.record("write batch", times)
        self.record("writes a second", value=f"{self.count / elapsed:.0f}")

    def size(self):
        db = self.store.db
        self.store.optimize()
        db.execute("pragma wal_checkpoint(truncate)")
        total = os.path.getsize(self.path)
        self.record("database bytes", value=total)
        self.record("bytes a message", value=total // max(self.count, 1))
        self.record("times the text", value=f"{total / max(self.text, 1):.0f}")
        rows = db.execute("select name, sum(pgsize) from dbstat group by name order by 2 desc").fetchall()
        for name, size in rows[:14]:
            self.record(f"size: {name}", value=size)
        documents = db.execute("select sum(length(document)) from entities").fetchone()[0]
        self.record("document bytes a message", value=(documents or 0) // max(self.count, 1))

    def largest(self) -> str:
        """Return the conversation with the most messages."""
        v = self.build.v
        return max(self.build.conversations.values(),
                   key=lambda c: len(self.store.find_links(c, v.conversation, "main")))

    def page(self, conversation: str, after: str) -> Tuple[List[str], list]:
        """Read a page of a conversation, from the store, as a lens would
        through the service, without its cache."""
        ids = self.store.find_links(conversation, self.build.v.conversation, "main", after, PAGE)
        return ids, [self.store.get(i) for i in ids]

    def timed(self, metric: str, run: Callable[[], object], n: int = 200):
        times = []
        for _ in range(n):
            begun = time.perf_counter()
            run()
            times.append(time.perf_counter() - begun)
        self.record(metric, times)

    def read(self):
        """Read it, as the lens and a Person's page would."""
        self.store.close()
        self.store = store_module.Store(self.path)
        self.kb.store = self.store
        v = self.build.v
        conversation = self.largest()
        ids = self.store.find_links(conversation, v.conversation, "main")
        self.record("largest conversation", value=len(ids))
        moments = [time_key(self.store.get(i), v.time) for i in ids[:5000]]
        self.record("ids in time order", value=moments == sorted(moments))

        positions = iter(range(0, len(ids), max(1, len(ids) // 200)))
        offsets = {"next": 0}

        def page():
            at = next(positions, None)
            offsets["next"] = 0 if at is None else at
            self.page(conversation, ids[offsets["next"]])

        self.timed(f"read a page of {PAGE}", page, n=100)

        begun = time.perf_counter()
        after, seen = "", 0
        while True:
            batch = self.store.find_links(conversation, v.conversation, "main", after, BATCH)
            if not batch:
                break
            seen += len(batch)
            after = batch[-1]
        self.record("walk the ids", [time.perf_counter() - begun], value=seen)

        self.timed(f"newest {PAGE} of a conversation",
                   lambda: self.store.find_links(conversation, v.conversation, "main", "", PAGE, reverse=True))

        senders = list(self.build.people.values())
        most = max(senders, key=lambda s: len(self.store.find_links(s, v.sender, "main")))
        self.record("most from a sender", value=len(self.store.find_links(most, v.sender, "main")))
        self.timed("first 200 from a sender", lambda: self.store.find_links(most, v.sender, "main", "", 200))
        self.timed("a sender's groups", lambda: self.store.link_counts(most))
        self.timed("newest 200 of a sender's messages",
                   lambda: self.store.find_links(most, v.sender, "main", "", 200, True, "darq.message"))
        parents = [i for i in ids[:2000]]
        replies = {"at": 0}

        def thread():
            replies["at"] = (replies["at"] + 1) % len(parents)
            self.store.find_links(parents[replies["at"]], v.reply_to, "main")

        self.timed("replies to a message", thread)

    def close(self):
        self.store.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=pathlib.Path, required=True, help="the export's directory")
    parser.add_argument("--dir", type=pathlib.Path, required=True, help="directory for databases and results")
    parser.add_argument("--shapes", default=",".join(SHAPES), help="the shapes to try")
    arguments = parser.parse_args(argv)

    arguments.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(arguments.dir / "message-scale.csv", "w", newline="") as f:
        results = csv.writer(f)
        results.writerow(["shape", "metric", "n", "p50_ms", "p99_ms", "value"])
        for shape in arguments.shapes.split(","):
            run = Run(arguments.dir, shape, results)
            run.write(arguments.export)
            run.size()
            run.read()
            run.close()
        size, count = index_size(arguments.dir, arguments.export)
        results.writerow(["index", "bytes", count, "", "", size])
        print(f"index          bytes for {count} bodies: {size}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
