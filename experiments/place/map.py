import sys
import math
from threading import Thread
from PyQt6.QtCore import Qt, QPointF, pyqtSignal, QObject, QUrl
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QGraphicsEllipseItem
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError

class GeocodeWorker(QObject):
    """Handles address geocoding securely in a background thread."""
    finished = pyqtSignal(float, float, str)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.geolocator = Nominatim(user_agent="PyQt6_Native2D_MapApp")

    def fetch_coordinates(self, address):
        def run():
            try:
                location = self.geolocator.geocode(address, timeout=10)
                if location:
                    self.finished.emit(location.latitude, location.longitude, location.address)
                else:
                    self.error.emit("Address not found. Please try being more specific.")
            except GeocoderServiceError:
                self.error.emit("Could not connect to geocoding server.")
            except Exception as e:
                self.error.emit(f"Network error: {str(e)}")
        Thread(target=run, daemon=True).start()

class InteractiveMapView(QGraphicsView):
    """Custom canvas that handles mouse-drag panning and scroll-wheel zooming."""
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag) # Built-in pan movement
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.zoom_level = 1.0

    def wheelEvent(self, event):
        """Processes smooth programmatic zoom mapping via mouse scroll."""
        zoom_factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        
        # Keep boundaries safely clamped
        if 0.2 < (self.zoom_level * zoom_factor) < 10.0:
            self.zoom_level *= zoom_factor
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(zoom_factor, zoom_factor)

class NativeMapWidget(QWidget):
    """Main application map layout handling pure Python tile loading and marker plotting."""
    def __init__(self):
        super().__init__()
        self.zoom = 16  # Standard OSM Zoom profile level
        self.manager = QNetworkAccessManager(self)
        self.manager.finished.connect(self.handle_tile_download)

        # Core scene & canvas view structures
        self.scene = QGraphicsScene(self)
        self.view = InteractiveMapView(self.scene)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.loaded_tiles = {}
        self.marker = None

    def lat_lon_to_tile(self, lat, lon, zoom):
        """Converts geographic coordinates into OSM standard X/Y tile indexing math."""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        xtile = int((lon + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
        return xtile, ytile

    def tile_to_pixel_pos(self, xtile, ytile, center_lat, center_lon):
        """Calculates pixel grid displacements relative to the scene's focus point."""
        n = 2.0 ** self.zoom
        # Local focus point coordinate translations
        center_x = (center_lon + 180.0) / 360.0 * n * 256
        lat_rad = math.radians(center_lat)
        center_y = (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n * 256

        tile_x = xtile * 256
        tile_y = ytile * 256
        return tile_x - center_x, tile_y - center_y

    def update_map_view(self, lat, lon):
        """Clears old map canvas data and initiates a grid tile render sequence."""
        self.scene.clear()
        self.loaded_tiles.clear()
        self.view.resetTransform()
        self.view.zoom_level = 1.0

        center_xtile, center_ytile = self.lat_lon_to_tile(lat, lon, self.zoom)

        # Download an immediate 3x3 surrounding grid block centered on the address pin position
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                tx = center_xtile + dx
                ty = center_ytile + dy
                
                # Compute layout offset coordinate spacing
                px, py = self.tile_to_pixel_pos(tx, ty, lat, lon)
                
                # Fetch tiles securely via standard image streaming
                url = f"https://tile.openstreetmap.org/{self.zoom}/{tx}/{ty}.png"
                req = QNetworkRequest(QUrl(url))
                req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "PyQt6MapTileEngine/1.0")
                
                reply = self.manager.get(req)
                self.loaded_tiles[reply] = (px, py)

        # Plot a custom vector place marker right at the structural center coordinates (0, 0)
        self.marker = QGraphicsEllipseItem(-10, -10, 20, 20)
        self.marker.setBrush(QBrush(QColor(231, 76, 60))) # Vivid Map Red
        self.marker.setPen(QPen(QColor(255, 255, 255), 2)) # White outer border
        self.marker.setZValue(10) # Keep pin pinned clearly over background images
        self.scene.addItem(self.marker)
        
        self.view.centerOn(0, 0)

    def handle_tile_download(self, reply):
        """Renders map image tiles onto the coordinate scene as they arrive."""
        if reply.error() == QNetworkReply.NetworkError.NoError and reply in self.loaded_tiles:
            px, py = self.loaded_tiles[reply]
            data = reply.readAll()
            
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            
            # Place map canvas layer into graphics viewport grid
            item = QGraphicsPixmapItem(pixmap)
            item.setPos(px, py)
            item.setZValue(0)
            self.scene.addItem(item)
            
        reply.deleteLater()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 Native 2D Map Application")
        self.setGeometry(100, 100, 1024, 768)

        self.worker = GeocodeWorker()
        self.worker.finished.connect(self.on_geocode_success)
        self.worker.error.connect(self.on_geocode_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # Search Bar UI Components
        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter an address (e.g., 38 Hay Street, Leichhardt)...")
        self.address_input.returnPressed.connect(self.search_address)
        
        self.search_button = QPushButton("Search Location")
        self.search_button.clicked.connect(self.search_address)

        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        # Core Canvas Element
        self.map_widget = NativeMapWidget()
        layout.addWidget(self.map_widget)

        # Initial Canvas View (Sydney)
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
