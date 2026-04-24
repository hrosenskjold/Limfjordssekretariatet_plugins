import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from .jordprover_dialog import JordproverDialog


class Jordprover:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
        self.action = QAction(icon, 'Jordprover', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('Jordprover', self.action)

    def unload(self):
        self.iface.removePluginMenu('Jordprover', self.action)
        self.iface.removeToolBarIcon(self.action)
        del self.action

    def run(self):
        if self.dialog is None:
            self.dialog = JordproverDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
