from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingException,
    QgsRasterLayer,
)
import processing
import math


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

        result = processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": orig.source(),
                "BAND_A": 1,
                "INPUT_B": new.source(),
                "BAND_B": 1,
                "FORMULA": "A-B",
                "RTYPE": 5,
                "OUTPUT": out_path,
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        diff_layer = QgsRasterLayer(result["OUTPUT"], "diff")
        provider = diff_layer.dataProvider()

        cell_area = abs(
            diff_layer.rasterUnitsPerPixelX() *
            diff_layer.rasterUnitsPerPixelY()
        )

        block = provider.block(
            1,
            diff_layer.extent(),
            diff_layer.width(),
            diff_layer.height()
        )

        sum_pos = 0.0
        sum_neg = 0.0

        for i in range(diff_layer.width() * diff_layer.height()):
            val = block.value(i)
            if val is None or math.isnan(val):
                continue
            if val > 0:
                sum_pos += val
            elif val < 0:
                sum_neg += val

        vol_cut = sum_pos * cell_area
        vol_fill = -sum_neg * cell_area

        feedback.pushInfo(f"Jordafgravning: {vol_cut:.2f} m³")
        feedback.pushInfo(f"Jordtilførsel: {vol_fill:.2f} m³")

        return {
            self.PARAM_OUTPUT: result["OUTPUT"],
            "JORD_AFGRAVNING_M3": vol_cut,
            "JORD_TILFOERSEL_M3": vol_fill,
        }
