# darqos
# Copyright (C) 2026 David Arnold

# Tests for the HTTP service, services/http: fetching, against a web
# server in this process; keeping snapshots, with a KB service and KB type
# implementation in this process (see test_kb_type.py); and then the
# whole system, through a real p-kernel (see test_pkernel.py).

import hashlib
import http.server
import importlib
import importlib.util
import pathlib
import sys
import threading
import time

import pytest

import darq
import darq.items
import darq.kb as model
import darq.message
import darq.url

from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_pdf_type import make_pdf
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_vcard_import import History


SERVICE_PATH = pathlib.Path(__file__).parent.parent / "services" / "http"


def load():
    # Its modules import each other, as they do run by pyloader.
    sys.path.insert(0, str(SERVICE_PATH))
    try:
        modules = [importlib.import_module(name) for name in ("fetcher", "snapshots")]
        spec = importlib.util.spec_from_file_location("http_service", SERVICE_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
    finally:
        sys.path.remove(str(SERVICE_PATH))
    return (*modules, service)


fetcher, snapshots, service = load()

USER_AGENT = "DarqOS/test"
PDF = make_pdf(("Hello",), title="A Paper")
MODIFIED = "Mon, 02 Nov 2026 00:00:00 GMT"


class Web(http.server.ThreadingHTTPServer):
    """A web server, with what the tests need."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.pages: dict[str, tuple[int, str, bytes, dict[str, str]]] = {
            "/hello.txt": (200, "text/plain; charset=utf-8", b"Hello", {}),
            "/paper.pdf": (200, "application/pdf", PDF, {"ETag": '"v1"', "Last-Modified": MODIFIED}),
            "/noetag.txt": (200, "text/plain", b"The same", {}),
            "/unknown.bin": (200, "application/x-unknown", b"\x00\x01", {}),
            "/gone": (404, "text/plain", b"Not here", {}),
            "/big": (200, "text/plain", b"x" * 100_000, {}),
        }
        self.released = threading.Event()

        # Redirects, by path: their statuses and targets.
        self.redirects: dict[str, tuple[int, str]] = {}

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    server: Web  # pyright: ignore[reportIncompatibleVariableOverride]

    def log_message(self, format, *args):
        pass

    def answer(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        self.server.requests.append((method, self.path, dict(self.headers.items()), self.rfile.read(length)))
        if self.path in self.server.redirects:
            status, target = self.server.redirects[self.path]
            self.send_response(status)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/redirect/"):
            self.send_response(302)
            self.send_header("Location", "/" + self.path.split("/", 2)[2])
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/unbounded":
            self.send_response(200)
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"y" * 16384)
        elif self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b"z" * 1000)
            self.wfile.flush()
            self.server.released.wait(10)
        else:
            self.page(method)

    def page(self, method: str):
        status, media_type, body, headers = self.server.pages.get(self.path, (404, "text/plain", b"", {}))
        etag = headers.get("ETag")
        if etag is not None and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self.answer("GET")

    def do_HEAD(self):
        self.answer("HEAD")

    def do_POST(self):
        self.answer("POST")

    def do_DELETE(self):
        self.answer("DELETE")


@pytest.fixture
def web():
    server = Web()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.released.set()
    server.shutdown()
    server.server_close()


def get(web, path, tmp_path, max_size=1 << 20, method="GET", headers=(), body=b"", **options):
    return fetcher.fetch(method, web.base + path, headers, body, max_size, USER_AGENT, tmp_path / "body", **options)


################################################################
# Fetching
################################################################

def test_get(web, tmp_path):
    response = get(web, "/hello.txt", tmp_path)
    assert (response.http_status, response.size) == (200, 5)
    assert response.media_type == "text/plain; charset=utf-8"
    assert (tmp_path / "body").read_bytes() == b"Hello"
    assert ("Content-Type", "text/plain; charset=utf-8") in response.headers


def test_user_agent(web, tmp_path):
    get(web, "/hello.txt", tmp_path)
    assert web.requests[0][2]["User-Agent"] == USER_AGENT


def test_an_error_from_the_server_is_an_answer(web, tmp_path):
    response = get(web, "/gone", tmp_path)
    assert response.http_status == 404
    assert (tmp_path / "body").read_bytes() == b"Not here"


def test_redirects_are_followed(web, tmp_path):
    response = get(web, "/redirect/hello.txt", tmp_path)
    assert response.http_status == 200
    assert response.final_url == web.base + "/hello.txt"
    assert (tmp_path / "body").read_bytes() == b"Hello"


def test_hops_are_reported(web, tmp_path):
    web.redirects["/a"] = (301, "/b")
    web.redirects["/b"] = (302, "/hello.txt")
    response = get(web, "/a", tmp_path)
    assert response.hops == [fetcher.Hop(web.base + "/a", 301, web.base + "/b"),
                             fetcher.Hop(web.base + "/b", 302, web.base + "/hello.txt")]
    assert (tmp_path / "body").read_bytes() == b"Hello"


def test_no_hops(web, tmp_path):
    assert get(web, "/hello.txt", tmp_path).hops == []


def test_a_redirect_with_no_location_is_the_answer(web, tmp_path):
    web.pages["/nowhere"] = (302, "text/plain", b"Lost", {})
    assert get(web, "/nowhere", tmp_path).http_status == 302


def test_a_relative_redirect(web, tmp_path):
    web.redirects["/dir/a"] = (302, "../hello.txt")
    assert get(web, "/dir/a", tmp_path).final_url == web.base + "/hello.txt"


def test_credentials_dont_follow_to_another_host(web, tmp_path):
    other = web.base.replace("127.0.0.1", "localhost")
    web.redirects["/away"] = (302, other + "/hello.txt")
    get(web, "/away", tmp_path, headers=[("Authorization", "Bearer x"), ("Cookie", "a=b"), ("If-None-Match", '"e"')])
    first, second = web.requests[0][2], web.requests[1][2]
    assert first["Authorization"] == "Bearer x" and first["Cookie"] == "a=b"
    assert "Authorization" not in second and "Cookie" not in second
    assert second["If-None-Match"] == '"e"'


def test_credentials_follow_on_the_same_host(web, tmp_path):
    web.redirects["/near"] = (302, "/hello.txt")
    get(web, "/near", tmp_path, headers=[("Authorization", "Bearer x")])
    assert web.requests[1][2]["Authorization"] == "Bearer x"


@pytest.mark.parametrize("status, method", [(301, "GET"), (302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")])
def test_a_post_redirected(web, tmp_path, status, method):
    web.redirects["/form"] = (status, "/hello.txt")
    get(web, "/form", tmp_path, method="POST", body=b"data")
    assert web.requests[1][0] == method
    assert web.requests[1][3] == (b"data" if method == "POST" else b"")


def test_a_redirect_loop_stops(web, tmp_path):
    with pytest.raises(fetcher.FetchError) as e:
        get(web, "/loop", tmp_path)
    assert e.value.kind == fetcher.NETWORK
    assert len(web.requests) == fetcher.MAX_REDIRECTS + 1


def test_a_redirect_cant_leave_http(web, tmp_path):
    with pytest.raises(fetcher.FetchError) as e:
        get(web, "/to-file", tmp_path)
    assert e.value.kind in (fetcher.UNSUPPORTED_SCHEME, fetcher.NETWORK)


def test_headers_are_sent(web, tmp_path):
    get(web, "/hello.txt", tmp_path, headers=[("If-None-Match", '"x"')])
    assert web.requests[0][2]["If-None-Match"] == '"x"'


@pytest.mark.parametrize("method", ["HEAD", "DELETE"])
def test_other_methods(web, tmp_path, method):
    response = get(web, "/hello.txt", tmp_path, method=method)
    assert web.requests[0][0] == method
    assert response.http_status == 200


def test_head_has_no_body(web, tmp_path):
    assert get(web, "/hello.txt", tmp_path, method="HEAD").size == 0


def test_post_sends_its_body(web, tmp_path):
    get(web, "/hello.txt", tmp_path, method="POST", body=b"data")
    assert web.requests[0][0] == "POST" and web.requests[0][3] == b"data"


def test_too_large_by_its_length(web, tmp_path):
    with pytest.raises(fetcher.FetchError) as e:
        get(web, "/big", tmp_path, max_size=1000)
    assert e.value.kind == fetcher.TOO_LARGE


def test_too_large_with_no_length_stops_at_the_limit(web, tmp_path):
    with pytest.raises(fetcher.FetchError) as e:
        get(web, "/unbounded", tmp_path, max_size=20000)
    assert e.value.kind == fetcher.TOO_LARGE
    assert (tmp_path / "body").stat().st_size <= 20000


def test_a_larger_limit_can_be_asked_for(web, tmp_path):
    assert get(web, "/big", tmp_path, max_size=200_000).size == 100_000


def test_progress(web, tmp_path):
    seen = []
    get(web, "/big", tmp_path, progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (100_000, 100_000)


def test_cancelling(web, tmp_path):
    stop = threading.Event()

    def later():
        time.sleep(0.2)
        stop.set()

    threading.Thread(target=later, daemon=True).start()
    with pytest.raises(fetcher.FetchError) as e:
        get(web, "/slow", tmp_path, cancelled=stop.is_set)
    assert e.value.kind == fetcher.CANCELLED


def test_nothing_listening():
    with pytest.raises(fetcher.FetchError) as e:
        fetcher.fetch("GET", "http://127.0.0.1:1/", [], b"", 100, USER_AGENT, pathlib.Path("/dev/null"))
    assert e.value.kind == fetcher.NETWORK


@pytest.mark.parametrize("url, kind", [("file:///etc/passwd", fetcher.UNSUPPORTED_SCHEME),
                                       ("ftp://example.org/x", fetcher.UNSUPPORTED_SCHEME),
                                       ("not a url", fetcher.INVALID_URL), ("", fetcher.INVALID_URL),
                                       ("http://", fetcher.INVALID_URL)])
def test_check_url(url, kind):
    with pytest.raises(fetcher.FetchError) as e:
        fetcher.check_url(url)
    assert e.value.kind == kind


@pytest.mark.parametrize("url", ["http://example.org/", "https://example.org/a?b=c", "HTTP://Example.org"])
def test_check_url_passes(url):
    assert fetcher.check_url(url) == url


def test_libcurl_has_what_the_design_expects():
    import pycurl
    features = pycurl.version_info()[4]
    assert features & pycurl.VERSION_HTTP2


################################################################
# Snapshots
################################################################

class Items:
    """Makes items of PDFs and text, with their entities, as their types
    do, so that a snapshot can link to one."""

    def __init__(self, store=None):
        self.kb = store
        self.made: list[tuple[str, str, bytes]] = []
        self.names: list[str] = []

    def __call__(self, media_type, extension, data, name):
        if media_type not in ("application/pdf", "text/plain") and extension != "pdf":
            return None
        self.made.append((media_type, extension, data))
        self.names.append(name)
        item_id = f"com.adobe.pdf:{len(self.made):032x}"
        if self.kb is not None:
            darq.items.create(self.kb, "com.adobe.pdf", item_id.partition(":")[2], file_name=name)
        return item_id


@pytest.fixture
def keeper(kb, implementation):  # noqa: F811
    items = model.KB(kb.client, Writer(implementation))
    made = Items(kb)
    return snapshots.Snapshots(items, made, History()), made


NOW = darq.message.time_value(1_700_000_000_000_000)


def take(keeper, web, tmp_path, path):
    def fetch(url, headers, body):
        return fetcher.fetch("GET", url, headers, b"", 1 << 20, USER_AGENT, body)

    return keeper[0].take(web.base + path, fetch, tmp_path / "body", NOW)


@pytest.mark.parametrize("url, name", [("https://a.example/papers/On%20Computable.pdf?x=1", "On Computable.pdf"),
                                       ("https://a.example/", ""), ("https://a.example", "")])
def test_a_snapshots_name(url, name):
    assert snapshots.name_of(url) == name


def test_a_snapshot(keeper, web, tmp_path, kb):  # noqa: F811
    outcome = take(keeper, web, tmp_path, "/paper.pdf")
    assert outcome.changed and outcome.http_status == 200
    assert outcome.url_item == darq.url.item_id(web.base + "/paper.pdf")
    assert keeper[1].made == [("application/pdf", "pdf", PDF)]
    assert keeper[1].names == ["paper.pdf"]

    entity = kb.get(darq.url.entity_id(web.base + "/paper.pdf"))
    assert model.recorded_type(entity) == "public.url"
    assert entity.get_label() == web.base + "/paper.pdf"
    assert entity.get_statements(darq.url.URL)[0].get_value().value == web.base + "/paper.pdf"
    latest = darq.url.latest_snapshot(entity, lambda e: darq.items.item_of(kb, e))
    assert latest is not None
    assert latest.item_id == outcome.item
    assert latest.hash == hashlib.sha256(PDF).hexdigest()
    assert latest.etag == '"v1"'
    assert latest.last_modified == MODIFIED


def test_snapshots_are_stated_in_the_service(keeper, web, tmp_path, kb):  # noqa: F811
    take(keeper, web, tmp_path, "/paper.pdf")
    entity = kb.get(darq.url.entity_id(web.base + "/paper.pdf"))
    assert model.is_sourced(entity.get_statements(darq.url.SNAPSHOT)[0], darq.url.HTTP_SERVICE)


def test_recorded_in_history(keeper, web, tmp_path):
    outcome = take(keeper, web, tmp_path, "/paper.pdf")
    assert keeper[0].history.events == [(outcome.url_item, "created", "http"), (outcome.url_item, "fetched", "http"),
                                        (outcome.item, "imported", "http")]


def test_an_unchanged_fetch_is_recorded_on_the_url(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/paper.pdf")
    keeper[0].history.events.clear()
    take(keeper, web, tmp_path, "/paper.pdf")
    assert keeper[0].history.events == [(first.url_item, "fetched", "http")]


def test_a_changed_fetch_is_recorded_on_both(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/noetag.txt")
    web.pages["/noetag.txt"] = (200, "text/plain", b"Different", {})
    keeper[0].history.events.clear()
    second = take(keeper, web, tmp_path, "/noetag.txt")
    assert keeper[0].history.events == [(first.url_item, "fetched", "http"), (second.item, "imported", "http")]


def test_a_refused_fetch_of_a_known_url_is_recorded(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/noetag.txt")
    web.pages["/noetag.txt"] = (404, "text/plain", b"", {})
    keeper[0].history.events.clear()
    take(keeper, web, tmp_path, "/noetag.txt")
    assert keeper[0].history.events == [(first.url_item, "fetched", "http")]


def test_a_failure_with_no_item_records_nothing(keeper, web, tmp_path):
    take(keeper, web, tmp_path, "/gone")
    assert keeper[0].history.events == []


def test_history_not_running(kb, implementation, web, tmp_path):  # noqa: F811
    items = model.KB(kb.client, Writer(implementation))
    assert take((snapshots.Snapshots(items, Items(kb), None),), web, tmp_path, "/paper.pdf").changed


def test_not_modified_keeps_the_latest(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/paper.pdf")
    again = take(keeper, web, tmp_path, "/paper.pdf")
    assert web.requests[-1][2]["If-None-Match"] == '"v1"'
    assert again.http_status == 304
    assert (again.item, again.changed) == (first.item, False)
    assert len(keeper[1].made) == 1


def test_the_same_content_keeps_the_latest(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/noetag.txt")
    again = take(keeper, web, tmp_path, "/noetag.txt")
    assert (again.http_status, again.item, again.changed) == (200, first.item, False)
    assert len(keeper[1].made) == 1


def test_changed_content_is_a_new_snapshot(keeper, web, tmp_path, kb):  # noqa: F811
    first = take(keeper, web, tmp_path, "/noetag.txt")
    web.pages["/noetag.txt"] = (200, "text/plain", b"Different", {})
    second = take(keeper, web, tmp_path, "/noetag.txt")
    assert second.changed and second.item != first.item
    entity = kb.get(darq.url.entity_id(web.base + "/noetag.txt"))
    assert len(entity.get_statements(darq.url.SNAPSHOT)) == 2
    latest = darq.url.latest_snapshot(entity, lambda e: darq.items.item_of(kb, e))
    assert latest is not None and latest.item_id == second.item


def test_a_url_is_one_item_however_its_spelled(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/paper.pdf")
    assert darq.url.item_id(web.base.replace("http://", "HTTP://") + "/paper.pdf") == first.url_item


def test_a_404_makes_nothing(keeper, web, tmp_path, kb):  # noqa: F811
    outcome = take(keeper, web, tmp_path, "/gone")
    assert (outcome.http_status, outcome.url_item, outcome.item, outcome.changed) == (404, "", "", False)
    assert keeper[1].made == []
    assert kb.get(darq.url.entity_id(web.base + "/gone")) is None


def test_a_404_keeps_earlier_snapshots(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/noetag.txt")
    web.pages["/noetag.txt"] = (404, "text/plain", b"", {})
    outcome = take(keeper, web, tmp_path, "/noetag.txt")
    assert (outcome.http_status, outcome.item, outcome.changed) == (404, first.item, False)
    assert outcome.url_item == first.url_item


def test_no_type_makes_nothing(keeper, web, tmp_path, kb):  # noqa: F811
    with pytest.raises(fetcher.FetchError) as e:
        take(keeper, web, tmp_path, "/unknown.bin")
    assert e.value.kind == snapshots.NO_TYPE
    assert kb.get(darq.url.entity_id(web.base + "/unknown.bin")) is None


def test_the_extension_helps_when_the_media_type_doesnt(keeper, web, tmp_path):
    web.pages["/odd.pdf"] = (200, "application/octet-stream", PDF, {})
    assert take(keeper, web, tmp_path, "/odd.pdf").changed


def test_a_redirects_extension_is_where_it_ended(keeper, web, tmp_path):
    take(keeper, web, tmp_path, "/redirect/paper.pdf")
    assert keeper[1].made[0][1] == "pdf"


def test_a_media_types_parameters_are_left_out(keeper, web, tmp_path):
    take(keeper, web, tmp_path, "/hello.txt")
    assert keeper[1].made[0][0] == "text/plain"


def entity_of(kb, web, path):  # noqa: F811
    return kb.get(darq.url.entity_id(web.base + path), follow=False)


def snapshot_items(entity) -> list[str]:
    """Return the items a URL's snapshots link to: Items makes PDFs."""
    return ["com.adobe.pdf:" + s.get_value().entity_id for s in entity.get_statements(darq.url.SNAPSHOT)]


def moved_a_page(web, status=301):
    """Move /old-home to /new-home: the same content, by a redirect."""
    page = web.pages.pop("/noetag.txt")
    web.pages["/new-home"] = page
    web.redirects["/noetag.txt"] = (status, "/new-home")


@pytest.mark.parametrize("status", [301, 308])
def test_a_move_shares_the_snapshot(keeper, web, tmp_path, kb, status):  # noqa: F811
    first = take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web, status)
    moved = take(keeper, web, tmp_path, "/noetag.txt")

    # Both URLs lead to the one item, and nothing was made.
    assert (moved.item, moved.changed) == (first.item, False)
    assert len(keeper[1].made) == 1
    old, new = entity_of(kb, web, "/noetag.txt"), entity_of(kb, web, "/new-home")
    assert snapshot_items(old) == snapshot_items(new) == [first.item]
    assert moved.url_item == darq.url.item_id(web.base + "/new-home")

    # The old one says where it went, when, and how it knows.
    assert darq.url.moved_to(old) == new.id
    [statement] = old.get_statements(darq.url.MOVED_TO)
    assert any(q.property_id == darq.url.RETRIEVED for q in statement.qualifiers)
    assert darq.url.qualifier(statement, darq.url.HTTP_STATUS) == str(status)


