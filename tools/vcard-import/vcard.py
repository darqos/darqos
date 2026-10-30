# darqos
# Copyright (C) 2026 David Arnold

"""A small vCard 3.0 reader.

Reads the cards in a file: unfolds its lines, splits each content line
into its group, name, parameters and value, and unescapes values on
request.  Apple's grouped labels (item1.X-ABLABEL) are kept with the
property they label.  Values aren't otherwise interpreted."""

import re

from typing import Dict, Iterator, List, Optional, Set


# Apple's built-in labels, such as _$!<Spouse>!$_.
_APPLE_LABEL = re.compile(r"_\$!<(.*)>!\$_")


def _split(value: str, separator: str) -> List[str]:
    """Split a value at unescaped separators, and unescape each part."""
    parts: List[str] = []
    part: List[str] = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            i += 1
            c = value[i]
            part.append("\n" if c in "nN" else c)
        elif c == separator:
            parts.append("".join(part))
            part = []
        else:
            part.append(c)
        i += 1
    parts.append("".join(part))
    return parts


class Property:
    """A content line of a card."""

    def __init__(self,
                 group: str,
                 name: str,
                 params: Dict[str, List[str]],
                 value: str):
        """Constructor.

        :param group: The line's group, such as "item1", or "".
        :param name: The property name, in upper case.
        :param params: Parameter values, by name in upper case.
        :param value: The value, still escaped."""
        self.group = group
        self.name = name
        self.params = params
        self.value = value

    def types(self) -> Set[str]:
        """Return the TYPE parameter's values, in upper case."""
        return {t.upper() for t in self.params.get("TYPE", [])}

    def param(self, name: str) -> Optional[str]:
        """Return a parameter's first value, if any."""
        values = self.params.get(name.upper())
        return values[0] if values else None

    def text(self) -> str:
        """Return the value, unescaped."""
        return _split(self.value, "\0")[0]

    def components(self, count: int = 0) -> List[str]:
        """Return the ;-separated components of the value, unescaped.

        :param count: The number of components to return, padded with
        empty strings if there are fewer."""
        parts = _split(self.value, ";")
        return parts + [""] * (count - len(parts))

    def values(self) -> List[str]:
        """Return the ,-separated values, unescaped."""
        return _split(self.value, ",")

    def __repr__(self):
        group = f"{self.group}." if self.group else ""
        return f"Property({group}{self.name})"


class Card:
    """A vCard: its properties, in order."""

    def __init__(self, properties: List[Property]):
        self.properties = properties

    def get(self, name: str) -> Optional[Property]:
        """Return the first property with a name, if any."""
        name = name.upper()
        return next((p for p in self.properties if p.name == name), None)

    def all(self, name: str) -> List[Property]:
        """Return the properties with a name."""
        name = name.upper()
        return [p for p in self.properties if p.name == name]

    def text(self, name: str) -> Optional[str]:
        """Return the first property's value, unescaped, if any."""
        p = self.get(name)
        return p.text() if p is not None else None

    def label(self, prop: Property) -> Optional[str]:
        """Return the X-ABLABEL in a property's group, if any.

        Apple's built-in labels are returned as their names, so
        _$!<Spouse>!$_ is Spouse."""
        if not prop.group:
            return None
        for p in self.properties:
            if p.group == prop.group and p.name == "X-ABLABEL":
                text = p.text()
                match = _APPLE_LABEL.fullmatch(text)
                return match.group(1) if match else text
        return None


def _parse_line(line: str) -> Optional[Property]:
    """Parse an unfolded content line, or return None if it isn't one."""

    # The name and parameters end at the first colon outside quotes.
    quoted = False
    end = -1
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif c == ":" and not quoted:
            end = i
            break
    if end < 0:
        return None

    head, value = line[:end], line[end + 1:]
    fields = re.findall(r'(?:"[^"]*"|[^;])+', head)
    if not fields:
        return None
    group, _, name = fields[0].rpartition(".")

    params: Dict[str, List[str]] = {}
    for field in fields[1:]:
        key, equals, text = field.partition("=")
        if not equals:
            # A vCard 2.1-style bare type, such as ;WORK.
            key, text = "TYPE", key
        for v in re.findall(r'"[^"]*"|[^,]+', text):
            params.setdefault(key.upper(), []).append(v.strip('"'))
    return Property(group, name.upper(), params, value)


def parse(text: str) -> Iterator[Card]:
    """Parse the cards in a string."""
    text = re.sub(r"\r?\n[ \t]", "", text)
    properties: Optional[List[Property]] = None
    for line in text.splitlines():
        upper = line.strip().upper()
        if upper == "BEGIN:VCARD":
            properties = []
        elif upper == "END:VCARD":
            if properties is not None:
                yield Card(properties)
            properties = None
        elif properties is not None:
            prop = _parse_line(line)
            if prop is not None:
                properties.append(prop)


def read(path: str) -> List[Card]:
    """Read the cards in a file."""
    with open(path, encoding="utf-8") as f:
        return list(parse(f.read()))
