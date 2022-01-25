#! /usr/bin/env python
# Copyright (C) 2022 David Arnold

# * Define and implement an API and protocol for the service
# * Define and implement a database schema for the service
# * Write a generic GUI client
#   * Able to CRUD types.
#   * Able to CRUD arbitrary data.
# * Figure out how this relates to the MIME-ish types system
# * Design and implement interface to vCard / CardDAV
# * Design and implement interface to vCalendar / CalDAV (birthdays, etc)
# * Research ISO standards for place addresses
# * Research LinkedIn import/export
# * Design and implement interface to OpenStreetMap / etc
# * Write dedicated person UI
# * Write dedicated place UI
# * Research means of sharing both types and information
#   * And ideally, some sort of standard handling for duplicates and
#     translation between local and remote representations

# Is it reasonable to try to replace MacOS contacts app using this as
# backend and a dedicated front-end?

import sys
import typing
from urllib.parse import urlparse
from datetime import datetime

#import PyQt5
#from PyQt5 import QtCore, QtWidgets
#from PyQt5.QtGui import QColor, QKeySequence, QMouseEvent, QIcon, QPixmap
#from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QMenu, QShortcut, QHBoxLayout, QVBoxLayout, QLineEdit, QLabel, QToolButton, QToolBar, qApp, QAction, QTableWidget, QTableWidgetItem

#from darq.type.text import TextTypeView
#from darq.rt.history import History


class AttributeType:
    def __init__(self, name: str, value_type: typing.Type):
        self._name: str = name
        self._type: typing.Type = value_type
        return

    def name(self):
        return self._name

    def value_type(self):
        return self._type


class Attribute:
    def __init__(self, attribute_type: AttributeType, value):
        self._type = attribute_type
        self._value = value
        return


class ThingType:
    def __init__(self, name: str):
        self._name: str = name
        self._required_attributes: typing.List[AttributeType] = []
        self._required_relationships: typing.List[str] = []
        return

    def add_attribute_requirement(self, attribute_type: AttributeType):
        self._required_attributes.append(attribute_type)
        return


class Thing:
    """An object, entity, or thing of relevance."""
    def __init__(self, thing_type: ThingType):
        self._type = thing_type
        self._attributes = []
        self._relationships = []
        self._start_time = None
        self._end_time = None
        self._tags: typing.List[str] = []
        return

    def set_attribute(self, attribute_type: AttributeType, value):
        attr = Attribute(attribute_type, value)
        self._attributes.append(attr)
        return

    def set_times(self, start, end):
        self._start_time = start
        self._end_time = end
        return


class RoleType:
    """Constraints on a role in a relationship."""
    def __init__(self, name: str, subject_type: ThingType):
        self._name: str = name
        self._type: ThingType = subject_type
        self._minimum: int = 0
        self._maximum: int = -1  # infinite
        return

    def set_cardinality(self, minimum: int, maximum: int):
        # FIXME: check values greater-or-equal -1 (assuming using -1 for inf)
        self._minimum = minimum
        self._maximum = maximum
        return


class Role:
    def __init__(self, role_type: RoleType, thing: Thing):
        # FIXME: check thing matches RoleType._type
        self._type = role_type
        self._value = thing
        return


class RelationshipType:
    """A category of relationships."""
    def __init__(self, name: str):
        self._name: str = name
        self._role_types = []
        self._tags: typing.List[str] = []
        return

    def add_role_type(self, role_type: RoleType):
        self._role_types.append(role_type)
        return


class Relationship:
    """A relationship, with one or more things taking its roles."""
    def __init__(self, relationship_type: RelationshipType):
        self._type: RelationshipType = relationship_type
        self._roles = []
        self._start_time = None
        self._end_time = None
        return

    def set_role(self, role_type: RoleType, thing: Thing):
        role = Role(role_type, thing)
        self._roles.append(role)
        return

    def set_times(self, start, end):
        self._start_time = start
        self._end_time = end
        return


def main():
    #app = QApplication(sys.argv)
    #ui = UI()
    #sys.exit(app.exec_())

    person_full_name = AttributeType('person_full_name', str)
    person_type = ThingType('person')
    person_type.add_attribute_requirement(person_full_name)

    spouse_role_type = RoleType('spouse', person_type)
    spouse_role_type.set_cardinality(2, 2)

    married_relationship_type = RelationshipType('married')
    married_relationship_type.add_role_type(spouse_role_type)

    me = Thing(person_type)
    me.set_attribute(person_full_name, 'David John Arnold')
    me.set_times("1969-03-13", "")

    luba = Thing(person_type)
    luba.set_attribute(person_full_name, 'Louise Elizabeth Gough')

    me_and_luba = Relationship(married_relationship_type)
    me_and_luba.set_role(spouse_role_type, me)
    me_and_luba.set_role(spouse_role_type, luba)


if __name__ == "__main__":
    main()
