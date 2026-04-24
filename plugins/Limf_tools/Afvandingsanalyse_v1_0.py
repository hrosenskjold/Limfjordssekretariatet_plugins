from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterRasterLayer, QgsProcessingParameterVectorLayer, QgsProcessingParameterField, QgsProcessingParameterFeatureSink, QgsProcessingParameterExtent
from qgis.core import QgsRendererCategory, QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsProject
import processing

class Afvandingsmodelqgisoktober2025gdal(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):

        self.addParameter(QgsProcessingParameterRasterLayer('dhm', 'DHM', defaultValue=None))
        self.addParameter(QgsProcessingParameterVectorLayer('vsp', 'Input vandspejlspunkter', types=[QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterField('vector_field', 'Vælg vandspejl', type=QgsProcessingParameterField.Any, parentLayerParameterName='vsp', allowMultiple=False, defaultValue='vsp_m'))
        self.addParameter(QgsProcessingParameterExtent('extent', 'Vælg extent for beregningen', defaultValue=None))
        self.addParameter(QgsProcessingParameterFeatureSink('Output', 'output', type=QgsProcessing.TypeVectorAnyGeometry, createByDefault=True, supportsAppend=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        feedback = QgsProcessingMultiStepFeedback(5, model_feedback)
        results = {}
        outputs = {}

        alg_params = {
            'DATA_TYPE': 5,
            'INPUT': parameters['vsp'],
            'MAX_POINTS': 12,
            'MIN_POINTS': 3,
            'NODATA': 0,
            'POWER': 2,
            'RADIUS': 1000,
            'SMOOTHING': 0,
            'Z_FIELD': parameters['vector_field'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['GridIdwWithNearestNeighborSearching'] = processing.run('gdal:gridinversedistancenearestneighbor', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        alg_params = {
            'CELL_SIZE': None,
            'CRS': None,
            'EXPRESSION': '("A@1" - "B@1")*100',
            'EXTENT': parameters['extent'],
            'LAYERS': [parameters['dhm'], outputs['GridIdwWithNearestNeighborSearching']['OUTPUT']],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['RasterCalculator'] = processing.run('native:modelerrastercalc', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        alg_params = {
            'DATA_TYPE': 11,
            'INPUT_RASTER': outputs['RasterCalculator']['OUTPUT'],
            'NODATA_FOR_MISSING': True,
            'NO_DATA': -9999,
            'RANGE_BOUNDARIES': 0,
            'RASTER_BAND': 1,
            'TABLE': ['-9000','0','1','0','25','2','25','50','3','50','75','4','75','100','5','100','125','6','125','9000','-9999'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['ReclassifyByTable'] = processing.run('native:reclassifybytable', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        alg_params = {
            'BAND': 1,
            'FIELD': 'Gridkode',
            'INPUT': outputs['ReclassifyByTable']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PolygonisrRasterTilVektor'] = processing.run('gdal:polygonize', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        alg_params = {
            'FIELD_LENGTH': 0,
            'FIELD_NAME': 'Navn',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 2,
            'FORMULA': """
CASE
  WHEN "Gridkode" = 1 THEN '< 0 cm Frit vandspejl'
  WHEN "Gridkode" = 2 THEN '0-25 cm Sump'
  WHEN "Gridkode" = 3 THEN '25-50 cm Våd eng'
  WHEN "Gridkode" = 4 THEN '50-75 cm Fugtig eng'
  WHEN "Gridkode" = 5 THEN '75-100 cm Tør eng'
  WHEN "Gridkode" = 6 THEN '100-125 cm Mark'
  ELSE 'Øvrigt'
END
""",
            'INPUT': outputs['PolygonisrRasterTilVektor']['OUTPUT'],
            'OUTPUT': parameters['Output']
        }
        outputs['Feltberegner'] = processing.run('native:fieldcalculator', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Output'] = outputs['Feltberegner']['OUTPUT']

        layer = context.takeResultLayer(outputs['Feltberegner']['OUTPUT'])

        categories = []
        style_defs = [
            ('< 0 cm Frit vandspejl', "#07256C"),
            ('0-25 cm Sump', "#3987ee"),
            ('25-50 cm Våd eng', "#46e31a"),
            ('50-75 cm Fugtig eng', "#0C3D04"),
            ('75-100 cm Tør eng', "#E0E323"),
            ('100-125 cm Mark', "#cc840f"),
            ('Øvrigt', "#cccccc"),
        ]

        for value, color in style_defs:
            symbol = QgsFillSymbol.createSimple({'color': color, 'outline_color': 'black', 'outline_width': '0.3'})
            categories.append(QgsRendererCategory(value, symbol, value))

        renderer = QgsCategorizedSymbolRenderer('Navn', categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        QgsProject.instance().addMapLayer(layer)

        return results

    def name(self): return 'AfvandingsmodelQGISoktober2025GDAL'
    def displayName(self): return 'AfvandingsmodelQGISoktober2025GDAL'
    def group(self): return ''
    def groupId(self): return ''
    def createInstance(self): return Afvandingsmodelqgisoktober2025gdal()
