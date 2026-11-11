# darqos
# Copyright (C) 2022-2026 David Arnold

"""The Knowledge Base's data model, and a client of its service.

Entities, items and properties, have labels, descriptions and aliases
per language, and statements: a property and value, with qualifiers,
references and a rank, after Wikidata's model.  They're kept by the
Knowledge Base service, darq.kb, described in doc/kb-service.rst, as
JSON documents (see document()), and sent to and from it as the
structs of its protocol (lib/didl/kb.didl).  KB is its client.

Changes to an entity can be made as edits, which can be made again to
a later revision (see Edit), and an operator's changes, by hand, keep a
source's statements its own (see change_value).

Questions across entities are asked as a Query: patterns of statements
that must all match."""

import json
import re
import unicodedata
import uuid

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import (AbstractSet, Any, Callable, Dict, Iterable, Iterator, List,
                    NamedTuple, Optional, Protocol, Sequence, Set, Tuple, Union)

from darq import identifier, system
from darq.protocols import kb as wire


# Wikidata's properties for an item's classes, and their hierarchy.
INSTANCE_OF = "P31"
SUBCLASS_OF = "P279"

# Namespace for identifiers derived from keys (see key_id).
NAMESPACE = uuid.UUID("5c0f6a52-3d0e-4b7c-9d8e-2a61f3b4c7d9")


def key_id(key: str) -> str:
    """Return the identifier for an entity created from a key.

    The same key always gives the same identifier, so a re-import finds
    the entities it created before."""
    return str(uuid.uuid5(NAMESPACE, key))


def fold(text: str) -> str:
    """Return text folded for matching, ignoring case and form."""
    return unicodedata.normalize("NFKC", text).casefold()


# A phone number: digits, perhaps with a leading +, and separators.
_PHONE = re.compile(r"\+?[\d\s().-]*\d[\d\s().-]*")


def normalise(value: str) -> str:
    """Return a string value normalised for matching.

    Case is ignored, and a phone number is just its digits, with any
    leading +, so it matches however it was written."""
    value = value.strip()
    if _PHONE.fullmatch(value):
        plus = "+" if value.startswith("+") else ""
        return plus + re.sub(r"\D", "", value)
    return fold(value)


class Rank(Enum):
    """Rank values for statements."""

    # This reference is currently the best available.
    Preferred = 1

    # This reference is accurate, but there might be a better one.
    Normal = 2

    # This reference is no longer recommended.
    Deprecated = 3


class Datatype(Enum):
    """Data types for property values."""

    # Reference to an Item.
    Item = 1

    # Reference to a Property.
    Property = 2

    # UTF-8 string.  Note that this is a universal string, not text in
    # a specific language.  As such, it's good for enum-type strings,
    # not text-type strings.  See Text.
    String = 3

    # Multi-lingual text value.
    Text = 4

    # Numeric quantity, with optional unit and bounds.
    Quantity = 5

    # Datetime, with precision, and bounds.
    Time = 6

    # URL, such as a website or a mailto: address.
    Url = 7

    # Identifier in an external system.
    ExternalId = 8


class EntityValue:
    """Item or Property reference value type."""

    def __init__(self, entity_id: str):
        self.entity_id: str = entity_id

    def get_id(self) -> str:
        """Return the identifier of the referenced entity."""
        return self.entity_id

    def __repr__(self):
        return f"EntityValue({self.entity_id!r})"


# Types accepted for a quantity's amount and bounds.
Number = Union[int, float, str, Decimal]


def _decimal(value: Number) -> Decimal:
    """Convert a number to a finite Decimal.

    A float is converted from its shortest representation, so 1.5 is
    Decimal("1.5"), and 0.1 is Decimal("0.1"), not its binary value."""
    if isinstance(value, float):
        value = repr(value)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"invalid number: {value!r}") from None
    if not result.is_finite():
        raise ValueError(f"number isn't finite: {value!r}")
    return result


class QuantityValue:
    """Quantity value type.

    The amount and bounds are Decimals, which keep their significant
    digits: 1.50 is not the same as 1.5.  The bounds give the amount's
    uncertainty, and are in the same unit."""

    def __init__(self,
                 amount: Number,
                 unit: Optional[str] = None,
                 lower: Optional[Number] = None,
                 upper: Optional[Number] = None):
        """Constructor.

        :param amount: The quantity's amount.
        :param unit: Identifier of the Item that's its unit, if any.
        :param lower: Lower bound of the amount, if any.
        :param upper: Upper bound of the amount, if any."""
        self.amount: Decimal = _decimal(amount)
        self.unit: Optional[str] = unit
        self.lower: Optional[Decimal] = \
            None if lower is None else _decimal(lower)
        self.upper: Optional[Decimal] = \
            None if upper is None else _decimal(upper)

    def __str__(self):
        text = str(self.amount)
        if self.lower is not None or self.upper is not None:
            lower = "" if self.lower is None else str(self.lower)
            upper = "" if self.upper is None else str(self.upper)
            text += f" [{lower}, {upper}]"
        if self.unit is not None:
            text += f" {self.unit}"
        return text

    def __repr__(self):
        return f"QuantityValue({str(self)!r})"


class StringValue:
    """String value type."""

    def __init__(self, value: str):
        self.value = value

    def __repr__(self):
        return f"StringValue({self.value!r})"


class TextValue:
    """Multi-language value type."""

    def __init__(self, text: Optional[str] = None, language: str = "en"):
        """Constructor.

        :param text: Unicode string value for this text.
        :param language: Language tag for this text."""
        self.text: Dict[str, str] = {}
        if text is not None:
            self.text[language] = text

    def set_text(self, text: str, language: str = "en"):
        """Set text for the specified language."""
        self.text[language] = text
        return text

    def get_text(self, language: str = "en") -> Optional[str]:
        """Return text in given language."""
        return self.text.get(language)

    def __repr__(self):
        return f"TextValue({self.text!r})"


class TimePrecision(Enum):
    """Time precision specifier."""

    BillionYears = 0
    HundredMillionYears = 1
    TenMillionYears = 2
    MillionYears = 3
    HundredThousandYears = 4
    TenThousandYears = 5
    ThousandYears = 6
    HundredYears = 7
    TenYears = 8
    Years = 9
    Months = 10
    Days = 11
    Hours = 12
    Minutes = 13
    Seconds = 14
    # Milli = 15
    # Micro = 16
    Nanoseconds = 17


# [+-]YYYY[-MM[-DD[Thh:mm[:ss[.fffffffff]]]]], with any number of year
# digits.
_TIME_PATTERN = re.compile(
    r"([+-]?\d+)(?:-(\d\d)(?:-(\d\d)"
    r"(?:T(\d\d):(\d\d)(?::(\d\d)(?:\.(\d{1,9}))?)?)?)?)?")


def _check_range(name: str, value: Optional[int], low: int, high: int):
    """Raise ValueError if a value is set and outside low..high."""
    if value is not None and not low <= value <= high:
        raise ValueError(f"{name} out of range: {value}")


class TimeValue:
    """Time value type.

    The year can be any integer, so a time can be long before or after
    the range of the datetime module.  Fields finer than the precision
    are ignored.  The bounds, before and after, are counts of the
    precision's unit: 1974 with Years precision, before 2 and after 1,
    is some time from 1972 to 1975."""

    def __init__(self,
                 year: int,
                 month: Optional[int] = None,
                 day: Optional[int] = None,
                 hour: Optional[int] = None,
                 minute: Optional[int] = None,
                 second: Optional[int] = None,
                 nanosecond: Optional[int] = None,
                 precision: TimePrecision = TimePrecision.Days,
                 before: int = 0,
                 after: int = 0,
                 calendar: Optional[str] = None):
        _check_range("month", month, 1, 12)
        _check_range("day", day, 1, 31)
        _check_range("hour", hour, 0, 23)
        _check_range("minute", minute, 0, 59)
        _check_range("second", second, 0, 60)
        _check_range("nanosecond", nanosecond, 0, 999_999_999)
        if before < 0 or after < 0:
            raise ValueError("bounds can't be negative")

        self.year: int = year
        self.month: Optional[int] = month
        self.day: Optional[int] = day
        self.hour: Optional[int] = hour
        self.minute: Optional[int] = minute
        self.second: Optional[int] = second
        self.nanosecond: Optional[int] = nanosecond

        self.precision: TimePrecision = precision
        self.before: int = before
        self.after: int = after
        self.calendar = calendar if calendar is not None else "gregorian"

    @classmethod
    def parse(cls,
              text: str,
              precision: Optional[TimePrecision] = None,
              before: int = 0,
              after: int = 0,
              calendar: Optional[str] = None) -> "TimeValue":
        """Create a time from an ISO 8601-style string.

        :param text: [+-]YYYY[-MM[-DD[Thh:mm[:ss[.fffffffff]]]]], where
        the year has any number of digits, and is negative for BCE.
        :param precision: Precision of the time; by default, that of the
        last field given.
        :param before: Lower bound, in units of the precision.
        :param after: Upper bound, in units of the precision.
        :param calendar: Calendar model; by default, gregorian."""
        match = _TIME_PATTERN.fullmatch(text)
        if match is None:
            raise ValueError(f"invalid time: {text!r}")
        year, month, day, hour, minute, second, fraction = match.groups()

        if precision is None:
            if fraction is not None:
                precision = TimePrecision.Nanoseconds
            elif second is not None:
                precision = TimePrecision.Seconds
            elif minute is not None:
                precision = TimePrecision.Minutes
            elif day is not None:
                precision = TimePrecision.Days
            elif month is not None:
                precision = TimePrecision.Months
            else:
                precision = TimePrecision.Years

        def number(field: Optional[str]) -> Optional[int]:
            return None if field is None else int(field)

        nanosecond = None if fraction is None else int(fraction.ljust(9, "0"))
        return cls(int(year), number(month), number(day), number(hour),
                   number(minute), number(second), nanosecond,
                   precision, before, after, calendar)

    def __str__(self):
        p = self.precision.value
        text = f"{self.year:05d}" if self.year < 0 else f"{self.year:04d}"
        if p >= TimePrecision.Months.value and self.month is not None:
            text += f"-{self.month:02d}"
            if p >= TimePrecision.Days.value and self.day is not None:
                text += f"-{self.day:02d}"
                if p >= TimePrecision.Hours.value and self.hour is not None:
                    text += f"T{self.hour:02d}"
                    if (p >= TimePrecision.Minutes.value
                            and self.minute is not None):
                        text += f":{self.minute:02d}"
                        if (p >= TimePrecision.Seconds.value
                                and self.second is not None):
                            text += f":{self.second:02d}"
                            if (p >= TimePrecision.Nanoseconds.value
                                    and self.nanosecond is not None):
                                text += f".{self.nanosecond:09d}"

        if self.before == self.after and self.before != 0:
            text += f" ±{self.before} {self.precision.name}"
        elif self.before != 0 or self.after != 0:
            text += f" -{self.before}/+{self.after} {self.precision.name}"
        if self.calendar != "gregorian":
            text += f" ({self.calendar})"
        return text

    def __repr__(self):
        return f"TimeValue({str(self)!r}, precision={self.precision.name})"