def test_a_moves_history(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web)
    keeper[0].history.events.clear()
    moved = take(keeper, web, tmp_path, "/noetag.txt")
    assert keeper[0].history.events == [(first.url_item, "fetched", "http"), (moved.url_item, "created", "http"),
                                        (moved.url_item, "fetched", "http")]


def test_a_move_with_new_content_is_a_new_snapshot_on_the_new_url(keeper, web, tmp_path, kb):  # noqa: F811
    first = take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web)
    web.pages["/new-home"] = (200, "text/plain", b"Rewritten", {})
    moved = take(keeper, web, tmp_path, "/noetag.txt")
    assert moved.changed and moved.item != first.item
    assert snapshot_items(entity_of(kb, web, "/noetag.txt")) == [first.item]
    assert snapshot_items(entity_of(kb, web, "/new-home")) == [moved.item]


def test_a_moved_url_is_asked_again(keeper, web, tmp_path, kb):  # noqa: F811
    first = take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web)
    take(keeper, web, tmp_path, "/noetag.txt")
    web.requests.clear()
    again = take(keeper, web, tmp_path, "/noetag.txt")
    assert [path for _, path, _, _ in web.requests] == ["/noetag.txt", "/new-home"]
    assert (again.item, again.changed) == (first.item, False)
    assert len(entity_of(kb, web, "/noetag.txt").get_statements(darq.url.MOVED_TO)) == 1
    assert len(entity_of(kb, web, "/new-home").get_statements(darq.url.SNAPSHOT)) == 1


