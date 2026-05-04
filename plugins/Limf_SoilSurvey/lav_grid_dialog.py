import math
import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsPointXY, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext, QgsFeatureRequest
)

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'lav_grid_dialog.ui'))

MARKKORT_PATH = os.path.join(os.path.dirname(__file__), 'Data', 'Markkort', 'Markkort2024_simpl.shp')
MAX_ASPECT_RATIO = 3.0   # default max length/width ratio before subdivision kicks in


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

        avg_ha = self.spinAvgHa.value()
        max_ha = self.spinMaxHa.value()
        min_ha = self.spinMinHa.value()

        if min_ha > avg_ha:
            QMessageBox.warning(self, 'Fejl', 'Min størrelse må ikke være større end gennemsnitsstørrelse.')
            return
        if avg_ha > max_ha:
            QMessageBox.warning(self, 'Fejl', 'Gennemsnitsstørrelse må ikke være større end max størrelse.')
            return

        work_crs = QgsCoordinateReferenceSystem('EPSG:25832')
        src_crs = grense_layer.crs()
        to_work = QgsCoordinateTransform(src_crs, work_crs, QgsCoordinateTransformContext())
        need_transform = src_crs != work_crs

        union_geom = None
        for feat in grense_layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isNull() or geom.isEmpty():
                continue
            if need_transform:
                geom.transform(to_work)
            union_geom = QgsGeometry(geom) if union_geom is None else union_geom.combine(geom)

        if not union_geom or union_geom.isNull() or union_geom.isEmpty():
            QMessageBox.warning(self, 'Fejl', 'Laget indeholder ingen geometri.')
            return

        parcels = self._load_markkort_parcels(union_geom, work_crs)
        parcels = self._subdivide_large(parcels, avg_ha, max_ha, min_ha)
        parcels = self._merge_small(parcels, min_ha)

        grid_layer = QgsVectorLayer('Polygon?crs=EPSG:25832', 'Grid', 'memory')
        provider = grid_layer.dataProvider()
        provider.addAttributes([
            QgsField('id', QVariant.Int),
            QgsField('areal_ha', QVariant.Double),
        ])
        grid_layer.updateFields()

        features = []
        fid = 1
        for geom in parcels:
            for part in self._single_parts(geom):
                if part.area() < 1:
                    continue
                feat = QgsFeature()
                feat.setGeometry(part)
                feat.setAttributes([fid, round(part.area() / 10000, 4)])
                features.append(feat)
                fid += 1

        if not features:
            QMessageBox.warning(self, 'Fejl', 'Ingen felter blev oprettet.')
            return

        provider.addFeatures(features)
        grid_layer.updateExtents()
        QgsProject.instance().addMapLayer(grid_layer)

        total_ha = sum(f.geometry().area() / 10000 for f in features)
        avg_result = total_ha / len(features)
        QMessageBox.information(
            self, 'Grid oprettet',
            f'Grid oprettet med {len(features)} felter.\n'
            f'Gennemsnitsstørrelse: {avg_result:.2f} ha\n'
            f'Total areal: {total_ha:.1f} ha'
        )
        self.accept()

    def _load_markkort_parcels(self, union_geom, work_crs):
        """Clip Markkort2024 to union_geom, dissolve overlapping/duplicate features
        via unaryUnion (no buffer — avoids pill-shaped artefacts), and return
        a list of individual polygon QgsGeometry objects."""
        if not os.path.exists(MARKKORT_PATH):
            return [QgsGeometry(union_geom)]

        mk_layer = QgsVectorLayer(MARKKORT_PATH, 'mk', 'ogr')
        if not mk_layer.isValid():
            return [QgsGeometry(union_geom)]

        mk_crs = mk_layer.crs()
        to_work = QgsCoordinateTransform(mk_crs, work_crs, QgsCoordinateTransformContext())
        from_work = QgsCoordinateTransform(work_crs, mk_crs, QgsCoordinateTransformContext())
        need_transform = mk_crs != work_crs

        if need_transform:
            filter_geom = QgsGeometry.fromRect(union_geom.boundingBox())
            filter_geom.transform(from_work)
            filter_rect = filter_geom.boundingBox()
        else:
            filter_rect = union_geom.boundingBox()

        raw_geoms = []
        for feat in mk_layer.getFeatures(QgsFeatureRequest().setFilterRect(filter_rect)):
            geom = feat.geometry()
            if not geom or geom.isNull() or geom.isEmpty():
                continue
            if need_transform:
                geom.transform(to_work)
            if not geom.isGeosValid():
                geom = geom.makeValid()
            clipped = geom.intersection(union_geom)
            if not clipped or clipped.isNull() or clipped.isEmpty():
                continue
            clipped = self._extract_polygons(clipped)
            if clipped is None or clipped.area() < 1:
                continue
            raw_geoms.append(clipped)

        if not raw_geoms:
            return [QgsGeometry(union_geom)]

        # Dissolve overlapping / duplicate features cleanly (no buffer → no artefacts)
        dissolved = QgsGeometry.unaryUnion(raw_geoms)
        if not dissolved or dissolved.isNull() or dissolved.isEmpty():
            return [QgsGeometry(union_geom)]

        # Split dissolved result into individual single-polygon parcels,
        # clipped to union_geom.  We iterate over every polygon part so that
        # concave Markkort features never produce disconnected MultiPolygons.
        parcels = []
        mk_coverage = None
        for part in dissolved.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) != QgsWkbTypes.PolygonGeometry:
                continue
            cb = part.intersection(union_geom)
            if not cb or cb.isNull() or cb.isEmpty():
                continue
            for sub in self._single_parts(cb):
                if sub.area() < 100:
                    continue
                parcels.append(sub)
                mk_coverage = (QgsGeometry(sub) if mk_coverage is None
                               else mk_coverage.combine(sub))

        # Include any area inside union_geom not covered by the dissolved Markkort
        if mk_coverage is not None:
            uncovered = union_geom.difference(mk_coverage)
            if uncovered and not uncovered.isNull() and not uncovered.isEmpty():
                for part in uncovered.asGeometryCollection():
                    if (QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry
                            and part.area() > 100):
                        parcels.append(part)

        return parcels if parcels else [QgsGeometry(union_geom)]

    def _single_parts(self, geom):
        """Yield each individual Polygon part from any geometry (drops non-polygon parts)."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return
        for part in geom.asGeometryCollection():
            if QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry:
                yield part

    def _extract_polygons(self, geom):
        """Return polygon-only geometry (drops lines/points from mixed collections)."""
        if geom is None or geom.isNull() or geom.isEmpty():
            return None
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry:
            return geom
        poly_parts = list(self._single_parts(geom))
        if not poly_parts:
            return None
        result = poly_parts[0]
        for p in poly_parts[1:]:
            result = result.combine(p)
        return result

    def _get_mbr_params(self, geom):
        """Compute minimum bounding rectangle (rotating calipers on convex hull).
        Returns (cx, cy, angle_rad, length, width) where length >= width and
        angle_rad is the direction of the long axis (east = 0, north = π/2)."""
        hull = geom.convexHull()
        poly = hull.asPolygon()
        if not poly or not poly[0] or len(poly[0]) < 3:
            return self._bbox_params(geom)

        pts = poly[0]
        n = len(pts) - 1  # last point == first
        best_area = float('inf')
        best = None

        for i in range(n):
            dx = pts[(i + 1) % n].x() - pts[i].x()
            dy = pts[(i + 1) % n].y() - pts[i].y()
            edge = math.hypot(dx, dy)
            if edge < 1e-10:
                continue
            ux, uy = dx / edge, dy / edge   # along edge (candidate long axis)
            vx, vy = -uy, ux               # perpendicular

            u_vals = [ux * p.x() + uy * p.y() for p in pts[:n]]
            v_vals = [vx * p.x() + vy * p.y() for p in pts[:n]]
            u0, u1 = min(u_vals), max(u_vals)
            v0, v1 = min(v_vals), max(v_vals)
            rw, rh = u1 - u0, v1 - v0
            area = rw * rh

            if area < best_area:
                best_area = area
                uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
                cx = ux * uc + vx * vc
                cy = uy * uc + vy * vc
                if rw >= rh:
                    best = (cx, cy, math.atan2(uy, ux), rw, rh)
                else:
                    best = (cx, cy, math.atan2(vy, vx), rh, rw)

        return best if best else self._bbox_params(geom)

    def _bbox_params(self, geom):
        bb = geom.boundingBox()
        cx = (bb.xMinimum() + bb.xMaximum()) / 2
        cy = (bb.yMinimum() + bb.yMaximum()) / 2
        w, h = bb.width(), bb.height()
        if w >= h:
            return cx, cy, 0.0, w, h
        return cx, cy, math.pi / 2, h, w

    def _subdivide_large(self, parcels, avg_ha, max_ha, min_ha):
        result = []
        for geom in parcels:
            area_ha = geom.area() / 10000
            if area_ha > max_ha:
                result.extend(self._subdivide(geom, avg_ha, max_ha, min_ha))
            else:
                # Also subdivide if aspect ratio is bad AND pieces would stay >= min_ha.
                # If not possible without going below min_ha, accept the exception.
                _, _, _, length, width = self._get_mbr_params(geom)
                ratio = (length / width) if width > 1 else 1.0
                if ratio > MAX_ASPECT_RATIO and area_ha / 2 >= min_ha:
                    result.extend(self._subdivide(geom, avg_ha, max_ha, min_ha))
                else:
                    result.append(geom)
        return result

    def _subdivide(self, geom, avg_ha, max_ha, min_ha, depth=0):
        """Split polygon into strips aligned with the polygon's own long axis
        (minimum bounding rectangle).  n is chosen to satisfy both the size
        target (avg_ha) and the aspect-ratio limit (MAX_ASPECT_RATIO), but
        is capped so individual strips do not fall below min_ha."""
        if depth > 10:
            return [geom]

        area_ha = geom.area() / 10000
        cx, cy, angle, length, width = self._get_mbr_params(geom)

        # Use sqrt so fewer strips are cut per pass; SIZE recursion then cuts
        # perpendicular, producing roughly-square cells without a 2-D grid.
        n_size = max(2, round(math.sqrt(area_ha / avg_ha)))
        # Strips needed so each strip's aspect ratio stays ≤ MAX_ASPECT_RATIO
        n_ratio = math.ceil(length / (MAX_ASPECT_RATIO * width)) if width > 1 else 1
        n = max(n_size, n_ratio)
        # Cap n so pieces don't go below min_ha (exceptions allowed per user requirement)
        if min_ha > 0:
            n = min(n, max(2, int(area_ha / min_ha)))

        cos_a, sin_a = math.cos(angle), math.sin(angle)
        strip_len = length / n

        bb = geom.boundingBox()
        margin = math.hypot(bb.width(), bb.height())

        def make_pt(u, v):
            return QgsPointXY(cx + u * cos_a - v * sin_a,
                              cy + u * sin_a + v * cos_a)

        pieces = []
        for i in range(n):
            u0 = -length / 2 + i * strip_len
            u1 = u0 + strip_len
            strip = QgsGeometry.fromPolygonXY([[
                make_pt(u0, -margin), make_pt(u1, -margin),
                make_pt(u1,  margin), make_pt(u0,  margin),
                make_pt(u0, -margin),
            ]])
            clipped = geom.intersection(strip)
            if not clipped or clipped.isNull() or clipped.isEmpty():
                continue
            for part in self._single_parts(clipped):
                if part.area() > 1:
                    pieces.append(part)

        result = []
        for piece in pieces:
            if piece.area() / 10000 > max_ha:
                # Only recurse if piece is roughly convex — cutting a concave piece
                # through its complex boundary creates GEOS sliver artefacts.
                hull = piece.convexHull()
                convex_ratio = (piece.area() / hull.area()
                                if hull and not hull.isNull() and hull.area() > 0
                                else 1.0)
                if convex_ratio >= 0.85:
                    result.extend(self._subdivide(piece, avg_ha, max_ha, min_ha, depth + 1))
                else:
                    result.append(piece)
            else:
                result.append(piece)
        return result if result else [geom]

    def _merge_small(self, parcels, min_ha):
        """Iteratively merge parcels below min_ha with the neighbour sharing
        the longest common boundary.  A merge is only accepted when the result
        is a single connected polygon — this prevents disconnected MultiPolygons."""
        min_area = min_ha * 10000
        parcels = list(parcels)

        changed = True
        while changed:
            changed = False
            for i in range(len(parcels)):
                if parcels[i].area() >= min_area:
                    continue

                # Build scored candidate list (require a real shared edge, not just a point)
                candidates = []
                for j in range(len(parcels)):
                    if i == j:
                        continue
                    try:
                        if not parcels[i].intersects(parcels[j]):
                            continue
                        shared = parcels[i].intersection(parcels[j])
                        if shared is None or shared.isNull() or shared.isEmpty():
                            continue
                        score = shared.length() + shared.area()
                        if score > 0.1:   # discard corner-only touches (score ≈ 0)
                            candidates.append((score, j))
                    except Exception:
                        continue

                # Try candidates best-first; accept only if merge stays connected
                for _, j in sorted(candidates, reverse=True):
                    merged = parcels[i].combine(parcels[j])
                    poly_parts = list(self._single_parts(merged))
                    if len(poly_parts) == 1:
                        parcels = [merged if k == j else p
                                   for k, p in enumerate(parcels) if k != i]
                        changed = True
                        break

                if changed:
                    break   # restart outer loop from beginning

        return parcels