# Types of property value.
Value = Union[EntityValue, StringValue, TextValue, QuantityValue, TimeValue]


class Snak:
    """A property:value pair.

    Used for a statement's claim, its qualifiers, and the attributes of
    its references."""

    def __init__(self, property_id: str, value: Value):
        """Constructor.

        :param property_id: String property identifier.
        :param value: Property value."""
        self.property_id: str = property_id
        self.value: Value = value

    def get_property_id(self) -> str:
        """Return the property identifier for this snak."""
        return self.property_id

    def get_value(self) -> Value:
        """Return the property value for this snak."""
        return self.value

    def __repr__(self):
        return f"[{self.property_id}: {self.value!r}]"


class Reference:
    """A collection of snaks defining a source for a statement."""

    def __init__(self, snaks: Optional[Iterable[Snak]] = None):
        """Constructor.

        :param snaks: A sequence of Snaks to initialise the reference."""
        self.snaks: List[Snak] = []

        if snaks is not None:
            self.snaks.extend(snaks)

    def add(self, property_id: str, value: Value) -> "Reference":
        """Add a snak to a Reference's collection, and return it."""
        self.snaks.append(Snak(property_id, value))
        return self

    def get_snaks(self) -> Iterable[Snak]:
        """Return an iterator over the snaks of this Reference."""
        return iter(self.snaks)

    def __repr__(self):
        return f"Reference({self.snaks!r})"


class Statement:
    """A statement associates a Property value with an Item or Property."""

    def __init__(self,
                 main_snak: Snak,
                 qualifiers: Optional[Iterable[Snak]] = None,
                 references: Optional[Iterable[Reference]] = None,
                 rank: Optional[Rank] = None,
                 statement_id: Optional[str] = None):
        """Constructor.

        :param main_snak: The claimed property and value.
        :param qualifiers: Qualifiers for the claim.
        :param references: References for the claim.
        :param rank: Status of the claim.
        :param statement_id: String identifier; by default, a UUID."""

        # Identifier, unique within the entity, for changing the claim.
        self.id: str = statement_id or str(uuid.uuid4())

        # Entity this statement's about, once it's added to one.
        self.entity: Optional["Entity"] = None

        # Claim a property and value.
        self.main_snak: Snak = main_snak

        # Collection of qualifiers for this claim.
        self.qualifiers: List[Snak] = []
        if qualifiers is not None:
            self.qualifiers.extend(qualifiers)

        # Collection of references, providing evidence for the claim.
        self.references: List[Reference] = []
        if references is not None:
            self.references.extend(references)

        # Claim rank.
        self.rank: Rank = rank if rank is not None else Rank.Normal

    def get_main_snak(self) -> Snak:
        """Return the claimed property and value."""
        return self.main_snak

    def get_property_id(self) -> str:
        """Return the claimed property's identifier."""
        return self.main_snak.get_property_id()

    def get_value(self) -> Value:
        """Return the claimed value."""
        return self.main_snak.get_value()

    def get_rank(self) -> Rank:
        """Return the claim's rank."""
        return self.rank

    def get_id(self) -> str:
        """Return the statement's identifier."""
        return self.id

    def set_rank(self, rank: Rank):
        """Set the claim's rank."""
        self.rank = rank
        self._changed()

    def add_qualifier(self, property_id: str, value: Value) -> "Statement":
        """Add a qualifier to the claim, and return the statement."""
        self.qualifiers.append(Snak(property_id, value))
        self._changed()
        return self

    def add_reference(self, reference: Reference) -> "Statement":
        """Add a reference to the claim, and return the statement."""
        self.references.append(reference)
        self._changed()
        return self

    def _changed(self):
        if self.entity is not None:
            self.entity.changed()

    def __repr__(self):
        text = f"Statement({self.main_snak!r}"
        if self.qualifiers:
            text += f", qualifiers={self.qualifiers!r}"
        if self.references:
            text += f", references={self.references!r}"
        return text + f", rank={self.rank.name})"


class Registry(Protocol):
    """What an entity tells of its changes: a KnowledgeBase."""

    def label_changed(self,
                      entity: "Entity",
                      language: str,
                      old: Optional[str],
                      new: str) -> None: ...

    def changed(self, entity: "Entity") -> None: ...


class Entity:
    """Base class for Items and Properties.

    Labels, descriptions and aliases are kept per language, by language
    tag (eg. "en", "fr", "en-GB").  An entity's labels are indexed by
    the KnowledgeBase it's registered with, if any, and kept current as
    they're changed, and the KnowledgeBase is told of every change, so
    it can save them.

    An entity merged into another redirects to it; a destroyed entity is
    kept, but left out of lookups."""

    def __init__(self, entity_id: Optional[str] = None):
        """Constructor.

        :param entity_id: String identifier, such as "Q42"; by default,
        a UUID."""
        if entity_id is None or len(entity_id) == 0:
            self.id: str = str(uuid.uuid4())
        else:
            self.id = entity_id

        self.labels: Dict[str, str] = {}
        self.descriptions: Dict[str, str] = {}
        self.aliases: Dict[str, List[str]] = {}
        self.statements: List[Statement] = []
        self.redirect: Optional[str] = None
        self.destroyed = False

        # KnowledgeBase this entity's registered with, if any.
        self.kb: Optional[Registry] = None

        # Revision, and document, as last stored: 0 and None if never.
        self.revision = 0
        self.stored: Optional[str] = None

    def get_id(self) -> str:
        return self.id

    def changed(self):
        """Tell the KnowledgeBase, if any, that the entity's changed."""
        if self.kb is not None:
            self.kb.changed(self)

    def set_label(self, label: str, language: str = "en"):
        old = self.labels.get(language)
        self.labels[language] = label
        if self.kb is not None:
            self.kb.label_changed(self, language, old, label)
        self.changed()

    def get_label(self, language: str = "en") -> Optional[str]:
        return self.labels.get(language)

    def set_description(self, description: str, language: str = "en"):
        self.descriptions[language] = description
        self.changed()

    def get_description(self, language: str = "en") -> Optional[str]:
        return self.descriptions.get(language)

    def add_alias(self, alias: str, language: str = "en"):
        self.aliases.setdefault(language, []).append(alias)
        self.changed()

    def get_aliases(self, language: str = "en") -> List[str]:
        return list(self.aliases.get(language, []))

    def add_statement(self, statement: Statement) -> Statement:
        self.statements.append(statement)
        statement.entity = self
        self.changed()
        return statement

    def remove_statement(self, statement: Statement):
        self.statements.remove(statement)
        statement.entity = None
        self.changed()

    def get_statements(self,
                       property_id: Optional[str] = None) -> List[Statement]:
        """Return the statements, or those claiming a property."""
        return [s for s in self.statements
                if property_id is None or s.get_property_id() == property_id]

    def claim(self,
              property_id: str,
              value: Value,
              rank: Optional[Rank] = None) -> Statement:
        """Add a statement claiming a property value, and return it."""
        return self.add_statement(
            Statement(Snak(property_id, value), rank=rank))

    def __repr__(self):
        label = self.labels.get("en")
        name = f" {label!r}" if label is not None else ""
        return f"{type(self).__name__}({self.id!r}{name})"


class Item(Entity):
    """Represents an item of knowledge.

    Items have properties describing them."""


class Property(Entity):
    """A property, whose values have a single datatype.

    Properties can have statements describing them too."""

    def __init__(self, datatype: Datatype, property_id: Optional[str] = None):
        """Constructor.

        :param datatype: Type of this property's values.
        :param property_id: String identifier, such as "P31"; by
        default, a UUID."""
        super().__init__(property_id)
        self.datatype: Datatype = datatype

    def set_datatype(self, data_type: Datatype):
        self.datatype = data_type
        self.changed()

    def get_datatype(self) -> Datatype:
        return self.datatype


# Value type for each datatype.
VALUE_TYPES = {
    Datatype.Item: EntityValue,
    Datatype.Property: EntityValue,
    Datatype.String: StringValue,
    Datatype.Text: TextValue,
    Datatype.Quantity: QuantityValue,
    Datatype.Time: TimeValue,
    Datatype.Url: StringValue,
    Datatype.ExternalId: StringValue,
}