def test_a_move_sends_what_it_knows_to_the_new_url(keeper, web, tmp_path):
    first = take(keeper, web, tmp_path, "/paper.pdf")
    web.pages["/moved.pdf"] = web.pages.pop("/paper.pdf")
    web.redirects["/paper.pdf"] = (301, "/moved.pdf")
    moved = take(keeper, web, tmp_path, "/paper.pdf")
    assert (moved.http_status, moved.item, moved.changed) == (304, first.item, False)
    assert web.requests[-1][2]["If-None-Match"] == '"v1"'


def test_a_url_that_comes_back_ends_its_move(keeper, web, tmp_path, kb):  # noqa: F811
    first = take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web)
    take(keeper, web, tmp_path, "/noetag.txt")

    del web.redirects["/noetag.txt"]
    web.pages["/noetag.txt"] = web.pages["/new-home"]
    back = take(keeper, web, tmp_path, "/noetag.txt")
    assert (back.url_item, back.item, back.changed) == (first.url_item, first.item, False)

    old = entity_of(kb, web, "/noetag.txt")
    assert darq.url.moved_to(old) == ""
    [statement] = old.get_statements(darq.url.MOVED_TO)
    assert any(q.property_id == model.END_TIME for q in statement.qualifiers)


def test_a_second_move_ends_the_first(keeper, web, tmp_path, kb):  # noqa: F811
    take(keeper, web, tmp_path, "/noetag.txt")
    moved_a_page(web)
    take(keeper, web, tmp_path, "/noetag.txt")
    web.pages["/third-home"] = web.pages.pop("/new-home")
    web.redirects["/noetag.txt"] = (301, "/third-home")
    take(keeper, web, tmp_path, "/noetag.txt")
    old = entity_of(kb, web, "/noetag.txt")
    assert darq.url.moved_to(old) == entity_of(kb, web, "/third-home").id
    assert len(old.get_statements(darq.url.MOVED_TO)) == 2


