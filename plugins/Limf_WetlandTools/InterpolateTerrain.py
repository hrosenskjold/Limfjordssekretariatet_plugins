# -*- coding: utf-8 -*-
"""
Model exported as python.
Name : Interpoler terræn
Group :
With QGIS : 34002
"""

from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterVectorLayer
from qgis.core import QgsProcessingParameterRasterLayer
from qgis.core import QgsProcessingParameterRasterDestination
from qgis.core import QgsProcessingUtils
from qgis.core import QgsVectorFileWriter, QgsCoordinateTransformContext
from osgeo import gdal
import processing


class InterpolerTerrn(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('omrde', 'Område', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterLayer('dhm', 'DHM', defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination('Merge', 'Fyldt højdemodel', createByDefault=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(5, model_feedback)
        results = {}
        outputs = {}

        # Points along geometry
        alg_params = {
            'DISTANCE': 1,
            'END_OFFSET': 0,
            'INPUT': parameters['omrde'],
            'START_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsAlongGeometry'] = processing.run('native:pointsalonglines', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # Sample raster values
        alg_params = {
            'COLUMN_PREFIX': 'z',
            'INPUT': outputs['PointsAlongGeometry']['OUTPUT'],
            'RASTERCOPY': parameters['dhm'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['SampleRasterValues'] = processing.run('native:rastersampling', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # Grid (IDW with nearest neighbor searching)
        alg_params = {
            'DATA_TYPE': 5,  # Float32
            'EXTRA': '',
            'INPUT': outputs['SampleRasterValues']['OUTPUT'],
            'MAX_POINTS': 12,
            'MIN_POINTS': 0,
            'NODATA': 0,
            'OPTIONS': None,
            'POWER': 5,
            'RADIUS': 100,
            'SMOOTHING': 0,
            'Z_FIELD': 'z1',
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['GridIdwWithNearestNeighborSearching'] = processing.run('gdal:gridinversedistancenearestneighbor', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # Clip raster til polygon via Python GDAL (håndterer memory/scratch-lag)
        idw_path     = outputs['GridIdwWithNearestNeighborSearching']['OUTPUT']
        cutline_path = self._layer_to_path(
            self.parameterAsVectorLayer(parameters, 'omrde', context))
        clipped_path = QgsProcessingUtils.generateTempFilename('clipped.tif')
        gdal.Warp(
            clipped_path, idw_path,
            cutlineDSName=cutline_path,
            cropToCutline=True,
            dstNodata=-9999.0,
            outputType=gdal.GDT_Float32,
            format='GTiff',
        )

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Merge via Python GDAL (undgår gdal_merge.bat Windows-encodingfejl)
        out_path  = self.parameterAsOutputLayer(parameters, 'Merge', context)
        dhm_path  = self.parameterAsRasterLayer(parameters, 'dhm', context).source()
        gdal.Warp(out_path, [dhm_path, clipped_path], format='GTiff', outputType=gdal.GDT_Float32)

        results['Merge'] = out_path
        return results

    def _layer_to_path(self, layer):
        """Returnerer filsti til vektorlag – eksporterer memory/scratch-lag til temp-GPKG."""
        src = layer.source()
        if src.startswith('memory:') or '?geometrytype' in src:
            temp_path = QgsProcessingUtils.generateTempFilename('clip_mask.gpkg')
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = 'GPKG'
            QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, temp_path, QgsCoordinateTransformContext(), opts)
            return temp_path
        return src.split('|')[0]

    def name(self):
        return 'Interpoler terræn'

    def displayName(self):
        return 'Interpoler terræn'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def createInstance(self):
        return InterpolerTerrn()
