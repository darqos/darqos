"""Shows a street address on a pannable, zoomable map.

The map is Leaflet, with OpenStreetMap tiles, in a QWebEngineView.
Addresses are geocoded in Python: Australian ones from a local copy of
G-NAF (see gnaf_import.py), for house-level positions, then Nominatim
for everywhere else.
"""

import itertools
import json
import os
import re
import sqlite3
import sys
from threading import Thread

from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

# Identifies us to the tile and geocoding servers: both their usage
# policies require it.
USER_AGENT = "DarqOS-place-experiment/1.0"

# The page needs a real origin: with setHtml()'s default, it has a null
# origin, sends no Referer, and OpenStreetMap's tile servers refuse it.
PAGE_BASE_URL = QUrl("https://darqos.local/place/")

# Built by gnaf_import.py.  Without it, every lookup goes to Nominatim.
GNAF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gnaf", "gnaf.sqlite")
GNAF_CREDIT = "G-NAF \u00a9 Geoscape Australia, CC BY 4.0"

STATES = {
    "NSW": "NSW", "NEW SOUTH WALES": "NSW", "VIC": "VIC", "VICTORIA": "VIC",
    "QLD": "QLD", "QUEENSLAND": "QLD", "SA": "SA", "SOUTH AUSTRALIA": "SA",
    "WA": "WA", "WESTERN AUSTRALIA": "WA", "TAS": "TAS", "TASMANIA": "TAS",
    "NT": "NT", "NORTHERN TERRITORY": "NT", "ACT": "ACT",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}

# Common abbreviations beyond G-NAF's own street-type ones ('ST', 'AV', ...).
OTHER_ABBREVIATIONS = {"MT": "MOUNT", "AVE": "AVENUE", "CRES": "CRESCENT"}

# 'Unit 2, 29 ...', 'U2/29 ...', 'Apt 2 29 ...' -> '2/29 ...'
UNIT_RE = re.compile(r"^(?:UNIT|U|APT|APARTMENT|FLAT|SUITE|SHOP)\s*(\w+)[\s/]+(?=\d)")


class GnafLookup:
    """Finds Australian addresses in the local G-NAF table."""

    def __init__(self, path):
        self.path = path
        self.street_types = None  # 'ST' -> 'STREET', from the database

    def available(self):
        return os.path.exists(self.path)

    def parse(self, address):
        """Splits an address into (candidate keys, state, postcode).

        Each word that might be an abbreviation ('ST' could be STREET or
        SAINT) gives two candidates: as typed, and expanded."""
        text = re.sub(r"[^A-Z0-9/ -]", " ", address.upper().replace(",", " "))
        text = " ".join(text.split())
        text = UNIT_RE.sub(r"\1/", text)
        text = re.sub(r"\s*/\s*", "/", text)

        state = postcode = None
        text = re.sub(r"\s+AUSTRALIA$", "", text)
        for _ in range(2):  # state and postcode, in either order
            m = re.search(r"\s+(\d{4})$", text)
            if m and postcode is None:
                postcode = m.group(1)
                text = text[:m.start()]
                continue
            for name in sorted(STATES, key=len, reverse=True):
                if state is None and text.endswith(" " + name):
                    state = STATES[name]
                    text = text[:-len(name) - 1]
                    break

        choices = []
        for word in text.split():
            full = self.street_types.get(word) or OTHER_ABBREVIATIONS.get(word)
            choices.append((word, full) if full else (word,))
        keys = [" ".join(c) for c in itertools.islice(itertools.product(*choices), 16)]
        return keys, state, postcode

    def lookup(self, address):
        """Returns (lat, lon, description, note), or None.

        `note` says how the match is inexact, or is empty."""
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            if self.street_types is None:
                self.street_types = dict(db.execute("SELECT abbreviation, name FROM street_type"))
            keys, state, postcode = self.parse(address)
            if not keys or not keys[0][:1].isdigit() and not keys[0].startswith("LOT "):
                return None

            filters = ""
            filter_params = []
            if state:
                filters += " AND state = ?"
                filter_params.append(state)
            if postcode:
                filters += " AND postcode = ?"
                filter_params.append(postcode)

            def find(where, *params):
                return db.execute(
                    "SELECT key, state, postcode, latitude, longitude FROM address"
                    f" WHERE {where}{filters} ORDER BY principal DESC LIMIT 50",
                    [*params, *filter_params]).fetchall()

            def find_address(key):
                # '29 HAY STREET LEICHHARDT', or with no suburb, '29 HAY STREET ...'
                return (find("key = ?", key)
                        or find("key > ? AND key < ?", key + " ", key + "!"))

            rows, note = [], ""
            for key in keys:
                rows = find_address(key)
                if rows:
                    break
            if not rows:
                # A number in a range: '1 FLINDERS STREET' is '1-17 FLINDERS STREET'.
                for key in keys:
                    m = re.match(r"(\d+) (.*)", key)
                    if m:
                        number, rest = m.groups()
                        rows = find("key > ? AND key < ? AND (key LIKE ? OR key LIKE ?)",
                                    number + "-", number + ".",
                                    f"{number}-% {rest}", f"{number}-% {rest} %")
                        if rows:
                            break
            if not rows:
                # A unit that isn't listed: show its building.
                for key in keys:
                    if "/" in key.split(" ", 1)[0]:
                        rows = find_address(key.split("/", 1)[1])
                        if rows:
                            note = "Unit not found, showing the building: "
                            break
            if not rows:
                return None

            key, state, postcode, lat, lon = rows[0]
            if len({(r[0], r[1]) for r in rows}) > 1:
                note += "Several places match, showing the first: "
            return lat, lon, f"{key} {state} {postcode}", note
        finally:
            db.close()


