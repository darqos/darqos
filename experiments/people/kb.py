# Wikibase-inspired knowledge base implementation.

# So ... integration
#
# I think I want Items to be registered with the index service, and
# the history service.  Not sure about metadata ... how does that fit
# with properties?

#
# Tools:
# - Wikidata JSON lookup by item/property and label
# - Import definition from JSON
# - Import definition from vCard


import sys
import uuid
import vobject

from enum import Enum
from typing import Optional, List

from darq.rt.kb import *


# PROPERTIES = {}
# ITEMS = {}
#
#
# class Datatype(Enum):
#     String = 1
#     Integer = 2
#     IRI = 3
#
#
# class Rank(Enum):
#     Preferred = 1
#     Normal = 2
#     Deprecated = 3
#
#
# class Snak:
#     def __init__(self, property: str = "", value = None):
#         self.property: str = property
#         # Value Type: value, None, Some ?
#         self.value = value
#
#
# class Qualifier:
#     def __init__(self, property: str, value):
#         self.snak: Snak = Snak(property, value)
#
#
# class Reference:
#     def __init__(self):
#         self.snaks: list[Snak] = []
#
#
# class Claim:
#     def __init__(self, property: str = "",
#                  value = None,
#                  qualifiers: Optional[List[Qualifier]] = None):
#         self.snak: Snak = Snak(property, value)
#         self.qualifiers = qualifiers or []
#
#     def add_qualifier(self, property, value):
#         self.qualifiers.append(Qualifier(property, value))
#
#
# class Statement:
#     def __init__(self, claim: Optional[Claim] = None,
#                  references: Optional[List[Reference]] = None,
#                  rank = Rank.Normal):
#         self.claim = claim or Claim()
#         self.references = references or []
#         self.rank = rank
#
#
# class Fingerprint:
#     def __init__(self):
#         self.label: str = ""
#         self.description: str = ""
#         self.aliases: List[str] = []
#
#
# class Property:
#     def __init__(self, iid: str = ""):
#         if iid is None or len(iid) == 0:
#             self.id = str(uuid.uuid4())
#         else:
#             self.id = iid
#
#         self.fingerprint: Fingerprint = Fingerprint()
#         self.statements: List[Statement] = []
#         self.datatype: Datatype = None
#
#     def get_id(self):
#         return self.id
#
#     def set_label(self, label: str):
#         self.fingerprint.label = label
#
#     def get_label(self) -> str:
#         return self.fingerprint.label
#
#     def set_description(self, description: str):
#         self.fingerprint.description = description
#
#     def set_datatype(self, data_type: Datatype):
#         self.datatype = data_type
#
#     def add_alias(self, alias: str):
#         self.fingerprint.aliases.append(alias)
#
#     def add_statement(self, property: 'Property', value, qualifiers):
#         s = Statement(property, value)
#         self.statements.append(s)
#
#
# class Item:
#     def __init__(self, iid: Optional[str] = None):
#         if iid is None or len(iid) == 0:
#             self.id = str(uuid.uuid4())
#         else:
#             self.id = iid
#
#         self.fingerprint: Fingerprint = Fingerprint()
#         self.statements: List[Statement] = []
#         # site links?
#
#     def get_id(self):
#         return self.id
#
#     def set_label(self, label: str):
#         self.fingerprint.label = label
#
#     def get_label(self) -> str:
#         return self.fingerprint.label
#
#     def set_description(self, description: str):
#         self.fingerprint.description = description
#
#     def add_alias(self, alias: str):
#         self.fingerprint.aliases.append(alias)
#
#     def add_statement(self, property: Property, value, qualifiers):
#         s = Statement(property, value)
#         self.statements.append(s)
#
#
# def add_item(label: str, item_id: str = None, desc: str = None):
#     """Create and register an item."""
#     item = Item(item_id)
#     item.set_label(label)
#     item.set_description(desc)
#
#     ITEMS[label] = item
#     return item
#
#
# def find_item(label: str) -> Optional[Item]:
#     """Look up item by label"""
#     for i in ITEMS.values():
#         if i.get_label() == label:
#             return i
#
#
# def add_property(label: str, prop_id: str = None):
#     """Create and register a property."""
#     p = Property(prop_id)
#     p.set_label(label)
#
#     PROPERTIES[label] = p
#     return p
#
#
# def find_property(label: str) -> Optional[Property]:
#     """Look up property by label."""
#     for p in PROPERTIES.values():
#         if p.get_label() == label:
#             return p
#

################################################################

api = KnowledgeBase.api()

# Create the Item and Property instances needed to represent vCard data.
# These will use our internal identifiers, but the WikiData structure.
# Where there's a correspondence to an external ontology's concept, this
# can be recorded as a property of the definition.

# Human
# A human person.
# WikiData Q5
person_item = api.add_item("human", desc="a human person")
# Needs a bunch of properties and qualifiers ...

start_time_qualifier = api.add_property("start time")
end_time_qualifier = api.add_property("end time")

# WikiData P735: given name
given_name_property = api.add_property("given name")
# optionally, should have date qualifiers for name changes

# WikiData P734: family name
family_name_property = api.add_property("family name")
# optionally, should have date qualifiers for name changes

# WikiData P1449: nickname
nick_name_property = api.add_property("nickname")
# optionally, should have date qualifiers for name changes

