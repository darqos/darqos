# darqos
# Copyright (C) 2026 David Arnold

# Rendering for the entity lens: an entity, as HTML for a QTextBrowser.
#
# It uses no Qt, so it can be tested without a display.  Every value is
# escaped.  A link to another item is an anchor, "#" and its number in
# the page's links, so the lens can follow it without parsing item
# identifiers out of URLs.

import html
import typing

import darq.kb as model


# The kinds' names, by type.
KIND_NAMES = {
    "darq.entity": "Entity",
    "darq.person": "Person",
    "darq.organisation": "Organisation",
    "darq.organisational-unit": "Organisational Unit",
    "darq.place": "Place",
    "darq.group": "Group",
}

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

STYLE = """
body { color: #e0e0e0; font-size: 15px; }
a { color: #8ab4f8; text-decoration: none; }
.description { color: #c0c0c0; }
.aliases { color: #9a9a9a; }
.property { color: #9a9a9a; padding-right: 24px; }
.detail { color: #9a9a9a; font-size: 13px; }
h3 { color: #c0c0c0; font-size: 15px; margin-top: 18px; }
"""


class Link(typing.NamedTuple):
    """Another entity, as an item: its item identifier (empty for a
    property, which isn't an item), and label (empty for none)."""

    item_id: str
    label: str


def languages(locale_name: str) -> list[str]:
    """Return the chain of languages for a locale, such as en_AU: its
    language tag, then its language, then English."""
    tag = locale_name.split(".")[0].replace("_", "-")
    chain = [tag, tag.split("-")[0], "en"]
    return [t for n, t in enumerate(chain) if t and t not in chain[:n]]


def best(texts: dict[str, str], chain: list[str]) -> str | None:
    """Return the text in the first language of a chain, or else any."""
    for language in chain:
        if language in texts:
            return texts[language]
    return next(iter(texts.values()), None)


def kind_name(item_id: str) -> str:
    """Return the name of an item's kind."""
    type_id = item_id.partition(":")[0]
    return KIND_NAMES.get(type_id, type_id)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_time(t: model.TimeValue) -> str:
    """Return a time as text, to its precision: 1974, March 1974, or
    10 December 1815, with BCE for a negative year."""
    P = model.TimePrecision
    p = t.precision.value
    era = " BCE" if t.year < 0 else ""
    year = abs(t.year)

    if p <= P.HundredYears.value:
        if p == P.HundredYears.value:
            text = f"{ordinal((year - 1) // 100 + 1)} century{era}"
        else:
            text = f"about {year}{era}"
    elif p == P.TenYears.value:
        text = f"{year // 10 * 10}s{era}"
    elif p == P.Years.value or t.month is None:
        text = f"{year}{era}"
    elif p == P.Months.value or t.day is None:
        text = f"{MONTHS[t.month - 1]} {year}{era}"
    else:
        text = f"{t.day} {MONTHS[t.month - 1]} {year}{era}"
        if p >= P.Hours.value and t.hour is not None:
            text += f", {t.hour:02d}:{t.minute or 0:02d}"
            if p >= P.Seconds.value and t.second is not None:
                text += f":{t.second:02d}"

    if t.before or t.after:
        unit = t.precision.name.lower()
        if t.before == t.after:
            text += f" ± {t.before} {unit}"
        else:
            text += f" (−{t.before}, +{t.after} {unit})"
    if t.calendar != "gregorian":
        text += f" ({t.calendar})"
    return text


