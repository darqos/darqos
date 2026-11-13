# darqos
# Copyright (C) 2026 David Arnold

"""Wikidata: finding an entity there, and a few of its facts, for one
that's here (see doc/wikidata.rst).

It turns what Wikidata says into the KB's model, and knows nothing of how
it's fetched, or of the KB itself, so it's tested alone: Linker, below,
does what it says to a KB.  It's only used when it's asked to be, as it
sends what's asked about to Wikimedia, and it says who's asking, as
Wikimedia require: the operator's name and address, from the run
directory, which aren't in the source."""

import dataclasses
import datetime
import json
import os
import pathlib
import typing
import urllib.parse

from . import kb as model
from .kb import Datatype, EntityValue, Item, Property, Reference, Snak, Statement, StringValue, TimeValue


# Control exports
__all__ = [
    "API",
    "Candidate",
    "Facts",
    "Fetch",
    "LANGUAGE_CLASS",
    "Language",
    "Linker",
    "QUERY",
    "WIKIDATA",
    "WikidataError",
    "contact",
    "facts",
    "is_wikidata",
    "page_of",
    "languages",
    "search",
    "user_agent",
    "vocabulary",
]


API = "https://www.wikidata.org/w/api.php"
QUERY = "https://query.wikidata.org/sparql"
PAGE = "https://www.wikidata.org/wiki/"

# Wikidata, as the source of what's from it, and when it was retrieved
# (a reference's qualifier).
WIKIDATA = "Q2013"
RETRIEVED = "P813"

# The run directory's file of the operator's name and address.
CONTACT_FILE = "wikidata-contact"

# Most candidates to offer for a name.
MAX_CANDIDATES = 8

# Most entities to ask for at once.
BATCH = 50

# Calendars, by Wikidata's items.
CALENDARS = {"http://www.wikidata.org/entity/Q1985786": "julian"}

# The facts that are taken: everything's, a person's, and an
# organisation's, as in the docs.
DESCRIBED_AT = "P973"
PERSON = ["P569", "P570", "P19", "P20", "P27", "P106"]
ORGANISATION = ["P571", "P576", "P17", "P159", "P749"]
PERSON_CLASS = "Q5"
ORGANISATION_CLASS = "Q43229"

# Languages, and their ISO 639-1 codes.
LANGUAGE_CLASS = "Q34770"
ISO_639_1 = "P218"

# Fetches a URL, with headers, returning its body.
Fetch = typing.Callable[[str, list[tuple[str, str]]], bytes]


class WikidataError(Exception):
    """Wikidata can't be asked, or what it said can't be read."""


@dataclasses.dataclass
class Candidate:
    """An entity Wikidata has, that something might be."""

    id: str
    label: str
    description: str = ""

    def describe(self) -> str:
        return f"{self.label} — {self.description}" if self.description else self.label


@dataclasses.dataclass
class Facts:
    """What's taken of an entity: its label, in English, or else in mul,
    Wikidata's code for a label in every language, as many people's names
    are; its description and aliases, in English; its statements; and the
    entities they refer to."""

    id: str
    label: str = ""
    description: str = ""
    aliases: list[str] = dataclasses.field(default_factory=list)
    statements: list[Statement] = dataclasses.field(default_factory=list)
    classes: list[str] = dataclasses.field(default_factory=list)
    label_language: str = "en"

    def refers_to(self) -> list[str]:
        """Return the entities the statements' values are."""
        found = (s.get_value() for s in self.statements)
        return list(dict.fromkeys(v.entity_id for v in found if isinstance(v, EntityValue)))


@dataclasses.dataclass
class Language:
    id: str
    code: str
    label: str


def is_wikidata(entity_id: str) -> bool:
    """Return whether an identifier is one of Wikidata's items."""
    return entity_id[:1] == "Q" and entity_id[1:].isdigit()


def page_of(entity_id: str) -> str:
    return PAGE + entity_id


def contact(run_dir: str | None = None) -> str:
    """Return the operator's name and address, to say who's asking.

    :raises WikidataError: if they haven't said."""
    directory = pathlib.Path(run_dir or os.environ.get("DARQ_RUN", "run"))
    try:
        text = (directory / CONTACT_FILE).read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        text = []
    if not text or not text[0].strip():
        raise WikidataError(f"Wikidata needs to know who's asking: put your name and address in "
                            f"{directory / CONTACT_FILE}")
    return text[0].strip()