def test_a_first_fetch_that_redirects(keeper, web, tmp_path, kb):  # noqa: F811
    web.redirects["/plain"] = (301, "/hello.txt")
    outcome = take(keeper, web, tmp_path, "/plain")
    assert outcome.changed
    old, new = entity_of(kb, web, "/plain"), entity_of(kb, web, "/hello.txt")
    assert snapshot_items(old) == [] and darq.url.moved_to(old) == new.id
    assert snapshot_items(new) == [outcome.item]
    assert outcome.url_item == darq.url.item_id(web.base + "/hello.txt")


def test_a_temporary_redirect_isnt_a_move(keeper, web, tmp_path, kb):  # noqa: F811
    web.redirects["/today"] = (302, "/hello.txt")
    outcome = take(keeper, web, tmp_path, "/today")
    assert outcome.url_item == darq.url.item_id(web.base + "/today")
    assert entity_of(kb, web, "/hello.txt") is None
    entity = entity_of(kb, web, "/today")
    assert darq.url.moved_to(entity) == ""
    [statement] = entity.get_statements(darq.url.SNAPSHOT)
    assert darq.url.qualifier(statement, darq.url.FINAL_URL) == web.base + "/hello.txt"


@pytest.mark.parametrize("status", [302, 303, 307])
def test_temporary_statuses(keeper, web, tmp_path, kb, status):  # noqa: F811
    web.redirects["/today"] = (status, "/hello.txt")
    take(keeper, web, tmp_path, "/today")
    assert entity_of(kb, web, "/hello.txt") is None


