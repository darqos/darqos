# darqos
# Copyright (C) 2026 David Arnold

"""A fetch, with libcurl, into a file: the HTTP service's use of the
network (see doc/http-service.rst).  It knows nothing of items, or of
Darq, so it's tested alone.

A body goes to a file, rather than memory, so that an unbounded response
can't exhaust it.  HTTP/3 is used where libcurl has it, falling back to
HTTP/2 or HTTP/1.1 by itself."""

import dataclasses
import pathlib
import typing
import urllib.parse

import pycurl


# Schemes it fetches: a redirect can't lead to others.
SCHEMES = ("http", "https")

# Most redirects followed.
MAX_REDIRECTS = 10

# Statuses that are redirects, and those that say it's for good.
REDIRECTS = (301, 302, 303, 307, 308)
PERMANENT = (301, 308)

# Headers that aren't sent on to another host.
PRIVATE_HEADERS = ("authorization", "cookie")

# Seconds to connect, and that a fetch can make no progress.
CONNECT_TIMEOUT = 30
STALL_TIMEOUT = 60


# What can go wrong.
INVALID_URL = "invalid url"
UNSUPPORTED_SCHEME = "unsupported scheme"
TOO_LARGE = "too large"
NETWORK = "network"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
FAILED = "failed"

# Which of those each of libcurl's errors is: others are NETWORK.
CURL_ERRORS = {
    pycurl.E_URL_MALFORMAT: INVALID_URL,
    pycurl.E_UNSUPPORTED_PROTOCOL: UNSUPPORTED_SCHEME,
    pycurl.E_OPERATION_TIMEDOUT: TIMEOUT,
    pycurl.E_PARTIAL_FILE: TIMEOUT,
    pycurl.E_ABORTED_BY_CALLBACK: CANCELLED,
    pycurl.E_FILESIZE_EXCEEDED: TOO_LARGE,
}


class FetchError(Exception):
    """A fetch failed: kind is what went wrong, of those above."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


class Hop(typing.NamedTuple):
    """A redirect: where it was, what it said, and where it led."""

    url: str
    http_status: int
    target: str


@dataclasses.dataclass
class Response:
    """What came back: the body's in the file given to fetch()."""

    http_status: int
    final_url: str
    media_type: str
    headers: list[tuple[str, str]]
    size: int

    # The redirects that led here, in order.
    hops: list[Hop] = dataclasses.field(default_factory=list)


def check_url(url: str) -> str:
    """Return a URL, if it can be fetched.

    :raises FetchError: if it can't."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise FetchError(INVALID_URL, str(e)) from None
    if not parts.scheme or not parts.netloc and parts.scheme in SCHEMES:
        raise FetchError(INVALID_URL, url)
    if parts.scheme.lower() not in SCHEMES:
        raise FetchError(UNSUPPORTED_SCHEME, parts.scheme)
    return url


def http_version() -> int | None:
    """Return the version to ask for: HTTP/3, if libcurl has it."""
    has_http3 = pycurl.version_info()[4] & getattr(pycurl, "VERSION_HTTP3", 0)
    return pycurl.CURL_HTTP_VERSION_3 if has_http3 else None


def origin(url: str) -> tuple[str, str]:
    """Return the scheme and host of a URL."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), (parts.netloc or "").lower()


