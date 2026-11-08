# darqos
# Copyright (C) 2026 David Arnold

# Paging for the e-book lens: where the reader is, in a book's parts, and
# what to tell the page (see doc/book.rst).
#
# A part's laid out in CSS columns, a window wide.  They aren't scrolled
# to: the body's moved with a transform, so that every page, the last
# too, is an exact number of windows along.  The book's scripts are off,
# so this runs in a world of the lens's own.
#
# It uses no Qt, and only talks to a Page, so it's tested alone.

import dataclasses
import json
import re
import typing
import urllib.parse

from darq.ui import reader


# Pixels of margin around a page, and left around an image: one a column
# tall, with its paragraph's margins, wouldn't fit, and leave a page empty.
SIDE_MARGIN = 48
TOP_MARGIN = 32
IMAGE_MARGIN = 64

# What a lens does to any part: it stays in columns, and nothing's wider
# than a column, or longer than one.
LAYOUT = f"""
html {{ height: 100vh; overflow: hidden; margin: 0; }}
body {{ margin: 0; padding: {TOP_MARGIN}px {SIDE_MARGIN}px; box-sizing: border-box; width: 100vw; height: 100vh;
        column-width: calc(100vw - {2 * SIDE_MARGIN}px); column-gap: {2 * SIDE_MARGIN}px; column-fill: auto;
        overflow-wrap: break-word; }}
img, svg, video {{ max-width: 100%; max-height: calc(100vh - {2 * TOP_MARGIN + IMAGE_MARGIN}px);
                  object-fit: contain; break-inside: avoid; }}
pre {{ white-space: pre-wrap; }}
table {{ max-width: 100%; }}
"""


def theme(name: str) -> str:
    """Return the style of a mode: light leaves a book's colours alone."""
    mode = next(m for m in reader.MODES if m[0] == name)
    _, background, text, link = mode
    style = f"html, body {{ background: {background} !important; color: {text} !important; }}"
    if name != "light":
        style += (f"body * {{ color: inherit !important; background-color: transparent !important; }}"
                  f"a, a * {{ color: {link} !important; }}")
    return style


# A part's documents, which are styled as they're served.
DOCUMENT_TYPES = ("text/html", "application/xhtml+xml")

# The element of the style a part's served with, and what keeps its text
# hidden until it's at the page it's arrived at.
FIRST_STYLE = "darq-first"
HIDDEN = "body { visibility: hidden !important; }"

_HEAD = re.compile(rb"<head(\s[^>]*)?>", re.IGNORECASE)
_HTML = re.compile(rb"<html(\s[^>]*)?>", re.IGNORECASE)


def first_style(mode: str) -> str:
    """Return the style a part's served with: laid out, in its mode, and
    hidden till it's arrived at its page, so that it's never painted
    otherwise, as it would be, between loading and being laid out."""
    return LAYOUT + theme(mode) + HIDDEN


def styled(data: bytes, media_type: str, mode: str) -> bytes:
    """Return a part as it's served: a document with the first style at
    the start of its head, or else of its html, or else at its start."""
    if media_type.split(";")[0].strip().lower() not in DOCUMENT_TYPES:
        return data
    element = f'<style id="{FIRST_STYLE}">{first_style(mode)}</style>'.encode()
    for pattern in (_HEAD, _HTML):
        found = pattern.search(data)
        if found is not None:
            return data[:found.end()] + element + data[found.end():]
    return element + data


# Set up a part, in the lens's world.  It's run once a part's loaded, and
# the functions stay there.
SET_UP = """
(function (layout, theme) {
    var style = document.getElementById("darq-layout") || document.createElement("style");
    style.id = "darq-layout";
    style.textContent = layout;
    (document.head || document.documentElement).appendChild(style);
    var colours = document.getElementById("darq-theme") || document.createElement("style");
    colours.id = "darq-theme";
    colours.textContent = theme;
    (document.head || document.documentElement).appendChild(colours);
    window.darqPages = function () {
        // Without a doctype, the body's width is the viewport's scrollable
        // area, which the transform changes, so it's measured without one.
        var transform = document.body.style.transform;
        document.body.style.transform = "";
        var width = document.body.scrollWidth;
        document.body.style.transform = transform;
        return Math.max(1, Math.ceil(width / window.innerWidth));
    };
    window.darqShow = function (page) {
        document.documentElement.scrollLeft = 0;
        document.body.scrollLeft = 0;
        document.body.style.transform = "translateX(" + (-page * window.innerWidth) + "px)";
        // Shown at last, at its page: the style it was served with goes.
        var first = document.getElementById("darq-first");
        if (first) {
            first.remove();
        }
        return window.darqPages();
    };
    window.darqPageOf = function (element, page) {
        if (!element) {
            return -1;
        }
        var left = element.getBoundingClientRect().left + page * window.innerWidth;
        return Math.max(0, Math.floor(left / window.innerWidth));
    };
    return window.darqPages();
})(%s, %s)
"""


def set_up(mode: str) -> str:
    return SET_UP % (json.dumps(LAYOUT), json.dumps(theme(mode)))


def show(page: int) -> str:
    return f"window.darqShow({page})"