def test_a_snapshot_with_no_redirect_has_no_final_url(keeper, web, tmp_path, kb):  # noqa: F811
    take(keeper, web, tmp_path, "/hello.txt")
    [statement] = entity_of(kb, web, "/hello.txt").get_statements(darq.url.SNAPSHOT)
    assert darq.url.qualifier(statement, darq.url.FINAL_URL) == ""


def test_a_chain_stops_at_the_first_temporary_redirect(keeper, web, tmp_path, kb):  # noqa: F811
    web.redirects["/one"] = (301, "/two")
    web.redirects["/two"] = (301, "/three")
    web.redirects["/three"] = (302, "/hello.txt")
    outcome = take(keeper, web, tmp_path, "/one")
    one, two, three = (entity_of(kb, web, f"/{n}") for n in ("one", "two", "three"))
    assert darq.url.moved_to(one) == two.id and darq.url.moved_to(two) == three.id
    assert darq.url.moved_to(three) == ""
    assert snapshot_items(three) == [outcome.item]
    assert entity_of(kb, web, "/hello.txt") is None


def test_a_redirects_target_is_normalised(keeper, web, tmp_path, kb):  # noqa: F811
    web.redirects["/shouty"] = (301, web.base.replace("http://", "HTTP://") + "/hello.txt")
    take(keeper, web, tmp_path, "/shouty")
    assert entity_of(kb, web, "/hello.txt") is not None


