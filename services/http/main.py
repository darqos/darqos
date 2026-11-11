# darqos
# Copyright (C) 2026 David Arnold

# HTTP service: fetching from the web, registered as darq.http.
#
# It serves the Http protocol (lib/didl/http.didl), as described in
# doc/http-service.rst: plain requests, of any method, which make no
# items, and snapshots, which keep what's at a URL as an item of its real
# type, recorded on the URL's item.
#
# A fetch can take a long time, so each runs on a thread of its own, and
# is answered from there, through Deferred: the service goes on answering
# others, and Cancel, meanwhile.  Bodies go to files in the run directory,
# readable only by its owner.

import logging
import os
import pathlib
import tempfile
import threading
import time
import typing

import darq
import darq.kb as model
import darq.launch
import darq.message
import darq.url
from darq.protocols.http import (
    Cancel,
    Fetched,
    Header,
    Http,
    RequestRequest,
    RequestResponse,
    SnapshotRequest,
    SnapshotResponse,
    Status,
)
from darq.protocols.kb import EntityWriter
from darq.protocols.type import Status as TypeStatus
from darq.protocols.type import Type

import fetcher
import snapshots


# Run directory, if the p-kernel didn't say (see doc/distribution.rst).
DEFAULT_RUN_DIR = "/darq/run"

# Directory for bodies being fetched, in the run directory.
SPOOL = "http"

# Bytes a fetch can be, unless it says.
DEFAULT_MAX_SIZE = 64 * 1024 * 1024

# Most fetches at once: others wait for one.
MAX_FETCHES = 8

# Seconds between reports of progress.
PROGRESS_INTERVAL = 0.25

# Seconds to wait for other services' replies: a large item takes a while.
TIMEOUT = 60.0

STATUSES = {
    fetcher.INVALID_URL: Status.INVALID_URL,
    fetcher.UNSUPPORTED_SCHEME: Status.UNSUPPORTED_SCHEME,
    fetcher.TOO_LARGE: Status.TOO_LARGE,
    fetcher.NETWORK: Status.NETWORK,
    fetcher.TIMEOUT: Status.TIMEOUT,
    fetcher.CANCELLED: Status.CANCELLED,
    fetcher.FAILED: Status.FAILED,
    snapshots.NO_TYPE: Status.NO_TYPE,
}


class Reporter:
    """Tells a client how a fetch is going, now and then."""

    def __init__(self, fetch_id: int, listener: int | None):
        self.fetch_id = fetch_id
        self.listener = listener
        self.next_report = 0.0

    def __call__(self, done: int, total: int):
        if self.listener is None or time.monotonic() < self.next_report:
            return
        self.next_report = time.monotonic() + PROGRESS_INTERVAL
        try:
            darq.send(self.listener, Fetched(id=self.fetch_id, done=done, total=total))
        except darq.DarqError as e:
            logging.debug(f"Can't report progress: {e!r}")
            self.listener = None


