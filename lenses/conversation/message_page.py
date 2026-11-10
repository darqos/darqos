# darqos
# Copyright (C) 2026 David Arnold

# Rendering for the Conversation lens: messages, as HTML for a
# QTextBrowser.
#
# It uses no Qt, so it can be tested without a display.  Every value is
# escaped.  As in the entity lens, a link is an anchor, "#" and its
# number in the page's targets, so nothing is parsed out of URLs.  Each
# message has a named anchor of its own, to scroll to.

import datetime
import html
import re
import typing

import darq.kb as model
import darq.message
from darq.protocols.kb import Linked
from darq.ui import style


# How long after a message the same sender's next is shown under it,
# without their name again.
CONTINUED = datetime.timedelta(minutes=5)

# A message's body is a small subset of Markdown (see
# doc/message-type.rst): fenced code, inline code, bold, and italics.
FENCE = re.compile(r"```\n?(.*?)```", re.DOTALL)
CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")

STYLE = f"""
body {{ color: {style.TEXT}; font-size: 15px; }}
a {{ color: {style.LINK}; text-decoration: none; }}
p {{ margin-top: 0; margin-bottom: 0; }}
pre, code {{ font-family: monospace; font-size: 14px; }}
h3 {{ color: {style.TEXT_SOFT}; font-size: 14px; margin-top: 18px; margin-bottom: 6px; }}
.sender {{ font-weight: bold; margin-top: 12px; }}
.time, .detail, .quote {{ color: {style.TEXT_DIM}; font-size: 13px; font-weight: normal; }}
.quote {{ margin-top: 12px; }}
.gap {{ color: {style.TEXT_DIM}; font-size: 13px; margin-top: 8px; }}
"""


class Open(typing.NamedTuple):
    """A target: an item, to use in its own lens."""

    item_id: str


class Jump(typing.NamedTuple):
    """A target: another message of the conversation, to go to."""

    entity_id: str


Target = Open | Jump


def moment(message: model.Entity) -> datetime.datetime | None:
    """Return when a message was sent, in local time, or None."""
    for statement in message.get_statements(darq.message.TIME):
        t = statement.get_value()
        if isinstance(t, model.TimeValue) and t.month and t.day:
            sent = datetime.datetime(t.year, t.month, t.day, t.hour or 0, t.minute or 0, t.second or 0,
                                     tzinfo=datetime.timezone.utc)
            return sent.astimezone()
    return None


def inline(text: str) -> str:
    """Return a line of a body as HTML: code, then what's outside it."""
    parts = []
    for n, part in enumerate(CODE.split(text)):
        if n % 2:
            parts.append(f"<code>{html.escape(part)}</code>")
        else:
            escaped = html.escape(part)
            escaped = BOLD.sub(r"<b>\1</b>", escaped)
            parts.append(ITALIC.sub(r"<i>\1</i>", escaped))
    return "".join(parts)


def body_html(text: str) -> str:
    """Return a message's body as HTML."""
    parts = []
    for n, part in enumerate(FENCE.split(text)):
        if n % 2:
            parts.append(f"<pre>{html.escape(part.rstrip())}</pre>")
        elif part.strip():
            parts.append("<p>" + "<br>".join(inline(line) for line in part.strip("\n").split("\n")) + "</p>")
    return "\n".join(parts)


