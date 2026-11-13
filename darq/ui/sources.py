# darqos
# Copyright (C) 2026 David Arnold

"""Where an import's data comes from (see doc/tools.rst): an item, as
everything else is, so it's recorded, and can be imported again.

An item to import is fetched from a URL, by the HTTP service, which makes
it, as Search does; or chosen from the items of its kind that came in, by
name, newest first; or made from a local file, as the Terminal's Import
File does.  A local directory, as a Slack export can be, is read as it
is, for development, while there's a host underneath.

Once it's imported, the import's own item, such as an address book,
links to the item it was imported from (*imported from*), so it's offered
first, the next time, with Fetch Again, if it came from a URL; and the
item's described as what it is.

The functions use no Qt, and run on any thread: SourceChooser, the
widget each import tool shows, runs them off Qt's."""

import dataclasses
import logging
import mimetypes
import os
import pathlib
import typing

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget

import darq
import darq.history
import darq.items
import darq.kb as model
import darq.launch
import darq.url
from darq.protocols.http import Status as HttpStatus
from darq.protocols.kb import Context as KBContext
from darq.protocols.kb import EntityItem
from darq.protocols.type import Context as TypeContext

from . import dialogs
from .launcher import FETCH_FAILURES
from .names import item_names


# Control exports
__all__ = ["SourceChooser", "Source", "SourceError", "accepting", "candidates", "describe", "fetch", "import_file",
           "previous", "read", "remember", "runner"]

# Seconds to wait for services' replies: a large item takes a while.
TIMEOUT = 60.0

# Most events read from History, looking for items to offer, and most
# items offered.
EVENTS = 500
OFFERED = 40


class SourceError(Exception):
    """Why a source can't be had, in words."""


@dataclasses.dataclass
class Source:
    """Where an import's data comes from: an item, or, for development, a
    local directory, read as it is; with the URL it was fetched from, if
    it was, to fetch again, or the local file it was made from, if it
    was, beside which there can be more, as a signature."""

    item_id: str = ""
    name: str = ""
    path: str = ""
    url: str = ""
    file: str = ""


# Says whether an item's of the kind an import reads: its media type, and
# its file's name, either of which can be empty.
Accepts = typing.Callable[[str, str], bool]


def accepting(media_types: typing.Iterable[str], extensions: typing.Iterable[str]) -> Accepts:
    """Return what accepts items of some media types, or whose files have
    some extensions, without their dots."""
    types = {t.lower() for t in media_types}
    endings = {f".{e.lower()}" for e in extensions}
    return lambda media_type, file_name: (media_type.split(";")[0].strip().lower() in types
                                          or any(file_name.lower().endswith(e) for e in endings))


def fetch(url: str, http: typing.Any) -> Source:
    """Fetch what's at a URL, as an item, with the HTTP service, or use
    the last that was, if the server says it's unchanged.

    :raises SourceError: if it can't be had."""
    if http is None:
        raise SourceError("The HTTP service isn't running")
    try:
        response = http.snapshot(url=url)
    except (darq.DarqError, TimeoutError) as e:
        raise SourceError(f"Can't fetch {url}: {e}") from None
    if response.status != HttpStatus.OK:
        raise SourceError(FETCH_FAILURES[response.status].format(url=url))
    if not response.item:
        raise SourceError(f"{url}: the server said {response.http_status}")
    name = url.rstrip("/").rsplit("/", 1)[-1] or url
    return Source(item_id=response.item, name=name, url=url)


def import_file(path: str, source: str) -> Source:
    """Make an item of a local file, of the first type that takes it, or
    as data, recorded as imported, from an import, with the file's path.

    :raises SourceError: if it can't be read, or made."""
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError as e:
        raise SourceError(f"Can't read {path}: {e.strerror}") from None
    name = os.path.basename(path)
    media_type = mimetypes.guess_type(name)[0] or ""
    extension = os.path.splitext(name)[1].removeprefix(".")
    try:
        item_id = darq.launch.import_data(media_type, extension, data, name,
                                          TypeContext(event=darq.history.IMPORTED, source=source, note=path))
    except (darq.DarqError, TimeoutError) as e:
        raise SourceError(f"Can't import {name}: {e}") from None
    if item_id is None:
        raise SourceError(f"No type can hold {name}")
    return Source(item_id=item_id, name=name, file=path)


def read(source: Source) -> bytes:
    """Return a source item's data.

    :raises SourceError: if it can't be read."""
    try:
        data, _ = darq.launch.export_item(source.item_id)
    except (darq.DarqError, TimeoutError) as e:
        raise SourceError(f"Can't read {source.name or source.item_id}: {e}") from None
    return data


