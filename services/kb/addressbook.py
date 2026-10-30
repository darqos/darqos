# darqos
# Copyright (C) 2026 David Arnold

"""Example: an address book as a knowledge base.

Reads a vCard 3.0 file, such as one exported from Apple Contacts or
Fastmail, into a KnowledgeBase using the Wikidata vocabulary from
wikidata.py: people, organisations and groups become items, as do the
departments, given names, family names, localities and addresses they
refer to.

    python addressbook.py [--db PATH] FILE [NAME ...]

prints counts of what was read, and describes the entries with the
labels given.  It never prints the whole address book.

With --db, the knowledge base is kept in an SQLite database, and the
import can be repeated: entities' identifiers are derived from keys,
such as a card's UID, and the import's statements are stated in the
address book, so a re-import replaces them, and keeps any others."""

import argparse
import collections
import re
import sys

from typing import Counter, Dict, List, Optional, Set, Tuple

import vcard
import wikidata

from kb import (EntityValue, Import, Item, KnowledgeBase, Property, Rank,
                Statement, StringValue, TextValue, TimeValue, Value, fold,
                key_id)
from store import Store


# Properties read, and properties skipped as bookkeeping or images.
# Any others are counted as unmapped.
HANDLED = {
    "FN", "N", "UID", "NICKNAME", "X-MAIDENNAME", "ORG", "TITLE", "BDAY",
    "X-ABDATE", "X-ABRELATEDNAMES", "TEL", "EMAIL", "URL", "IMPP", "X-AIM",
    "X-JABBER", "X-YAHOO", "X-MS-IMADDRESS", "X-SOCIALPROFILE", "ADR",
    "NOTE", "X-ABADR", "X-ABLABEL", "X-ABSHOWAS", "X-ADDRESSBOOKSERVER-KIND",
    "X-ADDRESSBOOKSERVER-MEMBER",
}
IGNORED = {
    "VERSION", "PRODID", "REV", "CATEGORIES", "PHOTO", "X-IMAGETYPE",
    "X-IMAGEHASH", "X-SHARED-PHOTO-DISPLAY-PREF", "X-ADDRESSING-GRAMMAR",
    "CREATED", "X-CREATED", "X-BUSYMAC-MODIFIED-BY", "MAILER", "CLASS",
    "X-ACTIVITY-ALERT",
}

# Contact types, from TYPE parameters.
CONTACT_TYPES = {
    "WORK": "work",
    "HOME": "home",
    "CELL": "mobile",
    "MAIN": "main",
    "IPHONE": "iPhone",
    "PAGER": "pager",
    "OTHER": "other",
}

# Services of Apple's older instant messaging properties.
IM_SERVICES = {
    "X-AIM": "AIM",
    "X-JABBER": "Jabber",
    "X-YAHOO": "Yahoo",
    "X-MS-IMADDRESS": "MSN",
}

# A date: YYYYMMDD or YYYY-MM-DD, perhaps followed by a time.
DATE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")

# Apple's placeholder year, for a date without one.
NO_YEAR = 1604


