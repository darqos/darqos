# darqos
# Copyright (C) 2022-2026 David Arnold

"""The Knowledge Base's data model, and a client of its service.

Entities, items and properties, have labels, descriptions and aliases
per language, and statements: a property and value, with qualifiers,
references and a rank, after Wikidata's model.  They're kept by the
Knowledge Base service, darq.kb, described in doc/kb-service.rst, as
JSON documents (see document()), and sent to and from it as the
structs of its protocol (lib/didl/kb.didl).  KB is its client."""

import json
import re
import unicodedata
import uuid

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import (Any, Callable, Dict, Iterable, Iterator, List, Optional,
                    Protocol, Set, Tuple, Union)

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


# Kinds: the types of KB items (see doc/kb-service.rst).  An entity of
# no other kind is a darq.entity.
ENTITY_TYPE = "darq.entity"

# The other kinds, in order of priority, with the class of a new item of
# each: an entity that's of several kinds is of the first.
KINDS = [
    ("darq.person", "Q5"),
    ("darq.organisational-unit", "Q9261468"),
    ("darq.place", "Q2221906"),
    ("darq.organisation", "Q43229"),
    ("darq.group", "Q16334295"),
]

KIND_TYPES = [ENTITY_TYPE] + [kind for kind, _ in KINDS]

# The instance type of each class, by default, with its label: human,
# organizational subdivision, organization, geographic location,
# address, and group of humans.  An address isn't a geographic location
# on Wikidata.
DEFAULT_INSTANCE_TYPES = [
    ("Q5", "human", "darq.person"),
    ("Q9261468", "organizational subdivision", "darq.organisational-unit"),
    ("Q43229", "organization", "darq.organisation"),
    ("Q2221906", "geographic location", "darq.place"),
    ("Q319608", "address", "darq.place"),
    ("Q16334295", "group of humans", "darq.group"),
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


def _sourced(statement: Statement, source_id: str) -> bool:
    """Return whether a statement has a reference to a source."""
    return any(a.property_id == STATED_IN
               and isinstance(a.value, EntityValue)
               and a.value.entity_id == source_id
               for r in statement.references for a in r.snaks)


def _content(statement: Statement) -> str:
    """Return a statement's content, without its identifier, as text."""
    return json.dumps(encode_statement(statement, with_id=False),
                      sort_keys=True)


class KB:
    """Client of the Knowledge Base service.

    Entities are returned as Items and Properties, which can be changed,
    and put back.  A lookup returns identifiers, getting every batch."""

    def __init__(self, client: wire.KB.Client):
        self.client = client

    @classmethod
    def connect(cls, timeout: Optional[float] = None) -> "KB":
        """Connect to the service, darq.kb."""
        return cls(wire.KB.Client(system.connect("darq.kb"), timeout))

    @staticmethod
    def _check(status: wire.Status, what: str):
        """Raise the exception for a status, unless it's OK."""
        if status == wire.Status.OK:
            return
        if status == wire.Status.CONFLICT:
            raise ConflictError(f"{what}: changed, or already exists")
        if status == wire.Status.NOT_FOUND:
            raise LookupError(f"{what}: not found")
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

    def put(self, *entities: Entity) -> List[Entity]:
        """Write entities, all or none, and set their new revisions.

        Raises ConflictError if one's changed since it was read, or is
        new and exists; ValueError if a statement doesn't fit its
        property."""
        response = self.client.put_entities(
            [entity_to_wire(e) for e in entities])
        self._check(response.status, response.failed_id or "put")
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
        for s in new:
            if not _sourced(s, source_id):
                s.add_reference(Reference().add(STATED_IN,
                                                EntityValue(source_id)))
        for _ in range(3):
            entity = self.get(entity_id, follow=False)
            if entity is None:
                raise LookupError(f"{entity_id}: not found")
            old = [s for s in entity.statements if _sourced(s, source_id)]
            if [_content(s) for s in old] == [_content(s) for s in new]:
                return entity
            for s in old:
                entity.remove_statement(s)
            for s in new:
                entity.add_statement(Statement(s.main_snak, s.qualifiers,
                                               s.references, s.rank))
            try:
                self.put(entity)
            except ConflictError:
                continue
            return entity
        raise ConflictError(f"{entity_id}: it keeps changing", entity_id)

    def merge(self, old_id: str, new_id: str):
        """Merge one entity into another."""
        self._check(self.client.merge(old_id, new_id).status,
                    f"merge {old_id} into {new_id}")

    def destroy(self, entity_id: str):
        """Mark an entity destroyed."""
        self._check(self.client.destroy(entity_id).status, entity_id)

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

    def instances(self, class_id: str) -> Iterator[str]:
        """Find the instances of a class, or of any of its subclasses."""
        return self._all(lambda after: self.client.instances(
            class_id, 0, after), class_id)

    def watch(self, ids: Iterable[str], listener: int) -> int:
        """Have changes to some entities, or all, reported to a port
        serving Listener; return the watch's identifier."""
        response = self.client.watch(list(ids), listener)
        self._check(response.status, "watch")
        return response.watch_id

    def unwatch(self, watch_id: int):
        self._check(self.client.unwatch(watch_id).status, "unwatch")