def user_agent(version: str, who: str) -> str:
    """Return the User-Agent Wikimedia ask for: a client, its version, and
    how to get in touch."""
    return f"DarqOS/{version} ({who})"


def load(body: bytes) -> typing.Any:
    try:
        return json.loads(body)
    except ValueError as e:
        raise WikidataError(f"Wikidata's answer can't be read: {e}") from None


def search_url(text: str) -> str:
    return API + "?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "search": text, "language": "en", "uselang": "en", "type": "item",
        "limit": MAX_CANDIDATES, "format": "json"})


def parse_search(body: bytes) -> list[Candidate]:
    answer = load(body)
    found = answer.get("search") if isinstance(answer, dict) else None
    return [Candidate(r["id"], str(r.get("label") or r["id"]), str(r.get("description") or ""))
            for r in found or () if isinstance(r, dict) and is_wikidata(str(r.get("id", "")))]


def search(text: str, fetch: Fetch, agent: str) -> list[Candidate]:
    """Return what Wikidata has by a name, best first."""
    return parse_search(fetch(search_url(text), [("User-Agent", agent)]))


def entities_url(ids: list[str], properties: str) -> str:
    return API + "?" + urllib.parse.urlencode({
        "action": "wbgetentities", "ids": "|".join(ids), "props": properties, "languages": "en|mul",
        "sitefilter": "enwiki", "format": "json"})


def english(values: typing.Any, language: str = "en") -> str:
    return str(values.get(language, {}).get("value", "")) if isinstance(values, dict) else ""


def time_of(value: dict) -> TimeValue | None:
    """Return a Wikidata time, to its precision, or None if it's one the
    KB doesn't take, such as a time with no year."""
    text = str(value.get("time", ""))
    sign = -1 if text.startswith("-") else 1
    try:
        year_text, month, rest = text.lstrip("+-").split("-", 2)
        year, month, day = sign * int(year_text), int(month), int(rest[:2])
        precision = model.TimePrecision(int(value.get("precision", 11)))
        return TimeValue(year, month or None if precision.value >= 10 else None,
                         day or None if precision.value >= 11 else None, precision=precision,
                         calendar=CALENDARS.get(str(value.get("calendarmodel", ""))))
    except (ValueError, TypeError):
        return None


def value_of(snak: dict) -> model.Value | None:
    """Return a snak's value, for the kinds that are taken."""
    data = snak.get("datavalue") if snak.get("snaktype") == "value" else None
    if not isinstance(data, dict):
        return None
    value = data.get("value")
    if data.get("type") == "wikibase-entityid" and isinstance(value, dict) and is_wikidata(str(value.get("id"))):
        return EntityValue(str(value["id"]))
    if data.get("type") == "time" and isinstance(value, dict):
        return time_of(value)
    if data.get("type") == "string" and isinstance(value, str):
        return StringValue(value)
    return None


def best(statements: typing.Any) -> list[dict]:
    """Return statements of Wikidata's best rank: its preferred ones, or
    else its normal ones, and never its deprecated ones."""
    found = [s for s in statements or () if isinstance(s, dict)]
    preferred = [s for s in found if s.get("rank") == "preferred"]
    return preferred or [s for s in found if s.get("rank") == "normal"]


def source(retrieved: datetime.date) -> Reference:
    """Return the reference of what's from Wikidata: stated in it, and
    when."""
    return Reference().add(model.STATED_IN, EntityValue(WIKIDATA)).add(
        RETRIEVED, TimeValue(retrieved.year, retrieved.month, retrieved.day))


def parse_facts(body: bytes, entity_id: str, properties: list[str], retrieved: datetime.date) -> Facts:
    """Return what's taken of an entity, from what Wikidata said of it."""
    answer = load(body)
    record = (answer.get("entities") or {}).get(entity_id) if isinstance(answer, dict) else None
    if not isinstance(record, dict) or "missing" in record:
        raise WikidataError(f"Wikidata has no {entity_id}")
    claims = record.get("claims") or {}
    label, label_language = english(record.get("labels")), "en"
    if not label and english(record.get("labels"), "mul"):
        label, label_language = english(record.get("labels"), "mul"), "mul"
    made = Facts(entity_id, label, english(record.get("descriptions")),
                 [str(a.get("value")) for a in (record.get("aliases") or {}).get("en", ()) if a.get("value")],
                 label_language=label_language)
    made.classes = [v.entity_id for s in best(claims.get(model.INSTANCE_OF)) if isinstance(v := value_of(s.get(
        "mainsnak", {})), EntityValue)]
    for property_id in properties:
        for statement in best(claims.get(property_id)):
            value = value_of(statement.get("mainsnak", {}))
            if value is not None:
                made.statements.append(Statement(Snak(property_id, value), references=[source(retrieved)]))
    article = ((record.get("sitelinks") or {}).get("enwiki") or {}).get("url")
    if isinstance(article, str) and article.startswith("https://en.wikipedia.org/"):
        made.statements.append(Statement(Snak(DESCRIBED_AT, StringValue(article)), references=[source(retrieved)]))
    return made


