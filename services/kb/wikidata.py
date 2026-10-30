# darqos
# Copyright (C) 2026 David Arnold

"""Base vocabulary from Wikidata, for the knowledge base examples.

Registers the properties and items the address book example needs,
with their Wikidata identifiers, plus a few local properties, for things
Wikidata has no clean match for, with fixed UUID identifiers, so they're
the same in every knowledge base.  The places are the countries and
regions the address book uses; its localities and addresses are created
as it's read.

The vocabulary is an import, whose statements are stated in Wikidata,
so loading it again updates it, rather than adding to it.

The identifiers, labels, datatypes and classes were checked against
Wikidata on 2026-10-30."""

from typing import Dict, List, Optional, Set, Tuple

from darq.kb import Datatype, EntityValue, Item
from kb import Import, KnowledgeBase


# Countries: identifier, label, class, and the names used for them,
# including their ISO 3166 codes.
_COUNTRIES = [
    ("Q408", "Australia", "Q6256", ["Australia", "Australian", "AU"]),
    ("Q40", "Austria", "Q6256", ["Austria", "AT"]),
    ("Q16", "Canada", "Q6256", ["Canada", "CA"]),
    ("Q148", "People's Republic of China", "Q6256", ["China", "CN"]),
    ("Q35", "Denmark", "Q6256", ["Denmark", "DK"]),
    ("Q142", "France", "Q6256", ["France", "FR"]),
    ("Q183", "Germany", "Q6256", ["Germany", "DE"]),
    ("Q668", "India", "Q6256", ["India", "IN"]),
    ("Q801", "Israel", "Q6256", ["Israel", "IL"]),
    ("Q145", "United Kingdom", "Q6256",
     ["United Kingdom", "UK", "GB", "Great Britain"]),
    ("Q30", "United States", "Q6256",
     ["United States", "United States of America", "USA", "US"]),
]

# Places that aren't countries, but are used as one in addresses:
# identifier, label, class, country, and names.
_TERRITORIES = [
    ("Q8646", "Hong Kong", "Q779415", "Q148", ["Hong Kong", "HK"]),
]

# Regions: identifier, label, class, country, and names.
_REGIONS = [
    ("Q3258", "Australian Capital Territory", "Q14192234", "Q408",
     ["ACT", "Australian Capital Territory"]),
    ("Q3224", "New South Wales", "Q5852411", "Q408",
     ["NSW", "New South Wales"]),
    ("Q36074", "Queensland", "Q5852411", "Q408", ["QLD", "Queensland"]),
    ("Q35715", "South Australia", "Q5852411", "Q408",
     ["SA", "South Australia"]),
    ("Q36687", "Victoria", "Q5852411", "Q408", ["VIC", "Victoria"]),
    ("Q1904", "Ontario", "Q11828004", "Q16", ["ON", "Ontario"]),
    ("Q816", "Arizona", "Q35657", "Q30", ["AZ", "Arizona"]),
    ("Q99", "California", "Q35657", "Q30", ["CA", "California"]),
    ("Q1261", "Colorado", "Q35657", "Q30", ["CO", "Colorado"]),
    ("Q779", "Connecticut", "Q35657", "Q30", ["CT", "Connecticut"]),
    ("Q812", "Florida", "Q35657", "Q30", ["FL", "Florida"]),
    ("Q1428", "Georgia", "Q35657", "Q30", ["GA", "Georgia"]),
    ("Q1204", "Illinois", "Q35657", "Q30", ["IL", "Illinois"]),
    ("Q1391", "Maryland", "Q35657", "Q30", ["MD", "Maryland"]),
    ("Q771", "Massachusetts", "Q35657", "Q30", ["MA", "Massachusetts"]),
    ("Q1581", "Missouri", "Q35657", "Q30", ["MO", "Missouri"]),
    ("Q759", "New Hampshire", "Q35657", "Q30", ["NH", "New Hampshire"]),
    ("Q1408", "New Jersey", "Q35657", "Q30", ["NJ", "New Jersey"]),
    ("Q1384", "New York", "Q35657", "Q30", ["NY", "New York"]),
    ("Q1454", "North Carolina", "Q35657", "Q30", ["NC", "North Carolina"]),
    ("Q1397", "Ohio", "Q35657", "Q30", ["OH", "Ohio"]),
    ("Q1400", "Pennsylvania", "Q35657", "Q30", ["PA", "Pennsylvania"]),
    ("Q1370", "Virginia", "Q35657", "Q30", ["VA", "Virginia"]),
]


