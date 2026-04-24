import numpy as np
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QComboBox, QPushButton, QLineEdit, QMessageBox,
)
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProject, QgsPointXY, QgsRasterLayer, QgsCoordinateTransform,
)
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker


class DrainDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.start_point = None   # in map canvas CRS
        self.map_tool = None
        self.marker_start = None
        self.marker_result = None
        self.setWindowTitle("Drænudløbspunkter – Find udløbspunkt")
        self.setMinimumWidth(420)
        self._build_ui()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        main = QVBoxLayout(self)

        # Startpunkt
        grp_pt = QGroupBox("Startpunkt")
        frm = QFormLayout(grp_pt)
        self.btn_pick = QPushButton("Klik startpunkt på kortet…")
        self.btn_pick.clicked.connect(self._pick_point)
        frm.addRow(self.btn_pick)
        self.x_edit = QLineEdit(); self.x_edit.setReadOnly(True)
        self.y_edit = QLineEdit(); self.y_edit.setReadOnly(True)
        frm.addRow("X:", self.x_edit)
        frm.addRow("Y:", self.y_edit)
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(-9999, 9999)
        self.height_spin.setDecimals(3)
        self.height_spin.setSuffix(" m.o.h.")
        frm.addRow("Starthøjde (drænbund):", self.height_spin)
        main.addWidget(grp_pt)

        # Parametre
        grp_par = QGroupBox("Parametre")
        frm2 = QFormLayout(grp_par)
        self.dem_combo = QComboBox()
        self._fill_dem_layers()
        frm2.addRow("DEM-lag:", self.dem_combo)
        self.slope_spin = QDoubleSpinBox()
        self.slope_spin.setRange(0.1, 500)
        self.slope_spin.setValue(2.0)
        self.slope_spin.setDecimals(2)
        self.slope_spin.setSuffix(" ‰")
        frm2.addRow("Fald:", self.slope_spin)

        self.radius_spin = QDoubleSpinBox()
        self.radius_spin.setRange(10, 50000)
        self.radius_spin.setValue(2000)
        self.radius_spin.setDecimals(0)
        self.radius_spin.setSuffix(" m")
        frm2.addRow("Max søgeradius:", self.radius_spin)
        main.addWidget(grp_par)

        # Knapper
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Find udløbspunkt")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._run)
        btn_row.addWidget(self.btn_run)
        btn_close = QPushButton("Luk")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        main.addLayout(btn_row)

        # Resultat
        self.lbl_result = QLabel()
        self.lbl_result.setWordWrap(True)
        main.addWidget(self.lbl_result)

    def _fill_dem_layers(self):
        self.dem_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsRasterLayer) and layer.isValid():
                self.dem_combo.addItem(layer.name(), layer.id())

    # --------------------------------------------------------- Map picking --

    def _pick_point(self):
        self.hide()
        self.map_tool = QgsMapToolEmitPoint(self.canvas)
        self.map_tool.canvasClicked.connect(self._on_canvas_click)
        self.canvas.setMapTool(self.map_tool)

    def _on_canvas_click(self, point, button):
        self.start_point = point
        self.x_edit.setText(f"{point.x():.3f}")
        self.y_edit.setText(f"{point.y():.3f}")

        # Auto-sample højde fra DEM
        dem = self._get_dem_layer()
        if dem:
            pt_dem = self._to_dem_crs(point, dem)
            val, ok = dem.dataProvider().sample(pt_dem, 1)
            if ok and val == val:   # not NaN
                self.height_spin.setValue(val)

        # Blå kryds ved startpunkt
        self._place_marker("start", point, QColor("blue"),
                           QgsVertexMarker.ICON_CROSS)

        self.canvas.unsetMapTool(self.map_tool)
        self.btn_run.setEnabled(True)
        self.show()
        self.raise_()

    # ----------------------------------------------------------- Processing --

    def _run(self):
        dem = self._get_dem_layer()
        if dem is None:
            QMessageBox.warning(self, "Fejl", "Vælg et DEM-lag.")
            return

        start_h = self.height_spin.value()
        slope      = self.slope_spin.value() / 1000.0   # ‰ → m/m
        max_radius = self.radius_spin.value()

        # Startpunkt i DEM's koordinatsystem
        pt_dem = self._to_dem_crs(self.start_point, dem)
        sx, sy = pt_dem.x(), pt_dem.y()

        # Læs kun DEM-udsnit inden for søgeradius
        try:
            dem_arr, px_w, px_h, x_min, y_max = self._read_dem_subset(
                dem, sx, sy, max_radius)
        except Exception as e:
            QMessageBox.critical(self, "Fejl ved læsning af DEM", str(e))
            return

        rows, cols = dem_arr.shape
        xs = x_min + (np.arange(cols) + 0.5) * px_w
        ys = y_max - (np.arange(rows) + 0.5) * px_h
        X, Y = np.meshgrid(xs, ys)

        # Afstand fra startpunkt til hvert pixel
        dist = np.sqrt((X - sx) ** 2 + (Y - sy) ** 2)

        # Drænbundskote ved hvert pixel = starthøjde minus fald gange afstand
        drain_elev = start_h - slope * dist

        # Vi søger: drain_elev = DEM + 0.10  →  diff = 0
        diff = drain_elev - (dem_arr + 0.10)

        min_d = max(px_w, px_h)           # ekskluder startpixel
        valid = (~np.isnan(dem_arr)) & (dist > min_d)

        note = ""
        # Find nærmeste pixel hvor drænet er >= 10 cm over terræn
        above = valid & (diff >= 0)
        if np.any(above):
            idx = np.unravel_index(
                np.argmin(np.where(above, dist, np.inf)), dist.shape)
        else:
            # Fallback: pixel med mindst |diff| (drænet når ikke 10 cm over DEM)
            if not np.any(valid):
                QMessageBox.warning(self, "Ingen resultat",
                                    "Ingen gyldige DEM-celler fundet.")
                return
            idx = np.unravel_index(
                np.argmin(np.where(valid, np.abs(diff), np.inf)), diff.shape)
            note = " (approx. – drænet når ikke 10 cm over terræn inden for DEM)"

        r_dist  = float(dist[idx])
        r_dem   = float(dem_arr[idx])
        r_drain = float(drain_elev[idx])
        rx_dem  = float(X[idx])
        ry_dem  = float(Y[idx])

        # Konverter resultatpunkt tilbage til kortets koordinatsystem
        rx_map, ry_map = self._from_dem_crs(QgsPointXY(rx_dem, ry_dem), dem)
        result_pt = QgsPointXY(rx_map, ry_map)

        # Rød boks ved udløbspunkt
        self._place_marker("result", result_pt, QColor("red"),
                           QgsVertexMarker.ICON_BOX)
        self.canvas.setCenter(result_pt)
        self.canvas.refresh()

        self.lbl_result.setText(
            f"<b>Udløbspunkt{note}:</b><br>"
            f"Koordinater: ({rx_map:.2f}, {ry_map:.2f})<br>"
            f"Afstand fra start: <b>{r_dist:.1f} m</b><br>"
            f"Terrænkote (DEM): {r_dem:.3f} m<br>"
            f"Drænbundskote ved udløb: {r_drain:.3f} m<br>"
            f"Frispejl over terræn: {r_drain - r_dem:.3f} m"
        )

    # ------------------------------------------------------------ Helpers --

    def _get_dem_layer(self):
        lid = self.dem_combo.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def _to_dem_crs(self, point, dem):
        map_crs = self.canvas.mapSettings().destinationCrs()
        dem_crs = dem.crs()
        if map_crs == dem_crs:
            return point
        tr = QgsCoordinateTransform(map_crs, dem_crs, QgsProject.instance())
        return tr.transform(point)

    def _from_dem_crs(self, point, dem):
        map_crs = self.canvas.mapSettings().destinationCrs()
        dem_crs = dem.crs()
        if map_crs == dem_crs:
            return point.x(), point.y()
        tr = QgsCoordinateTransform(dem_crs, map_crs, QgsProject.instance())
        p = tr.transform(point)
        return p.x(), p.y()

    def _read_dem_subset(self, dem, center_x, center_y, max_radius):
        """Read only the DEM cells within max_radius of (center_x, center_y)."""
        from qgis.core import QgsRectangle
        provider    = dem.dataProvider()
        full_extent = dem.extent()
        w_full      = dem.width()
        h_full      = dem.height()
        px_w = full_extent.width()  / w_full
        px_h = full_extent.height() / h_full

        # Clip search box to actual DEM extent
        search = QgsRectangle(
            center_x - max_radius, center_y - max_radius,
            center_x + max_radius, center_y + max_radius,
        )
        clip = full_extent.intersect(search)
        if clip.isEmpty():
            raise ValueError("Startpunktet ligger uden for DEM-laget.")

        w = max(1, int(round(clip.width()  / px_w)))
        h = max(1, int(round(clip.height() / px_h)))

        block = provider.block(1, clip, w, h)

        # QGIS DataType integer values (stable across versions):
        # 1=Byte, 2=UInt16, 3=Int16, 4=UInt32, 5=Int32, 6=Float32, 7=Float64
        dtype_map = {
            1: np.uint8,  2: np.uint16, 3: np.int16,
            4: np.uint32, 5: np.int32,  6: np.float32, 7: np.float64,
        }
        np_dtype = dtype_map.get(int(provider.dataType(1)), np.float32)

        arr = (np.frombuffer(bytes(block.data()), dtype=np_dtype)
                 .reshape(h, w)
                 .astype(np.float64))

        if provider.sourceHasNoDataValue(1):
            arr[arr == provider.sourceNoDataValue(1)] = np.nan

        return arr, px_w, px_h, clip.xMinimum(), clip.yMaximum()

    def _place_marker(self, name, point, color, icon):
        attr = f"marker_{name}"
        old = getattr(self, attr, None)
        if old:
            self.canvas.scene().removeItem(old)
        m = QgsVertexMarker(self.canvas)
        m.setCenter(point)
        m.setColor(color)
        m.setIconSize(14)
        m.setIconType(icon)
        m.setPenWidth(3)
        setattr(self, attr, m)

    def closeEvent(self, event):
        if self.map_tool:
            self.canvas.unsetMapTool(self.map_tool)
        for attr in ("marker_start", "marker_result"):
            m = getattr(self, attr, None)
            if m:
                self.canvas.scene().removeItem(m)
        super().closeEvent(event)


# ── Åbn dialogen (bring eksisterende i fokus hvis allerede åben) ──────────────
for _w in iface.mainWindow().findChildren(QDialog):
    if _w.windowTitle().startswith("Drænudløbspunkter") and _w.isVisible():
        _w.raise_()
        _w.activateWindow()
        break
else:
    _dlg = DrainDialog(iface)
    _dlg.show()