def properties_for(classes: list[str]) -> list[str]:
    """Return the facts taken of something of some classes."""
    if PERSON_CLASS in classes:
        return PERSON
    return ORGANISATION


def facts(entity_id: str, fetch: Fetch, agent: str, retrieved: datetime.date | None = None) -> Facts:
    """Return what's taken of an entity: a person's facts, if it's one, or
    else an organisation's."""
    retrieved = retrieved or datetime.date.today()
    body = fetch(entities_url([entity_id], "labels|descriptions|aliases|claims|sitelinks/urls"),
                 [("User-Agent", agent)])
    first = parse_facts(body, entity_id, [], retrieved)
    return parse_facts(body, entity_id, properties_for(first.classes), retrieved)


def minimal(ids: list[str], fetch: Fetch, agent: str) -> list[Facts]:
    """Return the labels, descriptions and classes of some entities, as
    what's referred to is made of no more than that."""
    found = []
    for start in range(0, len(ids), BATCH):
        body = fetch(entities_url(ids[start:start + BATCH], "labels|descriptions|claims"), [("User-Agent", agent)])
        answer = load(body)
        for entity_id in ids[start:start + BATCH]:
            try:
                found.append(parse_facts(json.dumps({"entities": answer.get("entities", {})}).encode(), entity_id,
                                         [], datetime.date.today()))
            except WikidataError:
                continue
    return found


LANGUAGES_QUERY = """SELECT ?language ?code ?label WHERE {
  ?language wdt:P218 ?code.
  ?language rdfs:label ?label FILTER(lang(?label) = "en")
}"""


def parse_languages(body: bytes) -> list[Language]:
    answer = load(body)
    rows = ((answer.get("results") or {}).get("bindings") or []) if isinstance(answer, dict) else []
    found: dict[str, Language] = {}
    for row in rows:
        try:
            entity_id = row["language"]["value"].rpartition("/")[2]
            code, label = row["code"]["value"], row["label"]["value"]
        except (KeyError, TypeError, AttributeError):
            continue
        if is_wikidata(entity_id) and entity_id not in found:
            found[entity_id] = Language(entity_id, code, label)
    return sorted(found.values(), key=lambda language: language.label)


def languages(fetch: Fetch, agent: str) -> list[Language]:
    """Return every language with an ISO 639-1 code."""
    url = QUERY + "?" + urllib.parse.urlencode({"query": LANGUAGES_QUERY, "format": "json"})
    return parse_languages(fetch(url, [("User-Agent", agent), ("Accept", "application/sparql-results+json")]))


def vocabulary() -> list[model.Entity]:
    """Return the properties and items what's taken of Wikidata needs."""
    def prop(property_id: str, label: str, datatype: Datatype) -> Property:
        made = Property(datatype, property_id)
        made.set_label(label)
        return made

    def item(entity_id: str, label: str) -> Item:
        made = Item(entity_id)
        made.set_label(label)
        return made

    return [
        prop("P569", "date of birth", Datatype.Time),
        prop("P570", "date of death", Datatype.Time),
        prop("P19", "place of birth", Datatype.Item),
        prop("P20", "place of death", Datatype.Item),
        prop("P27", "country of citizenship", Datatype.Item),
        prop("P106", "occupation", Datatype.Item),
        prop("P571", "inception", Datatype.Time),
        prop("P576", "dissolved, abolished or demolished date", Datatype.Time),
        prop("P17", "country", Datatype.Item),
        prop("P159", "headquarters location", Datatype.Item),
        prop("P749", "parent organization", Datatype.Item),
        prop(DESCRIBED_AT, "described at URL", Datatype.Url),
        prop(ISO_639_1, "ISO 639-1 code", Datatype.String),
        item(WIKIDATA, "Wikidata"),
        item(LANGUAGE_CLASS, "language"),
    ]