def candidates(kb: model.KB, history: typing.Any, accepts: Accepts) -> list[Source]:
    """Return the items an import could read, newest first: those History
    says were imported, of the kinds it accepts."""
    if history is None:
        return []
    try:
        events = history.get_events(events=[darq.history.IMPORTED], max=EVENTS).events
    except (darq.DarqError, TimeoutError) as e:
        logging.warning(f"Can't read History: {e!r}")
        return []
    item_ids = list(dict.fromkeys(e.item_id for e in events))
    try:
        found, _ = kb.get_entities([darq.split_item_id(i)[1] for i in item_ids])
    except (darq.DarqError, TimeoutError, LookupError) as e:
        logging.warning(f"Can't read the items: {e!r}")
        return []
    entities = {e.id: e for e in found if not e.destroyed}
    offered = []
    for item_id in item_ids:
        entity = entities.get(darq.split_item_id(item_id)[1])
        if entity is None:
            continue
        media_type, file_name = model.first_string(entity, darq.items.MEDIA_TYPE), \
            model.first_string(entity, darq.items.FILE_NAME)
        if accepts(media_type, file_name):
            offered.append(item_id)
        if len(offered) == OFFERED:
            break
    names = item_names(kb, offered)
    return [Source(item_id=i, name=names.get(i) or i) for i in offered]


def remember(kb: model.KB, entity_id: str, source: Source):
    """Say that an import's own item, such as an address book, was last
    imported from a source item: the only link it has of the kind."""
    if not source.item_id:
        return
    try:
        entity = kb.get(entity_id, follow=False)
        if entity is None:
            return
        for statement in entity.get_statements(darq.items.IMPORTED_FROM):
            entity.remove_statement(statement)
        entity.claim(darq.items.IMPORTED_FROM, model.EntityValue(darq.split_item_id(source.item_id)[1]))
        kb.put(entity)
    except (darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError) as e:
        logging.warning(f"Can't remember what {entity_id} was imported from: {e!r}")


def previous(kb: model.KB, entity_id: str) -> Source | None:
    """Return the item an import's own item was last imported from, if
    it's there, with the URL it was fetched from, if it was."""
    try:
        entity = kb.get(entity_id)
        if entity is None:
            return None
        targets = [s.get_value() for s in entity.get_statements(darq.items.IMPORTED_FROM)]
        target = next((v.entity_id for v in targets if isinstance(v, model.EntityValue)), "")
        item_id = darq.items.item_of(kb, target) if target else ""
        if not item_id:
            return None
        url = ""
        for url_id in kb.find_links(target, darq.url.SNAPSHOT):
            found = kb.get(url_id)
            url = model.first_string(found, darq.url.URL) if found is not None else ""
            if url:
                break
    except (darq.DarqError, TimeoutError, LookupError) as e:
        logging.warning(f"Can't find what {entity_id} was imported from: {e!r}")
        return None
    return Source(item_id=item_id, name=item_names(kb, [item_id]).get(item_id) or item_id, url=url)


def describe(item_id: str, description: str):
    """Describe a source item as what it is, such as "vCard address book,
    imported as Address book", through its type, unless it says something
    of itself already: it isn't an edit."""
    try:
        port = darq.launch.open_entity(item_id)
    except (darq.DarqError, TimeoutError) as e:
        logging.warning(f"Can't describe {item_id}: {e!r}")
        return
    try:
        client = EntityItem.Client(port, timeout=TIMEOUT)
        got = client.get_entity(languages=["en"])
        entity = model.entity_from_wire(got.entity)
        if entity.get_description() != description:
            entity.set_description(description)
            client.put_entity(model.entity_to_wire(entity), KBContext(quiet=True))
        client.close()
    except (darq.DarqError, TimeoutError, ValueError) as e:
        logging.warning(f"Can't describe {item_id}: {e!r}")
    finally:
        darq.close_port(port)


def runner(loop: typing.Any) -> typing.Callable[[typing.Callable[[], typing.Any], typing.Callable[[typing.Any], None]],
                                                None]:
    """Return what runs a job on a thread of its own, and then calls done
    with what it returned, or the exception it raised, on Qt's, by a
    QtEventLoop's post."""
    from .launcher import start_thread

    def run(job: typing.Callable[[], typing.Any], done: typing.Callable[[typing.Any], None]):
        def thread():
            try:
                outcome = job()
            except Exception as e:
                logging.exception("A job failed")
                outcome = e
            darq.release_reply_port()
            try:
                loop.post(lambda: done(outcome))
            except RuntimeError:
                logging.info("A job finished after its window")

        start_thread(thread)

    return run


