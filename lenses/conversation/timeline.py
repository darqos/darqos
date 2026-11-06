# darqos
# Copyright (C) 2026 David Arnold

# The Conversation lens's messages: those of a conversation that are
# held, oldest first, read a page at a time through the conversation's
# item port.  A conversation can have tens of thousands, so only so many
# are held, around where it's being read.  It uses no Qt, so it can be
# tested without a display.

import typing

import darq.kb as model
import darq.message
from darq.protocols.kb import EntityItem, Linked, Role, Status


# The type of a message.
MESSAGE_TYPE = "darq.message"

# Messages read at a time, and either side of one that's opened.
PAGE = 100
AROUND = 50

# Most messages held: the far end's dropped, and read again if it's
# scrolled back to.
MAX_HELD = 400


class TimelineError(Exception):
    """Messages can't be read."""


class Timeline:
    """The messages held, and the entities they refer to.

    With no client, it's a message that isn't in a conversation, alone."""

    def __init__(self, client: EntityItem.Client | None, languages: list[str]):
        self.client = client
        self.languages = languages
        self.messages: list[model.Entity] = []
        self.linked: dict[str, Linked] = {}

        # Whether there are messages either side of those held.
        self.more_older = False
        self.more_newer = False

    def read(self, after: str, reverse: bool, limit: int) -> list[model.Entity]:
        """Read messages after one, in the order they're asked for.

        :raises TimelineError: if they can't be read."""
        if self.client is None:
            return []
        response = self.client.get_linked_entities(property_id=darq.message.CONVERSATION, role=Role.MAIN,
                                                   type_id=MESSAGE_TYPE, languages=self.languages, max=limit,
                                                   after=after, reverse=reverse)
        if response.status != Status.OK:
            raise TimelineError(response.status.name)
        self.remember(response.linked)
        return [model.entity_from_wire(e) for e in response.entities]

    def remember(self, linked: typing.Iterable[Linked]):
        """Keep what's known of entities that messages refer to."""
        self.linked.update((link.entity_id, link) for link in linked)

    def load_newest(self):
        """Hold the newest messages."""
        page = self.read("", True, PAGE + 1)
        self.messages = page[:PAGE][::-1]
        self.more_older, self.more_newer = len(page) > PAGE, False

    def load_oldest(self):
        """Hold the oldest messages."""
        page = self.read("", False, PAGE + 1)
        self.messages = page[:PAGE]
        self.more_older, self.more_newer = False, len(page) > PAGE

    def load_around(self, message: model.Entity):
        """Hold the messages either side of one."""
        older = self.read(message.id, True, AROUND + 1)
        newer = self.read(message.id, False, AROUND + 1)
        self.messages = [*older[:AROUND][::-1], message, *newer[:AROUND]]
        self.more_older, self.more_newer = len(older) > AROUND, len(newer) > AROUND

    def load_older(self) -> bool:
        """Hold the page before those held: False if there wasn't one."""
        if not self.more_older or not self.messages:
            return False
        page = self.read(self.messages[0].id, True, PAGE + 1)
        self.messages[:0] = page[:PAGE][::-1]
        self.more_older = len(page) > PAGE
        if len(self.messages) > MAX_HELD:
            del self.messages[MAX_HELD:]
            self.more_newer = True
        return True

    def load_newer(self) -> bool:
        """Hold the page after those held: False if there wasn't one."""
        if not self.more_newer or not self.messages:
            return False
        page = self.read(self.messages[-1].id, False, PAGE + 1)
        self.messages.extend(page[:PAGE])
        self.more_newer = len(page) > PAGE
        if len(self.messages) > MAX_HELD:
            del self.messages[:-MAX_HELD]
            self.more_older = True
        return True

    def holds(self, entity_id: str) -> bool:
        return any(m.id == entity_id for m in self.messages)
