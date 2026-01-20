# -*- coding: utf-8 -*-
"""
/***************************************************************************
 ResilienceMapperDialog
                                 A QGIS plugin
 Calculates and maps urban resilience values based on isochrone accessibility.
                             -------------------
        begin                : 2023-04-28
        version              : 1.0.0
        qgisMinimumVersion   : 3.0
        author               : MKS DTECH, Luigi Pintacuda, Silvio Carta, Tommaso Turchi
        email                : l.pintacuda@herts.ac.uk
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "resilient_iso_dialog_base.ui")
)


class ResilientIsochronesDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
