import sys
from threading import Thread
from PyQt6.QtCore import QUrl, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, 
    QHBoxLayout, QLineEdit, QPushButton, QMessageBox
)
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError

# --- EMBEDDED LEAFLET ASSETS (Removes CDN dependency completely) ---
# Embedded minified Leaflet 1.9.4 CSS structure
LEAFLET_CSS = """
.leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,.leaflet-tile-container,.leaflet-pane>svg,.leaflet-pane>canvas,.leaflet-zoom-box,.leaflet-image-layer,.leaflet-layer{position:absolute;left:0;top:0}.leaflet-container{overflow:hidden;-ms-touch-action:none;touch-action:none;cursor:default;-webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow{-webkit-user-drag:none;user-select:none;-webkit-user-select:none}.leaflet-tile{filter:inherit;visibility:hidden}.leaflet-tile-loaded{visibility:inherit}.leaflet-zoom-box{width:0;height:0;box-sizing:border-box;z-index:800}.leaflet-pane{z-index:400}.leaflet-tile-pane{z-index:200}.leaflet-overlay-pane{z-index:400}.leaflet-shadow-pane{z-index:500}.leaflet-marker-pane{z-index:600}.leaflet-tooltip-pane{z-index:650}.leaflet-popup-pane{z-index:700}.leaflet-map-pane canvas{z-index:100}.leaflet-map-pane svg{z-index:200}.leaflet-vml-shape{width:1px;height:1px}.lvml{behavior:url(#default#VML);display:inline-block;position:absolute}.leaflet-control{position:relative;z-index:800;pointer-events:auto}.leaflet-top,.leaflet-bottom{position:absolute;z-index:1000;pointer-events:none}.leaflet-top{top:0}.leaflet-right{right:0}.leaflet-bottom{bottom:0}.leaflet-left{left:0}.leaflet-control{float:left;clear:both}.leaflet-right .leaflet-control{float:right}.leaflet-top .leaflet-control{margin-top:10px}.leaflet-bottom .leaflet-control{margin-bottom:10px}.leaflet-left .leaflet-control{margin-left:10px}.leaflet-right .leaflet-control{margin-right:10px}.leaflet-fade-anim .leaflet-tile{will-change:opacity}.leaflet-fade-anim .leaflet-popup{opacity:0;-webkit-transition:opacity .2s linear;-moz-transition:opacity .2s linear;transition:opacity .2s linear}.leaflet-fade-anim .leaflet-map-pane .leaflet-popup{opacity:1}.leaflet-zoom-animated .leaflet-tile{transform-origin:0 0}.leaflet-zoom-anim .leaflet-zoom-animated{will-change:transform;-webkit-transition:-webkit-transform .25s cubic-bezier(0,0,.25,1);-moz-transition:-moz-transform .25s cubic-bezier(0,0,.25,1);transition:transform .25s cubic-bezier(0,0,.25,1)}.leaflet-zoom-anim .leaflet-tile,.leaflet-pan-anim .leaflet-tile{-webkit-transition:none;-moz-transition:none;transition:none}.leaflet-zoom-anim .leaflet-zoom-hide{visibility:hidden}.leaflet-interactive{cursor:pointer}.leaflet-grab{cursor:-webkit-grab;cursor:-moz-grab;cursor:grab}.leaflet-dragging .leaflet-grab,.leaflet-dragging .leaflet-grab .leaflet-interactive{cursor:-webkit-dragging;cursor:-moz-dragging;cursor:dragging}.leaflet-marker-icon,.leaflet-marker-shadow{display:block}.leaflet-bar{box-shadow:0 1px 5px rgba(0,0,0,.65);border-radius:4px}.leaflet-bar a{background-color:#fff;border-bottom:1px solid #ccc;width:26px;height:26px;line-height:26px;display:block;text-align:center;text-decoration:none;color:#000}.leaflet-bar a:hover{background-color:#f4f4f4}.leaflet-bar a:first-child{border-top-left-radius:4px;border-top-right-radius:4px}.leaflet-bar a:last-child{border-bottom-left-radius:4px;border-bottom-right-radius:4px;border-bottom:none}.leaflet-control-zoom-in,.leaflet-control-zoom-out{font:bold 18px 'Lucida Console',Monaco,monospace}.leaflet-popup{position:absolute;text-align:center;margin-bottom:20px}.leaflet-popup-content-wrapper{padding:1px;text-align:left;border-radius:12px;background:#fff;box-shadow:0 3px 14px rgba(0,0,0,.4)}.leaflet-popup-content{margin:13px 24px;line-height:1.3;font-size:13px;min-height:1px}.leaflet-popup-tip-container{width:40px;height:20px;position:absolute;left:50%;margin-left:-20px;overflow:hidden;pointer-events:none}.leaflet-popup-tip{width:17px;height:17px;padding:1px;margin:-10px auto 0;transform:rotate(45deg);background:#fff;box-shadow:0 3px 14px rgba(0,0,0,.4)}.leaflet-popup-close-button{position:absolute;top:0;right:0;border:none;background:transparent;padding:8px 8px 0 0;width:24px;height:24px;font:16px/14px Tahoma,Verdana,sans-serif;color:#757575;text-weight:bold;text-decoration:none;cursor:pointer}
"""

