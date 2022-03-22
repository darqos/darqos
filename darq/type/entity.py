# Copyright (C) 2020-2021 David Arnold

import sys
import uuid

from darq.type.base import *
from darq.rt.storage import Storage
from darq.rt.history import History, Event

from PyQt5.QtWidgets import QApplication, QWidget, QTextEdit, QGridLayout
from PyQt5 import QtCore


class Entity(Object):

    def __init__(self):
        """Constructor."""
        super().__init__()
        return

    @staticmethod
    def new():
        return Entity()

    @staticmethod
    def from_bytes(buffer: bytes) -> "Entity":
        entity = Entity()
        #FIXME
        return entity


class Person(Entity):

    def __init__(self):
        super().__init__()

        self.sort_key = ""
        self.names = ""
        self.birth = None
        self.death = None

        # FIXME: should be a list of Places, with a relationship type?
        # FIXME: eg. place is 40 Hay St, and type is "home" ?
        self.addresses = []

        self.phones = []
        self.emails = []
        self.webs = []
        self.ims = []
        self.identifiers = []  # eg. Twitter name, GitHub name, etc.

        # FIXME: should be a list of people, with relationship type?
        # FIXME: eg. Louise Gough, and type is "spouse"?
        self.relationships = []

        # FIXME: should be a list of entities, with relationship type?
        # FIXME: eg. Mantara, and types is "employer"
        # FIXME: relationship would need time range too
        self.jobs = []
        self.notes = ""

        self.drivers_licence = []

        # FIXME: eg. country, number, issued, expires, etc.
        self.passport = []
        self.ssn = None
        self.abn = None
        self.tfn = None

        # etc
        return

#address_reltype = darq.rt.base.lookup_or_create_type("entity.address", "FIXME")

class Organisation(Entity):
    """A company, association, or other group of people."""
    def __init__(self):
        super().__init__()

        self.name = ""
        # FIXME: membership probably needs a time range
        self.members = []
        self.notes = ""

        self.locations = []
        self.phones = []
        self.emails = []
        self.webs = []
        self.identifiers = []

        return


class Place:
    def __init__(self):
        self.name = ""
        self.centre = None
        self.contained_by = None
        self.outline = []
        return
