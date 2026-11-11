import sys
import math
import json
import re
import urllib.parse
import urllib.request
from threading import Thread
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsEllipseItem
)
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError

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


def nsw_address_key(address):
    """Normalises '29 Hay St, Leichhardt NSW 2040' to '29 HAY STREET LEICHHARDT'."""
    words = re.sub(r"[,.]", " ", address).upper().split()
    while words and (words[-1] in TRAILING_NOISE or re.fullmatch(r"\d{4}", words[-1])):
        words.pop()
    return " ".join(STREET_TYPES.get(w, w) for w in words)


class MapWorker(QObject):
    """Handles both address geocoding and image tile streaming natively in pure Python threads."""
    geocode_finished = pyqtSignal(float, float, str, bool)  # lat, lon, address, exact
    tile_finished = pyqtSignal(int, int, int, bytes)  # generation, px, py, data
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.geolocator = Nominatim(user_agent="PyQt6_Threaded2D_MapApp")

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
                                     headers={'User-Agent': 'DarqOS-place-experiment/1.0'})
        # The service is usually sub-second, but has occasional slow spells.
        with urllib.request.urlopen(req, timeout=20) as response:
            features = json.load(response).get("features") or []
        if not features:
            return None
        f = features[0]
        return f["geometry"]["y"], f["geometry"]["x"], f["attributes"]["address"]

    def fetch_coordinates(self, address):
        def run():
            try:
                try:
                    found = self.lookup_nsw(address)
                except Exception as e:
                    print(f"NSW address lookup failed: {e}")
                    found = None
                if found:
                    self.geocode_finished.emit(*found, True)
                    return

                location = self.geolocator.geocode(address, timeout=10, addressdetails=True)
                if location:
                    exact = "house_number" in location.raw.get("address", {})
                    self.geocode_finished.emit(location.latitude, location.longitude,
                                               location.address, exact)
                else:
                    self.error.emit("Address not found. Please try being more specific.")
            except GeocoderServiceError:
                self.error.emit("Could not connect to geocoding server.")
            except Exception as e:
                self.error.emit(f"Network error: {str(e)}")
        Thread(target=run, daemon=True).start()

    def fetch_tile(self, generation, zoom, tx, ty, px, py):
        def run():
            try:
                url = f"https://a.tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
                req = urllib.request.Request(url, headers={'User-Agent': 'PyQt6NativeTileEngine/1.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    tile_bytes = response.read()
                    self.tile_finished.emit(generation, px, py, tile_bytes)
            except Exception as e:
                print(f"Failed to fetch tile {tx},{ty}: {e}")
        Thread(target=run, daemon=True).start()

class InteractiveMapView(QGraphicsView):
    """Custom canvas that handles mouse-drag panning and scroll-wheel zooming."""
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.zoom_level = 1.0

    def wheelEvent(self, event):
        """Processes smooth programmatic zoom mapping via mouse scroll."""
        zoom_factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        if 0.2 < (self.zoom_level * zoom_factor) < 10.0:
            self.zoom_level *= zoom_factor
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(zoom_factor, zoom_factor)

class NativeMapWidget(QWidget):
    """Main application map layout handling tile loading and marker plotting."""
    def __init__(self, worker):
        super().__init__()
        self.zoom = 16  
        self.worker = worker
        self.worker.tile_finished.connect(self.on_tile_downloaded)

        self.scene = QGraphicsScene(self)
        self.view = InteractiveMapView(self.scene)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.marker = None
        self.generation = 0  # bumped per view, so late tiles from an old view are dropped

    def lat_lon_to_tile_exact(self, lat, lon, zoom):
        """Calculates precise floating point tile coordinates to keep exact house offsets."""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        xtile = (lon + 180.0) / 360.0 * n
        ytile = (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n
        return xtile, ytile

    def update_map_view(self, lat, lon):
        self.scene.clear()
        self.view.resetTransform()
        self.view.zoom_level = 1.0
        self.generation += 1

        # 1. Get the precise fractional tile location for the address numbers
        exact_x, exact_y = self.lat_lon_to_tile_exact(lat, lon, self.zoom)

        # 2. Derive the integer grid coordinates for map tile downloading
        center_xtile = math.floor(exact_x)
        center_ytile = math.floor(exact_y)

        # 3. Establish our local 2D layout drawing reference zero-point 
        # (Using the top-left edge corner pixel of our center tile image block)
        ref_pixel_x = center_xtile * 256
        ref_pixel_y = center_ytile * 256

        # 4. Stream background tiles relative to our integer tile grid boundaries
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                tx = center_xtile + dx
                ty = center_ytile + dy
                px = (tx * 256) - ref_pixel_x
                py = (ty * 256) - ref_pixel_y
                self.worker.fetch_tile(self.generation, self.zoom, tx, ty, px, py)

        # Fix the scene to the whole tile grid now, so centring on the marker
        # holds as the tiles arrive, rather than the scene growing under it.
        self.scene.setSceneRect(-2 * 256, -2 * 256, 5 * 256, 5 * 256)

        # 5. FIXED: Pin layout math calculations using exact decimals instead of truncation
        marker_px = (exact_x * 256) - ref_pixel_x
        marker_py = (exact_y * 256) - ref_pixel_y

        # Plot a custom vector place marker exactly over the specific target location
        self.marker = QGraphicsEllipseItem(-10, -10, 20, 20)
        self.marker.setBrush(QBrush(QColor(231, 76, 60)))  
        self.marker.setPen(QPen(QColor(255, 255, 255), 2)) 
        self.marker.setZValue(10)                          
        self.marker.setPos(marker_px, marker_py)           
        self.scene.addItem(self.marker)
        
        # Shift camera perspective directly onto the pinpoint coordinate focus space
        self.view.centerOn(marker_px, marker_py)

    def on_tile_downloaded(self, generation, px, py, tile_bytes):
        if generation != self.generation:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(tile_bytes)
        item = QGraphicsPixmapItem(pixmap)
        item.setPos(px, py)
        item.setZValue(0)
        self.scene.addItem(item)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 Fixed 2D Address Map Application")
        self.setGeometry(100, 100, 1024, 768)

        self.worker = MapWorker()
        self.worker.geocode_finished.connect(self.on_geocode_success)
        self.worker.error.connect(self.on_geocode_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter an address (e.g., 29 Hay Street, Leichhardt)...")
        self.address_input.returnPressed.connect(self.search_address)
        
        self.search_button = QPushButton("Search Location")
        self.search_button.clicked.connect(self.search_address)

        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        self.map_widget = NativeMapWidget(self.worker)
        layout.addWidget(self.map_widget)
        # Create the status bar now: added later, it would shrink the map
        # view and push the marker off-centre.
        self.statusBar()

        # Initial View
        self.map_widget.update_map_view(-33.8688, 151.2093)

    def search_address(self):
        address = self.address_input.text().strip()
        if not address:
            return
        self.search_button.setEnabled(False)
        self.worker.fetch_coordinates(address)

    def on_geocode_success(self, lat, lon, full_address, exact):
        self.search_button.setEnabled(True)
        self.map_widget.update_map_view(lat, lon)
        if exact:
            self.statusBar().showMessage(full_address)
        else:
            self.statusBar().showMessage(f"House number not found, showing street: {full_address}")

    def on_geocode_error(self, message):
        self.search_button.setEnabled(True)
        QMessageBox.warning(self, "Search Failure", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
