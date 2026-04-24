import os
import csv
import base64
import datetime
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog

from qgis.core import (
    QgsProject, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext,
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'eksporter_rapport_dialog.ui'))

WGS84_CRS = QgsCoordinateReferenceSystem('EPSG:4326')


def _val(feat, name):
    try:
        v = feat[name]
        return str(v) if v is not None and str(v) not in ('NULL', 'None', '') else ''
    except Exception:
        return ''


def _resolve_path(path):
    """Løs relativ sti op mod projektmappen og dens 'files'-undermappe."""
    if not path:
        return None
    path = str(path)
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    # Brug projektmappen som rod
    project_path = QgsProject.instance().absolutePath()
    if project_path:
        candidates = [
            os.path.join(project_path, path),
            os.path.join(project_path, '..', path),
        ]
        for candidate in candidates:
            resolved = os.path.normpath(candidate)
            if os.path.isfile(resolved):
                return resolved
    return None


def _img_tag(path):
    resolved = _resolve_path(path)
    if not resolved:
        return '<span style="color:#bbb;font-style:italic;">Intet foto</span>'
    try:
        with open(resolved, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(resolved)[1].lower().lstrip('.')
        mime = 'jpeg' if ext in ('jpg', 'jpeg') else ext
        return f'<img src="data:image/{mime};base64,{data}" style="max-width:100%;max-height:180px;">'
    except Exception:
        return '<span style="color:#bbb;font-style:italic;">Foto fejlede</span>'


def _build_html(layer):
    src_crs = layer.crs()
    transform = QgsCoordinateTransform(src_crs, WGS84_CRS, QgsCoordinateTransformContext())
    need_transform = src_crs != WGS84_CRS
    is_polygon = layer.geometryType() == QgsWkbTypes.PolygonGeometry

    feature_rows = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue

        pt = geom.centroid().asPoint() if is_polygon else geom.asPoint()
        if need_transform:
            pt = transform.transform(pt)
        koordinat = f'{pt.y():.6f}° N, {pt.x():.6f}° Ø (WGS84)'

        feat_id = _val(feat, 'ID') or str(feat.id())
        status = _val(feat, 'status')
        status_color = '#27AE60' if status == 'Udtaget' else '#C0392B'

        # Lagbeskrivelser
        lag_rows = ''
        for i in range(1, 5):
            dybde = _val(feat, f'lag {i}')
            jordtype = _val(feat, f'lag {i} type')
            if dybde or jordtype:
                lag_rows += f'''
                <tr>
                  <td style="white-space:nowrap;padding-right:14px;vertical-align:top;
                             color:#555;font-size:9pt;">{dybde + ' cm' if dybde else ''}</td>
                  <td style="vertical-align:top;font-size:9pt;">{jordtype}</td>
                </tr>'''

        comment = _val(feat, 'comment')
        if comment:
            lag_rows += f'''
            <tr>
              <td colspan="2" style="padding-top:6px;font-style:italic;
                                     color:#666;font-size:8.5pt;">{comment}</td>
            </tr>'''

        if not lag_rows:
            lag_rows = '<tr><td style="color:#bbb;font-style:italic;">Ingen lagdata</td></tr>'

        # Ekstrafelter
        ekstra = ''
        for felt, alias in [('Vol.lgd', 'Vol. lgd.'), ('Tørv. Ty.', 'Tørvetykkelse'),
                             ('Perm.', 'Permeabilitet'), ('VSP', 'Vandspejl')]:
            v = _val(feat, felt)
            if v:
                ekstra += f'<div style="font-size:8pt;margin-top:2px;"><b>{alias}:</b> {v}</div>'

        foto_html = _img_tag(_val(feat, 'Foto'))

        feature_rows.append(f'''
        <tr>
          <td style="width:20%;vertical-align:top;padding:8px;border:1px solid #ccc;background:#f7f7f7;">
            <div style="font-size:11pt;font-weight:bold;margin-bottom:3px;">Område {feat_id}</div>
            <div style="font-size:8pt;color:#666;margin-bottom:6px;">{koordinat}</div>
            <div style="font-size:8.5pt;font-weight:bold;color:{status_color};">{status or 'Ukendt'}</div>
            {ekstra}
          </td>
          <td style="width:42%;vertical-align:top;padding:8px;border:1px solid #ccc;">
            <table style="border-collapse:collapse;width:100%;">{lag_rows}</table>
          </td>
          <td style="width:38%;vertical-align:top;padding:6px;border:1px solid #ccc;text-align:center;">
            {foto_html}
          </td>
        </tr>''')

    if not feature_rows:
        return None

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  /* A4 portrait: 210 x 297 mm, 10 mm margin = 190 x 277 mm indhold */
  @page {{
    size: A4 portrait;
    margin: 10mm;
  }}
  * {{
    box-sizing: border-box;
  }}
  body {{
    font-family: Arial, sans-serif;
    font-size: 9pt;
    color: #222;
    margin: 0;
    /* Skærmvisning: simulér A4-bredde */
    width: 190mm;
    margin-left: auto;
    margin-right: auto;
    padding: 10mm;
  }}
  h1        {{ font-size: 12pt; margin: 0 0 2px 0; }}
  p.meta    {{ font-size: 7.5pt; color: #888; margin: 0 0 8px 0; }}
  table.rap {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  table.rap tr {{ page-break-inside: avoid; }}
  table.rap td {{ overflow: hidden; word-wrap: break-word; }}
  /* Kolonnebredder i procent af 277 mm */
  col.c1 {{ width: 20%; }}
  col.c2 {{ width: 42%; }}
  col.c3 {{ width: 38%; }}
  img {{ max-width: 100%; max-height: 160px; display: block; margin: 0 auto; }}
  @media print {{
    body {{ width: 100%; padding: 0; margin: 0; }}
  }}
</style>
</head>
<body>
  <h1>Jordprøverapport &mdash; {layer.name()}</h1>
  <p class="meta">Genereret {datetime.date.today().strftime("%d.%m.%Y")} af Jordprøver-plugin</p>
  <table class="rap">
    <colgroup><col class="c1"><col class="c2"><col class="c3"></colgroup>
    {''.join(feature_rows)}
  </table>
</body>
</html>'''


CSV_HEADERS = [
    'ID', 'Status', 'Koordinat (WGS84)',
    'Vol. lgd. (cm)', 'Tørvetykkelse (cm)', 'Vandspejl (cm)',
    'Lag 1 (cm)', 'Lag 1 jordtype',
    'Lag 2 (cm)', 'Lag 2 jordtype',
    'Lag 3 (cm)', 'Lag 3 jordtype',
    'Lag 4 (cm)', 'Lag 4 jordtype',
    'Kommentar',
]


def _build_csv_rows(layer):
    src_crs = layer.crs()
    transform = QgsCoordinateTransform(src_crs, WGS84_CRS, QgsCoordinateTransformContext())
    need_transform = src_crs != WGS84_CRS
    is_polygon = layer.geometryType() == QgsWkbTypes.PolygonGeometry

    rows = []
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue

        pt = geom.centroid().asPoint() if is_polygon else geom.asPoint()
        if need_transform:
            pt = transform.transform(pt)
        koordinat = f'{pt.y():.6f}N {pt.x():.6f}E'

        feat_id = _val(feat, 'ID') or str(feat.id())
        lag_data = []
        for i in range(1, 5):
            lag_data.append(_val(feat, f'lag {i}'))
            lag_data.append(_val(feat, f'lag {i} type'))

        rows.append([
            feat_id,
            _val(feat, 'status'),
            koordinat,
            _val(feat, 'Vol.lgd'),
            _val(feat, 'Tørv. Ty.'),
            _val(feat, 'VSP'),
            *lag_data,
            _val(feat, 'comment'),
        ])
    return rows


def _write_csv(layer, csv_path):
    rows = _build_csv_rows(layer)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)
    return len(rows)


class EksporterRapportDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self._populate_lag()
        self.btnBrowse.clicked.connect(self._browse)
        self.btnKor.clicked.connect(self.kor)

    def _populate_lag(self):
        self.cboLag.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == layer.VectorLayer:
                if layer.geometryType() in (QgsWkbTypes.PolygonGeometry, QgsWkbTypes.PointGeometry):
                    self.cboLag.addItem(layer.name(), layer.id())

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Gem rapport', '', 'HTML-fil (*.html)'
        )
        if path:
            if not path.lower().endswith('.html'):
                path += '.html'
            self.txtUddata.setText(path)

    def kor(self):
        layer_id = self.cboLag.currentData()
        html_path = self.txtUddata.text().strip()

        if not layer_id:
            QMessageBox.warning(self, 'Fejl', 'Vælg et lag.')
            return
        if not html_path:
            QMessageBox.warning(self, 'Fejl', 'Vælg en placering til rapporten.')
            return

        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            QMessageBox.warning(self, 'Fejl', 'Laget kunne ikke findes.')
            return

        html = _build_html(layer)
        if html is None:
            QMessageBox.warning(self, 'Fejl', 'Ingen features med geometri fundet i laget.')
            return

        if not html_path.lower().endswith('.html'):
            html_path += '.html'
        csv_path = html_path[:-5] + '.csv'

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

        n_rows = _write_csv(layer, csv_path)

        QMessageBox.information(
            self, 'Rapport eksporteret',
            f'HTML-rapport gemt:\n{html_path}\n\n'
            f'CSV-tabel gemt ({n_rows} rækker):\n{csv_path}'
        )
        self.accept()