class Linker:
    """Does what Wikidata says to a KB: makes an entity Wikidata's, gives
    it its facts, and brings languages."""

    def __init__(self, kb: model.KB, items: model.KB, fetch: Fetch, agent: str):
        """Constructor.

        :param kb: Client of the KB service.
        :param items: Client that writes through the implementation, which
                      keeps the Index up to date."""
        self.kb = kb
        self.items = items
        self.fetch = fetch
        self.agent = agent

    def ensure_minimal(self, ids: list[str]):
        """Make the entities that facts refer to, if they aren't here: their
        label, description, and classes that are here, and no more."""
        found, _ = self.kb.get_entities(ids, follow=False) if ids else ([], None)
        missing = [i for i in ids if i not in {e.id for e in found}]
        made = []
        for fact in minimal(missing, self.fetch, self.agent):
            entity = Item(fact.id)
            entity.set_label(fact.label or fact.id, fact.label_language)
            if fact.description:
                entity.set_description(fact.description)
            made.append((entity, fact.classes))
        if made:
            classes, _ = self.kb.get_entities(list({c for _, cs in made for c in cs}), follow=False)
            known = {c.id for c in classes}
            for entity, cs in made:
                for class_id in cs:
                    if class_id in known:
                        entity.claim(model.INSTANCE_OF, EntityValue(class_id))
            self.kb.put(*(entity for entity, _ in made))

    def link(self, local_id: str, wikidata_id: str) -> model.Entity:
        """Make a local entity Wikidata's: it's merged into it, and given
        its facts.

        :raises WikidataError: if Wikidata can't be asked.
        :raises model.ConflictError, LookupError: if the KB won't."""
        got = facts(wikidata_id, self.fetch, self.agent)
        if self.kb.get(wikidata_id, follow=False) is None:
            entity = Item(wikidata_id)
            entity.set_label(got.label or wikidata_id, got.label_language)
            self.kb.put(entity)
        if local_id != wikidata_id:
            self.items.merge(local_id, wikidata_id)
        return self.state(got)

    def refresh(self, wikidata_id: str) -> model.Entity:
        """Give an entity Wikidata's facts as they are now."""
        return self.state(facts(wikidata_id, self.fetch, self.agent))

    def state(self, got: Facts) -> model.Entity:
        self.ensure_minimal(got.refers_to())
        for _ in range(3):
            entity = self.items.get(got.id)
            if entity is None:
                raise LookupError(f"unknown entity: {got.id}")
            if got.description and "en" not in entity.descriptions:
                entity.set_description(got.description)
            for alias in got.aliases:
                if alias != entity.get_label() and alias not in entity.get_aliases():
                    entity.add_alias(alias)
            model.restate(entity, WIKIDATA, got.statements, frozenset())
            try:
                self.items.put(entity)
                return entity
            except model.ConflictError:
                continue
        raise model.ConflictError(f"{got.id} keeps changing")

    def bring_languages(self) -> int:
        """Bring every language with an ISO 639-1 code, and merge into each
        the languages made here by its name: returns how many were."""
        brought = languages(self.fetch, self.agent)
        today = datetime.date.today()
        found, _ = self.kb.get_entities([language.id for language in brought], follow=False)
        here = {e.id: e for e in found}
        changed = []
        for language in brought:
            entity = here.get(language.id) or Item(language.id)
            if not entity.get_label():
                entity.set_label(language.label)
            if language.code not in entity.get_aliases():
                entity.add_alias(language.code)
            wanted = [Statement(Snak(ISO_639_1, StringValue(language.code)), references=[source(today)]),
                      Statement(Snak(model.INSTANCE_OF, EntityValue(LANGUAGE_CLASS)), references=[source(today)])]
            if model.restate(entity, WIKIDATA, wanted, frozenset()) or language.id not in here:
                changed.append(entity)
        for start in range(0, len(changed), BATCH):
            self.kb.put(*changed[start:start + BATCH])

        merged = 0
        for language in brought:
            for local_id in sorted(self.kb.find_by_label(language.label, "en")):
                if not is_wikidata(local_id):
                    local = self.kb.get(local_id, follow=False)
                    if local is not None and model.recorded_type(local) == model.THING_TYPE:
                        self.items.merge(local_id, language.id)
                        merged += 1
        return merged
