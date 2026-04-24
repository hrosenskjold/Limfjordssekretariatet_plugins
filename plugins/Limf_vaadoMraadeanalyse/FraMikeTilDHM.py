# -*- coding: utf-8 -*-
"""
Fra MIKE til DHM – QGIS Processing-algoritme
"""

import bisect
import os
import math
import re
import tempfile
import traceback

import numpy as np
from osgeo import gdal

from PyQt5.QtCore import QVariant

from qgis.core import (
    QgsPoint,
    QgsFeature,
    QgsGeometry,
    QgsFields,
    QgsField,
    QgsVectorLayer,
    QgsProject,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFile,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
)
from qgis import processing


# ---------------- Hjælpefunktion: lineær interpolation af profil til N punkter ----------------
def interpolate_profile(raw_pts, n=50):
    raw_pts = sorted(raw_pts, key=lambda x: x[0])
    d = [p[0] for p in raw_pts]
    z = [p[1] for p in raw_pts]
    dmin, dmax = d[0], d[-1]
    if n <= 1:
        return [(dmin, z[0])]
    if dmax == dmin:
        return [(dmin, z[0]) for _ in range(n)]
    new_d = [dmin + i * (dmax - dmin) / (n - 1) for i in range(n)]
    new_z = []
    j = 0
    for nd in new_d:
        while j < len(d) - 2 and nd > d[j + 1]:
            j += 1
        denom = (d[j + 1] - d[j]) if d[j + 1] != d[j] else 1.0
        t = (nd - d[j]) / denom
        new_z.append(z[j] + t * (z[j + 1] - z[j]))
    return list(zip(new_d, new_z))


