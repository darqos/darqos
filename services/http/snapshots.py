# darqos
# Copyright (C) 2026 David Arnold

"""Keeping what's at a URL (see doc/http-service.rst).

A snapshot's an ordinary item, of its real type, recorded on the URL's
item.  It knows nothing of how things are fetched or served, so it's
tested with what stands in for them."""

import dataclasses
import hashlib
import logging
import pathlib
import typing
import urllib.parse

import darq
import darq.history
import darq.kb as model
import darq.message
import darq.url

import fetcher


# When a type can't take what was fetched, as well as fetcher's.
NO_TYPE = "no type"

# History source for its events.
SOURCE = "http"

# Times a URL's item is written, when it's being changed elsewhere.
RECORD_ATTEMPTS = 3

# What's read of a file at a time, to hash it.
HASH_CHUNK = 1 << 20


class History(typing.Protocol):
    def add_event(self, item_id: str, event: str, source: str) -> typing.Any: ...


class MakeItem(typing.Protocol):
    def __call__(self, media_type: str, extension: str, data: bytes) -> str | None: ...


class Fetch(typing.Protocol):
    def __call__(self, url: str, headers: list[tuple[str, str]], path: pathlib.Path) -> fetcher.Response: ...


@dataclasses.dataclass
class Outcome:
    """What a snapshot came to: empty items if there are none."""

    http_status: int
    url_item: str = ""
    item: str = ""
    changed: bool = False


def file_hash(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def extension_of(url: str) -> str:
    """Return the extension of a URL's path, without its dot."""
    return pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).suffix.removeprefix(".")


def header(response: fetcher.Response, name: str) -> str:
    """Return one of a response's headers, or an empty string."""
    return next((value for key, value in response.headers if key.lower() == name.lower()), "")


def conditions(latest: darq.url.Snapshot | None) -> list[tuple[str, str]]:
    """Return the headers that ask for something newer than a snapshot."""
    if latest is None:
        return []
    headers = [("If-None-Match", latest.etag)] if latest.etag else []
    return headers + ([("If-Modified-Since", latest.last_modified)] if latest.last_modified else [])


def permanent_chain(url: str, response: fetcher.Response) -> list[tuple[str, int]]:
    """Return the URLs a fetch moved through by permanent redirects, each
    with the status that led to it, stopping at the first that isn't."""
    chain = [(url, 0)]
    for hop in response.hops:
        if hop.http_status not in fetcher.PERMANENT:
            break
        try:
            chain.append((darq.serialize_url(hop.target), hop.http_status))
        except darq.InvalidURLError:
            break
    return chain


