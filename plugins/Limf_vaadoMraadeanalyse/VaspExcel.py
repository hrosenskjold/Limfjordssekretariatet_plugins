"""
Model exported as python.
Name : VASPExcelbegge
Group : 
With QGIS : 34011
"""

from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterRasterLayer
from qgis.core import QgsProcessingParameterVectorLayer
from qgis.core import QgsProcessingParameterNumber
from qgis.core import QgsProcessingParameterEnum
from qgis.core import QgsProcessingParameterFileDestination
import processing


class Vaspexcelbegge(QgsProcessingAlgorithm):

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer('dhm', 'Højdemodel', defaultValue=None))
        self.addParameter(QgsProcessingParameterVectorLayer('points', 'Punkter fra VASP', types=[QgsProcessing.TypeVectorPoint], defaultValue=None))
        self.addParameter(QgsProcessingParameterNumber('startstation', 'Startstation', type=QgsProcessingParameterNumber.Integer, defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum('sidevalg', 'Vælg side til terræn', options=['Left','Right'], allowMultiple=False, usesStaticStrings=False, defaultValue=[]))
        self.addParameter(QgsProcessingParameterFileDestination('Outputexcel', 'outputexcel', fileFilter='Microsoft Excel (*.xlsx);;Open Document Spreadsheet (*.ods)', createByDefault=True, defaultValue=None))

    def processAlgorithm(self, parameters, context, model_feedback):
        # Use a multi-step feedback, so that individual child algorithm progress reports are adjusted for the
        # overall progress through the model
        feedback = QgsProcessingMultiStepFeedback(8, model_feedback)
        # Determine offset distance based on sidevalg
        side_choice = self.parameterAsEnum(parameters, 'sidevalg', context)
        offset_distance = 10 if side_choice == 0 else -10
        start_val = self.parameterAsInt(parameters, 'startstation', context)

        results = {}
        outputs = {}

        # Points to path
        alg_params = {
            'CLOSE_PATH': False,
            'GROUP_EXPRESSION': None,
            'INPUT': parameters['points'],
            'NATURAL_SORT': False,
            'ORDER_EXPRESSION': None,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT,
            'OUTPUT_TEXT_DIR': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsToPath'] = processing.run('native:pointstopath', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(1)
        if feedback.isCanceled():
            return {}

        # Offset lines
        alg_params = {
            'DISTANCE': offset_distance,
            'INPUT': outputs['PointsToPath']['OUTPUT'],
            'JOIN_STYLE': 0,  # Round
            'MITER_LIMIT': 2,
            'SEGMENTS': 1,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['OffsetLines'] = processing.run('native:offsetline', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(2)
        if feedback.isCanceled():
            return {}

        # Points along geometry
        alg_params = {
            'DISTANCE': 1,
            'END_OFFSET': 0,
            'INPUT': outputs['OffsetLines']['OUTPUT'],
            'START_OFFSET': 0,
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['PointsAlongGeometry'] = processing.run('native:pointsalonglines', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(3)
        if feedback.isCanceled():
            return {}

        # Field calculator station
        alg_params = {
            'FIELD_LENGTH': 0,
            'FIELD_NAME': 'station',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 0,  # Decimal (double)
            'FORMULA': f"{start_val} + \"distance\"",
            'INPUT': outputs['PointsAlongGeometry']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['FieldCalculatorStation'] = processing.run('native:fieldcalculator', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(4)
        if feedback.isCanceled():
            return {}

        # Sample raster values
        alg_params = {
            'COLUMN_PREFIX': 'bundkote',
            'INPUT': outputs['FieldCalculatorStation']['OUTPUT'],
            'RASTERCOPY': parameters['dhm'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['SampleRasterValues'] = processing.run('native:rastersampling', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(5)
        if feedback.isCanceled():
            return {}

        # field calc vsp
        alg_params = {
            'FIELD_LENGTH': 0,
            'FIELD_NAME': 'vsp',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 2,  # Text (string)
            'FORMULA': "''",
            'INPUT': outputs['SampleRasterValues']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['FieldCalcVsp'] = processing.run('native:fieldcalculator', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(6)
        if feedback.isCanceled():
            return {}

        # Field calculator bemærkning
        alg_params = {
            'FIELD_LENGTH': 0,
            'FIELD_NAME': 'Bemærkning',
            'FIELD_PRECISION': 0,
            'FIELD_TYPE': 2,  # Text (string)
            'FORMULA': "'Terrain'",
            'INPUT': outputs['FieldCalcVsp']['OUTPUT'],
            'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT
        }
        outputs['FieldCalculatorBemrkning'] = processing.run('native:fieldcalculator', alg_params, context=context, feedback=feedback, is_child_algorithm=True)

        feedback.setCurrentStep(7)
        if feedback.isCanceled():
            return {}

        # Export to spreadsheet
        alg_params = {
            'FORMATTED_VALUES': False,
            'LAYERS': outputs['FieldCalculatorBemrkning']['OUTPUT'],
            'OVERWRITE': True,
            'USE_ALIAS': False,
            'OUTPUT': parameters['Outputexcel']
        }
        outputs['ExportToSpreadsheet'] = processing.run('native:exporttospreadsheet', alg_params, context=context, feedback=feedback, is_child_algorithm=True)
        results['Outputexcel'] = outputs['ExportToSpreadsheet']['OUTPUT']
        return results

    def name(self):
        return 'VASPExcelbegge'

    def displayName(self):
        return 'VASPExcelbegge'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def createInstance(self):
        return Vaspexcelbegge()
