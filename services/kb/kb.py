# darqos
# Copyright (C) 2022-2026 David Arnold

import contextlib
import json
import re
import unicodedata
import uuid

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import (TYPE_CHECKING, Any, Dict, Iterable, Iterator, List,
                    Optional, Set, Tuple, Union)

if TYPE_CHECKING:
    from store import Store


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
        self.kb: Optional["KnowledgeBase"] = None

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
_VALUE_TYPES = {
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


def entity_strings(entity: Entity) -> Iterator[Tuple[str, str, str]]:
    """Return an entity's string values, normalised.

    Each is a statement identifier, property, and value, from the
    statement's main snak."""
    for s in entity.statements:
        value = s.main_snak.value
        if isinstance(value, StringValue):
            yield s.id, s.main_snak.property_id, normalise(value.value)


def _live(entity: Optional[Entity]) -> bool:
    """Return whether an entity exists, and isn't destroyed or merged."""
    return (entity is not None and not entity.destroyed
            and entity.redirect is None)


class ConflictError(Exception):
    """An entity was changed by someone else since it was read."""


class KnowledgeBase:
    """Interface to the KnowledgeBase service.

    Without a store, entities are kept in memory.  With one, they're
    read from it as they're needed, and kept; changes are written to it
    by commit(), or at the end of a transaction(), and a lookup sees
    changes not yet committed."""

    @staticmethod
    def api() -> "KnowledgeBase":
        return KnowledgeBase()

    def __init__(self, store: Optional["Store"] = None):
        self.store = store

        # Entities, by identifier: all of them, or those read from the
        # store.  Identifiers are unique across both.
        self.items: Dict[str, Item] = {}
        self.properties: Dict[str, Property] = {}

        # Entity identifiers, by language tag and label.
        self.labels: Dict[Tuple[str, str], Set[str]] = {}

        # Entities changed since they were written to the store, and
        # those written in its current transaction.
        self.dirty: Dict[str, Entity] = {}
        self.written: Dict[str, Entity] = {}

    def register(self, entity: Entity) -> Entity:
        """Register an Item or Property, and index its labels."""
        if entity.kb is not None:
            raise ValueError(f"{entity.id} is already registered")
        if not isinstance(entity, (Item, Property)):
            raise TypeError(f"not an Item or Property: {entity!r}")
        if self.get(entity.id, follow=False) is not None:
            raise ValueError(f"{entity.id} is already in use")

        self._cache(entity)
        self.changed(entity)
        return entity

    def _cache(self, entity: Entity):
        """Keep an entity, and index its labels."""
        if isinstance(entity, Property):
            self.properties[entity.id] = entity
        elif isinstance(entity, Item):
            self.items[entity.id] = entity
        entity.kb = self
        for language, label in entity.labels.items():
            self.label_changed(entity, language, None, label)

    def _evict(self, entity: Entity):
        """Forget an entity, so it's read from the store again."""
        self.items.pop(entity.id, None)
        self.properties.pop(entity.id, None)
        for language, label in entity.labels.items():
            ids = self.labels.get((language, label))
            if ids is not None:
                ids.discard(entity.id)
                if not ids:
                    del self.labels[(language, label)]
        entity.kb = None

    def label_changed(self,
                      entity: Entity,
                      language: str,
                      old: Optional[str],
                      new: str):
        """Update the label index for an entity's changed label."""
        if old is not None:
            ids = self.labels.get((language, old))
            if ids is not None:
                ids.discard(entity.id)
                if not ids:
                    del self.labels[(language, old)]
        self.labels.setdefault((language, new), set()).add(entity.id)

    def changed(self, entity: Entity):
        """Note that an entity's changed, to be written to the store."""
        if self.store is not None:
            self.dirty[entity.id] = entity

    def flush(self):
        """Write changed entities to the store, in its transaction.

        An entity whose document is the same as it was isn't written."""
        if self.store is None:
            return
        for entity in list(self.dirty.values()):
            text = document(entity)
            if text != entity.stored:
                self.store.put(entity, text)
                entity.stored = text
                self.written[entity.id] = entity
            del self.dirty[entity.id]

    def commit(self):
        """Write changed entities to the store, and commit them.

        Raises ConflictError, and rolls back, if an entity was changed
        in the store since it was read."""
        if self.store is None:
            return
        try:
            self.flush()
        except BaseException:
            self.rollback()
            raise
        self.store.commit()
        self.written.clear()

    def rollback(self):
        """Discard uncommitted changes.

        Changed entities are forgotten, and read from the store again
        when they're next needed."""
        if self.store is None:
            return
        self.store.rollback()
        for entity in [*self.dirty.values(), *self.written.values()]:
            if entity.kb is self:
                self._evict(entity)
        self.dirty.clear()
        self.written.clear()

    @contextlib.contextmanager
    def transaction(self) -> Iterator["KnowledgeBase"]:
        """Commit the changes made in a with block, or roll them back."""
        try:
            yield self
        except BaseException:
            self.rollback()
            raise
        self.commit()

    def add_item(self,
                 label: Optional[str] = None,
                 item_id: Optional[str] = None,
                 desc: Optional[str] = None,
                 language: str = "en") -> Item:
        """Create and register an item."""
        item = Item(item_id)
        if label is not None:
            item.set_label(label, language)
        if desc is not None:
            item.set_description(desc, language)

        self.register(item)
        return item

    def add_property(self,
                     label: Optional[str],
                     datatype: Datatype,
                     prop_id: Optional[str] = None,
                     desc: Optional[str] = None,
                     language: str = "en") -> Property:
        """Create and register a property."""
        p = Property(datatype, prop_id)
        if label is not None:
            p.set_label(label, language)
        if desc is not None:
            p.set_description(desc, language)

        self.register(p)
        return p

    def get(self, entity_id: str, follow: bool = True) -> Optional[Entity]:
        """Look up an item or property by identifier.

        :param follow: Return the entity a merged one redirects to,
        rather than the merged one itself."""
        entity: Optional[Entity] = \
            self.items.get(entity_id) or self.properties.get(entity_id)
        if entity is None and self.store is not None:
            entity = self.store.get(entity_id)
            if entity is not None:
                self._cache(entity)

        seen: Set[str] = set()
        while follow and entity is not None and entity.redirect is not None:
            if entity.id in seen:
                raise LookupError(f"redirect loop at {entity.id}")
            seen.add(entity.id)
            entity = self.get(entity.redirect, follow=False)
        return entity

    def get_item(self, item_id: str) -> Optional[Item]:
        """Look up an item by identifier."""
        entity = self.get(item_id)
        return entity if isinstance(entity, Item) else None

    def get_property(self, prop_id: str) -> Optional[Property]:
        """Look up a property by identifier."""
        entity = self.get(prop_id)
        return entity if isinstance(entity, Property) else None

    def destroy(self, entity_id: str):
        """Mark an entity destroyed.

        It keeps its statements, and the links to it, but is left out of
        lookups."""
        entity = self.get(entity_id, follow=False)
        if entity is None:
            raise LookupError(f"unknown entity: {entity_id}")
        entity.destroyed = True
        entity.changed()

    def merge(self, old_id: str, new_id: str) -> Entity:
        """Merge one entity into another, and return the other.

        The old entity's statements move to the new one, and its labels
        and aliases become the new one's aliases, unless the new one
        has no label in that language.  The old one redirects to the new
        one, so links to it still lead somewhere."""
        old = self.get(old_id, follow=False)
        new = self.get(new_id)
        if old is None or new is None:
            raise LookupError(f"unknown entity: {old_id if old is None else new_id}")
        if old is new or old.redirect is not None:
            raise ValueError(f"can't merge {old_id} into {new_id}")
        if type(old) is not type(new):
            raise ValueError("can't merge an item and a property")

        names = [(lang, label) for lang, label in old.labels.items()]
        names += [(lang, a) for lang, aliases in old.aliases.items()
                  for a in aliases]
        for language, name in names:
            if language not in new.labels:
                new.set_label(name, language)
            elif (name != new.labels[language]
                  and name not in new.get_aliases(language)):
                new.add_alias(name, language)
        for language, description in old.descriptions.items():
            if language not in new.descriptions:
                new.set_description(description, language)
        for s in list(old.statements):
            old.remove_statement(s)
            new.add_statement(s)

        old.redirect = new.id
        old.changed()
        return new

    def entities(self) -> Iterator[Entity]:
        """Return all entities, except destroyed and merged ones."""
        if self.store is not None:
            self.flush()
            ids = self.store.ids()
        else:
            ids = sorted([*self.items, *self.properties])
        for entity_id in ids:
            entity = self.get(entity_id, follow=False)
            if entity is not None and _live(entity):
                yield entity

    def _entities(self, ids: Iterable[str]) -> List[Entity]:
        """Return the entities with some identifiers, if live."""
        entities = [self.get(i, follow=False) for i in ids]
        return [e for e in entities if e is not None and _live(e)]

    def _label_ids(self, label: str, language: str) -> List[str]:
        if self.store is not None:
            self.flush()
            return self.store.find_label(label, language)
        return sorted(self.labels.get((language, label), ()))

    def find_items(self, label: str, language: str = "en") -> List[Item]:
        """Look up items by label, in order of identifier."""
        return [e for e in self._entities(self._label_ids(label, language))
                if isinstance(e, Item)]

    def find_item(self, label: str, language: str = "en") -> Optional[Item]:
        """Look up item by label.

        Raises LookupError if more than one item has the label."""
        items = self.find_items(label, language)
        if len(items) > 1:
            raise LookupError(f"{len(items)} items labelled {label!r}")
        return items[0] if items else None

    def find_properties(self,
                        label: str,
                        language: str = "en") -> List[Property]:
        """Look up properties by label, in order of identifier."""
        return [e for e in self._entities(self._label_ids(label, language))
                if isinstance(e, Property)]

    def find_property(self,
                      label: str,
                      language: str = "en") -> Optional[Property]:
        """Look up property by label.

        Raises LookupError if more than one property has the label."""
        properties = self.find_properties(label, language)
        if len(properties) > 1:
            raise LookupError(
                f"{len(properties)} properties labelled {label!r}")
        return properties[0] if properties else None

    def search(self, text: str, language: str = "en") -> List[Entity]:
        """Find entities whose label or an alias has words starting with
        each of the text's words, ignoring case."""
        if self.store is not None:
            self.flush()
            return self._entities(self.store.search(text, language))

        words = re.findall(r"\w+", fold(text))
        if not words:
            return []
        found = []
        for entity in self.entities():
            names = [entity.labels.get(language, ""),
                     *entity.get_aliases(language)]
            for name in names:
                have = re.findall(r"\w+", fold(name))
                if all(any(h.startswith(w) for h in have) for w in words):
                    found.append(entity)
                    break
        return found

    def find_by_value(self, property_id: str, value: str) -> List[Entity]:
        """Find entities with a string value for a property.

        Values are compared normalised, so phone numbers match however
        they're written."""
        value = normalise(value)
        if self.store is not None:
            self.flush()
            return self._entities(self.store.find_value(property_id, value))
        return [e for e in self.entities()
                if any(p == property_id and v == value
                       for _, p, v in entity_strings(e))]

    def find_links(self,
                   target_id: str,
                   property_id: Optional[str] = None,
                   role: str = "main") -> List[Entity]:
        """Find the entities that link to one.

        :param property_id: The linking property, or any.
        :param role: Where the link is in a statement: "main",
        "qualifier", or "reference"."""
        if self.store is not None:
            self.flush()
            return self._entities(
                self.store.find_links(target_id, property_id, role))
        return [e for e in self.entities()
                if any(t == target_id and r == role
                       and (property_id is None or p == property_id)
                       for _, p, t, r in entity_links(e))]

    def instances(self, class_id: str) -> List[Entity]:
        """Find the instances of a class, or of any of its subclasses."""
        if self.store is not None:
            self.flush()
            return self._entities(self.store.instances(class_id))

        def targets(entity: Entity, property_id: str) -> Set[str]:
            return {t for _, p, t, r in entity_links(entity)
                    if p == property_id and r == "main"}

        entities = list(self.entities())
        classes = {class_id}
        while True:
            more = {e.id for e in entities
                    if e.id not in classes
                    and targets(e, SUBCLASS_OF) & classes}
            if not more:
                break
            classes |= more
        return [e for e in entities if targets(e, INSTANCE_OF) & classes]

    def check(self, statement: Statement):
        """Check a statement against this KnowledgeBase.

        Raises ValueError unless each snak's property is registered, and
        its value has the property's datatype, refers to a registered
        entity of the right kind, and is within its bounds."""
        snaks = [statement.main_snak, *statement.qualifiers]
        for reference in statement.references:
            snaks.extend(reference.snaks)

        for snak in snaks:
            prop = self.get_property(snak.property_id)
            if prop is None:
                raise ValueError(f"unknown property: {snak.property_id}")

            value = snak.value
            expected = _VALUE_TYPES[prop.datatype]
            if not isinstance(value, expected):
                raise ValueError(
                    f"{prop.id} needs a {expected.__name__}, "
                    f"not {type(value).__name__}")

            if isinstance(value, EntityValue):
                kind = Item if prop.datatype == Datatype.Item else Property
                if not isinstance(self.get(value.entity_id), kind):
                    raise ValueError(
                        f"{prop.id}: unknown {prop.datatype.name.lower()}: "
                        f"{value.entity_id}")

            elif isinstance(value, QuantityValue):
                if (value.unit is not None
                        and self.get_item(value.unit) is None):
                    raise ValueError(f"{prop.id}: unknown unit: {value.unit}")
                if ((value.lower is not None and value.lower > value.amount)
                        or (value.upper is not None
                            and value.upper < value.amount)):
                    raise ValueError(
                        f"{prop.id}: amount out of bounds: {value}")

    def describe(self, entity_id: str, language: str = "en") -> str:
        """Return an entity's statements as text, with labels for ids."""
        entity = self.get(entity_id)
        if entity is None:
            raise LookupError(f"unknown entity: {entity_id}")

        lines = [self._name(entity_id, language)]
        description = entity.get_description(language)
        if description is not None:
            lines[0] += f": {description}"
        aliases = entity.get_aliases(language)
        if aliases:
            lines.append(f"  also known as: {', '.join(aliases)}")

        for s in entity.statements:
            line = "  " + self._snak(s.main_snak, language)
            if s.rank != Rank.Normal:
                line += f" ({s.rank.name.lower()})"
            lines.append(line)
            for q in s.qualifiers:
                lines.append("    " + self._snak(q, language))
            for r in s.references:
                lines.append("    source: " + "; ".join(
                    self._snak(a, language) for a in r.snaks))
        return "\n".join(lines)

    def _name(self, entity_id: str, language: str) -> str:
        """Return an entity's label and identifier, or its identifier."""
        entity = self.get(entity_id)
        label = entity.get_label(language) if entity is not None else None
        return f"{label} ({entity_id})" if label is not None else entity_id

    def _snak(self, snak: Snak, language: str) -> str:
        """Return a snak as text."""
        prop = self.get_property(snak.property_id)
        label = prop.get_label(language) if prop is not None else None
        name = label if label is not None else snak.property_id

        value = snak.value
        if isinstance(value, EntityValue):
            text = self._name(value.entity_id, language)
        elif isinstance(value, StringValue):
            text = repr(value.value)
        elif isinstance(value, TextValue):
            text = value.get_text(language) or repr(value)
        elif isinstance(value, QuantityValue) and value.unit is not None:
            unit = self.get(value.unit)
            unit_label = unit.get_label(language) if unit else None
            text = str(QuantityValue(value.amount, None,
                                     value.lower, value.upper))
            text += f" {unit_label or value.unit}"
        else:
            text = str(value)
        return f"{name}: {text}"


def _content(statement: Statement) -> str:
    """Return a statement's content, without its identifier, as text."""
    return json.dumps(encode_statement(statement, with_id=False),
                      sort_keys=True)


class Import:
    """Entities and statements from a source, such as an address book.

    An import can be repeated.  The first time it gets an entity, by
    item() or property(), the statements an earlier import from the same
    source made about it are removed, and the entity's other statements
    are kept.  finish() gives each new statement a reference to the
    source: one that's the same as a statement removed keeps its
    identifier, and its place, so an entity that hasn't changed isn't
    written again.
    An entity the earlier import made statements about, which this one
    didn't get, loses them, and is destroyed if it has none left."""

    def __init__(self, kb: KnowledgeBase, source_id: str, stated_in: str):
        """Constructor.

        :param kb: KnowledgeBase to import into.
        :param source_id: Identifier of the source's item.
        :param stated_in: Identifier of the property for a reference to
        the source, such as stated in (P248)."""
        self.kb = kb
        self.source_id = source_id
        self.stated_in = stated_in

        # Entities got, by identifier; the statements on them that are
        # kept; the identifiers of those removed, by content; and the
        # order of their statements, by identifier.
        self.entities: Dict[str, Entity] = {}
        self.kept: Set[str] = set()
        self.removed: Dict[str, Dict[str, List[str]]] = {}
        self.order: Dict[str, Dict[str, int]] = {}

    def _sourced(self, statement: Statement) -> bool:
        """Return whether a statement has a reference to the source."""
        return any(a.property_id == self.stated_in
                   and isinstance(a.value, EntityValue)
                   and a.value.entity_id == self.source_id
                   for r in statement.references for a in r.snaks)

    def _got(self, entity: Entity):
        """Remove an entity's statements from an earlier import."""
        if entity.id in self.entities:
            return
        self.entities[entity.id] = entity
        self.order[entity.id] = {s.id: i for i, s in
                                 enumerate(entity.statements)}
        removed: Dict[str, List[str]] = {}
        for s in list(entity.statements):
            if self._sourced(s):
                removed.setdefault(_content(s), []).append(s.id)
                entity.remove_statement(s)
            else:
                self.kept.add(s.id)
        self.removed[entity.id] = removed

    def _describe(self,
                  entity: Entity,
                  label: Optional[str],
                  desc: Optional[str],
                  language: str):
        if label is not None and entity.get_label(language) != label:
            entity.set_label(label, language)
        if desc is not None and entity.get_description(language) != desc:
            entity.set_description(desc, language)

    def item(self,
             item_id: str,
             label: Optional[str] = None,
             desc: Optional[str] = None,
             language: str = "en") -> Item:
        """Return an item, creating it if needed, with a label."""
        entity = self.kb.get(item_id)
        if entity is None:
            entity = self.kb.add_item(label, item_id, desc, language)
        elif not isinstance(entity, Item):
            raise ValueError(f"{item_id} isn't an item")
        else:
            self._describe(entity, label, desc, language)
        self._got(entity)
        return entity

    def property(self,
                 property_id: str,
                 label: Optional[str],
                 datatype: Datatype,
                 desc: Optional[str] = None,
                 language: str = "en") -> Property:
        """Return a property, creating it if needed, with a label."""
        entity = self.kb.get(property_id)
        if entity is None:
            entity = self.kb.add_property(label, datatype, property_id, desc,
                                          language)
        elif not isinstance(entity, Property):
            raise ValueError(f"{property_id} isn't a property")
        else:
            self._describe(entity, label, desc, language)
            if entity.get_datatype() != datatype:
                entity.set_datatype(datatype)
        self._got(entity)
        return entity

    def finish(self):
        """Give the new statements references to the source, and remove
        an earlier import's statements about entities not got since."""
        for entity in self.entities.values():
            removed = self.removed[entity.id]
            for s in entity.statements:
                if s.id in self.kept:
                    continue
                if not self._sourced(s):
                    s.add_reference(Reference().add(
                        self.stated_in, EntityValue(self.source_id)))
                ids = removed.get(_content(s))
                if ids:
                    s.id = ids.pop(0)

            # Statements that were there before go back in their places,
            # and new ones go after them.
            order = self.order[entity.id]
            entity.statements.sort(key=lambda st: order.get(st.id, len(order)))

        for entity in self.kb.find_links(self.source_id, self.stated_in,
                                         "reference"):
            if entity.id in self.entities:
                continue
            for s in list(entity.statements):
                if self._sourced(s):
                    entity.remove_statement(s)
            if not entity.statements:
                self.kb.destroy(entity.id)


if __name__ == "__main__":
    kb = KnowledgeBase()

    # Define some properties.
    kb.add_property("instance of", Datatype.Item, "P31")
    kb.add_property("educated at", Datatype.Item, "P69")
    kb.add_property("end time", Datatype.Time, "P582")
    kb.add_property("height", Datatype.Quantity, "P2048")
    kb.add_property("stated in", Datatype.Item, "P248")

    # Define some items.  The source has no Q-id, so it gets a UUID.
    kb.add_item("human", "Q5")
    kb.add_item("St John's College", "Q691283")
    kb.add_item("metre", "Q11573")
    source = kb.add_item("Example biography")

    douglas = kb.add_item("Douglas Adams", "Q42",
                          "English author and humorist")
    douglas.set_label("Douglas Adams", "fr")
    douglas.add_alias("Douglas Noël Adams")

    # Simple statement: Douglas Adams -> instance of -> human.
    douglas.claim("P31", EntityValue("Q5"))

    # Statement with a qualifier and a reference: Douglas Adams ->
    # educated at -> St John's College (end time -> 1974).
    douglas.claim("P69", EntityValue("Q691283")) \
        .add_qualifier("P582", TimeValue.parse("1974")) \
        .add_reference(Reference().add("P248", EntityValue(source.get_id())))

    # Quantity with a unit and bounds.
    douglas.claim("P2048", QuantityValue("1.96", "Q11573", "1.95", "1.97"))

    for statement in douglas.get_statements():
        kb.check(statement)

    print(kb.describe("Q42"))
    print(kb.find_item("Douglas Adams", "fr"))
