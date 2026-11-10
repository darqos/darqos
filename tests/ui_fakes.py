# darqos
# Copyright (C) 2026 David Arnold

# Fakes of the services the Terminal's parts use (darq.ui): the Type,
# History, Index and HTTP services, and darq.launch, for the Terminal's
# tests, the launcher's, and those of the parts themselves.

from darq.errors import DarqError, LaunchError
from darq.protocols.history import Event, GetEventsResponse
from darq.protocols.history import Status as HistoryStatus
from darq.protocols.http import SnapshotResponse
from darq.protocols.http import Status as HttpStatus
from darq.protocols.index import Result
from darq.protocols.type import (
    Description,
    DescribeResponse,
    FindLensesResponse,
    ListTypesResponse,
    Status,
)


TEXT = "public.utf8-plain-text"
DOC = f"{TEXT}:0123456789abcdef0123456789abcdef"
PDF = "com.adobe.pdf:0123456789abcdef0123456789abcdef"


class FakeTypes:
    def __init__(self, types=None):
        # Type identifier to (name, has a lens).
        self.types = {TEXT: ("UTF-8 Text", True), "public.png": ("PNG", False)} if types is None else types

    def list_types(self, implemented):
        assert implemented
        return ListTypesResponse(type_ids=sorted(self.types))

    def describe(self, type_id):
        name, _ = self.types[type_id]
        return DescribeResponse(description=Description(name=name, description=f"About {name}"),
                                status=Status.OK)

    def find_lenses(self, type_id, context):
        assert context == "terminal"
        return FindLensesResponse(lenses=["/lens"] if self.types[type_id][1] else [], status=Status.OK)


class FakeHistory:
    def __init__(self):
        self.added = []
        self.events: list[Event] = []
        self.queries = []

    def add_event(self, item_id, event, source):
        self.added.append((item_id, event, source))

    def get_events(self, **query):
        self.queries.append(query)
        if any(len(name) > 31 for name in query["events"]):
            return GetEventsResponse(status=HistoryStatus.INVALID_ARGUMENT)
        end = query["end"] or 2**62
        events = [e for e in self.events if query["start"] <= e.time < end]
        return GetEventsResponse(events=events[:query["max"]], status=HistoryStatus.OK)


class FakeLaunch:
    def __init__(self):
        self.created = []
        self.names: list[str] = []
        self.used = []
        self.fail = False

    def create_item(self, type_id):
        if self.fail:
            raise LaunchError(f"Can't activate {type_id}")
        self.created.append(type_id)
        return DOC, 42

    def use_item(self, item_id, item=None):
        if self.fail:
            raise LaunchError(f"No lens for {item_id}")
        self.used.append((item_id, item))
        return 7

    # Types by extension, for find_types, and those whose create_item_from
    # refuses data.
    extensions = {"txt": [TEXT], "jpg": ["public.jpeg"], "dat": ["public.png", "public.jpeg"]}
    refuses: set[str] = {"public.png"}

    def find_types(self, mime_type="", extension=""):
        return self.extensions.get(extension, [])

    def create_item_from(self, type_id, data, name=""):
        if type_id in self.refuses:
            raise LaunchError(f"Can't create a {type_id} item from data: INVALID_DATA")
        self.created.append((type_id, data))
        self.names.append(name)
        return DOC, 43


class FakeIndex:
    """Stands in for darq.index: finds the items whose text has every
    word of what's searched for."""

    def __init__(self):
        # Item identifier to text.
        self.items: dict[str, str] = {}
        self.searches = []
        self.fail = False

    def search(self, text, type_ids=(), max=0, offset=0):
        self.searches.append(text)
        if self.fail:
            raise DarqError("the Index isn't running")
        words = text.lower().split()
        found = [Result(item_id, body, 1_800_000_000_000_000) for item_id, body in self.items.items()
                 if all(w in body.lower() for w in words)]
        return found[offset:offset + max] if max else found[offset:]


class FakeHttp:
    """Stands in for an Http.Client: says what snapshot asks it to."""

    def __init__(self):
        self.response = SnapshotResponse(http_status=200, url_item="public.url:u", item=PDF, changed=True,
                                         status=HttpStatus.OK)
        self.fetched: list[str] = []
        self.error: Exception | None = None

    def snapshot(self, url):
        self.fetched.append(url)
        if self.error is not None:
            raise self.error
        return self.response


class FakeServices:
    def __init__(self, types=None):
        self.fake_http: FakeHttp | None = FakeHttp()
        self.types = FakeTypes(types)
        self.history = FakeHistory()
        self.index = FakeIndex()
        self.launch = FakeLaunch()
        self.calls = []

    def http(self):
        return self.fake_http

    def shutdown(self):
        self.calls.append("shutdown")

    def reboot(self):
        self.calls.append("reboot")


def inline(function):
    """Run a launch, or post its result, at once: see darq.ui.launcher."""
    function()