def fetch(method: str, url: str, headers: typing.Iterable[tuple[str, str]], body: bytes, max_size: int,
          user_agent: str, path: pathlib.Path,
          progress: typing.Callable[[int, int], None] = lambda done, total: None,
          cancelled: typing.Callable[[], bool] = lambda: False) -> Response:
    """Make a request, following redirects, with the last response's body
    going to a file.

    Redirects are followed here, a hop at a time, as what was said at
    each is part of the answer: the response's hops.  A request's
    credentials don't follow it to another host.

    :param max_size: Most bytes of body to take.
    :param progress: Told the bytes fetched, and the total, if it's known.
    :param cancelled: Asked now and then whether to stop.
    :raises FetchError: if it can't be done."""
    check_url(url)
    sent = list(headers)
    hops: list[Hop] = []
    while True:
        response = request(method, url, sent, body, max_size, user_agent, path, progress, cancelled)
        location = next((v for k, v in response.headers if k.lower() == "location"), "")
        if response.http_status not in REDIRECTS or not location:
            response.hops = hops
            return response
        if len(hops) == MAX_REDIRECTS:
            raise FetchError(NETWORK, f"more than {MAX_REDIRECTS} redirects")
        target = check_url(urllib.parse.urljoin(url, location))
        hops.append(Hop(url, response.http_status, target))
        if origin(target) != origin(url):
            sent = [(k, v) for k, v in sent if k.lower() not in PRIVATE_HEADERS]
        if response.http_status == 303 or (response.http_status in (301, 302) and method not in ("GET", "HEAD")):
            method, body = "GET", b""
        url = target


def request(method: str, url: str, headers: list[tuple[str, str]], body: bytes, max_size: int, user_agent: str,
            path: pathlib.Path, progress: typing.Callable[[int, int], None],
            cancelled: typing.Callable[[], bool]) -> Response:
    """Make one request, without following a redirect."""
    received: list[tuple[str, str]] = []
    size = 0
    too_large = False

    def header(line: bytes):
        text = line.decode("iso-8859-1").strip()
        if text.startswith("HTTP/"):
            received.clear()
        elif ":" in text:
            name, _, value = text.partition(":")
            received.append((name.strip(), value.strip()))

    def write(data: bytes):
        nonlocal size, too_large
        size += len(data)
        if size > max_size:
            too_large = True
            return 0
        output.write(data)
        return None

    def transfer(total: int, done: int, upload_total: int, uploaded: int):
        progress(done, total)
        return 1 if cancelled() else 0

    curl = pycurl.Curl()
    try:
        with open(path, "wb") as output:
            curl.setopt(pycurl.URL, url)
            curl.setopt(pycurl.USERAGENT, user_agent)
            curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
            curl.setopt(pycurl.CONNECTTIMEOUT, CONNECT_TIMEOUT)
            curl.setopt(pycurl.LOW_SPEED_LIMIT, 1)
            curl.setopt(pycurl.LOW_SPEED_TIME, STALL_TIMEOUT)
            curl.setopt(pycurl.ACCEPT_ENCODING, "")
            curl.setopt(pycurl.NOSIGNAL, True)
            curl.setopt(pycurl.MAXFILESIZE_LARGE, max_size)
            curl.setopt(pycurl.HTTPHEADER, [f"{name}: {value}" for name, value in headers])
            curl.setopt(pycurl.HEADERFUNCTION, header)
            curl.setopt(pycurl.WRITEFUNCTION, write)
            curl.setopt(pycurl.NOPROGRESS, False)
            curl.setopt(pycurl.XFERINFOFUNCTION, transfer)
            version = http_version()
            if version is not None:
                curl.setopt(pycurl.HTTP_VERSION, version)
            if method == "HEAD":
                curl.setopt(pycurl.NOBODY, True)
            elif method != "GET":
                curl.setopt(pycurl.CUSTOMREQUEST, method)
                curl.setopt(pycurl.POSTFIELDS, body)
            try:
                curl.perform()
            except pycurl.error as e:
                if too_large:
                    raise FetchError(TOO_LARGE, f"over {max_size} bytes") from None
                raise FetchError(CURL_ERRORS.get(int(e.args[0]), NETWORK), str(e.args[-1])) from None
        media_type = str(curl.getinfo(pycurl.CONTENT_TYPE) or "")
        return Response(int(curl.getinfo(pycurl.RESPONSE_CODE)), str(curl.getinfo(pycurl.EFFECTIVE_URL)),
                        media_type, received, size)
    except OSError as e:
        raise FetchError(FAILED, str(e)) from None
    finally:
        curl.close()
