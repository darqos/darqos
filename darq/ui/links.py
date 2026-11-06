# darqos
# Copyright (C) 2026 David Arnold

# What links to an item, as a lens shows it (see doc/kb-service.rst): not
# as a list, since people create, send and use a great many things, but
# in groups, by property and type, from an item's GetLinkSummary.  A
# small group's shown in place, and a large one by its count, which
# opens to a paged list.
#
# A group isn't an item, so it has no lens of its own: a lens shows it in
# place of its item's page, with a View saying which, and where.  This
# has what lenses share of that, as HTML, and uses no Qt.  A lens gives
# it a way to make anchors, and is told which target was chosen: a Row,
# an OpenGroup, or a Turn.

import dataclasses
import html
import typing

from darq.protocols.kb import LinkGroup, Linked, Role


# Most entities in a group that's shown in place.
INLINE_MAX = 12

# Entities on a page of a group.
PAGE_ROWS = 100

# Singular and plural names of the types of entities, for those with
# names.  Others are shown by their identifiers.
TYPE_NAMES = {
    "darq.entity": ("Entity", "Entities"),
    "darq.person": ("Person", "People"),
    "darq.organisation": ("Organisation", "Organisations"),
    "darq.organisational-unit": ("Organisational Unit", "Organisational Units"),
    "darq.place": ("Place", "Places"),
    "darq.group": ("Group", "Groups"),
    "darq.conversation": ("Conversation", "Conversations"),
    "darq.message": ("Message", "Messages"),
    "public.url": ("URL", "URLs"),
    "darq.book.work": ("Book", "Books"),
    "darq.book.expression": ("Text", "Texts"),
    "darq.book.manifestation": ("Edition", "Editions"),
    "darq.book.item": ("Copy", "Copies"),
}

# Said after a heading, for a link that isn't from a statement's value.
ROLE_NOTES = {Role.MAIN: "", Role.QUALIFIER: " (in a qualifier)", Role.REFERENCE: " (in a reference)"}


# A group: its property, role and type.
Key = tuple[str, Role, str]


class Row(typing.NamedTuple):
    """A target: an entity listed in a group, by its item identifier."""

    item_id: str


class OpenGroup(typing.NamedTuple):
    """A target: a group to show."""

    key: Key


class Turn(typing.NamedTuple):
    """A target: the next page of the group shown, or the previous."""

    step: int


# Makes an anchor to a target, with some text, as HTML.
Anchor = typing.Callable[[Row | OpenGroup | Turn, str], str]


@dataclasses.dataclass
class View:
    """A group being shown, and where: the entity each page shown so far
    starts after, so the last is the page shown.  A group's paged from
    its newest, by cursors, so there's no jumping to a page."""

    key: Key
    cursors: list[str] = dataclasses.field(default_factory=lambda: [""])

    @property
    def after(self) -> str:
        return self.cursors[-1]

    @property
    def first(self) -> int:
        """Return the number of the first row shown, from 0."""
        return (len(self.cursors) - 1) * PAGE_ROWS

    def copy(self) -> "View":
        return View(self.key, list(self.cursors))


@dataclasses.dataclass
class Section:
    """Groups under a heading: those of a property, in a role."""

    heading: str
    groups: list[LinkGroup]

    @property
    def count(self) -> int:
        return sum(group.count for group in self.groups)


def key_of(group: LinkGroup) -> Key:
    return group.property_id, group.role, group.type_id


def type_name(type_id: str, count: int = 1) -> str:
    """Return the name of a type, for a number of its items."""
    names = TYPE_NAMES.get(type_id)
    if names is None:
        return type_id
    return names[0] if count == 1 else names[1]


def heading(group: LinkGroup) -> str:
    """Return what a group's headed by, reading its property backwards,
    from the entity it links to."""
    if group.inverse_label and group.role == Role.MAIN:
        return group.inverse_label[:1].upper() + group.inverse_label[1:]
    return f"With this as {group.label or group.property_id}{ROLE_NOTES[group.role]}"


def sections(groups: typing.Iterable[LinkGroup]) -> list[Section]:
    """Return groups in sections: statements' values first, and then the
    largest first, or by name."""
    found: dict[tuple[str, Role], Section] = {}
    for group in groups:
        found.setdefault((group.property_id, group.role), Section(heading(group), [])).groups.append(group)
    for section in found.values():
        section.groups.sort(key=lambda g: (-g.count, g.type_id))
    return sorted(found.values(), key=lambda s: (s.groups[0].role != Role.MAIN, -s.count, s.heading))


def is_inline(group: LinkGroup) -> bool:
    """Return whether a group's entities are shown in place."""
    return group.count <= INLINE_MAX


def row_text(link: Linked) -> str:
    """Return what an entity's listed as: messages have no labels."""
    return link.label or link.summary or link.item_id


def render_group(group: LinkGroup, rows: list[Linked], anchor: Anchor) -> str:
    """Return a group's entry in its section."""
    name = type_name(group.type_id, group.count)
    if not is_inline(group):
        return anchor(OpenGroup(key_of(group)), f"{group.count:,} {name}")
    entries = ", ".join(anchor(Row(link.item_id), row_text(link)) for link in rows if link.item_id)
    return f'{entries} <span class="detail">({html.escape(name)})</span>'


def render_sections(all_sections: list[Section], inline: dict[Key, list[Linked]], anchor: Anchor) -> str:
    """Return what links to an item, as HTML.

    :param inline: The entities of the groups shown in place."""
    parts = []
    for section in all_sections:
        parts.append(f"<h3>{html.escape(section.heading)}</h3><p>")
        parts.append(" · ".join(render_group(group, inline.get(key_of(group), []), anchor)
                                for group in section.groups))
        parts.append("</p>")
    return "\n".join(parts)


def render_view(group: LinkGroup, view: View, rows: list[Linked], more: bool, anchor: Anchor) -> str:
    """Return a page of a group, as HTML.

    :param rows: The entities on the page, newest first.
    :param more: Whether there are more after them."""
    name = type_name(group.type_id, group.count)
    first = view.first + 1
    last = view.first + len(rows)
    parts = [f"<h3>{html.escape(heading(group))}: {group.count:,} {html.escape(name)}</h3>"]
    if rows:
        parts.append(f'<p class="detail">{first:,} to {last:,}, newest first</p>')
    else:
        parts.append('<p class="detail">None, now.</p>')

    def turns() -> str:
        entries = []
        if len(view.cursors) > 1:
            entries.append(anchor(Turn(-1), "◀ Newer"))
        if more:
            entries.append(anchor(Turn(1), "Older ▶"))
        return f"<p>{' · '.join(entries)}</p>" if entries else ""

    # At both ends, since a page is a long one.
    parts.append(turns())
    parts += [f"<p>{anchor(Row(link.item_id), row_text(link))}</p>" for link in rows if link.item_id]
    parts.append(turns())
    return "\n".join(parts)