# Documents: entities as JSON-compatible dicts, after Wikibase's JSON.
# Fields with default values are left out, and decimals are strings, so
# they keep their significant digits.

def encode_value(value: Value) -> Dict[str, Any]:
    """Return a value as a JSON-compatible dict."""
    if isinstance(value, EntityValue):
        return {"type": "entity", "id": value.entity_id}
    if isinstance(value, StringValue):
        return {"type": "string", "value": value.value}
    if isinstance(value, TextValue):
        return {"type": "text", "text": dict(value.text)}
    if isinstance(value, QuantityValue):
        d: Dict[str, Any] = {"type": "quantity", "amount": str(value.amount)}
        if value.unit is not None:
            d["unit"] = value.unit
        if value.lower is not None:
            d["lower"] = str(value.lower)
        if value.upper is not None:
            d["upper"] = str(value.upper)
        return d

    d = {"type": "time", "year": value.year}
    for name in _TIME_FIELDS:
        field = getattr(value, name)
        if field is not None:
            d[name] = field
    d["precision"] = value.precision.value
    if value.before:
        d["before"] = value.before
    if value.after:
        d["after"] = value.after
    if value.calendar != "gregorian":
        d["calendar"] = value.calendar
    return d


_TIME_FIELDS = ("month", "day", "hour", "minute", "second", "nanosecond")


def decode_value(d: Dict[str, Any]) -> Value:
    """Return the value a dict from encode_value() describes."""
    kind = d["type"]
    if kind == "entity":
        return EntityValue(d["id"])
    if kind == "string":
        return StringValue(d["value"])
    if kind == "text":
        text = TextValue()
        text.text = dict(d["text"])
        return text
    if kind == "quantity":
        return QuantityValue(d["amount"], d.get("unit"), d.get("lower"),
                             d.get("upper"))
    if kind == "time":
        return TimeValue(d["year"], *(d.get(name) for name in _TIME_FIELDS),
                         precision=TimePrecision(d["precision"]),
                         before=d.get("before", 0), after=d.get("after", 0),
                         calendar=d.get("calendar"))
    raise ValueError(f"unknown value type: {kind!r}")


def _encode_snak(snak: Snak) -> Dict[str, Any]:
    return {"property": snak.property_id, "value": encode_value(snak.value)}


def _decode_snak(d: Dict[str, Any]) -> Snak:
    return Snak(d["property"], decode_value(d["value"]))


def encode_statement(statement: Statement,
                     with_id: bool = True) -> Dict[str, Any]:
    """Return a statement as a JSON-compatible dict."""
    d: Dict[str, Any] = {"id": statement.id} if with_id else {}
    d["mainsnak"] = _encode_snak(statement.main_snak)
    if statement.rank != Rank.Normal:
        d["rank"] = statement.rank.name.lower()
    if statement.qualifiers:
        d["qualifiers"] = [_encode_snak(q) for q in statement.qualifiers]
    if statement.references:
        d["references"] = [[_encode_snak(a) for a in r.snaks]
                           for r in statement.references]
    return d


def decode_statement(d: Dict[str, Any]) -> Statement:
    """Return the statement a dict from encode_statement() describes."""
    return Statement(
        _decode_snak(d["mainsnak"]),
        [_decode_snak(q) for q in d.get("qualifiers", [])],
        [Reference(_decode_snak(a) for a in r)
         for r in d.get("references", [])],
        Rank[d.get("rank", "normal").capitalize()],
        d["id"])


def encode_entity(entity: Entity) -> Dict[str, Any]:
    """Return an entity as a JSON-compatible dict."""
    d: Dict[str, Any] = {"id": entity.id}
    if isinstance(entity, Property):
        d["type"] = "property"
        d["datatype"] = entity.datatype.name
    else:
        d["type"] = "item"
    if entity.labels:
        d["labels"] = dict(entity.labels)
    if entity.descriptions:
        d["descriptions"] = dict(entity.descriptions)
    aliases = {lang: list(a) for lang, a in entity.aliases.items() if a}
    if aliases:
        d["aliases"] = aliases
    if entity.statements:
        d["statements"] = [encode_statement(s) for s in entity.statements]
    if entity.redirect is not None:
        d["redirect"] = entity.redirect
    if entity.destroyed:
        d["destroyed"] = True
    return d


def decode_entity(d: Dict[str, Any]) -> Entity:
    """Return the entity a dict from encode_entity() describes."""
    if d["type"] == "property":
        entity: Entity = Property(Datatype[d["datatype"]], d["id"])
    elif d["type"] == "item":
        entity = Item(d["id"])
    else:
        raise ValueError(f"unknown entity type: {d['type']!r}")
    entity.labels = dict(d.get("labels", {}))
    entity.descriptions = dict(d.get("descriptions", {}))
    entity.aliases = {lang: list(a) for lang, a in d.get("aliases", {}).items()}
    for s in d.get("statements", []):
        entity.add_statement(decode_statement(s))
    entity.redirect = d.get("redirect")
    entity.destroyed = d.get("destroyed", False)
    return entity


