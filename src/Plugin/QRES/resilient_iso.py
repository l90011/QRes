# -*- coding: utf-8 -*-
"""
/***************************************************************************
 ResilienceMapper
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
import json

from PyQt5.QtCore import QSettings, QTranslator, QCoreApplication, QVariant, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QProgressDialog,
)

from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsField,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

# Plugin dialogs
from .resilient_iso_dialog import ResilientIsochronesDialog

# Plugin resources (Qt .qrc compiled into resources.py)
from .resources import *  # noqa: F401,F403


# ----------------------------
# Optional third party deps
# ----------------------------
_missing_deps = []

try:
    import requests
except Exception:
    requests = None
    _missing_deps.append("requests")

try:
    import simplejson
except Exception:
    simplejson = None
    _missing_deps.append("simplejson")

try:
    import shapely.geometry
except Exception:
    shapely = None
    _missing_deps.append("shapely")


# ----------------------------
# Config: facilities and profiles
# ----------------------------
FACILITIES = {
    "schools": ['"amenity"="school"'],
    "kindergarden": ['"amenity"="kindergarten"', '"amenity"="childcare"'],
    "transportation": ['"highway"="bus_stop"', '"railway"="station"'],
    "airports": ['"aeroway"="terminal"'],
    "leisure_and_parks": ['"leisure"~"."',
    '"landuse"="park"',
    '"boundary"="protected_area"',
    '"natural"="wood"',
    '"landuse"="forest"',
    '"natural"="grassland"',
    '"landuse"="farmyard"',
    '"landuse"="farmland"',
    '"landuse"="farm"',
    '"landuse"="meadow"',
    '"landuse"="orchard"',
    '"landuse"="vineyard"',
    '"landuse"="allotments"',
    '"landuse"="grass"',
    '"landuse"="village_green"',
    '"landuse"="recreation_ground"',
    '"landuse"="greenfield"'
],
    "shops": ['"shop"~"."'],
    "higher_education": ['"amenity"="university"'],
    "further_education": ['"amenity"="college"'],
    "hospitals": ['"healthcare"="hospital"'],
}

PROFILES = {
    "schools": {"profile": "walking", "intervals": [5, 15, 30]},
    "kindergarden": {"profile": "walking", "intervals": [5, 15, 30]},
    "transportation": {"profile": "walking", "intervals": [5, 15, 30]},
    "airports": {"profile": "driving", "intervals": [30, 60, 90]},
    "leisure_and_parks": {"profile": "walking", "intervals": [5, 15, 30]},
    "shops": {"profile": "walking", "intervals": [5, 15, 30]},
    "higher_education": {"profile": "walking", "intervals": [5, 15, 30]},
    "further_education": {"profile": "walking", "intervals": [5, 15, 30]},
    "hospitals": {"profile": "driving", "intervals": [15, 30, 60]},
}


# ----------------------------
# UI: Mapbox token dialog
# ----------------------------
class MapboxTokenDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("MapBox Token")

        layout = QVBoxLayout()

        self.label = QLabel("Please enter your MapBox Token:")
        layout.addWidget(self.label)

        self.lineEdit = QLineEdit()
        layout.addWidget(self.lineEdit)

        self.noteLabel = QLabel("Note: You can get your MapBox Token from:")
        layout.addWidget(self.noteLabel)

        self.linkLabel = QLabel(
            '<a href="https://docs.mapbox.com/help/getting-started/access-tokens/">docs.mapbox.com</a>'
        )
        self.linkLabel.setOpenExternalLinks(True)
        layout.addWidget(self.linkLabel)

        self.button = QPushButton("OK")
        layout.addWidget(self.button)

        self.button.clicked.connect(self.accept)

        self.setLayout(layout)


# ----------------------------
# Plugin main class
# ----------------------------
class ResilientIsochrones:
    """QGIS Plugin Implementation."""

    SETTINGS_KEY = "ResilienceMapper/mapbox_token"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # Dialog from Plugin Builder
        self.dlg = ResilientIsochronesDialog()

        # i18n (kept compatible with older file naming)
        locale = QSettings().value("locale/userLocale", "")[:2]
        locale_path = os.path.join(self.plugin_dir, "i18n", f"ResilientIsochrones_{locale}.qm")
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr("&QRES - ResilienceMapper")

        self.first_start = None
        self.point_layer = None

    def tr(self, message: str) -> str:
        return QCoreApplication.translate("ResilienceMapper", message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None,
    ):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.add_action(
            icon_path,
            text=self.tr("QRES - ResilienceMapper"),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )
        self.first_start = True

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.tr("&QRES - ResilienceMapper"), action)
            self.iface.removeToolBarIcon(action)

    # ----------------------------
    # Run
    # ----------------------------
    def run(self):
        # Dependency check
        if _missing_deps:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Missing Python dependencies",
                "This plugin requires the following Python packages:\n\n"
                + "\n".join(_missing_deps)
                + "\n\nInstall them in the QGIS Python environment and restart QGIS.",
            )
            return

        token = self._get_or_prompt_mapbox_token()
        if not token:
            return

        # Populate point layers in combo
        self._populate_point_layers_combo()

        # Show dialog
        result = self.dlg.exec_()
        if result != QDialog.Accepted:
            QMessageBox.information(
                self.iface.mainWindow(),
                "Operation Canceled",
                "The operation was canceled by the user.",
            )
            return

        selected_layer_name = self.dlg.layersComboBox.currentText().strip()
        if not selected_layer_name:
            QMessageBox.information(self.iface.mainWindow(), "No layer selected", "Please select a point layer.")
            return

        layers = QgsProject.instance().mapLayersByName(selected_layer_name)
        if not layers:
            QMessageBox.critical(self.iface.mainWindow(), "Layer not found", "Selected layer was not found.")
            return

        self.point_layer = layers[0]
        if not isinstance(self.point_layer, QgsVectorLayer) or self.point_layer.geometryType() != QgsWkbTypes.PointGeometry:
            QMessageBox.critical(self.iface.mainWindow(), "Invalid layer", "Selected layer must be a point vector layer.")
            return

        total_operations = int(self.point_layer.featureCount())
        progress = QProgressDialog("Processing...", "Cancel", 0, total_operations, self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle("Processing...")
        progress.show()

        # Start editing
        self.point_layer.startEditing()

        # Ensure fields exist
        self._ensure_fields()

        layer_crs = self.point_layer.crs()
        transform = None
        if layer_crs.authid() != "EPSG:4326":
            transform = QgsCoordinateTransform(
                layer_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )

        processed = 0

        for feature in self.point_layer.getFeatures():
            if progress.wasCanceled():
                break
            if feature.geometry() is None or feature.geometry().isNull():
                continue

            pt = feature.geometry().asPoint()
            if transform is not None:
                pt = transform.transform(pt)

            resilience_dict = calculate_resilience(pt, FACILITIES, PROFILES, token)

            feature_id = feature.id()

            # Update resilience fields
            changes = {}
            for key, value in resilience_dict.items():
                idx = self.point_layer.fields().indexOf(key)
                if idx >= 0:
                    changes[idx] = value

            # Update coords
            x_idx = self.point_layer.fields().indexOf("X_coord")
            y_idx = self.point_layer.fields().indexOf("Y_coord")
            if x_idx >= 0:
                changes[x_idx] = pt.x()
            if y_idx >= 0:
                changes[y_idx] = pt.y()

            if changes:
                self.point_layer.dataProvider().changeAttributeValues({feature_id: changes})

            processed += 1
            progress.setValue(processed)
            QCoreApplication.processEvents()
            progress.setLabelText(f"Calculating resilience for point {feature_id}")

        # Commit changes
        self.point_layer.commitChanges()
        self.point_layer.triggerRepaint()

        progress.reset()

        if progress.wasCanceled():
            QMessageBox.information(self.iface.mainWindow(), "Canceled", "Processing was canceled.")
            return

        QMessageBox.information(self.iface.mainWindow(), "Operation Complete", "The operation is complete.")

    # ----------------------------
    # Helpers
    # ----------------------------
    def _get_or_prompt_mapbox_token(self) -> str:
        settings = QSettings()
        token = settings.value(self.SETTINGS_KEY, "", type=str).strip()

        if token:
            return token

        dialog = MapboxTokenDialog(self.iface.mainWindow())
        result = dialog.exec_()
        if result != QDialog.Accepted:
            QMessageBox.information(
                self.iface.mainWindow(),
                "Operation Canceled",
                "The operation was canceled by the user.",
            )
            return ""

        token = dialog.lineEdit.text().strip()
        if not token:
            QMessageBox.information(
                self.iface.mainWindow(),
                "Missing token",
                "Please enter a valid MapBox Token.",
            )
            return ""

        settings.setValue(self.SETTINGS_KEY, token)
        return token

    def _populate_point_layers_combo(self):
        self.dlg.layersComboBox.clear()

        layer_names = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PointGeometry:
                layer_names.append(layer.name())

        layer_names.sort(key=lambda s: s.lower())
        self.dlg.layersComboBox.addItems(layer_names)

    def _ensure_fields(self):
        existing = {f.name() for f in self.point_layer.fields()}

        to_add = []
        if "X_coord" not in existing:
            to_add.append(QgsField("X_coord", QVariant.Double))
        if "Y_coord" not in existing:
            to_add.append(QgsField("Y_coord", QVariant.Double))

        for facility_key in FACILITIES.keys():
            field_name = f"R_{facility_key}"
            if field_name not in existing:
                to_add.append(QgsField(field_name, QVariant.Double))

        if "R_total" not in existing:
            to_add.append(QgsField("R_total", QVariant.Double))

        if to_add:
            self.point_layer.dataProvider().addAttributes(to_add)
            self.point_layer.updateFields()


# ----------------------------
# Core logic
# ----------------------------
def calculate_resilience(point, facilities, profiles, token: str) -> dict:
    latitude = point.y()
    longitude = point.x()
    coordinates = [longitude, latitude]

    resilience_dict = {}

    for facility_key in facilities.keys():
        profile = profiles[facility_key]["profile"]
        intervals = profiles[facility_key]["intervals"]

        isochrones_features = create_isochrones(token, coordinates, intervals, profile)
        isochrone_wkts = [shapely.geometry.shape(f["geometry"]).wkt for f in isochrones_features]

        all_facilities = []
        for wkt_poly in isochrone_wkts:
            polygon = wkt_polygon_to_overpass_format(wkt_poly)
            for query in facilities[facility_key]:
                all_facilities = [set(get_osm_data_within_polygon(polygon, query))] + all_facilities

        # remove overlaps between time bands
        for i in range(len(isochrone_wkts)):
            for j in range(i + 1, len(isochrone_wkts)):
                all_facilities[j] = all_facilities[j] - all_facilities[i]

        counts = [len(x) for x in all_facilities]
        # expected 3 bands
        while len(counts) < 3:
            counts.append(0)

        r_value = counts[0] * 1.0 + counts[1] * 0.75 + counts[2] * 0.5
        resilience_dict[f"R_{facility_key}"] = r_value

    if resilience_dict:
        resilience_dict["R_total"] = sum(resilience_dict.values()) / float(len(resilience_dict))
    else:
        resilience_dict["R_total"] = 0.0

    return resilience_dict


def wkt_polygon_to_overpass_format(wkt_polygon: str) -> str:
    polygon_coordinates_str = wkt_polygon.split("((")[1].split("))")[0]
    polygon_coordinates_pairs = polygon_coordinates_str.split(", ")
    overpass_pairs = [pair.split(" ")[::-1] for pair in polygon_coordinates_pairs]
    return " ".join([" ".join(p) for p in overpass_pairs])


def get_osm_data_within_polygon(polygon_wkt: str, query: str):
    url = "http://overpass-api.de/api/interpreter"
    data_query = f"""
[out:json];
(
    node[{query}](poly:"{polygon_wkt}");
    way[{query}](poly:"{polygon_wkt}");
    relation[{query}](poly:"{polygon_wkt}");
);
out body;
>;
out skel qt;
""".strip()

    response = requests.get(url, params={"data": data_query}, timeout=120)
    if response.status_code != 200:
        return []

    try:
        data = response.json()
        names = [
            elem["tags"]["name"]
            for elem in data.get("elements", [])
            if "tags" in elem and "name" in elem["tags"]
        ]
        return names
    except Exception:
        return []


def create_isochrones(token: str, coordinates, intervals, profile: str):
    try:
        url = f"https://api.mapbox.com/isochrone/v1/mapbox/{profile}/{coordinates[0]},{coordinates[1]}"
        url += "?contours_minutes=" + ",".join(map(str, intervals))
        url += "&polygons=true"
        url += f"&access_token={token}"

        response = requests.get(url, timeout=120)
        data = json.loads(response.text)

        if "features" in data:
            return data["features"]

        return []
    except Exception:
        return []
