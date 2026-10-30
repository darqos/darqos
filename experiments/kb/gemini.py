from datetime import datetime
from typing import Any, Dict, List, Optional, Union

# --- 1. Values & Data Types ---
class TimeValue:
    def __init__(self, time_str: str, precision: str = "day"):
        self.time = datetime.fromisoformat(time_str)
        self.precision = precision  # e.g., "year", "month", "day"

    def __repr__(self):
        return f"Time({self.time.date()}, precision={self.precision})"

class QuantityValue:
    def __init__(self, amount: float, unit_item_id: Optional[str] = None):
        self.amount = amount
        self.unit = unit_item_id  # Points to an Item ID (e.g., "Q11573" for metres)

    def __repr__(self):
        return f"Quantity({self.amount} {self.unit or 'units'})"


# --- 2. Core Entities ---
class Entity:
    def __init__(self, id: str, labels: Dict[str, str], descriptions: Dict[str, str]):
        self.id = id  # Q-id or P-id
        self.labels = labels  # e.g., {"en": "Douglas Adams", "fr": "Douglas Adams"}
        self.descriptions = descriptions

class Item(Entity):
    def __init__(self, id: str, labels: Dict[str, str], descriptions: Dict[str, str]):
        super().__init__(id, labels, descriptions)
        self.statements: List['Statement'] = []

    def add_statement(self, statement: 'Statement'):
        self.statements.append(statement)

class Property(Entity):
    def __init__(self, id: str, labels: Dict[str, str], descriptions: Dict[str, str], datatype: str):
        super().__init__(id, labels, descriptions)
        self.datatype = datatype  # e.g., "wikibase-item", "time", "quantity", "string"


# --- 3. Statements & Qualifiers (The Wikidata "Snak") ---
class Snak:
    """A property-value pair used for main claims or qualifiers."""
    def __init__(self, property: Property, value: Union[Item, TimeValue, QuantityValue, str]):
        self.property = property
        self.value = value

    def __repr__(self):
        val_repr = self.value.id if isinstance(self.value, Entity) else repr(self.value)
        return f"[{self.property.labels.get('en', self.property.id)}: {val_repr}]"

class Statement:
    """A main claim wrapped with structural metadata (Qualifiers & Rank)."""
    def __init__(self, main_snak: Snak, rank: str = "normal"):
        self.main_snak = main_snak
        self.qualifiers: List[Snak] = []
        self.rank = rank  # "preferred", "normal", "deprecated"

    def add_qualifier(self, property: Property, value: Any):
        self.qualifiers.append(Snak(property, value))

    def __repr__(self):
        qual_str = f" with Qualifiers: {self.qualifiers}" if self.qualifiers else ""
        return f"Statement({self.main_snak}{qual_str}, rank={self.rank})"


# --- 4. Live Simulation ---
if __name__ == "__main__":
    # Define some properties
    p_instance_of = Property("P31", {"en": "instance of"}, {}, "wikibase-item")
    p_educated_at = Property("P69", {"en": "educated at"}, {}, "wikibase-item")
    p_end_time = Property("P582", {"en": "end time"}, {}, "time")

    # Define some items
    q_human = Item("Q5", {"en": "human"}, {})
    q_st_johns = Item("Q691283", {"en": "St John's College"}, {})
    q_douglas = Item("Q42", {"en": "Douglas Adams"}, {"en": "English author and humorist"})

    # 1. Add simple statement: Douglas Adams -> instance of -> human
    stmt1 = Statement(Snak(p_instance_of, q_human))
    q_douglas.add_statement(stmt1)

    # 2. Add complex statement with qualifier: 
    # Douglas Adams -> educated at -> St John's College (Qualifier: end time -> 1974)
    stmt2 = Statement(Snak(p_educated_at, q_st_johns))
    stmt2.add_qualifier(p_end_time, TimeValue("1974-01-01", precision="year"))
    q_douglas.add_statement(stmt2)

    # Inspect the constructed graph node
    print(f"Entity: {q_douglas.labels['en']} ({q_douglas.id})")
    for s in q_douglas.statements:
        print(f"  - {s}")