def test_a_move_to_a_failure_records_no_move(keeper, web, tmp_path, kb):  # noqa: F811
    take(keeper, web, tmp_path, "/noetag.txt")
    del web.pages["/noetag.txt"]
    web.redirects["/noetag.txt"] = (301, "/gone")
    outcome = take(keeper, web, tmp_path, "/noetag.txt")
    assert outcome.http_status == 404
    assert darq.url.moved_to(entity_of(kb, web, "/noetag.txt")) == ""
    assert entity_of(kb, web, "/gone") is None


def test_a_move_with_no_type_keeps_nothing(keeper, web, tmp_path, kb):  # noqa: F811
    web.redirects["/plain"] = (301, "/unknown.bin")
    with pytest.raises(fetcher.FetchError):
        take(keeper, web, tmp_path, "/plain")
    assert entity_of(kb, web, "/plain") is None and entity_of(kb, web, "/unknown.bin") is None


def test_moved_from_reads_backwards(kb, implementation, web):  # noqa: F811
    assert kb.get(darq.url.MOVED_TO).get_statements(model.INVERSE_LABEL)


@pytest.mark.parametrize("url, extension", [("http://x/a.pdf", "pdf"), ("http://x/a.tar.gz?y=1", "gz"),
                                            ("http://x/", ""), ("http://x", "")])