class HttpService(Http.Handler):
    """Answers the Http protocol."""

    def __init__(self, spool: pathlib.Path, user_agent: str):
        self.spool = spool
        self.user_agent = user_agent
        self.slots = threading.Semaphore(MAX_FETCHES)

        # What stops each fetch that has an id, by its id.
        self.running: dict[int, threading.Event] = {}
        self.lock = threading.Lock()

    def writer(self) -> model.KB:
        """Return a KB client that writes through its implementation,
        which may have exited since last time."""
        types = Type.Client(darq.connect("darq.type"), timeout=TIMEOUT)
        activated = types.activate(type_id=darq.url.URL_TYPE)
        if activated.status != TypeStatus.OK or activated.implementation is None:
            raise fetcher.FetchError(fetcher.FAILED, f"Can't activate {darq.url.URL_TYPE}: {activated.status.name}")
        return model.KB.connect(TIMEOUT, EntityWriter.Client(activated.implementation, timeout=TIMEOUT))

    def run(self, job: typing.Callable[[pathlib.Path, threading.Event], None], fetch_id: int, listener: int | None):
        """Run a fetch on a thread of its own, with a file for its body."""
        stop = threading.Event()
        if fetch_id:
            with self.lock:
                self.running[fetch_id] = stop

        def work():
            with self.slots:
                descriptor, name = tempfile.mkstemp(dir=self.spool)
                os.close(descriptor)
                try:
                    job(pathlib.Path(name), stop)
                finally:
                    os.unlink(name)
                    if fetch_id:
                        with self.lock:
                            self.running.pop(fetch_id, None)
                    if listener is not None:
                        try:
                            darq.close_port(listener)
                        except darq.DarqError as e:
                            logging.debug(f"Can't close a listener: {e!r}")
                    darq.release_reply_port()

        threading.Thread(target=work, name="fetch", daemon=True).start()

    def request(self, request: RequestRequest, source: int) -> darq.Deferred:
        deferred = self.defer()
        limit = request.max_size or DEFAULT_MAX_SIZE
        method = request.method.upper() or "GET"
        headers = [(h.name, h.value) for h in request.headers]
        report = Reporter(request.id, request.listener)

        def job(path: pathlib.Path, stop: threading.Event):
            try:
                response = fetcher.fetch(method, request.url, headers, request.body, limit, self.user_agent, path,
                                         report, stop.is_set)
                reply = RequestResponse(http_status=response.http_status, final_url=response.final_url,
                                        media_type=response.media_type,
                                        headers=[Header(n, v) for n, v in response.headers],
                                        body=path.read_bytes(), status=Status.OK)
            except fetcher.FetchError as e:
                logging.info(f"{method} failed: {e.kind}: {e}")
                reply = RequestResponse(status=STATUSES[e.kind])
            except Exception:
                logging.exception(f"{method} failed")
                reply = RequestResponse(status=Status.FAILED)
            deferred.reply(reply)

        self.run(job, request.id, request.listener)
        return deferred

    def snapshot(self, request: SnapshotRequest, source: int) -> darq.Deferred:
        deferred = self.defer()
        limit = request.max_size or DEFAULT_MAX_SIZE
        report = Reporter(request.id, request.listener)

        def job(path: pathlib.Path, stop: threading.Event):
            def fetch(url: str, headers: list[tuple[str, str]], path: pathlib.Path) -> fetcher.Response:
                return fetcher.fetch("GET", url, headers, b"", limit, self.user_agent, path, report, stop.is_set)

            try:
                keeper = snapshots.Snapshots(self.writer(), darq.launch.import_data)
                now = darq.message.time_value(time.time_ns() // 1000)
                outcome = keeper.take(request.url, fetch, path, now)
                reply = SnapshotResponse(http_status=outcome.http_status, url_item=outcome.url_item,
                                         item=outcome.item, changed=outcome.changed, status=Status.OK)
            except fetcher.FetchError as e:
                logging.info(f"Snapshot failed: {e.kind}: {e}")
                reply = SnapshotResponse(status=STATUSES[e.kind])
            except Exception:
                logging.exception("Snapshot failed")
                reply = SnapshotResponse(status=Status.FAILED)
            deferred.reply(reply)

        self.run(job, request.id, request.listener)
        return deferred

    def cancel(self, request: Cancel, source: int) -> None:
        with self.lock:
            stop = self.running.get(request.id)
        if stop is not None:
            stop.set()


def prepare_spool(run_dir: str) -> pathlib.Path:
    """Return the spool directory, emptied of what a crash left."""
    spool = pathlib.Path(run_dir) / SPOOL
    spool.mkdir(parents=True, exist_ok=True)
    spool.chmod(0o700)
    for left in spool.iterdir():
        left.unlink(missing_ok=True)
    return spool


def darq_main():
    spool = prepare_spool(os.environ.get("DARQ_RUN", DEFAULT_RUN_DIR))
    user_agent = f"DarqOS/{darq.get_version().version}"
    darq.serve("darq.http", HttpService(spool, user_agent))
