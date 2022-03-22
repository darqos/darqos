# darqos
# Copyright (C) 2022 David Arnold

import socket
import uuid

from enum import Enum
from typing import Any, Dict, List, Optional, Iterable


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

    # Object reference.
    IRI = 1

    # UTF-8 string.  Note that this is a universal string, not text in
    # a specific language.  As such, it's good for enum-type strings,
    # not text-type strings.  See TextType.
    String = 2

    # Multi-lingual text value.  There is no default language.
    Text = 3

    # Numeric quantity.  Can be integer or real.
    Quantity = 4

    # Datetime, with optional precision, and bounds.
    Time = 4


class IriValue:
    """Object reference value type."""

    def __init__(self, iri: str):
        self.iri = iri


class QuantityValue:
    """Quantity value type."""

    def __init__(self, value):
        self.value = value

class StringValue:
    """String value type."""

    def __init__(self, value: str):
        self.value = value

class TextValue:
    """Multi-language value type."""

    def __init__(self,
                 language: Optional[str] = None,
                 text: Optional[str] = None):
        """Constructor.

        :param language: Language tag for this text.
        :param text: Unicode string value for this text."""
        if language and text:
            self.text: Dict[str, str] = {language: text}

    def set_text(self, language: str, text: str):
        """Set text for the specified language."""
        self.text[language] = text
        return text

    def get_text(self, language: str):
        """Return text in given language."""
        return self.text.get(language)


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

class TimeValue:
    def __init__(self,
                 year: int,
                 month: Optional[int] = None,
                 day: Optional[int] = None,
                 hour: Optional[int] = None,
                 minute: Optional[int] = None,
                 second: Optional[int] = None,
                 nanosecond: Optional[int] = None,
                 precision: TimePrecision = TimePrecision.Days,
                 calendar: str = None):
        self.year: int = year
        self.month: int = month
        self.day: int = day
        self.hour: int = hour
        self.minute: int = minute
        self.second: int = second
        self.nanosecond: int = nanosecond

        self.precision: TimePrecision = precision
        self.calendar = calendar if calendar is not None else "gregorian"


class ReferenceAttribute:
    """A reference is comprised of a collection of attributes, represented
    as property:value pairs."""

    def __init__(self, property_id: str, value: Any):
        """Constructor.

        :param property_id: String property identifier.
        :param value: Property value (string or integer"""
        self.property_id: str = property_id
        self.value: Any = value

    def get_property_id(self) -> str:
        """Return the property identifier for this attribute."""
        return self.property_id

    def get_value(self) -> Any:
        """Return the property value for this attribute."""
        return self.value


class Reference:
    """A collection of attributes defining a source for a statement."""

    def __init__(self, attributes: Optional[Iterable[ReferenceAttribute]] = None):
        """Constructor.

        :param attributes: A ssequence of ReferenceAttributes to initialise
        the reference."""
        self.attributes: List = []

        if attributes is not None:
            self.attributes.extend(attributes)

    def add_attribute(self, property_id: str, value: Any):
        """Add an attribute to a Reference's collection."""
        ra = ReferenceAttribute(property_id, value)
        self.attributes.append(ra)

    def get_attributes(self) -> Iterable[ReferenceAttribute]:
        """Return an iterator over the attributes of this Reference."""
        return iter(self.attributes)


class Qualifier:
    """A Qualifier constrains the application of a Property."""

    def __init__(self, property_id: str, value: Any):
        """Constructor.

        :param property_id: String property identifier.
        :param value: Property value."""
        self.property_id: str = property_id
        self.value: Any = value

    def get_property_id(self) -> str:
        """Return the property identifier for this attribute."""
        return self.property_id

    def get_value(self) -> Any:
        """Return the property value for this attribute."""
        return self.value


class Statement:
    def __init__(self,
                 property_id: str = '',
                 value = None,
                 qualifiers: Optional[Iterable[Qualifier]] = None,
                 references: Optional[Iterable[Reference]] = None,
                 rank: Optional[Rank] = None):
        # Claim a property and value.
        self.property_id: str = property_id
        self.value: Any = value

        # Collection of qualifiers for this claim.
        self.qualifiers: List[Qualifier] = []
        if qualifiers is not None:
            self.qualifiers.extend(qualifiers)

        # Collection of references, providing evidence for the claim.
        self.references: List[Reference] = []
        if references is not None:
            self.references.extend(references)

        # Claim rank.
        if rank is None:
            self.rank = Rank.Normal
        else:
            self.rank: Rank = rank


class Property:
    def __init__(self, iid: str = ""):
        if len(iid) == 0:
            self.id = str(uuid.uuid4())
        else:
            self.id = iid

        self.label: str = ''
        self.description: str = ''
        self.aliases: List[str] = []
        self.statements: List[Statement] = []
        self.datatype: Datatype = None

    def get_id(self):
        return self.id

    def set_label(self, label: str):
        self.label = label

    def get_label(self) -> str:
        return self.label

    def set_description(self, description: str):
        self.description = description

    def set_datatype(self, data_type: Datatype):
        self.datatype = data_type

    def add_alias(self, alias: str):
        self.aliases.append(alias)

    def add_statement(self, property: 'Property', value, qualifiers):
        s = Statement(property, value)
        self.statements.append(s)


class Item:
    def __init__(self, item_id: Optional[str] = None):
        if len(item_id) == 0
            self.item_id = str(uuid.uuid4())
        else:
            self.item_id = item_id

        self.label: str = ''
        self.description: str = ''
        self.aliases: List[str] = []
        self.statements = []


class KnowledgeBase:
    """Interface to the KnowledgeBase service."""

    @staticmethod
    def api() -> "KnowledgeBase":
        return KnowledgeBase()

    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)


