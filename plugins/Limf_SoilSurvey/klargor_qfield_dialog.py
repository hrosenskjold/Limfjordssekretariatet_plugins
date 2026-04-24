import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtWidgets import QMessageBox, QTableWidgetItem, QHeaderView
from qgis.core import (
    QgsProject, QgsField, QgsWkbTypes,
    QgsEditorWidgetSetup, QgsDefaultValue,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsFillSymbol,
    QgsMarkerSymbol,
    QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsVectorFileWriter, QgsCoordinateTransformContext,
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'klargor_qfield_dialog.ui'))


def _to_single_polygon_2d(geom):
    """Udtag den arealmæssigt største del og returnér simpel 2D-Polygon.

    Håndterer MultiPolygon, Z- og M-koordinater som alle er ukompatible
    med QField når lagets geometry type er Polygon.
    """
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    flat = QgsWkbTypes.flatType(geom.wkbType())
    if flat == QgsWkbTypes.Polygon:
        result = geom
    elif flat == QgsWkbTypes.MultiPolygon:
        parts = geom.asGeometryCollection()
        poly_parts = [p for p in parts if not p.isNull() and not p.isEmpty() and p.area() > 0]
        if not poly_parts:
            return None
        result = max(poly_parts, key=lambda p: p.area())
    else:
        return None
    if QgsWkbTypes.hasZ(result.wkbType()) or QgsWkbTypes.hasM(result.wkbType()):
        g = result.get().clone()
        g.dropZValue()
        g.dropMValue()
        result = QgsGeometry(g)
    return result


def _konverter_til_simpel_polygon(src_layer, out_path):
    """Gem src_layer som simpel 2D-Polygon GeoPackage på out_path.

    Returnerer (out_path, None) ved succes, (None, fejlbesked) ved fejl.
    """
    crs = src_layer.crs()
    mem = QgsVectorLayer(f'Polygon?crs={crs.authid()}', 'tmp', 'memory')
    mem.dataProvider().addAttributes(src_layer.fields().toList())
    mem.updateFields()

    feats = []
    for feat in src_layer.getFeatures():
        g = _to_single_polygon_2d(feat.geometry())
        if g is None:
            continue
        nf = QgsFeature(mem.fields())
        nf.setGeometry(g)
        nf.setAttributes(feat.attributes())
        feats.append(nf)

    if not feats:
        return None, 'Ingen konverterbare features fundet i laget.'

    mem.dataProvider().addFeatures(feats)

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.fileEncoding = 'UTF-8'
    err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, out_path, QgsCoordinateTransformContext(), opts
    )
    if err != QgsVectorFileWriter.NoError:
        return None, msg
    return out_path, None

STATUS_FIELD = 'status'
VAL_IKKE = 'Ikke-udtaget'
VAL_UDTAGET = 'Udtaget'

JORDTYPE_VALG = [
    'Groft og fint grus',
    'Grovkornet sand',
    'Uomsat tørv',
    'Mellemkornet sand',
    'Mellemkornet sand med indslag af omsat tørv',
    'Finkornet sand',
    'Moderat omsat tørv',
    'Gytjeholdig sand',
    'Stærkt omsat tørv',
    'Silt',
    'Ler',
    'Kalkgytje',
    'Fuldstændig omsat tørv',
]

# (feltnavn, type, alias, widget-type, widget-config)
STANDARD_FIELDS = [
    ('status',      QVariant.String,  'Status',          'ValueMap',        {'map': [{VAL_IKKE: VAL_IKKE}, {VAL_UDTAGET: VAL_UDTAGET}]}),
    ('Vol.lgd',     QVariant.Double,  'Volumen lgd (cm)',     'TextEdit',        {}),
    ('Udtaget',     QVariant.String,  'Udtaget',              'TextEdit',        {}),
    ('Tørv. Ty.',   QVariant.Double,  'Tørvetykkelse (cm)',   'TextEdit',        {}),
    ('VSP',         QVariant.Double,  'Vandspejl (cm)',       'TextEdit',        {}),
    ('Foto',        QVariant.String,  'Foto af prøve',   'ExternalResource', {'StorageType': '0', 'DocumentViewer': '0', 'FileWidget': '1', 'FileWidgetButton': '1'}),
    ('lag 1',       QVariant.String,  'Lag 1 (cm)',      'TextEdit',        {}),
    ('lag 1 type',  QVariant.String,  'Lag 1 jordtype',  'ValueMap',        {'map': [{v: v} for v in JORDTYPE_VALG]}),
    ('lag 2',       QVariant.String,  'Lag 2 (cm)',      'TextEdit',        {}),
    ('lag 2 type',  QVariant.String,  'Lag 2 jordtype',  'ValueMap',        {'map': [{v: v} for v in JORDTYPE_VALG]}),
    ('lag 3',       QVariant.String,  'Lag 3 (cm)',      'TextEdit',        {}),
    ('lag 3 type',  QVariant.String,  'Lag 3 jordtype',  'ValueMap',        {'map': [{v: v} for v in JORDTYPE_VALG]}),
    ('lag 4',       QVariant.String,  'Lag 4 (cm)',      'TextEdit',        {}),
    ('lag 4 type',  QVariant.String,  'Lag 4 jordtype',  'ValueMap',        {'map': [{v: v} for v in JORDTYPE_VALG]}),
    ('comment',     QVariant.String,  'Kommentar',       'TextEdit',        {}),
]

