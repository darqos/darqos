# darqos
# Copyright (C) 2026 David Arnold

"""Importing a Slack export (see slack_export.py) into the KB.

It's a client of the Knowledge Base service, which it reads, and writes
the vocabulary to; and of the KB type implementation, through which
items are written (see doc/types.rst):

1. The vocabulary, and the workspace's item, which every statement here
   is stated in, are found or created.
2. Every conversation's read, for who's in it.
3. Each person's found: by their member identifier, from an earlier
   import; or by their name, which is logged, to be checked; or else
   they're created.  Bots are entities of their own, not people.
4. Each conversation's found or created, with its participants.
5. Each file's made an item, of its type, if it has one with an
   implementation, and isn't one already: otherwise it's just recorded,
   by its name, type and size, on its messages.
6. Each message is created, or restated if it exists, in batches.

A message's identifier comes from what it is (see darq.message), so
importing again finds the same ones, and changes nothing, unless a file
that wasn't an item now can be.  A batch that fails is logged, and the
import goes on.  Nothing of a message's content is logged."""

import logging
import typing
import uuid

import darq
import darq.history
import darq.kb as model
import darq.message
from darq.protocols.kb import EntityItem
from darq.protocols.type import Implementation
from darq.protocols.type import Status as TypeStatus

import slack_export
import slack_text


log = logging.getLogger("slack-import")

# History source for its events.
SOURCE = "slack-import"

# Entities read and written in each request.
BATCH = 200

# Bytes in the largest file made an item: they're sent whole, in a
# message, until Storage has ranged access (see doc/storage.rst).
MAX_FILE = 64 * 1024 * 1024

# The type of a message, and a conversation.
MESSAGE_TYPE = "darq.message"
CONVERSATION_TYPE = "darq.conversation"

# Human.
HUMAN = "Q5"

# Slack, and the classes of its workspaces, and of a bot: software agent.
SLACK = "Q17130715"
WORKSPACE_CLASS = "Q110008405"
SOFTWARE_AGENT = "Q2297769"

# Someone's account: website account on, and their identifier there, as
# its qualifier.
ACCOUNT = "P553"
MEMBER_ID = "P554"

# Which of Slack's files an attachment is (a qualifier): so an import
# can find the item it made.
FILE_ID = "7ea0abf7-4c37-4ced-b0a7-63290a7a9028"

# Slackbot, who's a member like any other, as Slack sees it.
SLACKBOT = "USLACKBOT"


class Files(typing.Protocol):
    """Makes items from files' data: darq.launch, or a fake in tests."""

    def create(self, media_type: str, extension: str, data: bytes) -> str | None:
        """Create an item, and return its identifier, or None if there's
        no type with an implementation that takes the data."""
        ...


class History(typing.Protocol):
    def add_event(self, item_id: str, event: str, source: str) -> typing.Any: ...


class Result:
    """What an import did."""

    def __init__(self):
        self.created = 0
        self.unchanged = 0
        self.updated = 0
        self.failed = 0
        self.conversations = 0
        self.empty = 0
        self.people = 0
        self.bots = 0
        self.files = 0
        self.files_recorded = 0

        # People found by name, to be checked: their name, and the item.
        self.matches: list[tuple[str, str]] = []

    def lines(self) -> list[str]:
        return [f"{self.created} messages created, {self.updated} updated, {self.unchanged} unchanged, "
                f"{self.failed} failed",
                f"{self.conversations} conversations, and {self.empty} left out with no messages",
                f"{self.people} people ({len(self.matches)} found by name), {self.bots} bots",
                f"{self.files} files are items, and {self.files_recorded} are recorded by name"]


