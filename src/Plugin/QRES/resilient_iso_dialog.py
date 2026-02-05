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
from qgis.PyQt.QtWidgets import QCheckBox, QGroupBox, QVBoxLayout, QPushButton, QHBoxLayout

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "resilient_iso_dialog_base.ui")
)


class ResilientIsochronesDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        
        # Store facility checkboxes
        self.facility_checkboxes = {}
        
        # Expand dialog height to fit checkboxes
        self.resize(self.width(), 500)
        
        # Create group box for facilities
        facilities_group = QGroupBox("Select Facilities to Calculate", self)
        facilities_group.setGeometry(20, 120, 371, 300)
        
        facilities_layout = QVBoxLayout()
        
        # Add Select All / Deselect All buttons
        button_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        deselect_all_btn = QPushButton("Deselect All")
        select_all_btn.clicked.connect(self._select_all_facilities)
        deselect_all_btn.clicked.connect(self._deselect_all_facilities)
        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(deselect_all_btn)
        facilities_layout.addLayout(button_layout)
        
        # Add checkbox for each facility
        facility_labels = {
            "schools": "Schools",
            "kindergarden": "Kindergartens",
            "transportation": "Transportation (Bus/Train)",
            "airports": "Airports",
            "leisure_and_parks": "Leisure & Parks",
            "shops": "Shops",
            "higher_education": "Higher Education",
            "further_education": "Further Education",
            "hospitals": "Hospitals"
        }
        
        for key, label in facility_labels.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)  # All checked by default
            self.facility_checkboxes[key] = checkbox
            facilities_layout.addWidget(checkbox)
        
        facilities_group.setLayout(facilities_layout)
        
        # Move button box down
        self.button_box.setGeometry(0, 430, 401, 41)
    
    def _select_all_facilities(self):
        """Check all facility checkboxes."""
        for checkbox in self.facility_checkboxes.values():
            checkbox.setChecked(True)
    
    def _deselect_all_facilities(self):
        """Uncheck all facility checkboxes."""
        for checkbox in self.facility_checkboxes.values():
            checkbox.setChecked(False)
    
    def get_selected_facilities(self):
        """Return list of selected facility keys."""
        return [key for key, checkbox in self.facility_checkboxes.items() if checkbox.isChecked()]
