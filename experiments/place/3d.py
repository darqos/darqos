import sys
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QLabel, QDoubleSpinBox, QPushButton, QGroupBox
)
import pyqtgraph.opengl as gl

class SpatialModeller(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Simple PyQt6 3D Spatial Modeller")
        self.resize(1024, 768)

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
        self.btn_add.clicked.connect(self.add_cube)
        control_layout.addWidget(self.btn_add)
        
        sidebar.addWidget(control_group)
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

    def add_cube(self):
        # Gather dimensions and coordinates from UI
        w, d, h = self.spin_w.value(), self.spin_d.value(), self.spin_h.value()
        x, y, z = self.spin_x.value(), self.spin_y.value(), self.spin_z.value()

        # Generate mesh structure for a solid cube bounding box
        verts = np.array([
            [0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0],
            [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]
        ])
        faces = np.array([
            [0, 1, 2], [0, 2, 3], # Bottom
            [4, 5, 6], [4, 6, 7], # Top
            [0, 1, 5], [0, 5, 4], # Front
            [1, 2, 6], [1, 6, 5], # Right
            [2, 3, 7], [2, 7, 6], # Back
            [3, 0, 4], [3, 4, 7]  # Left
        ])
        
        # Style faces with a semi-transparent layout color
        colors = np.array([[0.2, 0.6, 1.0, 0.6]] * 12)

        # Create mesh object
        mesh_data = gl.MeshData(vertexes=verts, faces=faces, faceColors=colors)
        mesh_item = gl.GLMeshItem(meshdata=mesh_data, smooth=False, drawEdges=True, edgeColor=(1, 1, 1, 1))
        
        # Translate to chosen spatial coordinates
        mesh_item.translate(x, y, z)
        
        # Render onto 3D viewport canvas
        self.view.addItem(mesh_item)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SpatialModeller()
    window.show()
    sys.exit(app.exec())