def identifier(team: str, kind: str, key: str) -> str:
    """Return the identifier of something in a workspace, the same each
    time."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"darq:slack:{team}:{kind}:{key}"))


def vocabulary() -> list[model.Entity]:
    """Return the properties and items an import needs."""
    def prop(entity_id: str, label: str, datatype: model.Datatype, description: str = "") -> model.Property:
        p = model.Property(datatype, entity_id)
        p.set_label(label)
        if description:
            p.set_description(description)
        return p

    def item(entity_id: str, label: str) -> model.Item:
        i = model.Item(entity_id)
        i.set_label(label)
        return i

    return [
        prop(ACCOUNT, "website account on", model.Datatype.Item),
        prop(MEMBER_ID, "website username or ID", model.Datatype.String),
        prop(FILE_ID, "Slack file identifier", model.Datatype.String, "Which of Slack's files an attachment is"),
        item(SLACK, "Slack"),
        item(WORKSPACE_CLASS, "Slack workspace"),
        item(SOFTWARE_AGENT, "software agent"),
    ]


def sender_key(message: dict) -> str:
    """Return who sent a message: a person's or bot user's identifier,
    or else a bot's.  A person's integration has both, and is theirs."""
    return message.get("user") or message.get("bot_id") or ""


def statement(property_id: str, value: model.Value, *qualifiers: tuple[str, model.Value]) -> model.Statement:
    made = model.Statement(model.Snak(property_id, value))
    for qualifier, qualified in qualifiers:
        made.add_qualifier(qualifier, qualified)
    return made


class Importer:
    def __init__(self, kb: model.KB, items: model.KB, implementation: Implementation.Client,
                 history: History | None, files: Files, export: slack_export.Export, name: str,
                 progress: typing.Callable[[str, int, int], None]):
        """Constructor.

        :param kb: Client of the KB service.
        :param items: Client that writes through the implementation.
        :param name: The workspace's name.
        :param progress: Told the step, how much is done, and the total."""
        self.kb = kb
        self.items = items
        self.implementation = implementation
        self.history = history
        self.files = files
        self.export = export
        self.name = name
        self.progress = progress
        self.team = export.team_id
        self.workspace = identifier(self.team, "workspace", self.team)
        self.result = Result()

        # Entities, by Slack's identifiers: people and bots, conversations,
        # and bots' names, as found in their messages.
        self.senders: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.conversations: dict[str, str] = {}

        # Conversations' messages' senders, the files they refer to, and
        # a message each file's attached to, by channel and time.
        self.spoke: dict[str, set[str]] = {}
        self.used_files: dict[str, dict] = {}
        self.file_messages: dict[str, tuple[str, str]] = {}

        # Those an earlier import found, by their member identifiers.
        self.members: dict[str, str] = {}

        # Files' items, by Slack's identifiers: empty if they aren't.
        self.attachments: dict[str, str] = {}

    def run(self) -> Result:
        self.ensure_vocabulary()
        self.survey()
        self.import_people()
        self.import_conversations()
        self.import_files()
        self.import_messages()
        return self.result

    def record(self, item_id: str, event: str):
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            log.warning(f"Can't record {event} for {item_id}: {e!r}")

    def ensure_vocabulary(self):
        workspace = model.Item(self.workspace)
        workspace.set_label(self.name)
        workspace.claim(model.INSTANCE_OF, model.EntityValue(WORKSPACE_CLASS))
        self.kb.ensure(*vocabulary())
        self.kb.ensure(workspace)

    def survey(self):
        """Read every conversation, for who's in it, and what it uses."""
        total = len(self.export.conversations)
        for n, conversation in enumerate(self.export.conversations):
            self.progress("Reading conversations", n, total)
            spoke: set[str] = set()
            for message in self.export.messages(conversation["id"]):
                key = sender_key(message)
                if key:
                    spoke.add(key)
                    self.names.setdefault(key, self.bot_name(message))
                for record in message.get("files", ()):
                    self.used_files[record["id"]] = self.export.files.get(record["id"]) or record
                    self.file_messages.setdefault(record["id"], (conversation["id"], message["ts"]))
            self.spoke[conversation["id"]] = spoke

    @staticmethod
    def bot_name(message: dict) -> str:
        return (message.get("bot_profile") or {}).get("name") or message.get("username") or ""

    def user_name(self, key: str) -> str:
        """Return someone's name, by their Slack identifier."""
        user = self.export.users.get(key)
        if user is None:
            return self.names.get(key, "")
        profile = user.get("profile", {})
        return user.get("real_name") or profile.get("real_name") or profile.get("display_name") or user.get("name", "")

    def is_person(self, key: str) -> bool:
        user = self.export.users.get(key)
        return user is not None and not user.get("is_bot") and key != SLACKBOT

    def find_members(self):
        """Find who an earlier import found: they have accounts on the
        workspace, with their member identifiers, as qualifiers."""
        ids = list(self.kb.find_links(self.workspace, ACCOUNT))
        for start in range(0, len(ids), BATCH):
            entities, _ = self.kb.get_entities(ids[start:start + BATCH], follow=False)
            for entity in entities:
                for s in entity.get_statements(ACCOUNT):
                    for q in s.qualifiers:
                        if q.property_id == MEMBER_ID and isinstance(q.value, model.StringValue):
                            self.members[q.value.value] = entity.id

    def import_people(self):
        self.find_members()
        keys = sorted(set(self.export.users) | {k for spoke in self.spoke.values() for k in spoke})
        for n, key in enumerate(keys):
            self.progress("Finding people", n, len(keys))
            if self.is_person(key):
                self.senders[key] = self.person(key)
                self.result.people += 1
            else:
                self.senders[key] = self.bot(key)
                self.result.bots += 1

    def person(self, key: str) -> str:
        """Return a person's entity: found, or created, with their account."""
        name = self.user_name(key)
        found = self.members.get(key)
        if found is None:
            matches = sorted(self.kb.find_by_label(name, "en", class_id=HUMAN)) if name else []
            if matches:
                found = matches[0]
                item_id = model.item_id(found, "darq.person")
                self.result.matches.append((name, item_id))
                log.info(f"Matched a Slack member by name: {item_id}")
            else:
                found = self.create_person()
        entity = self.items.get(found, follow=False)
        if entity is None:
            raise LookupError(f"{found}: not found")
        changed = model.restate(entity, self.workspace,
                                [statement(ACCOUNT, model.EntityValue(self.workspace),
                                           (MEMBER_ID, model.StringValue(key)))])
        if name and not entity.labels:
            entity.set_label(name)
            changed = True
        if changed:
            self.items.put(entity)
        return found

    def create_person(self) -> str:
        response = self.implementation.create(type_id="darq.person")
        if response.status != TypeStatus.OK or response.item is None:
            raise darq.DarqError(f"Can't create a person: {response.status.name}")
        try:
            EntityItem.Client(response.item, timeout=self.implementation.timeout).close()
        finally:
            darq.close_port(response.item)
        self.record(response.item_id, darq.history.CREATED)
        return darq.split_item_id(response.item_id)[1]

    def bot(self, key: str) -> str:
        """Return a bot's entity, or that of someone who's not a member
        now, which is all that can be said of them."""
        entity_id = identifier(self.team, "bot", key)
        if self.items.get(entity_id, follow=False) is None:
            bot = model.Item(entity_id)
            bot.set_label(self.user_name(key) or key)
            bot.claim(model.INSTANCE_OF, model.EntityValue(SOFTWARE_AGENT)).add_reference(
                model.Reference().add(model.STATED_IN, model.EntityValue(self.workspace)))
            bot.claim(ACCOUNT, model.EntityValue(self.workspace)).add_qualifier(
                MEMBER_ID, model.StringValue(key)).add_reference(
                model.Reference().add(model.STATED_IN, model.EntityValue(self.workspace)))
            self.items.create(model.ENTITY_TYPE, bot)
        return entity_id

    def conversation_label(self, conversation: dict) -> str:
        if conversation.get("is_im"):
            return f"Direct message with {self.user_name(conversation.get('user', '')) or 'someone'}"
        if conversation.get("is_mpim"):
            return conversation.get("purpose", {}).get("value") or "Group message"
        return f"#{conversation.get('name', conversation['id'])}"

    def import_conversations(self):
        total = len(self.export.conversations)
        for n, conversation in enumerate(self.export.conversations):
            self.progress("Conversations", n, total)
            channel = conversation["id"]
            if not self.spoke[channel]:
                self.result.empty += 1
                continue
            entity_id = identifier(self.team, "conversation", channel)
            self.conversations[channel] = entity_id
            people = set(self.spoke[channel])
            if conversation.get("user") in self.senders:
                people.add(conversation["user"])
            statements = [statement(darq.message.PLATFORM, model.EntityValue(SLACK)),
                          statement(darq.message.CONVERSATION, model.EntityValue(self.workspace))]
            statements += [statement(darq.message.PARTICIPANT, model.EntityValue(self.senders[key]))
                           for key in sorted(people)]
            entity = self.items.get(entity_id, follow=False)
            new = entity is None
            if entity is None:
                entity = model.Item(entity_id)
                entity.set_label(self.conversation_label(conversation))
                purpose = conversation.get("purpose", {}).get("value") or conversation.get("topic", {}).get("value")
                if purpose:
                    entity.set_description(purpose)
            changed = model.restate(entity, self.workspace, statements)
            if new:
                self.items.create(CONVERSATION_TYPE, entity)
            elif changed:
                self.items.put(entity)
            self.result.conversations += 1

    def existing_item(self, file_id: str) -> str | None:
        """Return the item an earlier import made of a file: empty if it
        made none, and None if it hasn't seen it.  That's on the message
        it's attached to."""
        entity = self.kb.get(self.message_id(*self.file_messages[file_id]), follow=False)
        if entity is None:
            return None
        for s in entity.get_statements(darq.message.ATTACHMENT):
            value = s.get_value()
            if isinstance(value, model.StringValue) and any(
                    q.property_id == FILE_ID and isinstance(q.value, model.StringValue)
                    and q.value.value == file_id for q in s.qualifiers):
                return value.value
        return None

    def import_files(self):
        total = len(self.used_files)
        for n, (file_id, record) in enumerate(sorted(self.used_files.items())):
            self.progress("Files", n, total)
            item_id = self.existing_item(file_id) or self.make_item(record)
            self.attachments[file_id] = item_id
            if item_id:
                self.result.files += 1
            else:
                self.result.files_recorded += 1

    def make_item(self, record: dict) -> str:
        """Make an item of a file, and return it, or an empty string if
        it can't be one."""
        path = self.export.file_path(record)
        if path is None or path.stat().st_size > MAX_FILE:
            return ""
        media_type = record.get("mimetype", "")
        extension = path.suffix.removeprefix(".")
        try:
            item_id = self.files.create(media_type, extension, path.read_bytes())
        except (OSError, darq.DarqError, TimeoutError) as e:
            log.warning(f"Can't make an item of {record['id']}: {e!r}")
            return ""
        if item_id is None:
            return ""
        self.record(item_id, darq.history.IMPORTED)
        return item_id

    def attachment(self, record: dict) -> model.Statement:
        file_id = record["id"]
        details = self.used_files.get(file_id, record)
        qualifiers: list[tuple[str, model.Value]] = [(FILE_ID, model.StringValue(file_id))]
        for property_id, value in ((darq.message.FILE_NAME, details.get("name")),
                                   (darq.message.MEDIA_TYPE, details.get("mimetype")),
                                   (darq.message.FILE_SIZE, details.get("size_on_disk") or details.get("size"))):
            if value:
                qualifiers.append((property_id, model.StringValue(str(value))))
        return statement(darq.message.ATTACHMENT, model.StringValue(self.attachments.get(file_id, "")), *qualifiers)

    def message_statements(self, channel: str, message: dict, said: set[str]) -> list[model.Statement]:
        """Return what a message's statements should be."""
        made = [statement(darq.message.CONVERSATION, model.EntityValue(self.conversations[channel])),
                statement(darq.message.TIME, darq.message.time_value(slack_export.microseconds(message["ts"])))]
        key = sender_key(message)
        if key in self.senders:
            made.append(statement(darq.message.SENDER, model.EntityValue(self.senders[key])))
        body, mentions = slack_text.body_of(message, self.user_name)
        if body:
            made.append(statement(darq.message.BODY, model.TextValue(body, darq.message.UNDETERMINED)))
        parent = message.get("thread_parent")
        if parent in said:
            made.append(statement(darq.message.REPLY_TO, model.EntityValue(self.message_id(channel, parent))))
        made += [statement(darq.message.MENTIONS, model.EntityValue(self.senders[m]))
                 for m in mentions if m in self.senders]
        edited = message.get("edited")
        if edited:
            made.append(statement(darq.message.LAST_EDITED,
                                  darq.message.time_value(slack_export.microseconds(edited["ts"]))))
        for reaction in message.get("reactions", ()):
            made.append(statement(darq.message.REACTION, model.StringValue(reaction["name"]),
                                  *((darq.message.REACTED_BY, model.EntityValue(self.senders[user]))
                                    for user in reaction.get("users", ()) if user in self.senders)))
        made += [self.attachment(record) for record in message.get("files", ())]
        return made

    @staticmethod
    def message_id(channel: str, ts: str) -> str:
        return darq.message.message_id(f"slack:{channel}:{ts}", slack_export.microseconds(ts))

    def import_messages(self):
        total = sum(len(self.spoke[c]) > 0 for c in self.spoke)
        for n, (channel, entity_id) in enumerate(self.conversations.items()):
            self.progress("Messages", n, total)
            messages = self.export.messages(channel)
            said = {m["ts"] for m in messages}
            created = self.result.created
            for start in range(0, len(messages), BATCH):
                self.import_batch(channel, messages[start:start + BATCH], said)
            if self.result.created > created:
                self.record(model.item_id(entity_id, CONVERSATION_TYPE), darq.history.IMPORTED)

    def import_batch(self, channel: str, messages: list[dict], said: set[str]):
        """Create or restate a batch of messages, all or none."""
        ids = {self.message_id(channel, m["ts"]): m for m in messages}
        try:
            found, _ = self.items.get_entities(list(ids), follow=False)
            existing = {e.id: e for e in found}
            new, changed = [], []
            for entity_id, message in ids.items():
                wanted = self.message_statements(channel, message, said)
                entity = existing.get(entity_id)
                if entity is None:
                    entity = model.Item(entity_id)
                    model.restate(entity, self.workspace, wanted)
                    new.append(entity)
                elif model.restate(entity, self.workspace, wanted):
                    changed.append(entity)
            if new:
                self.items.create(MESSAGE_TYPE, *new)
            if changed:
                self.items.put(*changed)
        except (darq.DarqError, TimeoutError, ValueError, LookupError, model.ConflictError) as e:
            log.error(f"Can't import {len(messages)} messages of {channel}: {e!r}")
            self.result.failed += len(messages)
            return
        self.result.created += len(new)
        self.result.updated += len(changed)
        self.result.unchanged += len(ids) - len(new) - len(changed)