class Importer:
    """Reads vCards into a KnowledgeBase."""

    def __init__(self,
                 kb: KnowledgeBase,
                 wd: wikidata.Vocabulary,
                 name: str = "Address book"):
        self.kb = kb
        self.wd = wd

        # The address book, which the statements are stated in.
        source = key_id(f"address book:{name}")
        self.source = Import(kb, source, wd.stated_in)
        self.source.item(source, name, "address book, imported from vCards")

        # Related-name labels, and the property each is.  Others are
        # related persons, with the label as a qualifier.
        self.relations = {
            "spouse": wd.spouse,
            "child": wd.child,
            "son": wd.child,
            "daughter": wd.child,
            "mother": wd.mother,
            "father": wd.father,
            "parent": wd.parent,
            "brother": wd.sibling,
            "sister": wd.sibling,
            "sibling": wd.sibling,
            "partner": wd.unmarried_partner,
        }

        # Countries whose regions are known: an unknown region of any
        # other country might be a locality.
        self.region_parents: Set[str] = set().union(
            *wd.region_countries.values())

        # Items created, by key, so they're shared.
        self.organisations: Dict[str, Item] = {}
        self.departments: Dict[Tuple[str, str], Item] = {}
        self.names: Dict[Tuple[str, str], Item] = {}
        self.localities: Dict[Tuple[str, str, str], Item] = {}
        self.addresses: Dict[Tuple[str, ...], Item] = {}
        self.stubs: Dict[str, Item] = {}

        # People, by their FN; entries, by their UID.
        self.people: Dict[str, List[Item]] = {}
        self.uids: Dict[str, Item] = {}

        # Related names and group members, resolved once all the cards
        # are read.
        self.related: List[Tuple[Item, str, str]] = []
        self.members: List[Tuple[Item, str]] = []

        # Counts.
        self.cards: Counter[str] = collections.Counter()
        self.unmapped: Counter[str] = collections.Counter()
        self.linked = 0
        self.stub_count = 0

    def read(self, cards: List[vcard.Card]):
        """Read cards, then resolve their related names and members."""
        for card in cards:
            self.card(card)

        for person, label, name in self.related:
            self.relate(person, label, name)

        for group, uid in self.members:
            member = self.uids.get(uid)
            if member is None:
                self.unmapped["group member without a card"] += 1
            else:
                group.claim(self.wd.has_parts, EntityValue(member.get_id()))

        self.source.finish()

    def item(self, key: str, label: Optional[str] = None,
             desc: Optional[str] = None) -> Item:
        """Return the item for a key, creating it if needed."""
        return self.source.item(key_id(key), label, desc)

    def card(self, card: vcard.Card):
        """Read a card."""
        kind = (card.text("X-ADDRESSBOOKSERVER-KIND") or "").casefold()
        show_as = (card.text("X-ABSHOWAS") or "").casefold()
        uid = (card.text("UID") or "").strip()
        key = f"vcard:{uid}" if uid else f"card:{card.text('FN') or ''}"
        if kind == "group":
            self.cards["group"] += 1
            entity = self.group(card, key)
        elif show_as == "company":
            self.cards["organisation"] += 1
            entity = self.organisation_card(card)
        else:
            self.cards["person"] += 1
            entity = self.person(card, key)

        if uid:
            entity.claim(self.wd.vcard_uid, StringValue(uid))
            self.uids[uid] = entity

        self.contacts(card, entity, show_as == "company")

        for p in card.properties:
            if p.name not in HANDLED and p.name not in IGNORED:
                self.unmapped[f"property {p.name}"] += 1

    def person(self, card: vcard.Card, key: str) -> Item:
        """Create a person from a card."""
        wd = self.wd
        fn = (card.text("FN") or "").strip()
        person = self.item(key, fn or None)
        person.claim(wd.instance_of, EntityValue(wd.human))
        if fn:
            self.people.setdefault(fn.casefold(), []).append(person)

        n = card.get("N")
        if n is not None:
            family, given, additional = n.components(3)[:3]
            for name in given.split(",") + additional.split(","):
                if name.strip():
                    item = self.name(wd.given_name_class, name.strip())
                    person.claim(wd.given_name, EntityValue(item.get_id()))
            for name in family.split(","):
                if name.strip():
                    item = self.name(wd.family_name_class, name.strip())
                    person.claim(wd.family_name, EntityValue(item.get_id()))

        for p in card.all("NICKNAME"):
            for name in p.values():
                if name.strip():
                    person.claim(wd.nickname, TextValue(name.strip()))
        for p in card.all("X-MAIDENNAME"):
            if p.text().strip():
                person.claim(wd.birth_name, TextValue(p.text().strip()))

        # Employers, with their departments and the job title.
        employers: List[Tuple[str, Statement]] = []
        for p in card.all("ORG"):
            name, *units = [c.strip() for c in p.components()]
            if not name:
                continue
            unit = org = self.organisation(name)
            s = person.claim(wd.employer, EntityValue(org.get_id()))
            for u in units:
                if u:
                    unit = self.department(unit, u)
            if unit is not org:
                s.add_qualifier(wd.department, EntityValue(unit.get_id()))
            employers.append((name, s))

        for p in card.all("TITLE"):
            title = p.text().strip()
            if not title:
                continue
            if employers:
                employers[0][1].add_qualifier(wd.job_title, StringValue(title))
            else:
                person.claim(wd.job_title, StringValue(title))

        for p in card.all("BDAY"):
            date = self.date(p)
            if date is None:
                continue
            year, month, day = date
            if year is None:
                person.claim(wd.birthday, StringValue(f"--{month:02d}-{day:02d}"))
            else:
                self.claim_time(person, wd.date_of_birth, year, month, day)

        for p in card.all("X-ABDATE"):
            self.labelled_date(person, p, card.label(p) or "", employers)

        for p in card.all("X-ABRELATEDNAMES"):
            name = p.text().strip()
            if name:
                self.related.append((person, card.label(p) or "", name))
        return person

    def organisation_card(self, card: vcard.Card) -> Item:
        """Return the organisation an organisation card describes."""
        org = card.get("ORG")
        name = org.components(1)[0].strip() if org is not None else ""
        return self.organisation(name or (card.text("FN") or "").strip())

    def group(self, card: vcard.Card, key: str) -> Item:
        """Create a group from a card; its members are added later."""
        group = self.item(key, (card.text("FN") or "").strip() or None)
        group.claim(self.wd.instance_of, EntityValue(self.wd.group_of_humans))
        for p in card.all("X-ADDRESSBOOKSERVER-MEMBER"):
            uid = p.text().strip()
            if uid.casefold().startswith("urn:uuid:"):
                uid = uid[len("urn:uuid:"):]
            self.members.append((group, uid))
        return group

    def contacts(self, card: vcard.Card, entity: Item, organisation: bool):
        """Add a card's contact details, addresses and notes."""
        wd = self.wd

        for p in card.all("TEL"):
            number = p.text().strip()
            if number:
                prop = wd.fax_number if "FAX" in p.types() \
                    else wd.phone_number
                self.claim(card, p, entity, prop, StringValue(number))

        for p in card.all("EMAIL"):
            address = p.text().strip()
            if address:
                if not address.casefold().startswith("mailto:"):
                    address = "mailto:" + address
                self.claim(card, p, entity, wd.email_address,
                           StringValue(address))

        for p in card.all("URL"):
            url = p.text().strip()
            if url:
                self.claim(card, p, entity, wd.official_website,
                           StringValue(url))

        # Apple writes some handles twice: as IMPP, and as X-AIM.
        seen: Set[Tuple[str, str]] = set()
        for p in card.properties:
            if p.name == "IMPP":
                value = p.text().strip()
                scheme, colon, handle = value.partition(":")
                if not colon:
                    scheme, handle = "", value
                service = p.param("X-SERVICE-TYPE") or scheme
            elif p.name in IM_SERVICES:
                service, handle = IM_SERVICES[p.name], p.text().strip()
            else:
                continue
            key = (service.casefold(), handle.casefold())
            if not handle or key in seen:
                continue
            seen.add(key)
            s = self.claim(card, p, entity, wd.instant_messaging,
                           StringValue(handle))
            if service:
                s.add_qualifier(wd.service, StringValue(service))

        for p in card.all("X-SOCIALPROFILE"):
            url = p.text().strip()
            if url:
                s = self.claim(card, p, entity, wd.social_profile,
                               StringValue(url))
                for t in sorted(p.params.get("TYPE", [])):
                    if t.upper() != "PREF":
                        s.add_qualifier(wd.service, StringValue(t))

        for p in card.all("ADR"):
            place = self.address(card, p)
            if place is None:
                continue
            types = p.types()
            if organisation:
                prop = wd.headquarters_location
            elif "HOME" in types:
                prop = wd.residence
            elif "WORK" in types:
                prop = wd.work_location
            else:
                prop = wd.other_address
            self.claim(card, p, entity, prop, EntityValue(place.get_id()))

        for p in card.all("NOTE"):
            note = p.text().strip()
            if note:
                entity.claim(wd.note, TextValue(note))

    def claim(self,
              card: vcard.Card,
              p: vcard.Property,
              entity: Item,
              property_id: str,
              value: Value) -> Statement:
        """Add a contact detail, qualified by its types and label.

        A preferred detail has preferred rank."""
        types = p.types()
        rank = Rank.Preferred if "PREF" in types else None
        s = entity.claim(property_id, value, rank)
        for t in sorted(types):
            if t in CONTACT_TYPES:
                s.add_qualifier(self.wd.contact_type,
                                StringValue(CONTACT_TYPES[t]))
        label = card.label(p)
        if label and label != "HomePage":
            s.add_qualifier(self.wd.label, StringValue(label))
        return s

    def date(self, p: vcard.Property) -> Optional[Tuple[Optional[int],
                                                        int, int]]:
        """Return a date's year, or None if it has none, month and day."""
        match = DATE.match(p.text().strip())
        if match is None:
            self.unmapped["unreadable date"] += 1
            return None
        year, month, day = (int(g) for g in match.groups())
        if p.param("X-APPLE-OMIT-YEAR") is not None or year == NO_YEAR:
            return None, month, day
        return year, month, day

    def claim_time(self,
                   entity: Item,
                   property_id: str,
                   year: int,
                   month: int,
                   day: int) -> Optional[Statement]:
        """Add a date, or count it as unmapped if it isn't valid."""
        try:
            time = TimeValue(year, month, day)
        except ValueError:
            self.unmapped["invalid date"] += 1
            return None
        return entity.claim(property_id, time)

    def labelled_date(self,
                      person: Item,
                      p: vcard.Property,
                      label: str,
                      employers: List[Tuple[str, Statement]]):
        """Add a labelled date.

        "died" is the date of death; "<employer> start" and "<employer>
        end" qualify that employer; any other is a significant date."""
        wd = self.wd
        date = self.date(p)
        if date is None:
            return
        year, month, day = date
        if year is None:
            self.unmapped["labelled date without a year"] += 1
            return

        if label.casefold() == "died":
            self.claim_time(person, wd.date_of_death, year, month, day)
            return

        match = re.fullmatch(r"(.*\S)\s+(start|end)", label, re.IGNORECASE)
        if match is not None:
            for name, s in employers:
                if name.casefold() == match.group(1).casefold():
                    prop = wd.start_time \
                        if match.group(2).casefold() == "start" \
                        else wd.end_time
                    try:
                        s.add_qualifier(prop, TimeValue(year, month, day))
                    except ValueError:
                        self.unmapped["invalid date"] += 1
                    return

        s = self.claim_time(person, wd.significant_date, year, month, day)
        if s is not None and label:
            s.add_qualifier(wd.label, StringValue(label))

    def relate(self, person: Item, label: str, name: str):
        """Link a person to a related name.

        The name is linked to the card with that name, if there's only
        one; otherwise, to a stub person."""
        matches = [p for p in self.people.get(name.casefold(), [])
                   if p is not person]
        if len(matches) == 1:
            target = matches[0]
            self.linked += 1
        else:
            target = self.stub(person, label, name)

        prop = self.relations.get(label.casefold())
        s = person.claim(prop or self.wd.related_person,
                         EntityValue(target.get_id()))
        if prop is None and label:
            s.add_qualifier(self.wd.label, StringValue(label))

    def stub(self, person: Item, label: str, name: str) -> Item:
        """Return a stub person for a related name with no card.

        Stubs for full names are shared; a single word, such as a first
        name, might be anyone, so it gets a stub of its own, for the
        person and label it's related by."""
        shared = len(name.split()) > 1
        key = fold(name)
        if shared and key in self.stubs:
            return self.stubs[key]

        stub = self.item(f"stub:{key}" if shared else
                         f"stub:{person.get_id()}:{fold(label)}:{key}", name)
        stub.claim(self.wd.instance_of, EntityValue(self.wd.human))
        self.stub_count += 1
        if shared:
            self.stubs[key] = stub
        return stub

    def organisation(self, name: str) -> Item:
        """Return the organisation with a name, creating it if needed."""
        key = name.casefold()
        org = self.organisations.get(key)
        if org is None:
            org = self.item(f"organisation:{fold(name)}", name or None)
            org.claim(self.wd.instance_of, EntityValue(self.wd.organization))
            self.organisations[key] = org
        return org

    def department(self, parent: Item, name: str) -> Item:
        """Return a department of an organisation, creating it if needed."""
        key = (parent.get_id(), name.casefold())
        unit = self.departments.get(key)
        if unit is None:
            parent_label = parent.get_label()
            unit = self.item(f"department:{parent.get_id()}:{fold(name)}",
                             name,
                             f"department of {parent_label}"
                             if parent_label else None)
            unit.claim(self.wd.instance_of, EntityValue(self.wd.organization))
            unit.claim(self.wd.parent_organization,
                       EntityValue(parent.get_id()))
            self.departments[key] = unit
        return unit

    def name(self, cls: str, text: str) -> Item:
        """Return the given or family name item for a name."""
        key = (cls, text)
        item = self.names.get(key)
        if item is None:
            item = self.item(f"name:{cls}:{text}", text)
            item.claim(self.wd.instance_of, EntityValue(cls))
            self.names[key] = item
        return item

    def locality(self,
                 name: str,
                 region: Optional[str],
                 country: Optional[str]) -> Item:
        """Return the locality with a name, creating it if needed."""
        key = (country or "", region or "", name.casefold())
        item = self.localities.get(key)
        if item is None:
            wd = self.wd
            item = self.item("locality:" + "\x1f".join(key), name)
            item.claim(wd.instance_of, EntityValue(wd.human_settlement))
            if region is not None:
                item.claim(wd.located_in, EntityValue(region))
            if country is not None:
                item.claim(wd.country, EntityValue(country))
            self.localities[key] = item
        return item

    def address(self, card: vcard.Card, p: vcard.Property) -> Optional[Item]:
        """Return the place for an ADR, creating it if needed.

        The country is the ADR's, or else its region's, or else the one
        Apple's X-ABADR gives as the address's format.  Identical
        addresses share a place.  Returns None if the ADR is empty."""
        wd = self.wd
        pobox, extended, street, locality, region, postcode, country = \
            [c.strip() for c in p.components(7)[:7]]
        lines = "\n".join(line.strip()
                          for part in (pobox, extended, street)
                          for line in part.splitlines() if line.strip())
        if not (lines or locality or region or postcode or country):
            return None

        country_id = wd.find_country(country) if country else None
        if country and country_id is None:
            self.unmapped[f"country {country!r}"] += 1
        if country_id is None and not country and region:
            country_id = wd.region_country(region)
        if country_id is None and not country and p.group:
            code = next((q.text().strip() for q in card.properties
                         if q.group == p.group and q.name == "X-ABADR"), "")
            country_id = wd.find_country(code) if code else None

        region_id = None
        if region:
            region_id = wd.find_region(country_id, region)
            if region_id is None:
                if (country_id not in self.region_parents
                        and locality.casefold() in ("", region.casefold())):
                    # Such as London, in a UK address.
                    locality = region
                else:
                    self.unmapped[f"region {country or '?'}/{region}"] += 1

        key = (lines.casefold(), locality.casefold(),
               region_id or region.casefold(), postcode.casefold(),
               country_id or country.casefold())
        place = self.addresses.get(key)
        if place is not None:
            return place

        place_locality = self.locality(locality, region_id, country_id) \
            if locality else None
        first = lines.splitlines()[0] if lines else ""
        label = ", ".join(x for x in (first, locality) if x) \
            or postcode or region or country
        place = self.item("address:" + "\x1f".join(key), label)
        place.claim(wd.instance_of, EntityValue(wd.address))
        if lines:
            place.claim(wd.street_address, TextValue(lines))
        if postcode:
            place.claim(wd.postal_code, StringValue(postcode))
        if place_locality is not None:
            place.claim(wd.located_in, EntityValue(place_locality.get_id()))
        elif region_id is not None:
            place.claim(wd.located_in, EntityValue(region_id))
        if country_id is not None:
            place.claim(wd.country, EntityValue(country_id))
        self.addresses[key] = place
        return place