class Snapshots:
    def __init__(self, kb: model.KB, make_item: MakeItem, history: History | None):
        """Constructor.

        :param kb: Client that writes through the implementation.
        :param make_item: Makes an item from data, returning its identifier,
                          or None if no type will take it.
        :param history: Where to record what's made, if anywhere."""
        self.kb = kb
        self.make_item = make_item
        self.history = history

    def known_latest(self, url: str) -> list[darq.url.Snapshot]:
        """Return the latest snapshots of a URL, and of where it's known
        to have moved, last first."""
        found = []
        entity = self.kb.get(darq.url.entity_id(url), follow=False)
        seen = set()
        while entity is not None and entity.id not in seen:
            seen.add(entity.id)
            latest = darq.url.latest_snapshot(entity)
            if latest is not None:
                found.insert(0, latest)
            moved = darq.url.moved_to(entity)
            entity = self.kb.get(moved, follow=False) if moved else None
        return found

    def take(self, url: str, fetch: Fetch, path: pathlib.Path, now: model.TimeValue) -> Outcome:
        """Keep what's at a URL.

        A permanent redirect is a move: the URL it leads to gets an item,
        and the one it's from says so.  If the content's what was there
        before the move, the new URL has the same snapshot, so both lead
        to one item.

        :param fetch: Fetches it, with some headers, into a file.
        :param path: The file to use.
        :param now: When it's being fetched.
        :raises fetcher.FetchError: if it can't be done."""
        fetcher.check_url(url)
        try:
            location = darq.serialize_url(url)
        except darq.InvalidURLError as e:
            raise fetcher.FetchError(fetcher.INVALID_URL, str(e)) from None

        known = self.known_latest(location)
        response = fetch(location, conditions(known[0] if known else None), path)
        chain = permanent_chain(location, response)
        urls = [address for address, _ in chain]
        here = urls[-1]

        for address in urls:
            if self.kb.get(darq.url.entity_id(address), follow=False) is not None:
                self.note(darq.url.item_id(address), darq.history.FETCHED)

        latest = known[0] if known else None
        unmodified = response.http_status == 304 and latest is not None
        if not unmodified and not 200 <= response.http_status < 300:
            existing = self.kb.get(darq.url.entity_id(location), follow=False)
            return Outcome(response.http_status, darq.url.item_id(location) if existing is not None else "",
                           latest.item_id if latest is not None else "")

        digest = latest.hash if unmodified and latest is not None else file_hash(path)
        for address in urls[1:]:
            known += self.known_latest(address)
        same = next((snapshot for snapshot in known if snapshot.hash == digest), None)

        media_type = response.media_type.partition(";")[0].strip().lower()
        if same is not None:
            item_id, changed = same.item_id, False
        else:
            made = self.make_item(media_type, extension_of(response.final_url), path.read_bytes())
            if made is None:
                raise fetcher.FetchError(NO_TYPE, media_type or "no media type")
            item_id, changed = made, True

        # What's kept goes on the URL it's at, unless that's its latest.
        here_entity = self.kb.get(darq.url.entity_id(here), follow=False)
        here_latest = darq.url.latest_snapshot(here_entity) if here_entity is not None else None
        if here_latest is None or here_latest.item_id != item_id:
            kept = same if same is not None and unmodified else None
            snapshot = darq.url.Snapshot(item_id, digest, (kept.etag if kept else header(response, "ETag")),
                                         kept.last_modified if kept else header(response, "Last-Modified"))
            final = response.final_url if darq.serialize_url(response.final_url) != here else ""
            self.record(here, darq.url.snapshot_statement(snapshot, now, media_type, final))

        self.record_moves(chain, now)
        if len(urls) == 1:
            self.end_move(here, now)
        if changed:
            self.note(item_id, darq.history.IMPORTED)
        return Outcome(response.http_status, darq.url.item_id(here), item_id, changed)

    def note(self, item_id: str, event: str):
        """Record an event in History, if it's running."""
        if self.history is None:
            return
        try:
            self.history.add_event(item_id=item_id, event=event, source=SOURCE)
        except darq.DarqError as e:
            logging.warning(f"Can't record {event} for {item_id}: {e!r}")

    @staticmethod
    def source() -> model.Reference:
        return model.Reference().add(model.STATED_IN, model.EntityValue(darq.url.HTTP_SERVICE))

    def ensure(self, url: str):
        """Make a URL's item, if it's new, and record that it was, and
        that it's been fetched."""
        entity_id = darq.url.entity_id(url)
        if self.kb.get(entity_id, follow=False) is not None:
            return
        entity = model.Item(entity_id)
        entity.set_label(url)
        entity.claim(darq.url.URL, model.StringValue(url)).add_reference(self.source())
        try:
            self.kb.create(darq.url.URL_TYPE, entity)
        except model.ConflictError:
            return
        self.note(darq.url.item_id(url), darq.history.CREATED)
        self.note(darq.url.item_id(url), darq.history.FETCHED)

    def change(self, url: str, change: typing.Callable[[model.Entity], bool]):
        """Change a URL's item, as it is, if change says it did."""
        entity_id = darq.url.entity_id(url)
        for _ in range(RECORD_ATTEMPTS):
            entity = self.kb.get(entity_id, follow=False)
            if entity is None or not change(entity):
                return
            try:
                self.kb.put(entity)
                return
            except model.ConflictError:
                continue
        raise fetcher.FetchError(fetcher.FAILED, f"{url} keeps being changed")

    def record(self, url: str, snapshot: model.Statement):
        """Add a snapshot to a URL's item, which is made if it's new."""
        snapshot.add_reference(self.source())
        self.ensure(url)

        def add(entity: model.Entity) -> bool:
            entity.add_statement(snapshot)
            return True

        self.change(url, add)

    def record_moves(self, chain: list[tuple[str, int]], now: model.TimeValue):
        """Say that each URL of a chain moved to the next, from the last
        back, since a link needs its target to exist."""
        for (url, _), (target, status) in reversed(list(zip(chain, chain[1:]))):
            self.ensure(target)
            self.ensure(url)
            target_id = darq.url.entity_id(target)

            def add(entity: model.Entity, target_id: str = target_id, status: int = status) -> bool:
                if darq.url.moved_to(entity) == target_id:
                    return False
                self.end_moves(entity, now)
                statement = entity.claim(darq.url.MOVED_TO, model.EntityValue(target_id))
                statement.add_qualifier(darq.url.RETRIEVED, now)
                statement.add_qualifier(darq.url.HTTP_STATUS, model.StringValue(str(status)))
                statement.add_reference(self.source())
                return True

            self.change(url, add)

    @staticmethod
    def end_moves(entity: model.Entity, now: model.TimeValue) -> bool:
        """End a URL's moves that aren't over: False if it had none."""
        ended = False
        for statement in entity.get_statements(darq.url.MOVED_TO):
            if not any(q.property_id == model.END_TIME for q in statement.qualifiers):
                statement.add_qualifier(model.END_TIME, now)
                ended = True
        return ended

    def end_move(self, url: str, now: model.TimeValue):
        """A URL that's answered for itself has come back, if it moved."""
        self.change(url, lambda entity: self.end_moves(entity, now))