class GeocodeWorker(QObject):
    """Uses geopy to communicate reliably with OpenStreetMap servers."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        # Fallback to an explicitly unique user agent format
        self.geolocator = Nominatim(user_agent="PyQt6_OfflineLeaflet_MapApp_v2")

    def fetch_coordinates(self, address):
        def run():
            try:
                cleaned_address = " ".join(address.split())
                location = self.geolocator.geocode(cleaned_address, timeout=10)
                if location:
                    self.finished.emit(location)
                else:
                    self.error.emit("Address not found. Try adding a city or postcode.")
            except GeocoderServiceError:
                # If Nominatim is completely blocked by your network profile, suggest checking connections
                self.error.emit("Could not reach geocoding server. Check your system proxy/firewall.")
            except Exception as e:
                self.error.emit(f"Network error: {str(e)}")
        
        Thread(target=run, daemon=True).start()

class MapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 Direct OSM Map Engine")
        self.setGeometry(100, 100, 1024, 768)

        self.worker = GeocodeWorker()
        self.worker.finished.connect(self.on_geocode_success)
        self.worker.error.connect(self.on_geocode_error)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("Enter a street address (e.g., 38 Hay Street, Leichhardt NSW)...")
        self.address_input.returnPressed.connect(self.search_address)
        
        self.search_button = QPushButton("Find on Map")
        self.search_button.clicked.connect(self.search_address)

        search_layout.addWidget(self.address_input)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        self.web_view = QWebEngineView()
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        layout.addWidget(self.web_view)

        # Initialize Default Location (Sydney)
        self.load_map(-33.8688, 151.2093, "Welcome to Sydney")

    def load_map(self, lat, lng, popup_text="Location"):
        # We fetch the primary leaflet library file via an official cloud link over standard HTTPS.
        # If your firewall drops this, you can manually replace the src link below with a local downloaded leaflet.js script path.
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                {LEAFLET_CSS}
                html, body, #map {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
            </style>
            <script src="https://cloudflare.com"></script>
        </head>
        <body>
            <div id="map"></div>
            <script>
                // Fallback loop checking if script failed to download due to restrictive DNS policies
                if (typeof L === 'undefined') {{
                    document.getElementById('map').innerHTML = "<div style='padding:20px;font-family:sans-serif;color:#c0392b;'><b>JavaScript Canvas Error:</b> Leaflet engine could not be resolved from CDN servers. Check your local firewall policies or corporate network filters.</div>";
                }} else {{
                    var map = L.map('map').setView([{lat}, {lng}], 16);
                    
                    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                        maxZoom: 19, attribution: '© OpenStreetMap'
                    }}).addTo(map);
                    
                    L.marker([{lat}, {lng}]).addTo(map).bindPopup('{popup_text}').openPopup();
                }}
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

    def on_geocode_success(self, location_obj):
        self.search_button.setEnabled(True)
        lat = location_obj.latitude
        lng = location_obj.longitude
        display_name = location_obj.address.replace("'", "\\'")
        self.load_map(lat, lng, display_name)

    def on_geocode_error(self, message):
        self.search_button.setEnabled(True)
        QMessageBox.warning(self, "Lookup Error", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MapWindow()
    window.show()
    sys.exit(app.exec())
