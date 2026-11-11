# darqos
# Copyright (C) 2026 David Arnold

"""Asking Wikidata, from a lens or tool (see doc/wikidata.rst): a Linker,
with requests made through the HTTP service, saying who's asking."""

import darq
import darq.kb as model
import darq.wikidata
from darq.protocols.http import Header, Http
from darq.protocols.http import Status as HttpStatus
from darq.protocols.kb import Context as KBContext
from darq.ui import kb_writer


# Control exports
__all__ = ["agent", "fetch", "linker"]


# Seconds to wait for Wikidata, and the services, and most bytes of an
# answer.
TIMEOUT = 60
MAX_ANSWER = 16 * 1024 * 1024


def agent() -> str:
    """Return the User-Agent to ask with.

    :raises darq.wikidata.WikidataError: if the operator hasn't said who
                                         they are."""
    try:
        version = darq.get_version().version
    except (darq.DarqError, AttributeError, TimeoutError):
        version = "2"
    return darq.wikidata.user_agent(version, darq.wikidata.contact())


def fetch(url: str, headers: list[tuple[str, str]]) -> bytes:
    """Fetch a URL with the HTTP service, on the calling thread.

    :raises darq.wikidata.WikidataError: if it can't be done."""
    handle = darq.lookup_name("darq.http", blocking=False)
    if handle is None:
        raise darq.wikidata.WikidataError("The HTTP service isn't running")
    client = Http.Client(handle, timeout=TIMEOUT)
    try:
        response = client.request(method="GET", url=url, headers=[Header(name=k, value=v) for k, v in headers],
                                  body=b"",
                                  max_size=MAX_ANSWER, id=0, listener=None)
    except (darq.DarqError, TimeoutError) as e:
        raise darq.wikidata.WikidataError(f"Can't reach Wikidata: {e}") from None
    if response.status != HttpStatus.OK:
        raise darq.wikidata.WikidataError(f"Can't reach Wikidata: {response.status.name}")
    if response.http_status != 200:
        raise darq.wikidata.WikidataError(f"Wikidata said {response.http_status}")
    return response.body


def linker(source: str = "") -> darq.wikidata.Linker:
    """Return a Linker, writing through the KB type implementation, so
    that the Index is kept up to date, on the calling thread: which
    records what it merges and changes in History, from a source, noted
    as from Wikidata (see doc/history.rst).

    :raises darq.wikidata.WikidataError: if it can't be had."""
    try:
        kb = model.KB.connect(timeout=TIMEOUT)
        items = kb_writer.writer(kb)
        items.context = KBContext(source=source, note="from Wikidata")
    except (darq.DarqError, TimeoutError) as e:
        raise darq.wikidata.WikidataError(f"Can't reach the KB: {e}") from None
    return darq.wikidata.Linker(kb, items, fetch, agent())