class Page:
    """An entity's page: its HTML, and the item identifiers its links
    lead to, by number."""

    def __init__(self, chain: list[str], links: dict[str, Link]):
        """Constructor.

        :param chain: Languages, best first.
        :param links: The entities the page refers to, by identifier."""
        self.chain = chain
        self.links = links
        self.targets: list[str] = []

    def link(self, item_id: str, text: str) -> str:
        """Return an anchor for an item."""
        self.targets.append(item_id)
        return f'<a href="#{len(self.targets) - 1}">{html.escape(text)}</a>'

    def name(self, entity_id: str) -> str:
        """Return an entity's label, or its identifier, as a link if it's
        an item."""
        link = self.links.get(entity_id)
        text = link.label if link is not None and link.label else entity_id
        if link is not None and link.item_id:
            return self.link(link.item_id, text)
        return html.escape(text)

    def value(self, value: model.Value) -> str:
        if isinstance(value, model.EntityValue):
            return self.name(value.entity_id)
        if isinstance(value, model.StringValue):
            return html.escape(value.value)
        if isinstance(value, model.TextValue):
            return html.escape(best(value.text, self.chain) or "")
        if isinstance(value, model.QuantityValue):
            text = html.escape(str(value.amount))
            if value.lower is not None and value.upper is not None \
                    and value.amount - value.lower == value.upper - value.amount:
                text += f" ± {html.escape(str(value.upper - value.amount))}"
            elif value.lower is not None or value.upper is not None:
                lower = "" if value.lower is None else str(value.lower)
                upper = "" if value.upper is None else str(value.upper)
                text += f" [{html.escape(lower)}, {html.escape(upper)}]"
            if value.unit is not None:
                text += " " + self.name(value.unit)
            return text
        return html.escape(format_time(value))

    def snak(self, snak: model.Snak) -> str:
        return f"{self.name(snak.property_id)}: {self.value(snak.value)}"

    def statement(self, s: model.Statement, first: bool) -> str:
        """Return a statement's rows: the property's name only on the first
        of its statements."""
        value = self.value(s.main_snak.value)
        if s.rank == model.Rank.Preferred:
            value += " ★"
        elif s.rank == model.Rank.Deprecated:
            value = f"<s>{value}</s>"
        prop = self.name(s.main_snak.property_id) if first else ""
        rows = [f'<tr><td class="property">{prop}</td><td>{value}</td></tr>']
        for q in s.qualifiers:
            rows.append(f'<tr><td></td><td class="detail">&nbsp;&nbsp;{self.snak(q)}</td></tr>')
        for r in s.references:
            sources = "; ".join(self.snak(a) for a in r.snaks)
            rows.append(f'<tr><td></td><td class="detail">&nbsp;&nbsp;source: {sources}</td></tr>')
        return "\n".join(rows)

    def render(self,
               entity: model.Entity,
               linked_from: list[Link],
               more: int = 0,
               note: str | None = None) -> str:
        """Return an entity's page.

        :param linked_from: The items that link to it.
        :param more: How many more link to it, not listed.
        :param note: A note, such as that it was merged, shown first."""
        parts = [f"<html><head><style>{STYLE}</style></head><body>"]
        if note:
            parts.append(f'<p class="detail">{html.escape(note)}</p>')
        description = best(entity.descriptions, self.chain)
        if description:
            parts.append(f'<p class="description">{html.escape(description)}</p>')
        aliases = next((entity.aliases[lang] for lang in self.chain if entity.aliases.get(lang)), [])
        if aliases:
            parts.append(f'<p class="aliases">Also known as: {html.escape(", ".join(aliases))}</p>')

        # Statements, by property, in the order each first appears.
        order: list[str] = []
        for s in entity.statements:
            if s.get_property_id() not in order:
                order.append(s.get_property_id())
        if order:
            parts.append("<table>")
            for property_id in order:
                for n, s in enumerate(entity.get_statements(property_id)):
                    parts.append(self.statement(s, n == 0))
            parts.append("</table>")
        else:
            parts.append('<p class="detail">No statements.</p>')

        if linked_from:
            parts.append("<h3>Linked from</h3><p>")
            entries = []
            for link in linked_from:
                text = link.label or link.item_id
                entries.append(f"{self.link(link.item_id, text)} "
                               f'<span class="detail">({html.escape(kind_name(link.item_id))})</span>')
            parts.append(" · ".join(entries))
            if more:
                parts.append(f' <span class="detail">and {more} more</span>')
            parts.append("</p>")
        parts.append("</body></html>")
        return "\n".join(parts)