def test_extension_of(url, extension):
    assert snapshots.extension_of(url) == extension


def test_invalid_urls_arent_fetched(keeper, tmp_path):
    def fetch(url, headers, body):
        raise AssertionError("fetched")

    with pytest.raises(fetcher.FetchError) as e:
        keeper[0].take("file:///etc/passwd", fetch, tmp_path / "body", NOW)
    assert e.value.kind == fetcher.UNSUPPORTED_SCHEME


################################################################
# URL items
################################################################

def test_a_url_is_a_kind(implementation):  # noqa: F811
    assert "public.url" in model.KIND_TYPES


def test_entity_id_is_the_same_for_the_same_url():
    assert darq.url.entity_id("https://Example.org:443/a") == darq.url.entity_id("https://example.org/a")
    assert darq.url.entity_id("https://example.org/a") != darq.url.entity_id("https://example.org/b")


def test_no_snapshot():
    assert darq.url.latest_snapshot(model.Item("u"), lambda e: "") is None


def test_a_snapshot_from_before_links_is_still_found():
    url = model.Item("u")
    url.claim(darq.url.OLD_SNAPSHOT, model.StringValue("com.adobe.pdf:" + "1" * 32))
    latest = darq.url.latest_snapshot(url, lambda e: "")
    assert latest is not None and latest.item_id == "com.adobe.pdf:" + "1" * 32


def test_prepare_spool_clears_what_a_crash_left(tmp_path):
    (tmp_path / "http").mkdir()
    (tmp_path / "http" / "left").write_bytes(b"x")
    spool = service.prepare_spool(str(tmp_path))
    assert list(spool.iterdir()) == []
    assert spool.stat().st_mode & 0o077 == 0


################################################################
# The whole system
################################################################