class SourceChooser(QWidget):
    """Where an import's data comes from, as a row: what's chosen, and
    buttons to fetch a URL, choose an item, or bring in a local file, or
    choose a local directory; and to fetch again what came from a URL.

    What's slow, fetching, reading History, and making an item, is run
    off Qt's thread, by run(job, done), which calls done on Qt's."""

    def __init__(self, parent: QWidget, source: str, accepts: Accepts,
                 run: typing.Callable[[typing.Callable[[], typing.Any], typing.Callable[[typing.Any], None]], None],
                 file_filter: str = "All files (*)", directory: bool = False,
                 on_change: typing.Callable[[], None] = lambda: None):
        """Constructor.

        :param source: The import's name, History's source of what it
                       brings in, such as "vcard-import".
        :param directory: Whether a local source is a directory, read as
                          it is, rather than a file, made an item."""
        super().__init__(parent)
        self.tool = source
        self.accepts = accepts
        self.run = run
        self.file_filter = file_filter
        self.directory = directory
        self.on_change = on_change
        self.source: Source | None = None

        # Clients of the KB, History and HTTP, once they're needed: tests
        # replace them.
        self.kb: typing.Callable[[], model.KB] = lambda: model.KB.connect(timeout=TIMEOUT)
        self.history: typing.Callable[[], typing.Any] = darq.history.connect
        self.http: typing.Callable[[], typing.Any] = self.connect_http

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("From:", self))
        self.shown = QLabel("Nothing chosen", self)
        row.addWidget(self.shown, 1)
        self.buttons: list[QPushButton] = []
        for text, action in (("URL ...", self.ask_url), ("Item ...", self.ask_item),
                             ("Local directory ..." if directory else "Local file ...", self.ask_local)):
            button = QPushButton(text, self)
            button.clicked.connect(action)
            row.addWidget(button)
            self.buttons.append(button)
        self.again = QPushButton("Fetch again", self)
        self.again.clicked.connect(self.fetch_again)
        self.again.setEnabled(False)
        row.addWidget(self.again)

    @staticmethod
    def connect_http() -> typing.Any:
        from darq.protocols.http import Http
        handle = darq.lookup_name("darq.http", blocking=False)
        return Http.Client(handle, timeout=TIMEOUT) if handle is not None else None

    def choose(self, source: Source | None, said: str = ""):
        """Make a source the one chosen, or say why there's none."""
        self.source = source
        if source is None:
            self.shown.setText(said or "Nothing chosen")
        else:
            self.shown.setText(source.name + (f"  ({source.url})" if source.url else ""))
            self.shown.setToolTip(source.item_id or source.path)
        self.again.setEnabled(source is not None and bool(source.url))
        self.on_change()

    def set_enabled(self, enabled: bool):
        for button in self.buttons:
            button.setEnabled(enabled)
        self.again.setEnabled(enabled and self.source is not None and bool(self.source.url))

    def offer_previous(self, entity_id: str):
        """Offer what an import's own item was last imported from."""
        def done(found: typing.Any):
            if isinstance(found, Source):
                self.choose(found)
        self.run(lambda: previous(self.kb(), entity_id), done)

    def ask_url(self):
        url = dialogs.ask_text(self, "Import", "Fetch from what URL?", self.source.url if self.source else "")
        if url:
            self.fetch(url)

    def fetch_again(self):
        if self.source is not None and self.source.url:
            self.fetch(self.source.url)

    def fetch(self, url: str):
        self.shown.setText(f"Fetching {url} ...")
        self.run(lambda: self.attempt(lambda: fetch(url, self.http())), self.got)

    def ask_item(self):
        self.shown.setText("Looking for items ...")

        def found(offered: typing.Any):
            if isinstance(offered, Exception) or not offered:
                self.choose(self.source, "" if offered else "There are no items of this kind: fetch or import one")
                return
            chosen = dialogs.choose(self, "Import", "Import from which?", [s.name for s in offered])
            self.choose(offered[chosen] if chosen is not None else self.source)

        self.run(lambda: candidates(self.kb(), self.history(), self.accepts), found)

    def ask_local(self):
        # Qt's own dialogs: the compositor has no file chooser portal.
        home = str(pathlib.Path.home())
        if self.directory:
            path = QFileDialog.getExistingDirectory(self, "Import", home, QFileDialog.Option.DontUseNativeDialog)
            if path:
                self.choose(Source(name=os.path.basename(path), path=path))
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import", home, self.file_filter,
                                              options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.shown.setText(f"Importing {os.path.basename(path)} ...")
            self.run(lambda: self.attempt(lambda: import_file(path, self.tool)), self.got)

    @staticmethod
    def attempt(job: typing.Callable[[], Source]) -> Source | SourceError:
        try:
            return job()
        except SourceError as e:
            return e

    def got(self, outcome: typing.Any):
        if isinstance(outcome, Source):
            self.choose(outcome)
        else:
            self.choose(None, str(outcome))
