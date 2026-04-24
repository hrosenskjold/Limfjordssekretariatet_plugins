# -*- coding: utf-8 -*-
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingMultiStepFeedback,
    QgsCoordinateReferenceSystem,
    QgsProcessing,
    QgsWkbTypes,
)
import processing


class GridTilLER(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    CELL_WIDTH = 'CELL_WIDTH'
    CELL_HEIGHT = 'CELL_HEIGHT'
    BUFFER_DISTANCE = 'BUFFER_DISTANCE'

    def name(self):
        return 'grid_til_ler'

    def displayName(self):
        return 'Grid til LER'

    def group(self):
        return 'Limfjordssekretariatet tools'

    def groupId(self):
        return 'limf_tools'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, 'Projektgrænse',
            [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_WIDTH, 'Cellebredde (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_HEIGHT, 'Cellehøjde (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.BUFFER_DISTANCE, 'Bufferafstand (meter)',
            type=QgsProcessingParameterNumber.Double,
            defaultValue=1.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Til LER søgning'))

    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(7, model_feedback)
        cell_width  = self.parameterAsDouble(parameters, self.CELL_WIDTH, context)
        cell_height = self.parameterAsDouble(parameters, self.CELL_HEIGHT, context)
        # Halver: linjebuffer går begge sider, så total bredde = angivet afstand
        buffer_dist = self.parameterAsDouble(parameters, self.BUFFER_DISTANCE, context) / 2
        crs = QgsCoordinateReferenceSystem('EPSG:25832')

        # 0. Reprojectér input til EPSG:25832 så buffer er i meter
        input_25832 = processing.run('native:reprojectlayer', {
            'INPUT': parameters[self.INPUT],
            'TARGET_CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # 1. Konvertér projektgrænse til linje og buffer → yderkant-strip
        boundary_line = processing.run('native:polygonstolines', {
            'INPUT': input_25832,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        boundary_buffered = processing.run('native:buffer', {
            'INPUT': boundary_line,
            'DISTANCE': buffer_dist,
            'SEGMENTS': 5,
            'DISSOLVE': False,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # 2. Opret fishnet af linjer
        grid_lines = processing.run('qgis:creategrid', {
            'TYPE': 1,
            'EXTENT': input_25832,
            'HSPACING': cell_width,
            'VSPACING': cell_height,
            'CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # 3. Klip gridlinjer til projektgrænsen
        grid_clipped = processing.run('native:clip', {
            'INPUT': grid_lines,
            'OVERLAY': input_25832,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # 4. Buffer gridlinjer → smalle strips
        grid_buffered = processing.run('native:buffer', {
            'INPUT': grid_clipped,
            'DISTANCE': buffer_dist,
            'SEGMENTS': 5,
            'DISSOLVE': False,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # 5. Merge yderkant-strip + grid-strips
        merged = processing.run('native:mergevectorlayers', {
            'LAYERS': [boundary_buffered, grid_buffered],
            'CRS': crs,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # 6. Dissolve alt til ét samlet feature
        dissolved = processing.run('native:dissolve', {
            'INPUT': merged,
            'FIELD': [],
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)['OUTPUT']

        feedback.setCurrentStep(7)

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            dissolved.fields(), QgsWkbTypes.MultiPolygon, crs)

        for feat in dissolved.getFeatures():
            sink.addFeature(feat)

        return {self.OUTPUT: dest_id}

    def createInstance(self):
        return GridTilLER()