def page_of_fragment(fragment: str, page: int) -> str:
    target = json.dumps(fragment)
    return (f"window.darqPageOf(document.getElementById({target}) || "
            f"document.getElementsByName({target})[0], {page})")


def page_of_selection(page: int) -> str:
    return (f"(function () {{ var s = window.getSelection();"
            f" return s.rangeCount ? window.darqPageOf(s.getRangeAt(0), {page}) : -1; }})()")


def url_of(name: str) -> str:
    """Return the URL of a thing in the book."""
    path, hash_mark, fragment = name.partition("#")
    return "book:///" + urllib.parse.quote(path) + (f"#{urllib.parse.quote(fragment)}" if hash_mark else "")


def name_of(path: str) -> str:
    """Return the name a URL's path is for."""
    return urllib.parse.unquote(path).lstrip("/")


class Page(typing.Protocol):
    """What paging needs: Qt's page, in the lens."""

    def run(self, script: str, done: typing.Callable[[typing.Any], None]) -> None:
        """Run a script in the lens's world, and call back with its result."""
        ...

    def load(self, name: str) -> None:
        """Load a part."""
        ...


def format_position(part: int, fraction: float) -> str:
    return f"{part}:{fraction:.4f}"


def parse_position(text: str, parts: int) -> tuple[int, float]:
    """Return a part and how far through it a position is: the start, if
    it's nonsense, as it's only a convenience."""
    part, _, fraction = text.partition(":")
    try:
        found = int(part), float(fraction)
    except ValueError:
        return 0, 0.0
    if not 0 <= found[0] < parts or not 0.0 <= found[1] <= 1.0:
        return 0, 0.0
    return found


@dataclasses.dataclass
class Arrival:
    """Where to go in a part that's loading."""

    fraction: float = 0.0
    last: bool = False
    fragment: str = ""


class Pager:
    """Where the reader is."""

    def __init__(self, page: Page, parts: list[str], mode: str, shown: typing.Callable[[], None] = lambda: None):
        """Constructor.

        :param parts: The book's parts, in reading order.
        :param shown: Told when a page has been shown."""
        self.view = page
        self.parts = parts
        self.mode = mode
        self.shown = shown
        self.part = 0
        self.page = 0
        self.pages = 1
        self.arrival = Arrival()
        self.loaded = False

    def go(self, part: int, arrival: Arrival | None = None):
        """Load a part, to arrive at some place in it."""
        self.part = part
        self.arrival = arrival or Arrival()
        self.loaded = False
        self.view.load(self.parts[part])

    def open_at(self, position: str):
        part, fraction = parse_position(position, len(self.parts))
        self.go(part, Arrival(fraction))

    def go_to(self, target: str):
        """Go to a table of contents' target, or a link's."""
        name, _, fragment = target.partition("#")
        if name in self.parts:
            self.go(self.parts.index(name), Arrival(fragment=fragment))

    def part_loaded(self):
        """A part's finished loading: lay it out, and arrive."""
        self.view.run(set_up(self.mode), self.laid_out)

    def laid_out(self, pages: typing.Any):
        self.loaded = True
        self.pages = pages if isinstance(pages, int) else 1
        arrival = self.arrival
        if arrival.fragment:
            self.find_fragment(arrival.fragment)
        elif arrival.last:
            self.show(self.pages - 1)
        else:
            self.show(round(arrival.fraction * (self.pages - 1)))

    def find_fragment(self, fragment: str):
        self.view.run(page_of_fragment(fragment, self.page), lambda page: self.show(page if isinstance(page, int)
                                                                                     and page >= 0 else 0))

    def show(self, page: int):
        self.page = max(0, min(page, self.pages - 1))
        self.view.run(show(self.page), self.after_show)

    def after_show(self, pages: typing.Any):
        if isinstance(pages, int):
            self.pages = pages
        self.shown()

    def next(self):
        if not self.loaded:
            return
        if self.page + 1 < self.pages:
            self.show(self.page + 1)
        elif self.part + 1 < len(self.parts):
            self.go(self.part + 1)

    def previous(self):
        if not self.loaded:
            return
        if self.page > 0:
            self.show(self.page - 1)
        elif self.part > 0:
            self.go(self.part - 1, Arrival(last=True))

    def first(self):
        self.go(0)

    def last(self):
        self.go(len(self.parts) - 1, Arrival(last=True))

    def relaid(self):
        """The window or text changed size: stay at the same place."""
        if not self.loaded:
            return
        fraction = self.fraction()
        self.view.run("window.darqPages()", lambda pages: self.resized(pages, fraction))

    def resized(self, pages: typing.Any, fraction: float):
        self.pages = pages if isinstance(pages, int) else self.pages
        self.show(round(fraction * (self.pages - 1)))

    def set_mode(self, mode: str):
        self.mode = mode
        if self.loaded:
            fraction = self.fraction()
            self.view.run(set_up(mode), lambda pages: self.resized(pages, fraction))

    def fraction(self) -> float:
        return self.page / (self.pages - 1) if self.pages > 1 else 0.0

    def position(self) -> str:
        return format_position(self.part, self.fraction())

    def describe(self) -> str:
        """Return where the reader is, in words."""
        return f"Part {self.part + 1} of {len(self.parts)}, page {self.page + 1} of {self.pages}"
