import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsRectangle, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_grid_dialog.ui'))


class LavGridDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self._populate_lag()
        self.btnKorGrid.clicked.connect(self.kor_grid)

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    self.cboLag.addItem(layer.name(), layer.id())

    def kor_grid(self):
        layer_id = self.cboLag.currentData()
        if not layer_id:
            QMessageBox.warning(self, 'Fejl', 'Vælg et polygonlag.')
            return

        grense_layer = QgsProject.instance().mapLayer(layer_id)
        if not grense_layer:
            QMessageBox.warning(self, 'Fejl', 'Laget kunne ikke findes.')
            return

        bredde = self.spinBredde.value()
        laengde = self.spinLaengde.value()

        # Arbejds-CRS: altid i meter (EPSG:25832 for Danmark)
        work_crs = QgsCoordinateReferenceSystem('EPSG:25832')
        src_crs = grense_layer.crs()
        transform = QgsCoordinateTransform(src_crs, work_crs, QgsCoordinateTransformContext())
        need_transform = src_crs != work_crs

        # Saml alle features til én union-geometri i arbejds-CRS
        union_geom = None
        for feat in grense_layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            if need_transform:
                geom.transform(transform)
            if union_geom is None:
                union_geom = QgsGeometry(geom)
            else:
                union_geom = union_geom.combine(geom)

        if union_geom is None or union_geom.isNull() or union_geom.isEmpty():
            QMessageBox.warning(self, 'Fejl', 'Laget indeholder ingen geometri.')
            return

        extent = union_geom.boundingBox()
        x_min = extent.xMinimum()
        y_min = extent.yMinimum()
        x_max = extent.xMaximum()
        y_max = extent.yMaximum()
        bbox_w = x_max - x_min
        bbox_h = y_max - y_min

        # Opret output-lag i arbejds-CRS
        grid_layer = QgsVectorLayer(f'Polygon?crs=EPSG:25832', 'Grid', 'memory')
        provider = grid_layer.dataProvider()
        provider.addAttributes([
            QgsField('id', QVariant.Int),
            QgsField('col', QVariant.Int),
            QgsField('row', QVariant.Int),
        ])
        grid_layer.updateFields()

        features = []
        fid = 1
        row = 0
        y = y_min
        while y < y_max:
            col = 0
            x = x_min
            while x < x_max:
                rect_geom = QgsGeometry.fromRect(QgsRectangle(x, y, x + bredde, y + laengde))
                clipped = rect_geom.intersection(union_geom)
                if not clipped.isNull() and not clipped.isEmpty() and clipped.area() > 0:
                    feat = QgsFeature()
                    feat.setGeometry(clipped)
                    feat.setAttributes([fid, col, row])
                    features.append(feat)
                    fid += 1
                x += bredde
                col += 1
            y += laengde
            row += 1

        if not features:
            QMessageBox.warning(
                self, 'Fejl',
                f'Ingen felter blev oprettet.\n'
                f'Polygnets udstrækning: {bbox_w:.1f} × {bbox_h:.1f} m\n'
                f'Celle-størrelse: {bredde} × {laengde} m'
            )
            return

        provider.addFeatures(features)
        grid_layer.updateExtents()
        QgsProject.instance().addMapLayer(grid_layer)

        QMessageBox.information(
            self, 'Grid oprettet',
            f'Grid oprettet med {len(features)} felter ({bredde} × {laengde} m).\n'
            f'Polygnets udstrækning: {bbox_w:.1f} × {bbox_h:.1f} m.'
        )
        self.accept()
