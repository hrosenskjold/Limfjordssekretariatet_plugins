from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
    QgsProcessingUtils,
)
from osgeo import gdal, ogr, osr
import numpy as np
import os


class DHMVolumen(QgsProcessingAlgorithm):

    PARAM_ORIG   = "ORIGINAL_DHM"
    PARAM_NEW    = "NY_DHM"
    PARAM_MASK   = "MASK"
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
            QgsProcessingParameterVectorLayer(
                self.PARAM_MASK,
                self.tr("Afgræns til polygon (valgfrit)"),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.PARAM_OUTPUT,
                self.tr("Output differenceraster (Original - Ny)")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        orig       = self.parameterAsRasterLayer(parameters, self.PARAM_ORIG, context)
        new        = self.parameterAsRasterLayer(parameters, self.PARAM_NEW, context)
        mask_layer = self.parameterAsVectorLayer(parameters, self.PARAM_MASK, context)
        out_path   = self.parameterAsOutputLayer(parameters, self.PARAM_OUTPUT, context)

        if orig is None or new is None:
            raise QgsProcessingException("Kunne ikke læse input-rasterlag.")

        # Sørg for at out_path er en brugbar filsti
        if not out_path:
            out_path = QgsProcessingUtils.generateTempFilename('diff.tif')
        if not os.path.isabs(out_path):
            out_path = os.path.join(QgsProcessingUtils.tempFolder(), out_path)
        if not out_path.lower().endswith('.tif'):
            out_path += '.tif'
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        feedback.pushInfo("Beregner differenceraster...")

        src_a = gdal.Open(orig.source())
        src_b = gdal.Open(new.source())

        gt_a    = src_a.GetGeoTransform()
        proj_a  = src_a.GetProjection()
        xsize_a = src_a.RasterXSize
        ysize_a = src_a.RasterYSize

        nodata_b_src = src_b.GetRasterBand(1).GetNoDataValue()
        fill_nodata  = nodata_b_src if nodata_b_src is not None else -9999.0

        mem_driver = gdal.GetDriverByName('MEM')
        b_aligned = mem_driver.Create('', xsize_a, ysize_a, 1, gdal.GDT_Float32)
        b_aligned.SetGeoTransform(gt_a)
        b_aligned.SetProjection(proj_a)
        b_aligned.GetRasterBand(1).Fill(fill_nodata)
        b_aligned.GetRasterBand(1).SetNoDataValue(fill_nodata)
        gdal.ReprojectImage(src_b, b_aligned, None, None, gdal.GRA_Bilinear)

        band_a   = src_a.GetRasterBand(1)
        nodata_a = band_a.GetNoDataValue()
        data_a   = band_a.ReadAsArray().astype(np.float32)
        data_b   = b_aligned.GetRasterBand(1).ReadAsArray().astype(np.float32)

        diff = data_a - data_b

        nodata_out = -9999.0
        mask = np.zeros(diff.shape, dtype=bool)
        if nodata_a is not None:
            mask |= np.isclose(data_a, nodata_a)
        mask |= np.isclose(data_b, fill_nodata)

        # Polygon-afgrænsning
        if mask_layer is not None:
            feedback.pushInfo("Afgrænser til polygon...")
            poly_mask = self._rasterize_mask(mask_layer, gt_a, proj_a, xsize_a, ysize_a)
            if poly_mask is not None:
                mask |= ~poly_mask
            else:
                feedback.reportError("Kunne ikke rasterisere polygonlag – bruges ikke.")

        diff[mask] = nodata_out

        gtiff_driver = gdal.GetDriverByName('GTiff')
        out_ds = gtiff_driver.Create(out_path, xsize_a, ysize_a, 1, gdal.GDT_Float32)
        if out_ds is None:
            raise QgsProcessingException(f"GDAL kunne ikke oprette output: {out_path}")
        out_ds.SetGeoTransform(gt_a)
        out_ds.SetProjection(proj_a)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(nodata_out)
        out_band.WriteArray(diff)
        out_ds.FlushCache()
        out_ds = None
        src_a = None; b_aligned = None; src_b = None

        cell_area = abs(gt_a[1] * gt_a[5])
        valid     = diff[~mask]
        vol_cut   = float(valid[valid > 0].sum()) * cell_area
        vol_fill  = float(-valid[valid < 0].sum()) * cell_area

        feedback.pushInfo(f"Jordafgravning: {vol_cut:.2f} m³")
        feedback.pushInfo(f"Jordtilførsel: {vol_fill:.2f} m³")

        return {self.PARAM_OUTPUT: out_path}

    # ----------------------------------------------------------------- helpers

    def _rasterize_mask(self, mask_layer, gt, proj, xsize, ysize):
        """Returnerer bool-numpy-array (True = inden for polygon), eller None ved fejl."""
        try:
            source = mask_layer.source().split('|')[0]
            mask_ds = ogr.Open(source)
            if mask_ds is None:
                return None
            mask_lyr = mask_ds.GetLayer()

            raster_srs = osr.SpatialReference()
            raster_srs.ImportFromWkt(proj)
            raster_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

            vec_srs = mask_lyr.GetSpatialRef()
            need_transform = (vec_srs is not None
                              and not raster_srs.IsSame(vec_srs))
            if need_transform:
                ct = osr.CoordinateTransformation(vec_srs, raster_srs)

            mem_vec = ogr.GetDriverByName('Memory').CreateDataSource('')
            mem_lyr = mem_vec.CreateLayer('mask', srs=raster_srs,
                                          geom_type=ogr.wkbMultiPolygon)
            for feat in mask_lyr:
                geom = feat.GetGeometryRef().Clone()
                if need_transform:
                    geom.Transform(ct)
                nf = ogr.Feature(mem_lyr.GetLayerDefn())
                nf.SetGeometry(geom)
                mem_lyr.CreateFeature(nf)

            mem_raster = gdal.GetDriverByName('MEM').Create(
                '', xsize, ysize, 1, gdal.GDT_Byte)
            mem_raster.SetGeoTransform(gt)
            mem_raster.SetProjection(proj)
            mem_raster.GetRasterBand(1).Fill(0)
            gdal.RasterizeLayer(mem_raster, [1], mem_lyr, burn_values=[1])

            arr = (np.frombuffer(mem_raster.GetRasterBand(1).ReadRaster(),
                                 dtype=np.uint8)
                     .reshape(ysize, xsize)
                     .astype(bool))
            mem_raster = None
            return arr
        except Exception:
            return None