def test_http_service(live_pkernel, web, tmp_path):  # noqa: F811
    kernel = live_pkernel
    web.redirects["/old.pdf"] = (301, "/paper.pdf")
    client_path = write_app(tmp_path / "client", f"""
        import time
        import darq
        import darq.items
        import darq.kb as model
        import darq.launch
        import darq.url
        from darq.protocols.http import Header, Http, Status
        from darq.protocols.lens import Lens
        from darq.protocols.pdf import Pdf
        from darq.protocols.type import Type

        BASE = {web.base!r}

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            for type_id in ("com.adobe.pdf", "public.url"):
                while not types.describe(type_id=type_id).implementation:
                    if time.monotonic() > deadline:
                        return f"{{type_id}} never registered"
                    time.sleep(0.05)
            http = Http.Client(darq.connect("darq.http"), timeout=30)

            response = http.request(method="GET", url=BASE + "/hello.txt")
            if (response.status, response.http_status, response.body) != (Status.OK, 200, b"Hello"):
                return f"request: {{response!r}}"

            response = http.request(method="GET", url=BASE + "/gone")
            if (response.status, response.http_status) != (Status.OK, 404):
                return f"404: {{response!r}}"

            if http.request(url="file:///etc/passwd").status != Status.UNSUPPORTED_SCHEME:
                return "fetched a file"
            if http.request(url=BASE + "/big", max_size=1000).status != Status.TOO_LARGE:
                return "not too large"

            first = http.snapshot(url=BASE + "/paper.pdf")
            if first.status != Status.OK or not first.changed or not first.item.startswith("com.adobe.pdf:"):
                return f"snapshot: {{first!r}}"
            port = darq.launch.open_item(first.item)
            item = Pdf.Client(port, timeout=30)
            if item.stat().pages != 1:
                return "not the PDF"
            item.close()
            darq.close_port(port)

            again = http.snapshot(url=BASE + "/paper.pdf")
            if (again.http_status, again.item, again.changed) != (304, first.item, False):
                return f"again: {{again!r}}"

            kb = model.KB.connect(timeout=30)
            entity = kb.get(darq.split_item_id(first.url_item)[1])
            if darq.url.latest_snapshot(entity, lambda e: darq.items.item_of(kb, e)).item_id != first.item:
                return "not recorded"
            if darq.launch.find_lens("public.url").rsplit("/", 1)[1] != "entity":
                return "no lens"

            moved = http.snapshot(url=BASE + "/old.pdf")
            if (moved.status, moved.changed) != (Status.OK, False) or moved.item != first.item:
                return f"moved: {{moved!r}}"
            if moved.url_item != first.url_item:
                return f"moved to {{moved.url_item}}"

            # What no type takes is kept as data.
            unknown = http.snapshot(url=BASE + "/unknown.bin")
            if unknown.status != Status.OK or not unknown.item.startswith("public.data:"):
                return f"something with no type: {{unknown!r}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)


def test_fetching_from_search(live_pkernel, web, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # Plays the Terminal: its Services and Launcher, given a URL, as
    # Search gives it.
    client_path = write_app(tmp_path / "client", f"""
        import threading
        import time
        import darq
        import darq.ui
        import darq.url
        from darq.protocols.lens import Lens
        from darq.protocols.type import Type
        from darq.ui.launcher import Launcher

        class Host:
            def __init__(self):
                self.services = darq.ui.Services()
                self.launcher = Launcher(self, "test", lambda f: f(), run=lambda f: f())
                self.results = []

            def show_status(self, message):
                pass

            def launched(self, context, item_id, error):
                self.results.append((item_id, error))

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not types.find_lenses(type_id="com.adobe.pdf", context="terminal").lenses \\
                    or not types.describe(type_id="public.url").implementation:
                if time.monotonic() > deadline:
                    return "never registered"
                time.sleep(0.05)
            while darq.lookup_name("darq.http", blocking=False) is None:
                if time.monotonic() > deadline:
                    return "no HTTP service"
                time.sleep(0.05)

            host = Host()
            host.launcher.fetch({web.base + "/paper.pdf"!r})
            [(item_id, error)] = host.results
            if error is not None or not item_id.startswith("com.adobe.pdf:"):
                return f"fetched: {{host.results!r}}"

            events = [(e.event, e.source) for e in host.services.history.get_events(item_ids=[item_id]).events]
            if ("imported", "http") not in events:
                return f"events are {{events!r}}"
            url_item = darq.url.item_id({web.base + "/paper.pdf"!r})
            events = [(e.event, e.source) for e in host.services.history.get_events(item_ids=[url_item]).events]
            if ("created", "http") not in events or ("fetched", "http") not in events:
                return f"URL's events are {{events!r}}"
            events = []
            while ("opened", "pdf-lens") not in events:
                if time.monotonic() > deadline:
                    return f"never opened: {{events!r}}"
                time.sleep(0.05)
                events = [(e.event, e.source)
                          for e in host.services.history.get_events(item_ids=[item_id]).events]

            hits = host.services.index.search("Hello")
            if item_id not in [hit.item_id for hit in hits]:
                return f"not indexed: {{hits!r}}"

            # What no type takes is kept as data, which has no lens yet.
            host.launcher.fetch({web.base + "/unknown.bin"!r})
            if not (host.results[-1][0] or "").startswith("public.data:"):
                return f"no type: {{host.results[-1]!r}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