def summary(importer: Importer, kb: KnowledgeBase) -> str:
    """Return counts of what was read, and of any problems."""
    wd = importer.wd
    lines = []
    c = importer.cards
    groups = "group" if c["group"] == 1 else "groups"
    lines.append(f"{sum(c.values())} cards: {c['person']} people, "
                 f"{c['organisation']} organisations, {c['group']} {groups}")

    # Entities, by class.
    entities = list(kb.entities())
    items = [e for e in entities if isinstance(e, Item)]
    properties = [e for e in entities if isinstance(e, Property)]
    classes: Counter[str] = collections.Counter()
    for item in items:
        values = [s.get_value() for s in item.get_statements(wd.instance_of)]
        ids = [v.get_id() for v in values if isinstance(v, EntityValue)]
        classes[ids[0] if ids else ""] += 1
    lines.append(f"\n{len(items)} items, {len(properties)} properties")
    for cls, n in classes.most_common():
        item = kb.get_item(cls)
        label = item.get_label() if item is not None else None
        lines.append(f"  {n:6}  {label or 'unclassified'}")
    lines.append(f"  related names: {importer.linked} linked to cards, "
                 f"{importer.stub_count} stub people")

    # Statements, by property, and any that fail checks.
    statements: Counter[str] = collections.Counter()
    qualifiers = 0
    failures: Counter[str] = collections.Counter()
    for entity in entities:
        for s in entity.statements:
            statements[s.get_property_id()] += 1
            qualifiers += len(s.qualifiers)
            try:
                kb.check(s)
            except ValueError as e:
                failures[re.sub(r"[0-9a-f-]{36}", "<uuid>", str(e))] += 1
    lines.append(f"\n{sum(statements.values())} statements, "
                 f"{qualifiers} qualifiers")
    for pid, n in statements.most_common():
        prop = kb.get_property(pid)
        label = prop.get_label() if prop is not None else None
        name = pid if pid[:1] in "PQ" and pid[1:].isdigit() else "local"
        lines.append(f"  {n:6}  {label} ({name})")

    lines.append(f"\n{sum(failures.values())} statements fail checks")
    for message, n in failures.most_common():
        lines.append(f"  {n:6}  {message}")

    lines.append(f"\n{sum(importer.unmapped.values())} values not mapped")
    for what, n in importer.unmapped.most_common():
        lines.append(f"  {n:6}  {what}")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=argv[0], description="Import an address book, from a vCard "
        "file, into a knowledge base.")
    parser.add_argument("--db", help="SQLite database to import into")
    parser.add_argument("file", help="vCard file")
    parser.add_argument("names", nargs="*", metavar="NAME",
                        help="label of an entry to describe")
    args = parser.parse_args(argv[1:])

    store = Store(args.db) if args.db else None
    try:
        kb = KnowledgeBase(store)
        with kb.transaction():
            importer = Importer(kb, wikidata.load(kb))
            importer.read(vcard.read(args.file))
        print(summary(importer, kb))
        if store is not None:
            print(f"\n{store.writes} entities written to {args.db}")

        for name in args.names:
            print()
            entities = kb.find_items(name)
            if not entities:
                print(f"No entry labelled {name!r}")
            for entity in entities:
                print(kb.describe(entity.get_id()))
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