def document(entity: Entity) -> str:
    """Return an entity as a JSON document, the same each time."""
    return json.dumps(encode_entity(entity), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def entity_links(entity: Entity) -> Iterator[Tuple[str, str, str, str]]:
    """Return an entity's links to other entities.

    Each is a statement identifier, property, target, and role: the
    statement's "main" snak, a "qualifier", or a "reference"."""
    for s in entity.statements:
        value = s.main_snak.value
        if isinstance(value, EntityValue):
            yield s.id, s.main_snak.property_id, value.entity_id, "main"
        for role, snaks in (("qualifier", s.qualifiers),
                            ("reference", [a for r in s.references
                                           for a in r.snaks])):
            for snak in snaks:
                if isinstance(snak.value, EntityValue):
                    yield s.id, snak.property_id, snak.value.entity_id, role


def referenced(entity: Entity) -> Set[str]:
    """Return the identifiers of the entities an entity refers to: its
    statements' properties, values, and units."""
    ids: Set[str] = set()
    for s in entity.statements:
        for snak in [s.main_snak, *s.qualifiers,
                     *(a for r in s.references for a in r.snaks)]:
            ids.add(snak.property_id)
            if isinstance(snak.value, EntityValue):
                ids.add(snak.value.entity_id)
            elif (isinstance(snak.value, QuantityValue)
                  and snak.value.unit is not None):
                ids.add(snak.value.unit)
    ids.discard(entity.id)
    return ids


def entity_strings(entity: Entity) -> Iterator[Tuple[str, str, str]]:
    """Return an entity's string values, normalised.

    Each is a statement identifier, property, and value, from the
    statement's main snak."""
    for s in entity.statements:
        value = s.main_snak.value
        if isinstance(value, StringValue):
            yield s.id, s.main_snak.property_id, normalise(value.value)


def entity_snaks(entity: Entity) -> Iterator[Tuple[Statement, Snak, str]]:
    """Return each of an entity's snaks, with its statement, and its role
    there: the statement's "main" snak, a "qualifier", or in a
    "reference"."""
    for s in entity.statements:
        yield s, s.main_snak, "main"
        for q in s.qualifiers:
            yield s, q, "qualifier"
        for r in s.references:
            for a in r.snaks:
                yield s, a, "reference"


class ConflictError(Exception):
    """An entity was changed by someone else since it was read, or one
    being created already exists."""

    def __init__(self, message: str, entity_id: str = ""):
        super().__init__(message)
        self.entity_id = entity_id


# DarqOS's own vocabulary: local properties, and a source item, with
# fixed identifiers, so they're the same in every knowledge base.

# Stated in (P248): a reference to a statement's source.
STATED_IN = "P248"

# DarqOS type: an entity's type, its kind, recorded when it's created
# as an item of that type (a String).
DARQOS_TYPE = "2ce9711e-43eb-4c69-8739-9346b526a71a"

# DarqOS instance type: on a class, the type of its instances, and its
# subclasses' (a String).
DARQOS_INSTANCE_TYPE = "eac9f098-d3da-4133-8fa1-e29ee5c0d9be"

# DarqOS, as the source of its own statements, such as the default
# instance types.
DARQOS = "23333c92-3ca8-4a6f-8bd1-1d1cf24dc5c0"

# Qualifiers for annotating statements by hand: Wikidata's start time,
# end time, point in time, and reasons for deprecated and preferred
# rank.  A source's statement can be given these, and stay the source's
# (see edit_statement): no source states them.
START_TIME = "P580"
END_TIME = "P582"
POINT_IN_TIME = "P585"
REASON_FOR_DEPRECATED_RANK = "P2241"
REASON_FOR_PREFERRED_RANK = "P7452"
ANNOTATIONS = frozenset({START_TIME, END_TIME, POINT_IN_TIME,
                         REASON_FOR_DEPRECATED_RANK,
                         REASON_FOR_PREFERRED_RANK})

# Inverse label item: on a property, an item whose label reads the
# property backwards, for showing what links to an entity.  "Creator"
# reads from a message to its author: read from the author, it's
# "created".  A property's statements are one way, so this is only for
# showing them.
INVERSE_LABEL = "P7087"

# Series ordinal: a statement's place in order, among its property's,
# such as a person's given names (a String, "1", "2" and so on).
SERIES_ORDINAL = "P1545"


def in_order(statements: Iterable[Statement]) -> List[Statement]:
    """Return statements in order: by series ordinal, as a number, where
    they have one, and then those without, as they are."""
    def key(statement: Statement) -> Tuple[int, int]:
        for q in statement.qualifiers:
            if q.property_id == SERIES_ORDINAL and isinstance(q.value, StringValue):
                try:
                    return 0, int(q.value.value.strip())
                except ValueError:
                    pass
        return 1, 0

    return sorted(statements, key=key)


# Editors: the sources of the operator's statements, by hand, one for
# each tool that edits entities, such as the entity lens.  A statement
# whose references are all to editors, or that has none, is the
# operator's (see is_operators).
ENTITY_EDITOR = "516eac85-1dc1-4f78-b9aa-441e431ecfc6"
EDITORS = frozenset({ENTITY_EDITOR})

# Reasons for deprecated rank, for a source's statement: replaced by one
# of the operator's, or removed, by hand.
REPLACED = "e49c7e56-ea4b-4b90-8d00-fe0510b0a7b3"
REMOVED = "efc0fb84-3ef0-43d1-81c8-a1b40a0a12d8"


# Kinds: the types of KB items (see doc/kb-service.rst).  An entity of
# no other kind is a darq.entity.
ENTITY_TYPE = "darq.entity"

# The class of a conversation: local, as Wikidata's (Q52943) is of spoken
# ones.
CONVERSATION = "4b73743b-b6d3-4091-9afc-71bcbc69d0e1"

# The class of tags, a local one: what's tagged with one links to it (see
# darq.items.HAS_TAG).
TAG_CLASS = "2dd4c81c-0508-42f4-b861-952340702cbf"

# Instant message, and URL.
INSTANT_MESSAGE = "Q30070565"
URL_CLASS = "Q42253"

# The classes of books' four levels (see doc/book.rst): Wikidata's work,
# expression and manifestation, in FRBR's sense, and individual copy of
# a book.
WORK_CLASS = "Q111955339"
EXPRESSION_CLASS = "Q111955324"
MANIFESTATION_CLASS = "Q111955382"
COPY_CLASS = "Q53731850"

# The other kinds, in order of priority, with the class of a new item of
# each: an entity that's of several kinds is of the first.
KINDS = [
    ("darq.person", "Q5"),
    ("darq.organisational-unit", "Q9261468"),
    ("darq.place", "Q2221906"),
    ("darq.organisation", "Q43229"),
    ("darq.group", "Q16334295"),
    ("darq.conversation", CONVERSATION),
    ("darq.message", INSTANT_MESSAGE),
    ("public.url", URL_CLASS),
    ("darq.book.work", WORK_CLASS),
    ("darq.book.expression", EXPRESSION_CLASS),
    ("darq.book.manifestation", MANIFESTATION_CLASS),
    ("darq.book.item", COPY_CLASS),
    ("darq.tag", TAG_CLASS),
]

KIND_TYPES = [ENTITY_TYPE] + [kind for kind, _ in KINDS]

# The instance type of each class, by default, with its label: human,
# organizational subdivision, organization, geographic location,
# address, group of humans, conversation, instant message, URL, and a
# book's four levels.  An address isn't a geographic location on
# Wikidata.
DEFAULT_INSTANCE_TYPES = [
    ("Q5", "human", "darq.person"),
    ("Q9261468", "organizational subdivision", "darq.organisational-unit"),
    ("Q43229", "organization", "darq.organisation"),
    ("Q2221906", "geographic location", "darq.place"),
    ("Q319608", "address", "darq.place"),
    ("Q16334295", "group of humans", "darq.group"),
    (CONVERSATION, "conversation", "darq.conversation"),
    (INSTANT_MESSAGE, "instant message", "darq.message"),
    (URL_CLASS, "URL", "public.url"),
    (WORK_CLASS, "work", "darq.book.work"),
    (EXPRESSION_CLASS, "expression", "darq.book.expression"),
    (MANIFESTATION_CLASS, "manifestation", "darq.book.manifestation"),
    (COPY_CLASS, "individual copy of a book", "darq.book.item"),
    (TAG_CLASS, "tag", "darq.tag"),
]

# Most classes searched for an entity's kind.
MAX_CLASSES = 1000


def _strings(entity: Entity, property_id: str) -> List[str]:
    """Return an entity's string values for a property: those with
    preferred rank, if there are any, or else those with normal rank."""
    statements = [s for s in entity.get_statements(property_id)
                  if isinstance(s.get_value(), StringValue)
                  and s.rank != Rank.Deprecated]
    preferred = [s for s in statements if s.rank == Rank.Preferred]
    values = [s.get_value() for s in (preferred or statements)]
    return [v.value for v in values if isinstance(v, StringValue)]


def _targets(entity: Entity, property_id: str) -> List[str]:
    """Return the entities a property's values refer to."""
    values = [s.get_value() for s in entity.get_statements(property_id)
              if s.rank != Rank.Deprecated]
    return [v.entity_id for v in values if isinstance(v, EntityValue)]


def recorded_type(entity: Entity) -> str:
    """Return an entity's recorded DarqOS type, or darq.entity if it has
    none, as links are grouped by it: classes are left to kind_of, so
    that the store needn't read any."""
    recorded = _strings(entity, DARQOS_TYPE)
    return recorded[0] if recorded else ENTITY_TYPE


def kind_of(entity: Entity, get: Callable[[str], Optional[Entity]]) -> str:
    """Return an entity's kind: the type of its item.

    It's its recorded DarqOS type, if it has one; otherwise, the first
    kind, in order of priority, that any of its classes, or their
    superclasses, have as their instance type; otherwise darq.entity.

    :param get: Returns an entity, by identifier, or None."""
    recorded = _strings(entity, DARQOS_TYPE)
    if recorded:
        return recorded[0]

    found: Set[str] = set()
    seen: Set[str] = set()
    level = _targets(entity, INSTANCE_OF)
    while level and len(seen) < MAX_CLASSES:
        following = []
        for class_id in level:
            if class_id in seen:
                continue
            seen.add(class_id)
            cls = get(class_id)
            if cls is not None:
                found.update(_strings(cls, DARQOS_INSTANCE_TYPE))
                following.extend(_targets(cls, SUBCLASS_OF))
        level = following
    for kind, _ in KINDS:
        if kind in found:
            return kind
    return ENTITY_TYPE


def stamp(entity: Entity, type_id: str, class_id: Optional[str] = None):
    """Make an entity an item of a kind: its only DarqOS type is that,
    with a class, the kind's, if it's one of the KB's, or one given.

    These statements are DarqOS's: an import that says the same adds its
    own reference to them (see restate)."""
    for statement in entity.get_statements(DARQOS_TYPE):
        entity.remove_statement(statement)
    claims: List[Tuple[str, Value]] = [(DARQOS_TYPE, StringValue(type_id))]
    cls = dict(KINDS).get(type_id, class_id)
    if cls is not None and not any(isinstance(value := s.get_value(), EntityValue) and value.entity_id == cls
                                   for s in entity.get_statements(INSTANCE_OF)):
        claims.insert(0, (INSTANCE_OF, EntityValue(cls)))
    for property_id, value in claims:
        entity.claim(property_id, value).add_reference(
            Reference().add(STATED_IN, EntityValue(DARQOS)))


def item_id(entity_id: str, kind: str) -> str:
    """Return the item identifier of an entity of a kind."""
    return identifier.make_item_id(kind, entity_id)


# Structs: entities as the protocol's.  Its enums are numbered from 0,
# for their defaults, so they're mapped by name.

_RANKS = {
    Rank.Normal: wire.Rank.NORMAL,
    Rank.Preferred: wire.Rank.PREFERRED,
    Rank.Deprecated: wire.Rank.DEPRECATED,
}

_DATATYPES = {
    Datatype.Item: wire.Datatype.ITEM,
    Datatype.Property: wire.Datatype.PROPERTY,
    Datatype.String: wire.Datatype.STRING,
    Datatype.Text: wire.Datatype.TEXT,
    Datatype.Quantity: wire.Datatype.QUANTITY,
    Datatype.Time: wire.Datatype.TIME,
    Datatype.Url: wire.Datatype.URL,
    Datatype.ExternalId: wire.Datatype.EXTERNAL_ID,
}

ROLES = {
    "main": wire.Role.MAIN,
    "qualifier": wire.Role.QUALIFIER,
    "reference": wire.Role.REFERENCE,
}


def _inverse(table: Dict[Any, Any]) -> Dict[Any, Any]:
    return {v: k for k, v in table.items()}


_FROM_RANKS = _inverse(_RANKS)
_FROM_DATATYPES = _inverse(_DATATYPES)
FROM_ROLES = _inverse(ROLES)


def datatype_to_wire(entity: Entity) -> wire.Datatype:
    """Return an entity's datatype as the protocol's: NONE for an item."""
    if isinstance(entity, Property):
        return _DATATYPES[entity.datatype]
    return wire.Datatype.NONE


def datatype_from_wire(datatype: wire.Datatype) -> Optional[Datatype]:
    """Return the protocol's datatype, or None for NONE."""
    return _FROM_DATATYPES.get(datatype)


def _texts(table: Dict[str, str]) -> List[wire.Text]:
    return [wire.Text(language, text) for language, text in table.items()]


def _text_table(texts: Iterable[wire.Text]) -> Dict[str, str]:
    return {t.language: t.text for t in texts}


def value_to_wire(value: Value) -> wire.Value:
    """Return a value as the protocol's struct."""
    if isinstance(value, EntityValue):
        return wire.Value(wire.ValueType.ENTITY, entity_id=value.entity_id)
    if isinstance(value, StringValue):
        return wire.Value(wire.ValueType.STRING, string_value=value.value)
    if isinstance(value, TextValue):
        return wire.Value(wire.ValueType.TEXT, text=_texts(value.text))
    if isinstance(value, QuantityValue):
        return wire.Value(
            wire.ValueType.QUANTITY, entity_id=value.unit or "",
            amount=str(value.amount),
            lower="" if value.lower is None else str(value.lower),
            upper="" if value.upper is None else str(value.upper))

    # Only the fields the precision covers are sent.
    p = value.precision.value

    def field(v: Optional[int], precision: TimePrecision) -> int:
        return v if v is not None and p >= precision.value else 0

    return wire.Value(
        wire.ValueType.TIME, year=value.year,
        month=field(value.month, TimePrecision.Months),
        day=field(value.day, TimePrecision.Days),
        hour=field(value.hour, TimePrecision.Hours),
        minute=field(value.minute, TimePrecision.Minutes),
        second=field(value.second, TimePrecision.Seconds),
        nanosecond=field(value.nanosecond, TimePrecision.Nanoseconds),
        precision=p, before=value.before, after=value.after,
        calendar="" if value.calendar == "gregorian" else value.calendar)


def value_from_wire(v: wire.Value) -> Value:
    """Return the value the protocol's struct describes.

    Raises ValueError if it isn't valid."""
    if v.type == wire.ValueType.ENTITY:
        return EntityValue(v.entity_id)
    if v.type == wire.ValueType.STRING:
        return StringValue(v.string_value)
    if v.type == wire.ValueType.TEXT:
        text = TextValue()
        text.text = _text_table(v.text)
        return text
    if v.type == wire.ValueType.QUANTITY:
        return QuantityValue(v.amount, v.entity_id or None, v.lower or None,
                             v.upper or None)

    precision = TimePrecision(v.precision)
    p = precision.value

    def field(value: int, needed: TimePrecision) -> Optional[int]:
        return value if p >= needed.value else None

    # A month or day of 0 isn't one: it's not known.
    return TimeValue(v.year,
                     field(v.month, TimePrecision.Months) or None,
                     field(v.day, TimePrecision.Days) or None,
                     field(v.hour, TimePrecision.Hours),
                     field(v.minute, TimePrecision.Minutes),
                     field(v.second, TimePrecision.Seconds),
                     field(v.nanosecond, TimePrecision.Nanoseconds),
                     precision, v.before, v.after, v.calendar or None)


def _snak_to_wire(snak: Snak) -> wire.Snak:
    return wire.Snak(snak.property_id, value_to_wire(snak.value))


def _snak_from_wire(snak: wire.Snak) -> Snak:
    return Snak(snak.property_id, value_from_wire(snak.value))


def statement_to_wire(statement: Statement) -> wire.Statement:
    """Return a statement as the protocol's struct."""
    return wire.Statement(
        statement.id, _snak_to_wire(statement.main_snak),
        [_snak_to_wire(q) for q in statement.qualifiers],
        [wire.Reference([_snak_to_wire(a) for a in r.snaks])
         for r in statement.references],
        _RANKS[statement.rank])


def statement_from_wire(statement: wire.Statement) -> Statement:
    """Return the statement the protocol's struct describes."""
    return Statement(
        _snak_from_wire(statement.main_snak),
        [_snak_from_wire(q) for q in statement.qualifiers],
        [Reference(_snak_from_wire(a) for a in r.snaks)
         for r in statement.references],
        _FROM_RANKS[statement.rank],
        statement.id or None)


def entity_to_wire(entity: Entity) -> wire.Entity:
    """Return an entity as the protocol's struct."""
    if isinstance(entity, Property):
        kind, datatype = wire.Kind.PROPERTY, _DATATYPES[entity.datatype]
    else:
        kind, datatype = wire.Kind.ITEM, wire.Datatype.NONE
    return wire.Entity(
        entity.id, kind, datatype, entity.revision,
        _texts(entity.labels), _texts(entity.descriptions),
        [wire.Text(language, a)
         for language, aliases in entity.aliases.items() for a in aliases],
        [statement_to_wire(s) for s in entity.statements],
        entity.redirect or "", entity.destroyed)


def entity_from_wire(e: wire.Entity) -> Entity:
    """Return the entity the protocol's struct describes.

    Raises ValueError if it isn't valid."""
    if e.kind == wire.Kind.PROPERTY:
        if e.datatype not in _FROM_DATATYPES:
            raise ValueError(f"property {e.id} has no datatype")
        entity: Entity = Property(_FROM_DATATYPES[e.datatype], e.id or None)
    else:
        entity = Item(e.id or None)
    entity.labels = _text_table(e.labels)
    entity.descriptions = _text_table(e.descriptions)
    for alias in e.aliases:
        entity.aliases.setdefault(alias.language, []).append(alias.text)
    for s in e.statements:
        entity.add_statement(statement_from_wire(s))
    entity.redirect = e.redirect or None
    entity.destroyed = e.destroyed
    entity.revision = e.revision
    return entity


class Restated:
    """What restate() did to an entity's statements: true if it changed
    the entity."""

    def __init__(self):
        # The source's statements: those added, and those the entity
        # had already, as they are in it now.
        self.added: List[Statement] = []
        self.matched: List[Statement] = []

        # Statements the source no longer makes, which lost its
        # reference; and those of them kept, as the operator's, though
        # they had no other references (see restate()).
        self.dropped: List[Statement] = []
        self.kept: List[Statement] = []

        self.changed = False

    def __bool__(self) -> bool:
        return self.changed


def combine_statements(entity: Entity) -> bool:
    """Make statements of an entity that say the same thing one statement,
    with the references of each, and return whether there were any.

    They say the same if they've the same value, the same qualifiers, and
    the same rank, as a merge leaves when both entities said it: one of
    each, cited to each of their sources."""
    kept: Dict[Tuple[str, Tuple[str, ...], Rank], Statement] = {}
    combined = False
    for statement in list(entity.statements):
        key = (_snak_key(statement.main_snak), tuple(_qualifiers_key(statement, None)), statement.rank)
        first = kept.get(key)
        if first is None:
            kept[key] = statement
            continue
        cited = {_reference_key(r) for r in first.references}
        for reference in statement.references:
            if _reference_key(reference) not in cited:
                first.references.append(reference)
                cited.add(_reference_key(reference))
        entity.remove_statement(statement)
        combined = True
    return combined


def _reference_key(reference: Reference) -> Tuple[str, ...]:
    return tuple(sorted(_snak_key(a) for a in reference.snaks))


def restate(entity: Entity,
            source_id: str,
            statements: Iterable[Statement],
            qualifiers: Optional[AbstractSet[str]] = None) -> Restated:
    """Make an entity's statements from a source be some statements.

    As in Wikibase, a statement can have several references, each to a
    source (stated in, P248).  A statement that's the same as one the
    entity has, with references, gains a reference to the source,
    rather than being added again; others are added, with one.  A
    statement the source no longer makes loses its reference, and is
    removed if it has none left.  So a statement the operator has stated
    with an editor (see EDITORS) gains the source's reference too, and
    keeps the editor's once the source no longer makes it.  The entity's
    statements without any references are kept as they are: a statement
    that's the same as one of those isn't added.

    Statements are the same if they have the same value, and the same
    qualifiers: all of them, or, with qualifiers, those of its
    properties, and of the others the source's statement has.  So a
    statement can have qualifiers added by hand, such as start and end
    times, and still be the source's.  One the source no longer makes,
    with any qualifier not of those properties, or deprecated rank, is
    kept, as the operator's, rather than removed.

    Rank isn't compared: a source's preferred rank is given to a
    statement it matches with normal rank, but a rank's never lowered.

    :param qualifiers: The qualifier properties the source's statements
                       always say all of.
    :returns: What it did."""
    before = document(entity)
    result = Restated()
    wanted = list(statements)

    def same(have: Statement, want: Statement) -> bool:
        if _snak_key(have.main_snak) != _snak_key(want.main_snak):
            return False
        if qualifiers is None:
            compared = None
        else:
            compared = set(qualifiers) | {q.property_id for q in want.qualifiers}
        return _qualifiers_key(have, compared) == _qualifiers_key(want, compared)

    def annotated(statement: Statement) -> bool:
        return qualifiers is not None and (
            statement.rank == Rank.Deprecated
            or any(q.property_id not in qualifiers for q in statement.qualifiers))

    # Each wanted statement's match: the source's own, or else another
    # source's, or else the operator's.
    def preference(s: Statement) -> int:
        return 0 if is_sourced(s, source_id) else 1 if s.references else 2

    candidates = sorted(entity.statements, key=preference)
    pairs = [(want, next((s for s in candidates if same(s, want)), None))
             for want in wanted]
    matched = {id(have) for _, have in pairs if have is not None}

    # Statements the source no longer makes lose its references.
    for s in list(entity.statements):
        if is_sourced(s, source_id) and id(s) not in matched:
            unsource(s, source_id)
            result.dropped.append(s)
            if not s.references:
                if annotated(s):
                    result.kept.append(s)
                else:
                    entity.remove_statement(s)

    for want, have in pairs:
        if have is None:
            have = next((s for s in result.added if same(s, want)), None)
        if have is None:
            copy = Statement(want.main_snak, want.qualifiers, want.references, want.rank)
            if not is_sourced(copy, source_id):
                copy.add_reference(Reference().add(STATED_IN,
                                                   EntityValue(source_id)))
            entity.add_statement(copy)
            result.added.append(copy)
            continue
        if have.references:
            if not is_sourced(have, source_id):
                have.add_reference(Reference().add(STATED_IN,
                                                   EntityValue(source_id)))
            if want.rank == Rank.Preferred and have.rank == Rank.Normal:
                have.rank = Rank.Preferred
        if not any(s is have for s in [*result.added, *result.matched]):
            result.matched.append(have)

    result.changed = document(entity) != before
    return result


def _cites(reference: Reference, source_id: str) -> bool:
    """Return whether a reference is to a source."""
    return any(a.property_id == STATED_IN
               and isinstance(a.value, EntityValue)
               and a.value.entity_id == source_id
               for a in reference.snaks)


def _snak_key(snak: Snak) -> str:
    """Return a snak, for comparing."""
    return json.dumps(_encode_snak(snak), sort_keys=True)


def _qualifiers_key(statement: Statement,
                    properties: Optional[AbstractSet[str]]) -> List[str]:
    """Return a statement's qualifiers, or those of some properties, for
    comparing."""
    return sorted(_snak_key(q) for q in statement.qualifiers
                  if properties is None or q.property_id in properties)


def is_sourced(statement: Statement, source_id: str) -> bool:
    """Return whether a statement has a reference to a source."""
    return any(_cites(r, source_id) for r in statement.references)


def is_operators(statement: Statement) -> bool:
    """Return whether a statement is the operator's: whether its
    references, if any, are all to editors (see EDITORS)."""
    return all(any(_cites(r, editor) for editor in EDITORS)
               for r in statement.references)


def unsource(statement: Statement, source_id: str):
    """Remove a statement's references to a source."""
    statement.references = [r for r in statement.references
                            if not _cites(r, source_id)]


# Edits: changes to an entity, as operations, so that a change made to
# one revision can be made again to a later one, as a rebase does,
# unless what it changes has been changed there too.  Each knows what it
# changes as it was, and as it's to be: it's made if it's as it was, and
# does nothing if it's already as it's to be; otherwise it conflicts.
#
# The functions after them make an operator's changes as edits, by an
# editor, keeping a source's statements its own (see change_value).

class EditConflict(Exception):
    """What an edit changes has been changed, since it was made."""

    def __init__(self, edit: "Edit", message: str):
        super().__init__(message)
        self.edit = edit


def _value_key(value: Value) -> str:
    return json.dumps(encode_value(value), sort_keys=True)


def _statement_key(statement: Statement) -> str:
    return json.dumps(encode_statement(statement), sort_keys=True)


def copy_statement(statement: Statement) -> Statement:
    """Return a copy of a statement, with its identifier."""
    return decode_statement(encode_statement(statement))


def copy_entity(entity: Entity) -> Entity:
    """Return a copy of an entity, at its revision."""
    copy = decode_entity(encode_entity(entity))
    copy.revision = entity.revision
    return copy


def find_statement(entity: Entity, statement_id: str) -> Optional[Statement]:
    """Return an entity's statement with an identifier, or None."""
    return next((s for s in entity.statements if s.id == statement_id), None)


class Edit:
    """A change to an entity."""

    def apply(self, entity: Entity, force: bool = False):
        """Make the change to an entity, or raise EditConflict.

        :param force: Make it even if what it changes has been changed,
                      if that's still there."""
        raise NotImplementedError

    def inverse(self) -> "Edit":
        """Return the edit that undoes this one."""
        raise NotImplementedError

    def target(self) -> str:
        """Return what it changes: "labels:en", say, or "statement:" and
        the statement's identifier."""
        raise NotImplementedError

    def _statement(self, entity: Entity, statement_id: str) -> Statement:
        statement = find_statement(entity, statement_id)
        if statement is None:
            raise EditConflict(self, f"statement {statement_id} has been removed")
        return statement


class SetText(Edit):
    """Set, or remove (with None), a label or description in a language."""

    def __init__(self, field: str, language: str,
                 old: Optional[str], new: Optional[str]):
        assert field in ("labels", "descriptions")
        self.field = field
        self.language = language
        self.old = old
        self.new = new

    def apply(self, entity: Entity, force: bool = False):
        table: Dict[str, str] = getattr(entity, self.field)
        have = table.get(self.language)
        if have == self.new:
            return
        if have != self.old and not force:
            raise EditConflict(self, f"{self.target()} has been changed")
        if self.new is None:
            del table[self.language]
        else:
            table[self.language] = self.new

    def inverse(self) -> Edit:
        return SetText(self.field, self.language, self.new, self.old)

    def target(self) -> str:
        return f"{self.field}:{self.language}"


class SetAliases(Edit):
    """Set an entity's aliases in a language."""

    def __init__(self, language: str, old: Iterable[str], new: Iterable[str]):
        self.language = language
        self.old = list(old)
        self.new = list(new)

    def apply(self, entity: Entity, force: bool = False):
        have = entity.aliases.get(self.language, [])
        if have == self.new:
            return
        if have != self.old and not force:
            raise EditConflict(self, f"{self.target()} have been changed")
        if self.new:
            entity.aliases[self.language] = list(self.new)
        else:
            entity.aliases.pop(self.language, None)

    def inverse(self) -> Edit:
        return SetAliases(self.language, self.new, self.old)

    def target(self) -> str:
        return f"aliases:{self.language}"


class AddStatement(Edit):
    """Add a statement, at a position (or last)."""

    def __init__(self, statement: Statement, index: Optional[int] = None):
        self.statement = copy_statement(statement)
        self.index = index

    def apply(self, entity: Entity, force: bool = False):
        have = find_statement(entity, self.statement.id)
        if have is not None:
            if _statement_key(have) == _statement_key(self.statement):
                return
            if not force:
                raise EditConflict(self, f"statement {self.statement.id} exists")
            entity.remove_statement(have)
        statement = copy_statement(self.statement)
        index = len(entity.statements) if self.index is None else min(self.index, len(entity.statements))
        entity.statements.insert(index, statement)
        statement.entity = entity

    def inverse(self) -> Edit:
        return RemoveStatement(self.statement, self.index)

    def target(self) -> str:
        return f"statement:{self.statement.id}"


class RemoveStatement(Edit):
    """Remove a statement, as it was."""

    def __init__(self, statement: Statement, index: Optional[int] = None):
        self.statement = copy_statement(statement)
        self.index = index

    def apply(self, entity: Entity, force: bool = False):
        have = find_statement(entity, self.statement.id)
        if have is None:
            return
        if _statement_key(have) != _statement_key(self.statement) and not force:
            raise EditConflict(self, f"statement {self.statement.id} has been changed")
        entity.remove_statement(have)

    def inverse(self) -> Edit:
        return AddStatement(self.statement, self.index)

    def target(self) -> str:
        return f"statement:{self.statement.id}"


class SetValue(Edit):
    """Set a statement's value."""

    def __init__(self, statement_id: str, old: Value, new: Value):
        self.statement_id = statement_id
        self.old = old
        self.new = new

    def apply(self, entity: Entity, force: bool = False):
        statement = self._statement(entity, self.statement_id)
        have = _value_key(statement.get_value())
        if have == _value_key(self.new):
            return
        if have != _value_key(self.old) and not force:
            raise EditConflict(self, f"statement {self.statement_id}'s value has been changed")
        statement.main_snak = Snak(statement.get_property_id(), self.new)

    def inverse(self) -> Edit:
        return SetValue(self.statement_id, self.new, self.old)

    def target(self) -> str:
        return f"statement:{self.statement_id}"


class SetRank(Edit):
    """Set a statement's rank."""

    def __init__(self, statement_id: str, old: Rank, new: Rank):
        self.statement_id = statement_id
        self.old = old
        self.new = new

    def apply(self, entity: Entity, force: bool = False):
        statement = self._statement(entity, self.statement_id)
        if statement.rank == self.new:
            return
        if statement.rank != self.old and not force:
            raise EditConflict(self, f"statement {self.statement_id}'s rank has been changed")
        statement.rank = self.new

    def inverse(self) -> Edit:
        return SetRank(self.statement_id, self.new, self.old)

    def target(self) -> str:
        return f"statement:{self.statement_id}"


class SetQualifier(Edit):
    """Add a qualifier to a statement (old is None), remove one (new is
    None), or change one."""

    def __init__(self, statement_id: str, old: Optional[Snak], new: Optional[Snak]):
        assert old is not None or new is not None
        self.statement_id = statement_id
        self.old = old
        self.new = new

    def apply(self, entity: Entity, force: bool = False):
        self.change(self._statement(entity, self.statement_id), force)

    def change(self, statement: Statement, force: bool = False):
        """Make the change to a statement."""
        keys = [_snak_key(q) for q in statement.qualifiers]
        old = None if self.old is None else _snak_key(self.old)
        new = None if self.new is None else _snak_key(self.new)
        if old is not None and old in keys:
            n = keys.index(old)
            if self.new is None:
                del statement.qualifiers[n]
            else:
                statement.qualifiers[n] = self.new
        elif new is None or new in keys:
            # It's been removed, or it's already as it's to be.
            return
        elif old is None or force:
            assert self.new is not None
            statement.qualifiers.append(self.new)
        else:
            raise EditConflict(self, f"statement {self.statement_id}'s qualifier has been changed")

    def inverse(self) -> Edit:
        return SetQualifier(self.statement_id, self.new, self.old)

    def target(self) -> str:
        return f"statement:{self.statement_id}"


def apply_edits(entity: Entity, edits: Iterable[Edit], force: bool = False) -> Entity:
    """Return a copy of an entity with edits made, all or none.

    :raises EditConflict: if one can't be made."""
    copy = copy_entity(entity)
    for edit in edits:
        edit.apply(copy, force)
    return copy


def invert(edits: Iterable[Edit]) -> List[Edit]:
    """Return the edits that undo some edits."""
    return [edit.inverse() for edit in reversed(list(edits))]


def _get_statement(entity: Entity, statement_id: str) -> Statement:
    statement = find_statement(entity, statement_id)
    if statement is None:
        raise LookupError(f"{entity.id} has no statement {statement_id}")
    return statement


def _hand_reasons(statement: Statement) -> List[Snak]:
    """Return a statement's reasons for deprecated rank given by hand."""
    return [q for q in statement.qualifiers
            if q.property_id == REASON_FOR_DEPRECATED_RANK
            and isinstance(q.value, EntityValue)
            and q.value.entity_id in (REPLACED, REMOVED)]


def _deprecate(statement: Statement, reason: str) -> List[Edit]:
    """Return the edits that deprecate a source's statement, by hand."""
    edits: List[Edit] = []
    if statement.rank != Rank.Deprecated:
        edits.append(SetRank(statement.id, statement.rank, Rank.Deprecated))
    for q in _hand_reasons(statement):
        edits.append(SetQualifier(statement.id, q, None))
    edits.append(SetQualifier(statement.id, None, Snak(REASON_FOR_DEPRECATED_RANK, EntityValue(reason))))
    return edits


def _stated_by(editor: str) -> Reference:
    return Reference().add(STATED_IN, EntityValue(editor))


def _replace(entity: Entity, statement: Statement, changed: Statement, editor: str) -> List[Edit]:
    """Return the edits that replace a source's statement with a changed
    copy, the operator's, stated by an editor: it's deprecated, and the
    copy added after it."""
    copy = Statement(changed.main_snak,
                     [q for q in changed.qualifiers if q not in _hand_reasons(changed)],
                     [_stated_by(editor)],
                     rank=Rank.Normal if changed.rank == Rank.Deprecated else changed.rank)
    index = entity.statements.index(statement) + 1
    return _deprecate(statement, REPLACED) + [AddStatement(copy, index)]


def add_statement(entity: Entity, property_id: str, value: Value, editor: str) -> List[Edit]:
    """Return the edits that add an operator's statement, stated by an
    editor."""
    return [AddStatement(Statement(Snak(property_id, value), references=[_stated_by(editor)]))]


def change_value(entity: Entity, statement_id: str, value: Value, editor: str) -> List[Edit]:
    """Return the edits that change a statement's value, by an editor.

    An operator's statement (see is_operators) is changed.  A source's
    is deprecated, with the reason that it's been replaced, and an
    operator's statement with the new value, stated by the editor, added
    after it: changing it would only have a re-import state it again, as
    a new statement."""
    statement = _get_statement(entity, statement_id)
    if _value_key(statement.get_value()) == _value_key(value):
        return []
    if is_operators(statement):
        return [SetValue(statement_id, statement.get_value(), value)]
    changed = copy_statement(statement)
    changed.main_snak = Snak(statement.get_property_id(), value)
    return _replace(entity, statement, changed, editor)


def change_qualifier(entity: Entity, statement_id: str,
                     old: Optional[Snak], new: Optional[Snak], editor: str) -> List[Edit]:
    """Return the edits that add, change or remove a statement's qualifier,
    by an editor.

    A source's statement is changed if it's an annotation (see
    ANNOTATIONS), which a source doesn't state, so it stays the source's;
    for any other, it's replaced, as change_value() does."""
    statement = _get_statement(entity, statement_id)
    properties = {s.property_id for s in (old, new) if s is not None}
    if is_operators(statement) or properties <= ANNOTATIONS:
        return [SetQualifier(statement_id, old, new)]
    changed = copy_statement(statement)
    SetQualifier(statement_id, old, new).change(changed)
    return _replace(entity, statement, changed, editor)


def change_rank(entity: Entity, statement_id: str, rank: Rank) -> List[Edit]:
    """Return the edits that set a statement's rank: a source's too, as a
    re-import keeps a rank set by hand.  Raising a statement deprecated by
    hand removes the reason."""
    statement = _get_statement(entity, statement_id)
    if statement.rank == rank:
        return []
    edits: List[Edit] = [SetRank(statement_id, statement.rank, rank)]
    if rank != Rank.Deprecated:
        edits += [SetQualifier(statement_id, q, None) for q in _hand_reasons(statement)]
    return edits


def remove_statement(entity: Entity, statement_id: str) -> List[Edit]:
    """Return the edits that remove a statement.

    An operator's statement (see is_operators) is removed.  A source's,
    even if an editor states it too, is deprecated, with
    the reason that it's been removed: a re-import would only state it
    again."""
    statement = _get_statement(entity, statement_id)
    if is_operators(statement):
        return [RemoveStatement(statement, entity.statements.index(statement))]
    if statement.rank == Rank.Deprecated and any(
            isinstance(q.value, EntityValue) and q.value.entity_id == REMOVED
            for q in _hand_reasons(statement)):
        return []
    return _deprecate(statement, REMOVED)


# Queries

class Variable(NamedTuple):
    """A query's variable, by its number, from 1."""

    number: int

    def __repr__(self) -> str:
        return f"?{self.number}"


class AnyValue:
    """Any value at all, as a pattern's object: see ANY."""

    def __repr__(self) -> str:
        return "ANY"


ANY = AnyValue()


class Repeat(Enum):
    """How many times a pattern's property is followed, from its subject
    to its object: as SPARQL's P, P+ and P*."""

    Once = 0
    OneOrMore = 1
    ZeroOrMore = 2


# A pattern's subject: a variable, or an entity's identifier.
Subject = Union[Variable, str]

# A pattern's object: a variable, an entity, a string value (matched
# normalised), or ANY.
Term = Union[Variable, EntityValue, StringValue, AnyValue]


class Pattern(NamedTuple):
    """A query's pattern: an entity, the subject, with a statement that
    has a snak of a property, in a role ("main", "qualifier", or
    "reference"), with a value, the object.

    Patterns with the same statement variable match the same statement.
    A repeated pattern follows its property from its subject to its
    object, as subclass of (P279) leads from a class to its
    superclasses; it needs a constant at one end.  A negated pattern
    must not match: its variables that the query's other patterns don't
    bind match anything."""

    subject: Subject
    property_id: str
    object: Term
    role: str = "main"
    statement: Optional[Variable] = None
    repeat: Repeat = Repeat.Once
    negated: bool = False

    def variables(self) -> List[Variable]:
        """Return the pattern's variables."""
        terms = [self.subject, self.object, self.statement]
        return [t for t in terms if isinstance(t, Variable)]


class Query:
    """A query: patterns that must all match, and the variables whose
    values are its results, as rows.

    Built from patterns, such as, for the people employed by an
    organisation located, perhaps indirectly, in Queensland,

        q = Query()
        p, o = q.variables(2)
        q.match(p, "P108", o).match(o, "P131", "Q36074", repeat=Repeat.OneOrMore)
        q.select(p)

    and answered by the service (see KB.query).  An object given as a
    string is an entity's identifier: a string value is a StringValue.
    Deprecated statements match only if deprecated is set."""

    def __init__(self, deprecated: bool = False):
        self.patterns: List[Pattern] = []
        self.selected: List[Variable] = []
        self.deprecated = deprecated
        self.count = 0

    def variable(self) -> Variable:
        """Return a new variable."""
        self.count += 1
        return Variable(self.count)

    def variables(self, n: int) -> List[Variable]:
        """Return n new variables."""
        return [self.variable() for _ in range(n)]

    def match(self,
              subject: Subject,
              property_id: str,
              value: Union[Term, str],
              role: str = "main",
              statement: Optional[Variable] = None,
              repeat: Repeat = Repeat.Once) -> "Query":
        """Add a pattern that must match."""
        return self.add(Pattern(subject, property_id, _term(value), role, statement, repeat))

    def exclude(self,
                subject: Subject,
                property_id: str,
                value: Union[Term, str],
                role: str = "main",
                statement: Optional[Variable] = None,
                repeat: Repeat = Repeat.Once) -> "Query":
        """Add a pattern that must not match."""
        return self.add(Pattern(subject, property_id, _term(value), role, statement, repeat, True))

    def add(self, pattern: Pattern) -> "Query":
        if pattern.role not in ROLES:
            raise ValueError(f"no such role: {pattern.role}")
        self.patterns.append(pattern)
        self.count = max([self.count, *(v.number for v in pattern.variables())])
        return self

    def select(self, *variables: Variable) -> "Query":
        """Set the variables whose values are the results, in order: by
        default, all those the patterns that must match have."""
        self.selected = list(variables)
        return self


def _term(value: Union[Term, str]) -> Term:
    return EntityValue(value) if isinstance(value, str) else value


_REPEATS = {
    Repeat.Once: wire.Repeat.ONCE,
    Repeat.OneOrMore: wire.Repeat.ONE_OR_MORE,
    Repeat.ZeroOrMore: wire.Repeat.ZERO_OR_MORE,
}
_FROM_REPEATS = _inverse(_REPEATS)


def _term_to_wire(term: Union[Term, str]) -> wire.Term:
    if isinstance(term, Variable):
        return wire.Term(wire.TermType.VARIABLE, term.number)
    if isinstance(term, str):
        return wire.Term(wire.TermType.ENTITY, value=term)
    if isinstance(term, EntityValue):
        return wire.Term(wire.TermType.ENTITY, value=term.entity_id)
    if isinstance(term, StringValue):
        return wire.Term(wire.TermType.STRING, value=term.value)
    return wire.Term(wire.TermType.ANY)


def _term_from_wire(term: wire.Term) -> Term:
    if term.type == wire.TermType.VARIABLE:
        if term.variable == 0:
            raise ValueError("variables are numbered from 1")
        return Variable(term.variable)
    if term.type == wire.TermType.ENTITY:
        if not term.value:
            raise ValueError("no entity identifier")
        return EntityValue(term.value)
    if term.type == wire.TermType.STRING:
        return StringValue(term.value)
    return ANY


def query_to_wire(query: Query) -> Tuple[List[wire.Pattern], List[int]]:
    """Return a query's patterns, and its selected variables' numbers,
    as the protocol's."""
    patterns = [wire.Pattern(_term_to_wire(p.subject), p.property_id, _term_to_wire(p.object),
                             ROLES[p.role], p.statement.number if p.statement else 0,
                             _REPEATS[p.repeat], p.negated)
                for p in query.patterns]
    return patterns, [v.number for v in query.selected]


def query_from_wire(patterns: Iterable[wire.Pattern],
                    select: Iterable[int],
                    deprecated: bool) -> Query:
    """Return a query from the protocol's patterns and selected
    variables' numbers.

    :raises ValueError: if a term isn't valid."""
    query = Query(deprecated)
    for p in patterns:
        subject = _term_from_wire(p.subject)
        if isinstance(subject, EntityValue):
            subject = subject.entity_id
        elif not isinstance(subject, Variable):
            raise ValueError("a subject is a variable or an entity")
        query.add(Pattern(subject, p.property_id, _term_from_wire(p.object), FROM_ROLES[p.role],
                          Variable(p.statement) if p.statement else None, _FROM_REPEATS[p.repeat],
                          p.negated))
    if any(n == 0 for n in select):
        raise ValueError("variables are numbered from 1")
    query.select(*(Variable(n) for n in select))
    return query


class KB:
    """Client of the Knowledge Base service.

    Entities are returned as Items and Properties, which can be changed,
    and put back.  A lookup returns identifiers, getting every batch.

    Entities of a kind are written through the kind's type
    implementation (see doc/types.rst): given its writer, put, merge
    and destroy go through it, and the KB service is only read.  The
    writer records what they do in History, as their context describes
    it: each call's, or else the KB's (see doc/history.rst)."""

    def __init__(self, client: wire.KB.Client,
                 writer: Optional[wire.EntityWriter.Client] = None,
                 context: Optional[wire.Context] = None):
        self.client = client
        self.writer = writer
        self.context = context

    @classmethod
    def connect(cls, timeout: Optional[float] = None,
                writer: Optional[wire.EntityWriter.Client] = None) -> "KB":
        """Connect to the service, darq.kb."""
        return cls(wire.KB.Client(system.connect("darq.kb"), timeout), writer)

    @staticmethod
    def _check(status: wire.Status, what: str):
        """Raise the exception for a status, unless it's OK."""
        if status == wire.Status.OK:
            return
        if status == wire.Status.CONFLICT:
            raise ConflictError(f"{what}: changed, or already exists")
        if status == wire.Status.NOT_FOUND:
            raise LookupError(f"{what}: not found")
        if status == wire.Status.TOO_COSTLY:
            raise TimeoutError(f"{what}: ran past the service's time limit")
        raise ValueError(f"{what}: {status.name.lower().replace('_', ' ')}")

    def get_entities(self,
                     ids: Iterable[str],
                     follow: bool = True,
                     languages: Iterable[str] = ()) \
            -> Tuple[List[Entity], Dict[str, str]]:
        """Return the entities with some identifiers, that exist, and the
        labels, in a chain of languages, of the entities they refer to."""
        response = self.client.get_entities(list(ids), follow,
                                            list(languages))
        self._check(response.status, "get")
        return ([entity_from_wire(e) for e in response.entities],
                {label.entity_id: label.text for label in response.labels})

    def get(self, entity_id: str, follow: bool = True) -> Optional[Entity]:
        """Return an entity, or None if there's none."""
        entities, _ = self.get_entities([entity_id], follow)
        return entities[0] if entities else None

    def put(self, *entities: Entity, context: Optional[wire.Context] = None) -> List[Entity]:
        """Write entities, all or none, and set their new revisions.

        Raises ConflictError if one's changed since it was read, or is
        new and exists; ValueError if a statement doesn't fit its
        property."""
        wired = [entity_to_wire(e) for e in entities]
        if self.writer is not None:
            response = self.writer.write_entities(wired, context or self.context)
        else:
            response = self.client.put_entities(wired)
        self._check(response.status, response.failed_id or "put")
        for entity, written in zip(entities, response.entities):
            entity.revision = written.revision
        return list(entities)

    def create(self, type_id: str, *entities: Entity, context: Optional[wire.Context] = None) -> List[Entity]:
        """Create new entities of a kind, all or none, with the
        identifiers they have, and set their revisions.

        This needs a writer, the kind's implementation.  Raises
        ConflictError if one exists; ValueError if a statement doesn't
        fit its property."""
        if self.writer is None:
            raise ValueError("create: there's no writer")
        wired = [entity_to_wire(e) for e in entities]
        response = self.writer.create_entities(type_id, wired, context or self.context)
        self._check(response.status, response.failed_id or "create")
        for entity, written in zip(entities, response.entities):
            entity.revision = written.revision
        return list(entities)

    def ensure(self, *entities: Entity) -> Dict[str, Entity]:
        """Find, or create, some entities: those that exist are left as
        they are, and those that don't are put.

        :returns: The entities as they are, by identifier."""
        for _ in range(3):
            found, _labels = self.get_entities([e.id for e in entities],
                                               follow=False)
            have = {e.id: e for e in found}
            missing = [e for e in entities if e.id not in have]
            if not missing:
                return have
            try:
                self.put(*missing)
            except ConflictError:
                # Someone else created one: read them again.
                continue
            have.update((e.id, e) for e in missing)
            return have
        raise ConflictError("ensure: they keep changing")

    def state(self,
              entity_id: str,
              source_id: str,
              statements: Iterable[Statement]) -> Entity:
        """Make an entity's statements from a source be some statements.

        The statements are given references to the source (stated in,
        P248), and replace those it has from it, if they're different;
        its other statements are kept, as an Import keeps them.

        :returns: The entity, as it is.
        :raises LookupError: if there's no such entity."""
        new = list(statements)
        for _ in range(3):
            entity = self.get(entity_id, follow=False)
            if entity is None:
                raise LookupError(f"{entity_id}: not found")
            if not restate(entity, source_id, new):
                return entity
            try:
                self.put(entity)
            except ConflictError:
                continue
            return entity
        raise ConflictError(f"{entity_id}: it keeps changing", entity_id)

    def merge(self, old_id: str, new_id: str, context: Optional[wire.Context] = None):
        """Merge one entity into another."""
        if self.writer is not None:
            status = self.writer.merge_entity(old_id, new_id, context or self.context).status
        else:
            status = self.client.merge(old_id, new_id).status
        self._check(status, f"merge {old_id} into {new_id}")

    def destroy(self, entity_id: str, context: Optional[wire.Context] = None):
        """Mark an entity destroyed."""
        if self.writer is not None:
            status = self.writer.destroy_entity(entity_id, context or self.context).status
        else:
            status = self.client.destroy(entity_id).status
        self._check(status, entity_id)

    def _all(self, lookup, what: str) -> Iterator[str]:
        """Return the identifiers from every batch of a lookup."""
        after = ""
        while True:
            response = lookup(after)
            self._check(response.status, what)
            yield from response.ids
            if not response.ids:
                return
            after = response.ids[-1]

    def find_by_label(self,
                      text: str,
                      language: str = "en",
                      words: bool = False,
                      class_id: str = "") -> Iterator[str]:
        """Find entities by label, or, with words, by the words of their
        labels and aliases."""
        match = wire.Match.WORDS if words else wire.Match.EXACT
        return self._all(lambda after: self.client.find_by_label(
            text, language, match, class_id, 0, after), text)

    def find_by_value(self, property_id: str, value: str) -> Iterator[str]:
        """Find entities with a string value for a property."""
        return self._all(lambda after: self.client.find_by_value(
            property_id, value, 0, after), property_id)

    def find_links(self,
                   target_id: str,
                   property_id: Optional[str] = None,
                   role: str = "main") -> Iterator[str]:
        """Find the entities that link to one."""
        return self._all(lambda after: self.client.find_links(
            target_id, property_id or "", ROLES[role], 0, after), target_id)

    def link_counts(self, target_id: str) -> List[Tuple[str, str, str, int]]:
        """Return how many entities link to one, as (property, role,
        type, count)."""
        response = self.client.link_counts(target_id)
        self._check(response.status, target_id)
        return [(c.property_id, FROM_ROLES[c.role], c.type_id, c.count) for c in response.counts]

    def instances(self, class_id: str) -> Iterator[str]:
        """Find the instances of a class, or of any of its subclasses."""
        return self._all(lambda after: self.client.instances(
            class_id, 0, after), class_id)

    def query(self,
              query: Query,
              languages: Iterable[str] = (),
              labels: Optional[Dict[str, str]] = None) -> Iterator[Tuple[str, ...]]:
        """Return the rows that answer a query, from every batch: the
        values of its selected variables, sorted.

        :param labels: Filled in, if given, with the labels, in a chain
                       of languages, of the entities in the rows.
        :raises ValueError: if the query isn't valid.
        :raises TimeoutError: if it ran past the service's time limit."""
        patterns, select = query_to_wire(query)
        chain = list(languages)
        after: Sequence[str] = []
        while True:
            response = self.client.query(patterns, select, query.deprecated, chain, 0, list(after))
            self._check(response.status, "query")
            if labels is not None:
                labels.update((label.entity_id, label.text) for label in response.labels)
            for row in response.rows:
                yield tuple(row.values)
            if not response.rows:
                return
            after = response.rows[-1].values

    def watch(self, ids: Iterable[str], listener: int) -> int:
        """Have changes to some entities, or all, reported to a port
        serving Listener; return the watch's identifier."""
        response = self.client.watch(list(ids), listener)
        self._check(response.status, "watch")
        return response.watch_id

    def unwatch(self, watch_id: int):
        self._check(self.client.unwatch(watch_id).status, "unwatch")