class FraMikeTilDHMAlgorithm(QgsProcessingAlgorithm):
    """
    Brænder MIKE-profiler ned i terrænet og laver min(DHM, TIN).
    """

    PARAM_MIKE_TXT = "MIKE_TXT"
    PARAM_CENTERLINE = "CENTERLINE"
    PARAM_DHM = "DHM"
    PARAM_OUTPUT = "OUTPUT"

    def name(self):
        return "framike_til_dhm"

    def displayName(self):
        return "Brænd vandløb i terræn (Fra MIKE til DHM)"

    def group(self):
        return "Limfjordssekretariatet"

    def groupId(self):
        return "limfjordssekretariatet"

    def shortHelpString(self):
        return (
            "Læser MIKE-eksport (tværprofiler), interpolerer dem langs en centerlinje,\n"
            "opretter TIN, klipper med concave hull og beregner min(DHM, TIN)\n"
            "som et nyt raster. DHM bruges som reference-grid."
        )

    def createInstance(self):
        return FraMikeTilDHMAlgorithm()

    def initAlgorithm(self, config=None):
        # MIKE txt-fil
        self.addParameter(
            QgsProcessingParameterFile(
                self.PARAM_MIKE_TXT,
                "MIKE eksport (tekstfil)",
                extension="txt"
            )
        )

        # Centerlinje (linjelag)
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAM_CENTERLINE,
                "Centerlinje (linjelag)",
                [QgsProcessing.TypeVectorLine]
            )
        )

        # DHM raster
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.PARAM_DHM,
                "DHM raster"
            )
        )

        # Output raster
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.PARAM_OUTPUT,
                "Output min(DHM, TIN)"
            )
        )

    # ------------------------------------------------------------------
    # Selve algoritmen
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        try:
            # ----------------------------------------------------------
            # 1) INPUT: MIKE-tekstfil, centerlinje, DHM & output-raster
            # ----------------------------------------------------------
            input_file = self.parameterAsFile(parameters, self.PARAM_MIKE_TXT, context)
            if not input_file:
                raise QgsProcessingException("Ingen MIKE-tekstfil valgt")

            centerline_layer = self.parameterAsVectorLayer(parameters, self.PARAM_CENTERLINE, context)
            if centerline_layer is None or not centerline_layer.isValid():
                raise QgsProcessingException("Centerlinje-laget kunne ikke indlæses.")

            dhm_layer = self.parameterAsRasterLayer(parameters, self.PARAM_DHM, context)
            if dhm_layer is None or not dhm_layer.isValid():
                raise QgsProcessingException("DHM-laget kunne ikke indlæses.")

            merge_out = self.parameterAsOutputLayer(parameters, self.PARAM_OUTPUT, context)
            if not merge_out:
                raise QgsProcessingException("Ingen output-fil valgt")

            feedback.pushInfo(f"MIKE txt: {input_file}")
            feedback.pushInfo(f"Centerlinje: {centerline_layer.name()}")
            feedback.pushInfo(f"DHM: {dhm_layer.name()}")
            feedback.pushInfo(f"Output: {merge_out}")

            # ----------------------------------------------------------
            # 2) Læs centerlinje
            # ----------------------------------------------------------
            centerline_feat = next(centerline_layer.getFeatures(), None)
            if centerline_feat is None:
                raise QgsProcessingException("Centerlinje-laget er tomt.")
            centerline_geom = centerline_feat.geometry()
            center_len = centerline_geom.length()

            crs = centerline_layer.crs()
            crs_authid = crs.authid()  # fx "EPSG:25832"

            # ----------------------------------------------------------
            # 3) Opret punktlag til kanalpunkter og interpolerede punkter
            # ----------------------------------------------------------
            kanal_pts = QgsVectorLayer(f"Point?crs={crs_authid}", "Kanalpunkter", "memory")
            pr_kanal = kanal_pts.dataProvider()
            pr_kanal.addAttributes([
                QgsField("name", QVariant.String),
                QgsField("station", QVariant.Double),
                QgsField("offset", QVariant.Double),
                QgsField("z", QVariant.Double)
            ])
            kanal_pts.updateFields()

            interp_pts = QgsVectorLayer(f"Point?crs={crs_authid}", "Interpolerede_punkter", "memory")
            pr_interp = interp_pts.dataProvider()
            pr_interp.addAttributes([
                QgsField("offset_idx", QVariant.Int),
                QgsField("loc", QVariant.Double),
                QgsField("offset", QVariant.Double),
                QgsField("z", QVariant.Double)
            ])
            interp_pts.updateFields()

            # ----------------------------------------------------------
            # 4) Parse MIKE-tekstfil og opbyg profiles_info
            # ----------------------------------------------------------
            profiles_info = []

            with open(input_file, "r", encoding="latin-1") as f:
                lines = f.readlines()

            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue

                name = line
                i += 1
                # læs station
                try:
                    station = float(lines[i].strip())
                except Exception:
                    i += 1
                    continue
                i += 1

                # COORDINATES-linje
                i += 1
                parts = lines[i].split()
                base_x, base_y = float(parts[1]), float(parts[2])
                i += 1
                # PROFILE-linje
                i += 1

                raw = []
                while i < len(lines) and "****" not in lines[i]:
                    parts = re.sub(r"<.*?>", "", lines[i]).split()
                    if len(parts) >= 2:
                        try:
                            raw.append((float(parts[0]), float(parts[1])))
                        except Exception:
                            pass
                    i += 1
                if i < len(lines) and "****" in lines[i]:
                    i += 1

                if len(raw) < 2:
                    feedback.pushInfo(f"⚠️ Profil '{name}' station {station} springes over (for få punkter)")
                    continue

                pts50 = interpolate_profile(raw, 50)
                d_vals = [x[0] for x in pts50]
                d_mid = (min(d_vals) + max(d_vals)) / 2.0
                offsets = [d - d_mid for d, _ in pts50]
                zs = [z for _, z in pts50]

                # find position på centerline
                probe = QgsGeometry.fromPointXY(QgsPointXY(base_x, base_y))
                loc = centerline_geom.lineLocatePoint(probe)
                if loc < 0:
                    feedback.pushInfo(f"⚠️ Profil '{name}' kunne ikke lokaliseres på centerlinjen (springes over)")
                    continue
                loc = max(0, min(center_len, loc))

                # beregn normalretning ved punktet
                delta = max(0.1, min(1, 0.005 * center_len))
                p1 = centerline_geom.interpolate(max(0, loc - delta)).asPoint()
                p2 = centerline_geom.interpolate(min(center_len, loc + delta)).asPoint()
                ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x()) - math.pi / 2
                nx, ny = math.cos(ang), math.sin(ang)

                # Opret kanalpunkter (kun til visualisering)
                cpt = centerline_geom.interpolate(loc).asPoint()
                for off, z in zip(offsets, zs):
                    x = cpt.x() + off * nx
                    y = cpt.y() + off * ny
                    feat = QgsFeature(kanal_pts.fields())
                    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                    feat.setAttributes([name, station, float(off), float(z)])
                    pr_kanal.addFeature(feat)

                profiles_info.append(dict(name=name, station=station, loc=loc, offsets=offsets, zs=zs))

            kanal_pts.updateExtents()
           # QgsProject.instance().addMapLayer(kanal_pts)
            feedback.pushInfo(f"✅ Kanalpunkter oprettet — {len(profiles_info)} profiler læst")

            # ----------------------------------------------------------
            # 5) Interpoler langs længden (interp_pts)
            # ----------------------------------------------------------
            if len(profiles_info) < 2:
                raise QgsProcessingException("Ikke nok profiler til længde-interpolation (min. 2 nødvendig)")

            profiles_info.sort(key=lambda p: p["loc"])
            L = [p["loc"] for p in profiles_info]
            n_offsets = len(profiles_info[0]["offsets"])
            step = 1.0  # afstand langs centerlinjen (m) – hold >= pixelstørrelse

            # Pre-beregn normalretninger langs centerlinjen for hvert trin
            delta = max(0.1, min(1, 0.005 * center_len))
            s_vals = []
            s = L[0]
            while s <= L[-1] + 1e-9:
                s_vals.append(s)
                s += step

            normals = []
            for sv in s_vals:
                cpt = centerline_geom.interpolate(sv).asPoint()
                t1 = centerline_geom.interpolate(max(0, sv - delta)).asPoint()
                t2 = centerline_geom.interpolate(min(center_len, sv + delta)).asPoint()
                ang = math.atan2(t2.y() - t1.y(), t2.x() - t1.x()) - math.pi / 2
                normals.append((cpt, math.cos(ang), math.sin(ang)))

            feedback.pushInfo(f"Interpolerer {len(s_vals)} trin × {n_offsets} offsets = {len(s_vals)*n_offsets:,} punkter...")

            for vi in range(n_offsets):
                O = [p["offsets"][vi] for p in profiles_info]
                Z = [p["zs"][vi] for p in profiles_info]

                for si, sv in enumerate(s_vals):
                    if sv <= L[0]:
                        off, zz = O[0], Z[0]
                    elif sv >= L[-1]:
                        off, zz = O[-1], Z[-1]
                    else:
                        j = bisect.bisect_right(L, sv) - 1
                        j = max(0, min(j, len(L) - 2))
                        denom = L[j + 1] - L[j] if L[j + 1] != L[j] else 1.0
                        t = (sv - L[j]) / denom
                        off = O[j] + t * (O[j + 1] - O[j])
                        zz = Z[j] + t * (Z[j + 1] - Z[j])

                    cpt, nx, ny = normals[si]
                    x = cpt.x() + off * nx
                    y = cpt.y() + off * ny

                    feat = QgsFeature(interp_pts.fields())
                    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                    feat.setAttributes([int(vi), float(sv), float(off), float(zz)])
                    pr_interp.addFeature(feat)

            interp_pts.updateExtents()
           # QgsProject.instance().addMapLayer(interp_pts)
            feedback.pushInfo("✅ Interpolerede punkter langs længden oprettet")

            # ----------------------------------------------------------
            # 6) CONCAVE HULL
            # ----------------------------------------------------------
            feedback.pushInfo("Beregner concave hull (kan tage et øjeblik)...")
            concave_params = {
                'ALPHA': 0.001,
                'HOLES': False,
                'INPUT': interp_pts,
                'NO_MULTIGEOMETRY': False,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            }
            concave_res = processing.run(
                'native:concavehull',
                concave_params,
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            concave_layer = concave_res['OUTPUT']
            feedback.pushInfo("✅ Concave hull oprettet")

            # ----------------------------------------------------------
            # 7) Gem interp_pts som shapefile (krævet af TIN-tool)
            # ----------------------------------------------------------
            temp_dir = tempfile.mkdtemp(prefix="tin_interp_")
            interp_shp = os.path.join(temp_dir, "interp_pts.shp")

            save_params = {
                'INPUT': interp_pts,
                'OUTPUT': interp_shp
            }

            try:
                save_res = processing.run(
                    'native:savefeatures',
                    save_params,
                    context=context,
                    feedback=feedback,
                    is_child_algorithm=True
                )
                interp_shp = save_res['OUTPUT']
                feedback.pushInfo("✅ Shapefile gemt med 'native:savefeatures'")
            except Exception:
                save_res = processing.run(
                    'qgis:savefeatures',
                    save_params,
                    context=context,
                    feedback=feedback,
                    is_child_algorithm=True
                )
                interp_shp = save_res['OUTPUT']
                feedback.pushInfo("✅ Shapefile gemt med 'qgis:savefeatures'")

            # ----------------------------------------------------------
            # 8) TIN interpolation
            # ----------------------------------------------------------
            ext = interp_pts.extent()
            extent_str = f"{ext.xMinimum()},{ext.xMaximum()},{ext.yMinimum()},{ext.yMaximum()} [{crs_authid}]"

            feedback.pushInfo("Kører TIN-interpolation (kan tage et øjeblik)...")
            tin_params = {
                'EXTENT': extent_str,
                'INTERPOLATION_DATA': f"{interp_shp}::~::0::~::3::~::0",
                'METHOD': 0,  # Linear
                'PIXEL_SIZE': 0.2,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            }
            tin_res = processing.run(
                'qgis:tininterpolation',
                tin_params,
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            tin_raster = tin_res['OUTPUT']
            feedback.pushInfo("✅ TIN-interpolation gennemført")

            # ----------------------------------------------------------
            # 9) Clip raster by mask layer (concave hull)
            # ----------------------------------------------------------
            clip_params = {
                'ALPHA_BAND': False,
                'CROP_TO_CUTLINE': True,
                'DATA_TYPE': 0,
                'EXTRA': None,
                'INPUT': tin_raster,
                'KEEP_RESOLUTION': False,
                'MASK': concave_layer,
                'MULTITHREADING': False,
                'NODATA': -9999,
                'OPTIONS': None,
                'SET_RESOLUTION': False,
                'SOURCE_CRS': None,
                'TARGET_CRS': None,
                'TARGET_EXTENT': None,
                'X_RESOLUTION': None,
                'Y_RESOLUTION': None,
                'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
            }
            clip_res = processing.run(
                'gdal:cliprasterbymasklayer',
                clip_params,
                context=context,
                feedback=feedback,
                is_child_algorithm=True
            )
            clip_raster = clip_res['OUTPUT']
            feedback.pushInfo("✅ TIN klippet med concave hull")

            # ----------------------------------------------------------
            # 10) Align TIN til DHM-grid og beregn min(DHM, TIN)
            # ----------------------------------------------------------
            feedback.pushInfo("🔎 Align'er klippet TIN til DHM-grid og beregner min(DHM, TIN)...")

            dhm_path = dhm_layer.source()
            dhm_ds = gdal.Open(dhm_path, gdal.GA_ReadOnly)
            if dhm_ds is None:
                raise QgsProcessingException("Kunne ikke åbne DHM med GDAL")

            gt = dhm_ds.GetGeoTransform()
            proj = dhm_ds.GetProjection()
            xsize = dhm_ds.RasterXSize
            ysize = dhm_ds.RasterYSize

            xmin = gt[0]
            ymax = gt[3]
            xmax = xmin + gt[1] * xsize
            ymin = ymax + gt[5] * ysize  # gt[5] typisk negativ

            align_dir = tempfile.mkdtemp(prefix="tin_align_")
            aligned_tin = os.path.join(align_dir, "tin_aligned.tif")

            warp_opts = gdal.WarpOptions(
                format='GTiff',
                dstSRS=proj,
                outputBounds=(xmin, ymin, xmax, ymax),
                width=xsize,
                height=ysize,
                resampleAlg=gdal.GRA_Bilinear,
                srcNodata=-9999,
                dstNodata=-9999
            )
            warp_ds = gdal.Warp(
                aligned_tin,
                clip_raster,
                options=warp_opts
            )
            if warp_ds is None:
                raise QgsProcessingException("gdal.Warp fejlede – kunne ikke align'e TIN til DHM-grid")
            warp_ds = None

            tin_ds = gdal.Open(aligned_tin, gdal.GA_ReadOnly)
            if tin_ds is None:
                raise QgsProcessingException("Kunne ikke åbne aligned TIN med GDAL")

            dhm_band = dhm_ds.GetRasterBand(1)
            tin_band = tin_ds.GetRasterBand(1)

            x1, y1 = dhm_ds.RasterXSize, dhm_ds.RasterYSize
            x2, y2 = tin_ds.RasterXSize, tin_ds.RasterYSize

            if (x1, y1) != (x2, y2):
                raise QgsProcessingException(
                    f"Rasterstørrelse passer stadig ikke efter warp:\n"
                    f"  DHM: {x1} x {y1}\n"
                    f"  TIN: {x2} x {y2}"
                )

            dhm_nd = dhm_band.GetNoDataValue()
            tin_nd = tin_band.GetNoDataValue()

            if dhm_nd is None and tin_nd is None:
                common_nd = -9999.0
                dhm_nd = common_nd
                tin_nd = common_nd
            elif dhm_nd is None:
                common_nd = tin_nd
                dhm_nd = common_nd
            elif tin_nd is None:
                common_nd = dhm_nd
                tin_nd = common_nd
            else:
                common_nd = dhm_nd

            dhm_arr = dhm_band.ReadAsArray().astype(np.float32)
            tin_arr = tin_band.ReadAsArray().astype(np.float32)

            dhm_is_nd = (dhm_arr == dhm_nd)
            tin_is_nd = (tin_arr == tin_nd)

            out_arr = np.empty_like(dhm_arr, dtype=np.float32)

            both_nd = dhm_is_nd & tin_is_nd
            out_arr[both_nd] = common_nd

            only_dhm_nd = dhm_is_nd & ~tin_is_nd
            out_arr[only_dhm_nd] = tin_arr[only_dhm_nd]

            only_tin_nd = ~dhm_is_nd & tin_is_nd
            out_arr[only_tin_nd] = dhm_arr[only_tin_nd]

            both_valid = ~dhm_is_nd & ~tin_is_nd
            out_arr[both_valid] = np.minimum(
                dhm_arr[both_valid],
                tin_arr[both_valid]
            )

            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                merge_out,
                x1, y1,
                1,
                gdal.GDT_Float32
            )
            if out_ds is None:
                raise QgsProcessingException("Kunne ikke oprette output-GeoTIFF (tjek sti og rettigheder)")

            out_ds.SetGeoTransform(dhm_ds.GetGeoTransform())
            out_ds.SetProjection(dhm_ds.GetProjection())

            out_band = out_ds.GetRasterBand(1)
            out_band.WriteArray(out_arr)
            out_band.SetNoDataValue(common_nd)
            out_band.FlushCache()

            out_ds.FlushCache()
            out_ds = None
            dhm_ds = None
            tin_ds = None

            feedback.pushInfo(f"🎉 Færdig! Min-raster skrevet til: {merge_out}")


            return {
                self.PARAM_OUTPUT: merge_out
            }

        except QgsProcessingException:
            raise
        except Exception as e:
            traceback.print_exc()
            raise QgsProcessingException(str(e))
