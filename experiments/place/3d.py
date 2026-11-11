import sys
import json
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QLabel, QDoubleSpinBox, QPushButton, QGroupBox, QFileDialog, QMessageBox
)
import pyqtgraph.opengl as gl

class SpatialModellerJSON(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D Spatial Modeller with JSON Save/Load")
        self.resize(1100, 768)

        # Track data of all spawned items: [{"w":..., "d":..., "h":..., "x":..., "y":..., "z":...}, ...]
        self.saved_objects_data = []
        # Track the actual OpenGL items rendered on screen to allow clearing/reloading
        self.rendered_mesh_items = []

        # Main Layout Setup
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # 1. Sidebar Control Panel
        sidebar = QVBoxLayout()
        main_layout.addLayout(sidebar, stretch=1)

        # Object controls group
        control_group = QGroupBox("Add 3D Box")
        control_layout = QVBoxLayout(control_group)
        
        # Dimensions (Width, Depth, Height)
        control_layout.addWidget(QLabel("Width (X):"))
        self.spin_w = QDoubleSpinBox()
        self.spin_w.setValue(2.0)
        control_layout.addWidget(self.spin_w)

        control_layout.addWidget(QLabel("Depth (Y):"))
        self.spin_d = QDoubleSpinBox()
        self.spin_d.setValue(2.0)
        control_layout.addWidget(self.spin_d)

        control_layout.addWidget(QLabel("Height (Z):"))
        self.spin_h = QDoubleSpinBox()
        self.spin_h.setValue(2.0)
        control_layout.addWidget(self.spin_h)

        # Positions (X, Y, Z)
        control_layout.addWidget(QLabel("Position X:"))
        self.spin_x = QDoubleSpinBox()
        self.spin_x.setRange(-50, 50)
        self.spin_x.setValue(0.0)
        control_layout.addWidget(self.spin_x)

        control_layout.addWidget(QLabel("Position Y:"))
        self.spin_y = QDoubleSpinBox()
        self.spin_y.setRange(-50, 50)
        self.spin_y.setValue(0.0)
        control_layout.addWidget(self.spin_y)

        control_layout.addWidget(QLabel("Position Z:"))
        self.spin_z = QDoubleSpinBox()
        self.spin_z.setRange(-50, 50)
        self.spin_z.setValue(0.0)
        control_layout.addWidget(self.spin_z)

        # Add button
        self.btn_add = QPushButton("Spawn Object")
        self.btn_add.clicked.connect(self.handle_spawn_click)
        control_layout.addWidget(self.btn_add)
        
        sidebar.addWidget(control_group)

        # File I/O Group
        file_group = QGroupBox("Storage Commands")
        file_layout = QVBoxLayout(file_group)

        self.btn_save = QPushButton("💾 Save JSON Layout")
        self.btn_save.clicked.connect(self.save_to_json)
        file_layout.addWidget(self.btn_save)

        self.btn_load = QPushButton("📁 Load JSON Layout")
        self.btn_load.clicked.connect(self.load_from_json)
        file_layout.addWidget(self.btn_load)

        sidebar.addWidget(file_group)
        sidebar.addStretch()

        # 2. 3D GL Canvas Viewport
        self.view = gl.GLViewWidget()
        self.view.setCameraPosition(distance=20, elevation=30, azimuth=45)
        main_layout.addWidget(self.view, stretch=4)

        # Add spatial helpers (Grid and Axes)
        grid = gl.GLGridItem()
        grid.setSize(x=40, y=40)
        grid.setSpacing(x=2, y=2)
        self.view.addItem(grid)

        axes = gl.GLAxisItem()
        axes.setSize(x=5, y=5, z=5)
        self.view.addItem(axes)

    def handle_spawn_click(self):
        """Triggered when the user hits 'Spawn Object' manually."""
        w, d, h = self.spin_w.value(), self.spin_d.value(), self.spin_h.value()
        x, y, z = self.spin_x.value(), self.spin_y.value(), self.spin_z.value()
        
        # Save structural parameters to our layout dictionary
        obj_data = {"w": w, "d": d, "h": h, "x": x, "y": y, "z": z}
        self.saved_objects_data.append(obj_data)
        
        # Build the physical GL item
        self.render_box_mesh(obj_data)

    def render_box_mesh(self, data):
        """Generates the 3D visual mesh and renders it onto the canvas."""
        w, d, h = data["w"], data["d"], data["h"]
        x, y, z = data["x"], data["y"], data["z"]

        # Complete 3D corner coordinates for the prism geometry
        verts = np.array([
            [0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],  # Bottom 4 vertices (0-3)
            [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]   # Top 4 vertices (4-7)
        ])
        
        # Triangular indices mapping out the 6 faces of the cube (2 triangles per face)
        faces = np.array([
            [0, 1, 2], [0, 2, 3], # Bottom Face
            [4, 5, 6], [4, 6, 7], # Top Face
            [0, 1, 5], [0, 5, 4], # Front Face
            [1, 2, 6], [1, 6, 5], # Right Face
            [2, 3, 7], [2, 7, 6], # Back Face
            [3, 0, 4], [3, 4, 7]  # Left Face
        ])
        
        # Style faces with a semi-transparent blue layout color
        colors = np.array([[0.2, 0.6, 1.0, 0.6]] * 12)
        
        # Create the visual Mesh object
        mesh_data = gl.MeshData(vertexes=verts, faces=faces, faceColors=colors)
        mesh_item = gl.GLMeshItem(meshdata=mesh_data, smooth=False, drawEdges=True, edgeColor=(1, 1, 1, 1))
        
        # Translate the object out to its spatial coordinates
        mesh_item.translate(x, y, z)
        self.view.addItem(mesh_item)
        
        # Store a pointer to the live GL object so we can delete it during a clear/reload
        self.rendered_mesh_items.append(mesh_item)

    def save_to_json(self):
        """Exports the tracked python list structure into a local JSON document."""
        if not self.saved_objects_data:
            QMessageBox.warning(self, "Empty Canvas", "There are no spatial objects to save.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Spatial Layout", "", "JSON Files (*.json)"
        )
        
        if file_path:
            # Ensure proper file extension
            if not file_path.endswith('.json'):
                file_path += '.json'
                
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.saved_objects_data, f, indent=4)
                
            QMessageBox.information(self, "Success", "Layout successfully exported to JSON!")

    def load_from_json(self):
        """Imports spatial layout bounds from a JSON structure and recreates the scene."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Spatial Layout", "", "JSON Files (*.json)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                
                # Simple validation check
                if not isinstance(loaded_data, list):
                    raise ValueError("JSON data must contain a top-level list of elements.")

                # 1. Clear old view elements
                for item in self.rendered_mesh_items:
                    self.view.removeItem(item)
                self.rendered_mesh_items.clear()
                self.saved_objects_data.clear()

                # 2. Build the new scene
                for obj_data in loaded_data:
                    self.saved_objects_data.append(obj_data)
                    self.render_box_mesh(obj_data)

                QMessageBox.information(self, "Success", f"Loaded {len(loaded_data)} objects from JSON!")

            except Exception as e:
                QMessageBox.critical(self, "Error Loading File", f"Failed to parse JSON structure:\n{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SpatialModellerJSON()
    window.show()
    sys.exit(app.exec())
