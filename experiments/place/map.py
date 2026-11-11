import sys
import math
import json
import urllib.request
from threading import Thread
from PyQt6.QtCore import Qt, QPointF, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsEllipseItem
)
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError

class MapWorker(QObject):
    """Handles both address geocoding and image tile streaming natively in pure Python threads."""
    geocode_finished = pyqtSignal(float, float, str)
    tile_finished = pyqtSignal(int, int, bytes)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.geolocator = Nominatim(user_agent="PyQt6_Threaded2D_MapApp")

    def fetch_coordinates(self, address):
        def run():
            try:
                location = self.geolocator.geocode(address, timeout=10)
                if location:
                    self.geocode_finished.emit(location.latitude, location.longitude, location.address)
                else:
                    self.error.emit("Address not found. Please try being more specific.")
            except GeocoderServiceError:
                self.error.emit("Could not connect to geocoding server.")
            except Exception as e:
                self.error.emit(f"Network error: {str(e)}")
        Thread(target=run, daemon=True).start()

    def fetch_tile(self, zoom, tx, ty, px, py):
        def run():
            try:
                # Absolute, hard-coded clean string endpoint layout
                url = f"https://a.tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
                req = urllib.request.Request(
                    url, 
                    headers={'User-Agent': 'PyQt6NativeTileEngine/1.0'}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    tile_bytes = response.read()
                    self.tile_finished.emit(px, py, tile_bytes)
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

    def lat_lon_to_tile(self, lat, lon, zoom):
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        xtile = int((lon + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
        return xtile, ytile

    def lat_lon_to_pixel(self, lat, lon, zoom):
        n = 2.0 ** zoom
        x = (lon + 180.0) / 360.0 * n * 256
        lat_rad = math.radians(lat)
        y = (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n * 256
        return x, y

    def update_map_view(self, lat, lon):
        self.scene.clear()
        self.view.resetTransform()
        self.view.zoom_level = 1.0

        center_xtile, center_ytile = self.lat_lon_to_tile(lat, lon, self.zoom)
        ref_tile_x = center_xtile * 256
        ref_tile_y = center_ytile * 256

        # Stream tiles inside background worker threads completely isolated from Qt networking
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                tx = center_xtile + dx
                ty = center_ytile + dy
                px = (tx * 256) - ref_tile_x
                py = (ty * 256) - ref_tile_y
                
                self.worker.fetch_tile(self.zoom, tx, ty, px, py)

        target_abs_x, target_abs_y = self.lat_lon_to_pixel(lat, lon, self.zoom)
        marker_px = target_abs_x - ref_tile_x
        marker_py = target_abs_y - ref_tile_y

        self.marker = QGraphicsEllipseItem(-10, -10, 20, 20)
        self.marker.setBrush(QBrush(QColor(231, 76, 60)))  
        self.marker.setPen(QPen(QColor(255, 255, 255), 2)) 
        self.marker.setZValue(10)                          
        self.marker.setPos(marker_px, marker_py)           
        self.scene.addItem(self.marker)
        
        self.view.centerOn(marker_px, marker_py)

    def on_tile_downloaded(self, px, py, tile_bytes):
        """Converts raw byte data streams instantly into native graphic scene assets."""
        pixmap = QPixmap()
        pixmap.loadFromData(tile_bytes)
        item = QGraphicsPixmapItem(pixmap)
        item.setPos(px, py)
        item.setZValue(0)
        self.scene.addItem(item)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 Threaded 2D Map Application")
        self.setGeometry(100, 100, 1024, 768)

        self.worker = MapWorker()
        self.worker.geocode_finished.connect(self.on_geocode_success)
        self.worker.error.connect(self.on_geocode_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter an address (e.g., 38 Hay Street, Leichhardt)...")
        self.address_input.returnPressed.connect(self.search_address)
        
        self.search_button = QPushButton("Search Location")
        self.search_button.clicked.connect(self.search_address)

        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        self.map_widget = NativeMapWidget(self.worker)
        layout.addWidget(self.map_widget)

        self.map_widget.update_map_view(-33.8688, 151.2093)

    def search_address(self):
        address = self.address_input.text().strip()
        if not address:
            return
        self.search_button.setEnabled(False)
        self.worker.fetch_coordinates(address)

    def on_geocode_success(self, lat, lon, full_address):
        self.search_button.setEnabled(True)
        self.map_widget.update_map_view(lat, lon)

    def on_geocode_error(self, message):
        self.search_button.setEnabled(True)
        QMessageBox.warning(self, "Search Failure", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
