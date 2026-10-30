# darqos
# Copyright (C) 2022-2026 David Arnold

import re
import uuid

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union


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
                 rank: Optional[Rank] = None):
        """Constructor.

        :param main_snak: The claimed property and value.
        :param qualifiers: Qualifiers for the claim.
        :param references: References for the claim.
        :param rank: Status of the claim."""

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

    def set_rank(self, rank: Rank):
        """Set the claim's rank."""
        self.rank = rank

    def add_qualifier(self, property_id: str, value: Value) -> "Statement":
        """Add a qualifier to the claim, and return the statement."""
        self.qualifiers.append(Snak(property_id, value))
        return self

    def add_reference(self, reference: Reference) -> "Statement":
        """Add a reference to the claim, and return the statement."""
        self.references.append(reference)
        return self

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
    they're changed."""

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

        # KnowledgeBase this entity's registered with, if any.
        self.kb: Optional["KnowledgeBase"] = None

    def get_id(self) -> str:
        return self.id

    def set_label(self, label: str, language: str = "en"):
        old = self.labels.get(language)
        self.labels[language] = label
        if self.kb is not None:
            self.kb.label_changed(self, language, old, label)

    def get_label(self, language: str = "en") -> Optional[str]:
        return self.labels.get(language)

    def set_description(self, description: str, language: str = "en"):
        self.descriptions[language] = description

    def get_description(self, language: str = "en") -> Optional[str]:
        return self.descriptions.get(language)

    def add_alias(self, alias: str, language: str = "en"):
        self.aliases.setdefault(language, []).append(alias)

    def get_aliases(self, language: str = "en") -> List[str]:
        return list(self.aliases.get(language, []))

    def add_statement(self, statement: Statement) -> Statement:
        self.statements.append(statement)
        return statement

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


class KnowledgeBase:
    """Interface to the KnowledgeBase service."""

    @staticmethod
    def api() -> "KnowledgeBase":
        return KnowledgeBase()

    def __init__(self):
        #self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Entities, by identifier.  Identifiers are unique across both.
        self.items: Dict[str, Item] = {}
        self.properties: Dict[str, Property] = {}

        # Entity identifiers, by language tag and label.
        self.labels: Dict[Tuple[str, str], Set[str]] = {}

    def register(self, entity: Entity) -> Entity:
        """Register an Item or Property, and index its labels."""
        if entity.kb is not None:
            raise ValueError(f"{entity.id} is already registered")
        if entity.id in self.items or entity.id in self.properties:
            raise ValueError(f"{entity.id} is already in use")

        if isinstance(entity, Property):
            self.properties[entity.id] = entity
        elif isinstance(entity, Item):
            self.items[entity.id] = entity
        else:
            raise TypeError(f"not an Item or Property: {entity!r}")

        entity.kb = self
        for language, label in entity.labels.items():
            self.label_changed(entity, language, None, label)
        return entity

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

    def get(self, entity_id: str) -> Optional[Entity]:
        """Look up an item or property by identifier."""
        return self.items.get(entity_id) or self.properties.get(entity_id)

    def get_item(self, item_id: str) -> Optional[Item]:
        """Look up an item by identifier."""
        return self.items.get(item_id)

    def get_property(self, prop_id: str) -> Optional[Property]:
        """Look up a property by identifier."""
        return self.properties.get(prop_id)

    def find_items(self, label: str, language: str = "en") -> List[Item]:
        """Look up items by label, in order of identifier."""
        ids = sorted(self.labels.get((language, label), ()))
        return [self.items[i] for i in ids if i in self.items]

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
        ids = sorted(self.labels.get((language, label), ()))
        return [self.properties[i] for i in ids if i in self.properties]

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

    def check(self, statement: Statement):
        """Check a statement against this KnowledgeBase.

        Raises ValueError unless each snak's property is registered, and
        its value has the property's datatype, refers to a registered
        entity of the right kind, and is within its bounds."""
        snaks = [statement.main_snak, *statement.qualifiers]
        for reference in statement.references:
            snaks.extend(reference.snaks)

        for snak in snaks:
            prop = self.properties.get(snak.property_id)
            if prop is None:
                raise ValueError(f"unknown property: {snak.property_id}")

            value = snak.value
            expected = _VALUE_TYPES[prop.datatype]
            if not isinstance(value, expected):
                raise ValueError(
                    f"{prop.id} needs a {expected.__name__}, "
                    f"not {type(value).__name__}")

            if isinstance(value, EntityValue):
                table = self.items if prop.datatype == Datatype.Item \
                    else self.properties
                if value.entity_id not in table:
                    raise ValueError(
                        f"{prop.id}: unknown {prop.datatype.name.lower()}: "
                        f"{value.entity_id}")

            elif isinstance(value, QuantityValue):
                if value.unit is not None and value.unit not in self.items:
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
        prop = self.properties.get(snak.property_id)
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
