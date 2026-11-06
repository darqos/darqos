# darqos
# Copyright (C) 2026 David Arnold

"""Slack's message text, converted to the subset of Markdown that a
message's body is (see doc/message-type.rst).

Slack's markup is its own: <@U123> mentions someone, <#C123|general> a
channel, and <https://x|text> is a link; *bold*, _italic_ and ~struck~
are single characters; and &, < and > are escaped, so a < is always the
start of a token.  Code keeps its text, but not its tokens, as Slack
wraps URLs even there.  It knows nothing of the KB."""

import re
import typing


# Code, which is left alone.
CODE = re.compile(r"(```.*?```|`[^`\n]+`)", re.DOTALL)

# One of Slack's <...> tokens: a link's text can run over lines.
TOKEN = re.compile(r"<([^<>]+)>")

# What stands in for a token while the text around it is converted, so
# that a URL's underscores aren't taken for italics.
HELD = "\x00%d\x00"
HOLDS = re.compile("\x00([0-9]+)\x00")

BOLD = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
ITALIC = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")
STRUCK = re.compile(r"(?<![\w~])~([^~\n]+)~(?![\w~])")


def unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def token_text(body: str, name: typing.Callable[[str], str], mentions: list[str]) -> str:
    """Return what a token's shown as, adding anyone it mentions."""
    target, _, label = body.partition("|")
    label = " ".join(label.split())
    if target.startswith("@"):
        mentions.append(target[1:])
        return "@" + (name(target[1:]) or label or target[1:])
    if target.startswith("#"):
        return "#" + (label or target[1:])
    if target.startswith("!"):
        # A broadcast, such as <!here>, or a date or user group, with its
        # text as the label.
        return label or "@" + target[1:].partition("^")[0]
    url = target.removeprefix("mailto:")
    return f"{label} ({url})" if label and label != url and label != target else url


def convert_text(text: str, name: typing.Callable[[str], str], mentions: list[str]) -> str:
    """Convert text that isn't code."""
    tokens: list[str] = []

    def hold(match: re.Match[str]) -> str:
        tokens.append(token_text(match[1], name, mentions))
        return HELD % (len(tokens) - 1)

    text = TOKEN.sub(hold, text)
    text = BOLD.sub(r"**\1**", text)
    text = ITALIC.sub(r"*\1*", text)
    text = STRUCK.sub(r"\1", text)
    return unescape(HOLDS.sub(lambda match: tokens[int(match[1])], text))


def convert_code(code: str, name: typing.Callable[[str], str], mentions: list[str]) -> str:
    """Convert code: its text stays as it is."""
    return unescape(TOKEN.sub(lambda match: token_text(match[1], name, mentions), code))


def convert(text: str, name: typing.Callable[[str], str]) -> tuple[str, list[str]]:
    """Convert Slack's text to a body.

    :param name: Returns a person's name, by Slack identifier, or an
                 empty string.
    :returns: The body, and the identifiers of those it mentions."""
    mentions: list[str] = []
    parts = []
    for n, part in enumerate(CODE.split(text)):
        parts.append(convert_code(part, name, mentions) if n % 2 else convert_text(part, name, mentions))
    return "".join(parts), mentions


def attachment_text(attachment: dict) -> str:
    """Return the text of a message's attachment: what a bot says, or a
    link's preview."""
    parts = [attachment.get("pretext"), attachment.get("title"), attachment.get("text") or attachment.get("fallback")]
    return "\n".join(dict.fromkeys(part for part in parts if part))


def body_of(message: dict, name: typing.Callable[[str], str]) -> tuple[str, list[str]]:
    """Return a message's body, with its attachments' text, since a bot's
    often has only that, and those it mentions."""
    mentions: list[str] = []
    parts = []
    for text in [message.get("text") or "", *map(attachment_text, message.get("attachments", ()))]:
        if text:
            body, mentioned = convert(text, name)
            parts.append(body)
            mentions += mentioned
    return "\n".join(dict.fromkeys(parts)), list(dict.fromkeys(mentions))
