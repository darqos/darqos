import sys
import json
import urllib.request
from urllib.parse import quote
from threading import Thread
from PyQt6.QtCore import QUrl, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QLineEdit, QPushButton, QMessageBox
)
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView


class GeocodeWorker(QObject):
    """Bypasses QNetworkAccessManager using native python threads and robust headers."""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def fetch_coordinates(self, address):
        def run():
            try:
                cleaned_address = " ".join(address.split())
                
                params = {
                    'q': cleaned_address,
                    'format': 'json',
                    'limit': 1
                }
                
                base_url = "https://openstreetmap.org"
                url_str = f"{base_url}?{urllib.parse.urlencode(params)}"
                
                # Rigid header configuration to completely satisfy Nominatim's security rules
                headers = {
                    # Avoid generic names; include a mock descriptive identifier or contact email
                    'User-Agent': 'PyQt62DMapViewerProject/1.0 (contact: mapdeveloper@example.com)',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'Accept-Language': 'en-US,en;q=0.5'
                }
                
                req = urllib.request.Request(url_str, headers=headers)
                
                with urllib.request.urlopen(req, timeout=5) as response:
                    raw_data = response.read().decode("utf-8")
                    data = json.loads(raw_data)
                    
                    if data and len(data) > 0:
                        # Nominatim returns an array of results, pass the first match
                        self.finished.emit(data[0]) 
                    else:
                        self.error.emit("Address not found. Please try being more specific.")
            except urllib.error.HTTPError as he:
                self.error.emit(f"Server rejected request (HTTP {he.code}): {he.reason}")
            except Exception as e:
                self.error.emit(f"Network / DNS problem: {str(e)}")
        
        Thread(target=run, daemon=True).start()



class MapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 2D OSM Map Viewer")
        self.setGeometry(100, 100, 1024, 768)

        # Worker initialization
        self.worker = GeocodeWorker()
        self.worker.finished.connect(self.on_geocode_success)
        self.worker.error.connect(self.on_geocode_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter a street address (e.g., 100 George St, Sydney)...")
        self.address_input.returnPressed.connect(self.search_address)
        
        self.search_button = QPushButton("Find on Map")
        self.search_button.clicked.connect(self.search_address)

        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        self.web_view = QWebEngineView()
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        layout.addWidget(self.web_view)

        # Load standard canvas
        self.load_map(-33.8688, 151.2093, "Welcome to Sydney")

    def load_map(self, lat, lng, popup_text="Location"):
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link rel="stylesheet" href="https://unpkg.com" />
            <script src="https://unpkg.com"></script>
            <style>
                html, body, #map {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                var map = L.map('map').setView([{lat}, {lng}], 15);
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    maxZoom: 19, attribution: '© OpenStreetMap'
                }}).addTo(map);
                L.marker([{lat}, {lng}]).addTo(map).bindPopup('{popup_text}').openPopup();
            </script>
        </body>
        </html>
        """
        self.web_view.setHtml(html_content)

    def search_address(self):
        address = self.address_input.text().strip()
        if not address:
            return
        self.search_button.setEnabled(False)
        self.worker.fetch_coordinates(address)

    def on_geocode_success(self, location_data):
        self.search_button.setEnabled(True)
        lat = float(location_data["lat"])
        lng = float(location_data["lon"])
        display_name = location_data["display_name"].replace("'", "\\'")
        self.load_map(lat, lng, display_name)

    def on_geocode_error(self, message):
        self.search_button.setEnabled(True)
        QMessageBox.warning(self, "Lookup Error", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MapWindow()
    window.show()
    sys.exit(app.exec())