def size_text(value: str) -> str:
    """Return a size in bytes, as text, for reading."""
    try:
        size = float(value)
    except ValueError:
        return value
    for unit in ("bytes", "KB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} GB"


def qualifier(statement: model.Statement, property_id: str) -> str:
    """Return a statement's string qualifier, or an empty string."""
    for snak in statement.qualifiers:
        if snak.property_id == property_id and isinstance(snak.value, model.StringValue):
            return snak.value.value
    return ""


class Page:
    """Messages, as HTML, and what their links lead to, by number."""

    def __init__(self, linked: dict[str, Linked], highlight: str = ""):
        """Constructor.

        :param linked: The entities messages refer to, by identifier.
        :param highlight: The message that was opened, if one was."""
        self.linked = linked
        self.highlight = highlight
        self.targets: list[Target] = []

    def anchor(self, target: Target, text: str) -> str:
        self.targets.append(target)
        return f'<a href="#{len(self.targets) - 1}">{html.escape(text)}</a>'

    def name(self, entity_id: str) -> str:
        """Return an entity's name, as a link if it's an item."""
        link = self.linked.get(entity_id)
        text = link.label if link is not None and link.label else entity_id or "Unknown"
        if link is not None and link.item_id:
            return self.anchor(Open(link.item_id), text)
        return html.escape(text)

    def quote(self, parent_id: str) -> str:
        """Return the message that one replies to, quoted."""
        link = self.linked.get(parent_id)
        text = link.summary if link is not None and link.summary else "A message"
        return f'<p class="quote">↩ {self.anchor(Jump(parent_id), text)}</p>'

    def attachment(self, statement: model.Statement) -> str:
        """Return an attachment: a link to its item, or, for a file that
        isn't one, what's known of it."""
        value = statement.get_value()
        if isinstance(value, model.EntityValue):
            link = self.linked.get(value.entity_id)
            item_id = link.item_id if link is not None else ""
        else:
            # From before every item was an entity: its item's identifier.
            item_id = value.value if isinstance(value, model.StringValue) else ""
        name = qualifier(statement, darq.message.FILE_NAME) or item_id or "A file"
        media_type = qualifier(statement, darq.message.MEDIA_TYPE)
        size = qualifier(statement, darq.message.FILE_SIZE)
        details = ", ".join(filter(None, (media_type, size_text(size) if size else "")))
        shown = self.anchor(Open(item_id), name) if item_id else html.escape(name)
        if not item_id:
            details = ", ".join(filter(None, (details, "not imported")))
        note = f' <span class="detail">({html.escape(details)})</span>' if details else ""
        return f"<p>📎 {shown}{note}</p>"

    def reactions(self, message: model.Entity) -> str:
        """Return a message's reactions, or an empty string."""
        entries = []
        for statement in message.get_statements(darq.message.REACTION):
            value = statement.get_value()
            if isinstance(value, model.StringValue):
                count = sum(q.property_id == darq.message.REACTED_BY for q in statement.qualifiers)
                entries.append(f":{value.value}:" + (f" {count}" if count > 1 else ""))
        return f'<p class="detail">{html.escape("  ".join(entries))}</p>' if entries else ""

    def replying(self, message: model.Entity) -> str:
        """Return the message that one replies to, or an empty string."""
        for statement in message.get_statements(darq.message.REPLY_TO):
            value = statement.get_value()
            if isinstance(value, model.EntityValue):
                return value.entity_id
        return ""

    def message(self, message: model.Entity, continued: bool) -> str:
        """Return a message.

        :param continued: Whether it follows on from its sender's last."""
        sent = moment(message)
        parts = [f'<a name="{anchor_name(message.id)}"></a>']
        parent = self.replying(message)
        if parent:
            parts.append(self.quote(parent))
        if not continued or parent:
            sender = self.name(darq.message.sender_of(message))
            when = sent.strftime("%H:%M") if sent is not None else ""
            edited = " (edited)" if message.get_statements(darq.message.LAST_EDITED) else ""
            parts.append(f'<p class="sender">{sender} <span class="time">{when}{edited}</span></p>')
        parts.append(body_html(darq.message.body_of(message)))
        parts += [self.attachment(s) for s in (*message.get_statements(darq.message.ATTACHMENT),
                                               *message.get_statements(darq.message.OLD_ATTACHMENT))]
        parts.append(self.reactions(message))
        block = "\n".join(filter(None, parts))
        if message.id == self.highlight:
            return f'<table width="100%" bgcolor="{style.SELECTION}" cellpadding="6"><tr><td>{block}</td></tr></table>'
        return block

    def render(self, messages: list[model.Entity], more_older: bool, more_newer: bool) -> str:
        """Return the page: messages, oldest first, under their days."""
        parts = [f"<html><head><style>{STYLE}</style></head><body>"]
        if more_older:
            parts.append('<p class="gap">Earlier messages load as you scroll up.</p>')
        day = None
        previous: tuple[str, datetime.datetime] | None = None
        for message in messages:
            sent = moment(message)
            if sent is not None and sent.date() != day:
                day = sent.date()
                parts.append(f"<h3>{sent.day} {sent.strftime('%B %Y')}</h3>")
                previous = None
            sender = darq.message.sender_of(message)
            continued = previous is not None and sent is not None and sender == previous[0] \
                and sent - previous[1] < CONTINUED
            parts.append(self.message(message, continued))
            previous = (sender, sent) if sent is not None else None
        if not messages:
            parts.append('<p class="detail">No messages.</p>')
        if more_newer:
            parts.append('<p class="gap">Later messages load as you scroll down.</p>')
        parts.append("</body></html>")
        return "\n".join(parts)


def anchor_name(entity_id: str) -> str:
    """Return the name of a message's anchor."""
    return f"m-{entity_id}"