MAP_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
  html, body, #map { height: 100%; margin: 0; }
</style>
</head>
<body>
<div id="map"></div>
<script>
  const map = L.map('map').setView([0, 0], 2);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  let marker = null;

  // Called from Python: moves the marker to the place and centres on it.
  function showPlace(lat, lon, label, zoom) {
      if (marker === null) {
          marker = L.marker([lat, lon]).addTo(map);
      } else {
          marker.setLatLng([lat, lon]);
      }
      marker.bindPopup(label);
      map.setView([lat, lon], zoom);
  }
</script>
</body>
</html>
"""


class Geocoder(QObject):
    """Looks up addresses in a background thread, reporting by signal."""
    found = pyqtSignal(float, float, str, str)  # lat, lon, address, note
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.nominatim = Nominatim(user_agent=USER_AGENT)
        self.gnaf = GnafLookup(GNAF_PATH)

    def lookup(self, address):
        Thread(target=self._run, args=(address,), daemon=True).start()

    def _run(self, address):
        try:
            if self.gnaf.available():
                try:
                    found = self.gnaf.lookup(address)
                except Exception as e:
                    print(f"G-NAF lookup failed: {e}")
                    found = None
                if found:
                    lat, lon, description, note = found
                    self.found.emit(lat, lon, description, note + "{} \u2014 " + GNAF_CREDIT)
                    return

            location = self.nominatim.geocode(address, timeout=10, addressdetails=True)
            if location:
                exact = "house_number" in location.raw.get("address", {})
                note = "{}" if exact else "House number not found, showing street: {}"
                self.found.emit(location.latitude, location.longitude, location.address, note)
            else:
                self.error.emit("Address not found. Please try being more specific.")
        except GeocoderServiceError:
            self.error.emit("Could not connect to geocoding server.")
        except Exception as e:
            self.error.emit(f"Network error: {e}")


class MapPage(QWebEnginePage):
    """Passes the page's JavaScript console messages on to stdout."""

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"map.js:{line}: {message}")


class MapView(QWebEngineView):
    """A Leaflet map, with a single marker placed from Python."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setPage(MapPage(profile, self))
        self.loaded = False
        self.pending = None  # a showPlace() call made before the page loaded
        self.loadFinished.connect(self.on_load_finished)
        self.setHtml(MAP_HTML, PAGE_BASE_URL)

    def on_load_finished(self, ok):
        if not ok:
            print("Map page failed to load")
            return
        self.loaded = True
        if self.pending:
            self.show_place(*self.pending)
            self.pending = None

    def show_place(self, lat, lon, label, zoom=18):
        if not self.loaded:
            self.pending = (lat, lon, label, zoom)
            return
        self.page().runJavaScript(
            f"showPlace({lat!r}, {lon!r}, {json.dumps(label)}, {int(zoom)})")


class MainWindow(QMainWindow):
    def __init__(self, profile):
        super().__init__()
        self.setWindowTitle("Address Map")
        self.setGeometry(100, 100, 1024, 768)

        self.geocoder = Geocoder()
        self.geocoder.found.connect(self.on_found)
        self.geocoder.error.connect(self.on_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText(
            "Enter an address (e.g., 29 Hay Street, Leichhardt)...")
        self.address_input.returnPressed.connect(self.search_address)
        self.search_button = QPushButton("Search Location")
        self.search_button.clicked.connect(self.search_address)
        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        self.map_view = MapView(profile)
        layout.addWidget(self.map_view)

        self.map_view.show_place(-33.8688, 151.2093, "Sydney", 13)

    def search_address(self):
        address = self.address_input.text().strip()
        if not address:
            return
        self.search_button.setEnabled(False)
        self.statusBar().showMessage(f"Looking up {address}...")
        self.geocoder.lookup(address)

    def on_found(self, lat, lon, full_address, note):
        """Shows the place; `note` is the status-bar message, with {} for the address."""
        self.search_button.setEnabled(True)
        street_only = note.startswith("House number not found")
        self.map_view.show_place(lat, lon, full_address, 17 if street_only else 18)
        self.statusBar().showMessage(note.format(full_address))

    def on_error(self, message):
        self.search_button.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Search Failure", message)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("darqos-place")

    # A named profile keeps its HTTP cache on disk, so tiles aren't
    # fetched again on each run, as the OSM tile policy asks.
    profile = QWebEngineProfile("map", app)
    profile.setHttpUserAgent(USER_AGENT)

    window = MainWindow(profile)
    window.show()
    status = app.exec()

    # The page must go before its profile, or WebEngine complains.
    del window
    sys.exit(status)


if __name__ == "__main__":
    main()
