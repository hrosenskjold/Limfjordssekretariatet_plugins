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
import processing


class InterpolerTerrn(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('omrde', 'Område', types=[QgsProcessing.TypeVectorPolygon], defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterLayer('dhm', 'DHM', defaultValue=None))
        self.addParameter(QgsProcessingParameterRasterDestination('Merge', 'Fyldt højdemodel', createByDefault=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
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

        # Clip raster by mask layer
        alg_params = {
            'ALPHA_BAND': False,
            'CROP_TO_CUTLINE': True,
            'DATA_TYPE': 0,  # Use Input Layer Data Type
            'EXTRA': '',
            'INPUT': outputs['GridIdwWithNearestNeighborSearching']['OUTPUT'],
            'KEEP_RESOLUTION': False,
            'MASK': parameters['omrde'],
            'MULTITHREADING': False,
            'NODATA': None,
            'OPTIONS': None,
            'SET_RESOLUTION': False,
            'SOURCE_CRS': None,
            'TARGET_CRS': None,
            'TARGET_EXTENT': None,
            'X_RESOLUTION': None,
            'Y_RESOLUTION': None,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ClipRasterByMaskLayer'] = processing.run('gdal:cliprasterbymasklayer', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Merge
        alg_params = {
            'DATA_TYPE': 5,  # Float32
            'EXTRA': '',
            'INPUT': [parameters['dhm'],outputs['ClipRasterByMaskLayer']['OUTPUT']],
            'NODATA_INPUT': None,
            'NODATA_OUTPUT': None,
            'OPTIONS': None,
            'PCT': False,
            'SEPARATE': False,
            'OUTPUT': parameters['Merge']
        }
        outputs['Merge'] = processing.run('gdal:merge', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Merge'] = outputs['Merge']['OUTPUT']
        return results

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