# I think there's no need to try to record gender or sex, so WikiData P21
# is probably irrelevant here.
preferred_pronouns_property = api.add_property("preferred pronouns")
# optionally, should have date qualifiers for changes

# WikiData P569. date of birth
date_of_birth_property = api.add_property("date of birth")

# WikiData P19: place of birth (city)
place_of_birth_property = api.add_property("place of birth")

#
date_of_death_property = api.add_property("date of death")

# WikiData P20: place of death (city)
place_of_death_property = api.add_property("place of death")

# WikiData P856: website (as part of vCard)
url_property = api.add_property("url")

# WikiData P968: email address (as part of vCard)
email_address_property = api.add_property("email address")

# WikiData P1329: phone number, no tel: prefix
phone_number_property = api.add_property("phone number", "P1329")


# P279: subclass of
# P2918: PO Box (as part of vCard)
# P25: mother
# P3448: step-mother
# P1038: relative (qualify with P1039 for type of relationship)
# P6375: full street address
# P669: located on street; street part of address
# P690: street number
# P281: post code
# P40: child (not step-child)
# P625: geo-coordinates, WGS84 only
# P1628: equivalent property in other ontology
# P22: father
# P39: position
# P3373: sibling
# P26: spouse
# P463: member of (clubs, associations, etc)
# P17: country
# P18: image
# P5423: floor number
# P131: administrative region; county
# P4595: post town (city)
# P1334: coordinates of easternmost point
# P1335: coordinates of westernmost point
# P1332: coordinates of northernmost point
# P8017: generational suffix (Snr, Jnr, etc.  III ?)
# P2561: name


def test():

    item = Item("Q42")
    item.set_label("Douglas Adams")
    item.set_description("English writer and humorist")
    item.add_alias("Douglas Noel Adams")
    item.add_alias("Douglas Noël Adams")
    item.add_alias("Douglas N. Adams")

    # Wikibase JSON has Claims as the top-level, with Properties under
    # that, where each Property is named (ie. Claims is a dict), and
    # the Property is a list of dicts.  Those dicts are Statements.
    # identified by a "type": "statement" attribute in the dict.
    item.add_statement(api.find_property("educated at"),
                       StringValue("St John's College"))

    prop = Property("P26")
    prop.set_label("spouse")
    prop.set_description("the subject has the object as their spouse. "
                         "use 'unmarried partner (P451) for non-married "
                         "companions.")
    prop.add_alias("wife")
    prop.add_alias("husband")
    prop.add_alias("married to")

    # Me
    david = Item()
    david.set_label("David Arnold")
    david.set_description("")

    # Luba
    louise = Item()
    louise.set_label("Louise Gough")
    louise.set_description("")

    david.add_statement(api.find_property("spouse"),
                        louise.get_id(),
                        [Qualifier(api.find_property("start time").get_id(),
                                   "1998/11/14")])

    # It'd be nice if 'inverse property' (P1696) was set on eg. spouse,
    # this statement could be added automatically.  Not sure it'd be
    # safe to clone the qualifiers though?  Maybe?
    louise.add_statement(api.find_property("spouse"),
                         david.get_id(),
                         [Qualifier(api.find_property("start time").get_id(),
                                    "1998/11/14")])
    return


def create_from_vcard_object(vc):

    item = Item()
    #item. set subtype_of property to 'human' (unless it's a company)

    for line in vc.lines():

        name: str = line.name.upper()
        params = line.params
        value = line.value

        if name == 'FN':
            item.set_label(vc.fn.value)

        elif name == 'N':
            #print(dir(value))
            print("prefix: ", value.prefix)
            print("given:  ", value.given)
            print("family: ", value.family)
            print("suffix: ", value.suffix)
            print("additional: ", value.additional)

        elif name == 'EMAIL':
            print("Email")
            print(dir(value))

        elif name == 'TEL':
            print("Phone")
            print(value)

        elif name == 'ADR':
            print("Address")
            #print(dir(value))
            print("box: ", value.box)
            print("city: ", value.city)
            print("code: ", value.code)
            print("country: ", value.country)
            print("extended: ", value.extended)
            print("region: ", value.region)
            print("street: ", value.street)
            #print("one_line: ", value.one_line)
            #print("lines: ", str(value.lines))

        elif name == 'VERSION':
            print("version:", value)

        else:
            print("Unhandled: ", name)

    if hasattr(vc, 'x_abshowas') and vc.x_abshowas == 'COMPANY':
        # flag this item as an organisation, not a person
        pass

    # url(s)
    # UID

    #item.set_description()

    #item.add_statement(find_property("xxx"),
    #                   value,
    #                   qualifiers)

    # etc
    return item


def import_from_vcard(name: str) -> int:

    f = open(name)
    lines = f.readlines()
    f.close()

    count = 0
    tmp = ""
    for line in lines:
        tmp += line
        if line.strip() != "END:VCARD":
            continue

        v = vobject.readOne(tmp)
        #v.prettyPrint()

        item = create_from_vcard_object(v)

        print("=" * 72)
        tmp = ""
        count += 1

    return count


if __name__ == "__main__":
    test()
    #print("Total: %u\n" % import_from_vcard("/Users/d/Documents/Backups/vCards-20220306.vcf"))
    #print("Total: %u\n" % import_from_vcard("/Users/d/Documents/Backups/co.vcf"))
    #print("Total: %u\n" % import_from_vcard("/Users/d/work/personal/darqos/experiments/people/samples/google-company-cafeloup.vcf"))