STANDARD_NAMES = {f[0] for f in STANDARD_FIELDS}


class KlargorQFieldDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self._populate_lag()
        self.cboLag.currentIndexChanged.connect(self._load_fields)
        self.btnKor.clicked.connect(self.kor)

        # Kolonnebredder
        hdr = self.tblFelter.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        if self.cboLag.count():
            self._load_fields()

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() in (QgsWkbTypes.PolygonGeometry, QgsWkbTypes.PointGeometry):
                    self.cboLag.addItem(layer.name(), layer.id())

    def _current_layer(self):
        layer_id = self.cboLag.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def _load_fields(self):
        layer = self._current_layer()
        if not layer:
            self.tblFelter.setRowCount(0)
            return

        # Tilføj manglende standardfelter til laget
        existing = [f.name() for f in layer.fields()]
        new_fields = []
        for name, vtype, alias, _, _ in STANDARD_FIELDS:
            if name not in existing:
                f = QgsField(name, vtype)
                f.setAlias(alias)
                new_fields.append(f)
        if new_fields:
            layer.startEditing()
            for f in new_fields:
                layer.addAttribute(f)
            layer.commitChanges()

        # Byg tabellen
        form_cfg = layer.editFormConfig()
        fields = layer.fields()
        self.tblFelter.setRowCount(fields.count())

        for row in range(fields.count()):
            field = fields.field(row)
            name = field.name()
            alias = field.alias() or name
            is_standard = name in STANDARD_NAMES

            # Feltnavn (ikke redigerbar)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(Qt.ItemIsEnabled)
            if is_standard:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            self.tblFelter.setItem(row, 0, name_item)

            # Alias (redigerbar)
            alias_item = QTableWidgetItem(alias)
            self.tblFelter.setItem(row, 1, alias_item)

            # Medtages: felt er ikke skjult
            included = layer.editorWidgetSetup(row).type() != 'Hidden'
            chk_med = QTableWidgetItem()
            chk_med.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chk_med.setCheckState(Qt.Checked if included else Qt.Unchecked)
            chk_med.setTextAlignment(Qt.AlignCenter)
            self.tblFelter.setItem(row, 2, chk_med)

            # Redigerbar: felt er ikke read-only
            editable = not form_cfg.readOnly(row)
            chk_red = QTableWidgetItem()
            chk_red.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            chk_red.setCheckState(Qt.Checked if editable else Qt.Unchecked)
            chk_red.setTextAlignment(Qt.AlignCenter)
            self.tblFelter.setItem(row, 3, chk_red)

    def kor(self):
        layer = self._current_layer()
        if not layer:
            QMessageBox.warning(self, 'Fejl', 'Vælg et lag.')
            return

        # Geometritype-check: MultiPolygon og/eller Z-koordinater er ikke kompatible med QField
        wkb = layer.wkbType()
        if QgsWkbTypes.isMultiType(wkb) or QgsWkbTypes.hasZ(wkb) or QgsWkbTypes.hasM(wkb):
            wkb_name = QgsWkbTypes.displayString(wkb)
            reply = QMessageBox.question(
                self, 'Geometrikonvertering nødvendig',
                f'Laget "{layer.name()}" har geometritype {wkb_name}.\n\n'
                f'QField kræver simpel 2D-Polygon. Vil du konvertere og erstatte laget i projektet?\n\n'
                f'(En ny GeoPackage-fil oprettes med suffikset _2D, og laget swappes i projektet.)',
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

            src_uri = layer.dataProvider().dataSourceUri().split('|')[0]
            base, ext = os.path.splitext(src_uri)
            out_path = base + '_2D' + (ext if ext.lower() == '.gpkg' else '.gpkg')

            out_path, err = _konverter_til_simpel_polygon(layer, out_path)
            if err:
                QMessageBox.warning(self, 'Konvertering fejlede', err)
                return

            layer_name = layer.name()
            QgsProject.instance().removeMapLayer(layer.id())

            new_layer = QgsVectorLayer(out_path, layer_name, 'ogr')
            if not new_layer.isValid():
                QMessageBox.warning(self, 'Fejl', f'Den konverterede fil er ugyldig:\n{out_path}')
                return

            QgsProject.instance().addMapLayer(new_layer)
            self._populate_lag()
            idx = self.cboLag.findData(new_layer.id())
            if idx >= 0:
                self.cboLag.setCurrentIndex(idx)
            self._load_fields()

            QMessageBox.information(
                self, 'Konverteret',
                f'Laget er nu simpel 2D-Polygon og lagt ind i projektet:\n{out_path}\n\n'
                f'Klik "Kør" igen for at gennemføre QField-klargøringen.'
            )
            return

        form_cfg = layer.editFormConfig()
        fields = layer.fields()

        layer.startEditing()

        for row in range(self.tblFelter.rowCount()):
            field_name = self.tblFelter.item(row, 0).text()
            new_alias  = self.tblFelter.item(row, 1).text().strip()
            included   = self.tblFelter.item(row, 2).checkState() == Qt.Checked
            editable   = self.tblFelter.item(row, 3).checkState() == Qt.Checked

            idx = fields.indexOf(field_name)
            if idx < 0:
                continue

            # Alias
            layer.setFieldAlias(idx, new_alias)

            # Medtages / skjult
            if not included:
                layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup('Hidden', {}))
            else:
                # Gendan standard widget-opsætning for kendte felter
                std = next((s for s in STANDARD_FIELDS if s[0] == field_name), None)
                if std:
                    layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup(std[3], std[4]))
                else:
                    # Eksisterende felt: behold widget, men gør synligt
                    if layer.editorWidgetSetup(idx).type() == 'Hidden':
                        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup('TextEdit', {}))

            # Redigerbar
            form_cfg.setReadOnly(idx, not editable)

        # Default + udfyld status på nye features
        status_idx = fields.indexOf(STATUS_FIELD)
        if status_idx >= 0:
            layer.setDefaultValueDefinition(status_idx, QgsDefaultValue(f"'{VAL_IKKE}'"))
            for feat in layer.getFeatures():
                if not feat[STATUS_FIELD]:
                    layer.changeAttributeValue(feat.id(), status_idx, VAL_IKKE)

        layer.setEditFormConfig(form_cfg)
        layer.commitChanges()

        # Farverenderer
        self._apply_renderer(layer)
        layer.triggerRepaint()
        QgsProject.instance().setDirty(True)

        QMessageBox.information(self, 'Klargøring færdig',
                                f'"{layer.name()}" er nu klar til QField.')
        self.accept()

    def _apply_renderer(self, layer):
        geom_type = layer.geometryType()
        if geom_type == QgsWkbTypes.PolygonGeometry:
            sym_ikke    = QgsFillSymbol.createSimple({'color': '#e74c3c', 'outline_color': '#922b21', 'outline_width': '0.4'})
            sym_udtaget = QgsFillSymbol.createSimple({'color': '#2ecc71', 'outline_color': '#1a7a43', 'outline_width': '0.4'})
        else:
            sym_ikke    = QgsMarkerSymbol.createSimple({'color': '#e74c3c', 'outline_color': '#922b21', 'size': '3'})
            sym_udtaget = QgsMarkerSymbol.createSimple({'color': '#2ecc71', 'outline_color': '#1a7a43', 'size': '3'})

        categories = [
            QgsRendererCategory(VAL_IKKE,    sym_ikke,    VAL_IKKE),
            QgsRendererCategory(VAL_UDTAGET, sym_udtaget, VAL_UDTAGET),
        ]
        layer.setRenderer(QgsCategorizedSymbolRenderer(STATUS_FIELD, categories))