# Classes' superclasses, as on Wikidata, of those it has.
_SUPERCLASSES = [
    ("Q2366457", ["Q9261468"]),
    ("Q486972", ["Q2221906"]),
    ("Q6256", ["Q1048835"]),
    ("Q5852411", ["Q10864048"]),
    ("Q35657", ["Q10864048"]),
    ("Q11828004", ["Q10864048"]),
    ("Q10864048", ["Q1048835"]),
    ("Q1048835", ["Q56061"]),
    ("Q56061", ["Q2221906", "Q43229"]),
]


class Vocabulary:
    """The base properties and items, registered with a KnowledgeBase.

    Attributes are the entities' identifiers, so wd.spouse is "P26"."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.source = Import(kb, "Q2013", "P248")
        p = self._property
        i = self._item

        # Wikidata properties.
        self.instance_of = p("P31", "instance of", Datatype.Item)
        self.subclass_of = p("P279", "subclass of", Datatype.Item)
        self.stated_in = p("P248", "stated in", Datatype.Item)
        self.country = p("P17", "country", Datatype.Item)
        self.located_in = p("P131", "located in the administrative "
                            "territorial entity", Datatype.Item)
        self.postal_code = p("P281", "postal code", Datatype.String)
        self.street_address = p("P6375", "street address", Datatype.Text)
        self.official_website = p("P856", "official website", Datatype.Url)
        self.email_address = p("P968", "email address", Datatype.Url)
        self.phone_number = p("P1329", "phone number", Datatype.String)
        self.fax_number = p("P2900", "fax number", Datatype.String)
        self.date_of_birth = p("P569", "date of birth", Datatype.Time)
        self.date_of_death = p("P570", "date of death", Datatype.Time)
        self.spouse = p("P26", "spouse", Datatype.Item)
        self.child = p("P40", "child", Datatype.Item)
        self.father = p("P22", "father", Datatype.Item)
        self.mother = p("P25", "mother", Datatype.Item)
        self.parent = p("P8810", "parent", Datatype.Item)
        self.sibling = p("P3373", "sibling", Datatype.Item)
        self.unmarried_partner = p("P451", "unmarried partner", Datatype.Item)
        self.employer = p("P108", "employer", Datatype.Item)
        self.start_time = p("P580", "start time", Datatype.Time)
        self.end_time = p("P582", "end time", Datatype.Time)
        self.headquarters_location = p("P159", "headquarters location",
                                       Datatype.Item)
        self.residence = p("P551", "residence", Datatype.Item)
        self.work_location = p("P937", "work location", Datatype.Item)
        self.nickname = p("P1449", "nickname", Datatype.Text)
        self.birth_name = p("P1477", "birth name", Datatype.Text)
        self.given_name = p("P735", "given name", Datatype.Item)
        self.family_name = p("P734", "family name", Datatype.Item)
        self.parent_organization = p("P749", "parent organization or unit",
                                     Datatype.Item)
        self.has_parts = p("P527", "has part(s)", Datatype.Item)

        # Local properties, with fixed identifiers.
        self.birthday = p(
            "06931729-e677-4143-adfb-f0e3b421784e", "birthday",
            Datatype.String,
            "Day and month of birth, as --MM-DD, when the year isn't known")
        self.significant_date = p(
            "fc0719cc-07ff-495e-88cc-0289baf20811", "significant date",
            Datatype.Time, "A date, described by its label")
        self.label = p(
            "eb7b4373-5013-4608-8528-3eaf5f0af5a1", "label",
            Datatype.String, "Address book label for a value (qualifier)")
        self.contact_type = p(
            "c90439bb-a2c2-4f2d-876b-34098530874d", "contact type",
            Datatype.String,
            "Use of a contact detail, such as work, home or mobile "
            "(qualifier)")
        self.department = p(
            "c8139b4a-6575-4849-8067-13da5f5f608c", "department",
            Datatype.Item, "Department of an employer (qualifier)")
        self.job_title = p(
            "8af9b299-8ce6-419b-9521-e160e1c922dd", "job title",
            Datatype.String)
        self.instant_messaging = p(
            "f4a646ef-50fd-47a1-b785-9b5d83e47a2b",
            "instant messaging address", Datatype.String)
        self.service = p(
            "b4031891-785f-4e54-bc48-c185d20de4f7", "service",
            Datatype.String, "Service of an address or profile (qualifier)")
        self.social_profile = p(
            "3b7a1820-241f-406a-a2f4-0c34b6c9c2f2", "social profile",
            Datatype.Url)
        self.related_person = p(
            "6be66fa4-1bea-4a3c-9b87-2aef048317a3", "related person",
            Datatype.Item)
        self.other_address = p(
            "4422f2ff-5375-4ed5-9e56-b243ce827c0e", "other address",
            Datatype.Item)
        self.note = p(
            "fea1107a-9668-4706-b784-fcf7f40867f7", "note", Datatype.Text)
        self.vcard_uid = p(
            "dcf85e3f-6247-42f7-b2b3-e5c77fa1b987", "vCard UID",
            Datatype.ExternalId)

        # Sources, and classes.
        i("Q2013", "Wikidata")
        self.human = i("Q5", "human").get_id()
        self.organization = i("Q43229", "organization").get_id()
        self.group_of_humans = i("Q16334295", "group of humans").get_id()
        self.given_name_class = i("Q202444", "given name").get_id()
        self.family_name_class = i("Q101352", "family name").get_id()
        self.country_class = i("Q6256", "country").get_id()
        self.human_settlement = i("Q486972", "human settlement").get_id()
        self.address = i("Q319608", "address").get_id()
        self.department_class = i("Q2366457", "department").get_id()
        i("Q5852411", "state of Australia")
        i("Q14192234", "mainland territory of Australia")
        i("Q35657", "U.S. state")
        i("Q11828004", "province of Canada")
        i("Q779415", "special administrative region of China")

        # Superclasses, so the classes' instances are of the right kinds
        # (see darq.kb.kind_of): only those direct on Wikidata, enough to
        # reach organizational subdivision, or geographic location.
        # Mainland territories of Australia, and special administrative
        # regions of China, aren't reached.
        i("Q9261468", "organizational subdivision")
        i("Q2221906", "geographic location")
        i("Q56061", "administrative territorial entity")
        i("Q1048835", "political territorial entity")
        i("Q10864048", "first-level administrative division")
        for cls, superclasses in _SUPERCLASSES:
            item = self.kb.get(cls)
            assert item is not None
            for superclass in superclasses:
                item.claim(self.subclass_of, EntityValue(superclass))

        # Places, and the names used for them.  A territory is used as
        # a country in addresses.
        self.countries: Dict[str, str] = {}
        self.regions: Dict[Tuple[str, str], str] = {}
        self.region_countries: Dict[str, Set[str]] = {}

        for qid, label, cls, names in _COUNTRIES:
            i(qid, label, cls)
            self._names(self.countries, names, qid)
        for qid, label, cls, country, names in _TERRITORIES:
            i(qid, label, cls).claim(self.country, EntityValue(country))
            self._names(self.countries, names, qid)
        for qid, label, cls, country, names in _REGIONS:
            i(qid, label, cls).claim(self.country, EntityValue(country))
            for name in names:
                self.regions[(country, name.casefold())] = qid
                self.region_countries.setdefault(
                    name.casefold(), set()).add(country)

        self.source.finish()

    def _property(self,
                  pid: str,
                  label: str,
                  datatype: Datatype,
                  desc: Optional[str] = None) -> str:
        """Register a property, and return its identifier."""
        return self.source.property(pid, label, datatype, desc).get_id()

    def _item(self, qid: str, label: str, cls: Optional[str] = None) -> Item:
        """Register an item, an instance of a class if given."""
        item = self.source.item(qid, label)
        if cls is not None:
            item.claim(self.instance_of, EntityValue(cls))
        return item

    @staticmethod
    def _names(table: Dict[str, str], names: List[str], qid: str):
        for name in names:
            table[name.casefold()] = qid

    def find_country(self, name: str) -> Optional[str]:
        """Return the identifier of a country, by any of its names."""
        return self.countries.get(name.strip().casefold())

    def find_region(self, country: Optional[str], name: str) -> Optional[str]:
        """Return the identifier of a region, by any of its names.

        Without a country, the name must belong to a single country's
        region."""
        name = name.strip().casefold()
        if country is None:
            countries = self.region_countries.get(name, set())
            if len(countries) != 1:
                return None
            country = next(iter(countries))
        return self.regions.get((country, name))

    def region_country(self, name: str) -> Optional[str]:
        """Return the country of the only regions with a name, if any."""
        countries = self.region_countries.get(name.strip().casefold(), set())
        return next(iter(countries)) if len(countries) == 1 else None


def load(kb: KnowledgeBase) -> Vocabulary:
    """Register the base vocabulary with a KnowledgeBase, and return it."""
    return Vocabulary(kb)
