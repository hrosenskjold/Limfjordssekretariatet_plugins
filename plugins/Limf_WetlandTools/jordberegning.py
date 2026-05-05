from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
)
from osgeo import gdal
import numpy as np


class DHMVolumen(QgsProcessingAlgorithm):

    PARAM_ORIG = "ORIGINAL_DHM"
    PARAM_NEW = "NY_DHM"
    PARAM_OUTPUT = "OUTPUT_DIFF"

    def tr(self, text):
        return QCoreApplication.translate("DHMVolumen", text)

    def createInstance(self):
        return DHMVolumen()

    def name(self):
        return "dhm_volumen"

    def displayName(self):
        return self.tr("DHM volumen (afgravning/tilførsel)")

    def group(self):
        return self.tr("DHM værktøjer")

    def groupId(self):
        return "dhm_tools"

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.PARAM_ORIG,
                self.tr("Original DHM")
            )
        )

        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.PARAM_NEW,
                self.tr("Ny DHM")
            )
        )

        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.PARAM_OUTPUT,
                self.tr("Output differenceraster (Original - Ny)")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        orig = self.parameterAsRasterLayer(parameters, self.PARAM_ORIG, context)
        new = self.parameterAsRasterLayer(parameters, self.PARAM_NEW, context)
        out_path = self.parameterAsOutputLayer(parameters, self.PARAM_OUTPUT, context)

        if orig is None or new is None:
            raise QgsProcessingException("Kunne ikke læse input-rasterlag.")

        feedback.pushInfo("Beregner differenceraster...")

        src_a = gdal.Open(orig.source())
        src_b = gdal.Open(new.source())

        gt_a = src_a.GetGeoTransform()
        proj_a = src_a.GetProjection()
        xsize_a = src_a.RasterXSize
        ysize_a = src_a.RasterYSize

        # Tilpas B til A's grid i hukommelsen (håndterer forskellige extents/opløsninger)
        nodata_b_src = src_b.GetRasterBand(1).GetNoDataValue()
        fill_nodata = nodata_b_src if nodata_b_src is not None else -9999.0

        mem_driver = gdal.GetDriverByName('MEM')
        b_aligned = mem_driver.Create('', xsize_a, ysize_a, 1, gdal.GDT_Float32)
        b_aligned.SetGeoTransform(gt_a)
        b_aligned.SetProjection(proj_a)
        b_aligned.GetRasterBand(1).Fill(fill_nodata)
        b_aligned.GetRasterBand(1).SetNoDataValue(fill_nodata)
        gdal.ReprojectImage(src_b, b_aligned, None, None, gdal.GRA_Bilinear)

        band_a = src_a.GetRasterBand(1)
        nodata_a = band_a.GetNoDataValue()
        data_a = band_a.ReadAsArray().astype(np.float32)
        data_b = b_aligned.GetRasterBand(1).ReadAsArray().astype(np.float32)

        diff = data_a - data_b

        nodata_out = -9999.0
        mask = np.zeros(diff.shape, dtype=bool)
        if nodata_a is not None:
            mask |= np.isclose(data_a, nodata_a)
        mask |= np.isclose(data_b, fill_nodata)
        diff[mask] = nodata_out

        gtiff_driver = gdal.GetDriverByName('GTiff')
        out_ds = gtiff_driver.Create(out_path, xsize_a, ysize_a, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt_a)
        out_ds.SetProjection(proj_a)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(nodata_out)
        out_band.WriteArray(diff)
        out_ds.FlushCache()
        out_ds = None
        src_a = None
        b_aligned = None
        src_b = None

        cell_area = abs(gt_a[1] * gt_a[5])
        valid = diff[~mask]
        vol_cut = float(valid[valid > 0].sum()) * cell_area
        vol_fill = float(-valid[valid < 0].sum()) * cell_area

        feedback.pushInfo(f"Jordafgravning: {vol_cut:.2f} m³")
        feedback.pushInfo(f"Jordtilførsel: {vol_fill:.2f} m³")

        return {
            self.PARAM_OUTPUT: out_path,
            "JORD_AFGRAVNING_M3": vol_cut,
            "JORD_TILFOERSEL_M3": vol_fill,
        }
