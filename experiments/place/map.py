"""Shows a street address on a pannable, zoomable map.

The map is Leaflet, with OpenStreetMap tiles, in a QWebEngineView.
Addresses are geocoded in Python: NSW address points first, for
house-level positions in NSW, then Nominatim for everywhere else.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
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

# NSW Spatial Services address points (G-NAF based): house-level positions.
NSW_ADDRESS_URL = ("https://portal.spatial.nsw.gov.au/server/rest/services/"
                   "NSW_Geocoded_Addressing_Theme/MapServer/1/query")

# Street-type abbreviations, expanded to the NSW service's full words.
STREET_TYPES = {
    "ST": "STREET", "RD": "ROAD", "AVE": "AVENUE", "AV": "AVENUE",
    "PDE": "PARADE", "CRES": "CRESCENT", "CR": "CRESCENT", "DR": "DRIVE",
    "PL": "PLACE", "LN": "LANE", "CT": "COURT", "HWY": "HIGHWAY",
    "TCE": "TERRACE", "CL": "CLOSE", "ESP": "ESPLANADE", "BLVD": "BOULEVARD",
}

# Trailing words that aren't part of the NSW address string.
TRAILING_NOISE = {"NSW", "AUSTRALIA", "AU"}

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


def nsw_address_key(address):
    """Normalises '29 Hay St, Leichhardt NSW 2040' to '29 HAY STREET LEICHHARDT'."""
    words = re.sub(r"[,.]", " ", address).upper().split()
    while words and (words[-1] in TRAILING_NOISE or re.fullmatch(r"\d{4}", words[-1])):
        words.pop()
    return " ".join(STREET_TYPES.get(w, w) for w in words)


class Geocoder(QObject):
    """Looks up addresses in a background thread, reporting by signal."""
    found = pyqtSignal(float, float, str, bool)  # lat, lon, address, exact
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.nominatim = Nominatim(user_agent=USER_AGENT)

    def lookup_nsw(self, address):
        """Returns (lat, lon, address) from NSW address points, or None."""
        key = nsw_address_key(address)
        if not key or not key[0].isdigit():
            return None
        params = urllib.parse.urlencode({
            "where": "address LIKE '{}%'".format(key.replace("'", "''")),
            "outFields": "address",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": "1",
            "f": "json",
        })
        req = urllib.request.Request(f"{NSW_ADDRESS_URL}?{params}",
                                     headers={"User-Agent": USER_AGENT})
        # The service is usually sub-second, but has occasional slow spells.
        with urllib.request.urlopen(req, timeout=20) as response:
            features = json.load(response).get("features") or []
        if not features:
            return None
        f = features[0]
        return f["geometry"]["y"], f["geometry"]["x"], f["attributes"]["address"]

    def lookup(self, address):
        Thread(target=self._run, args=(address,), daemon=True).start()

    def _run(self, address):
        try:
            try:
                nsw = self.lookup_nsw(address)
            except Exception as e:
                print(f"NSW address lookup failed: {e}")
                nsw = None
            if nsw:
                self.found.emit(*nsw, True)
                return

            location = self.nominatim.geocode(address, timeout=10, addressdetails=True)
            if location:
                exact = "house_number" in location.raw.get("address", {})
                self.found.emit(location.latitude, location.longitude,
                                location.address, exact)
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

    def on_found(self, lat, lon, full_address, exact):
        self.search_button.setEnabled(True)
        self.map_view.show_place(lat, lon, full_address, 18 if exact else 17)
        if exact:
            self.statusBar().showMessage(full_address)
        else:
            self.statusBar().showMessage(
                f"House number not found, showing street: {full_address}")

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
