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
import time
from datetime import datetime

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
    QgsMessageLog,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    Qgis,
    NULL,
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

        analyze_only_null = bool(getattr(self.dlg, "analyzeNullCheckBox", None) and self.dlg.analyzeNullCheckBox.isChecked())
        log_to_file = bool(getattr(self.dlg, "logFileCheckBox", None) and self.dlg.logFileCheckBox.isChecked())
        normal_timeout = int(getattr(self.dlg, "normalTimeoutSpinBox", None).value()) if getattr(self.dlg, "normalTimeoutSpinBox", None) else 240
        null_only_timeout = int(getattr(self.dlg, "nullOnlyTimeoutSpinBox", None).value()) if getattr(self.dlg, "nullOnlyTimeoutSpinBox", None) else 420

        total_operations = int(self.point_layer.featureCount())
        progress = QProgressDialog("Processing...", "Cancel", 0, total_operations, self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle("Processing...")
        progress.show()

        # Start editing
        self.point_layer.startEditing()

        # Ensure fields exist
        self._ensure_fields()

        target_field_indices = self._get_target_field_indices()
        if analyze_only_null:
            total_operations = self._count_features_to_process(target_field_indices)
            progress.setMaximum(total_operations)
            if total_operations == 0:
                progress.reset()
                QMessageBox.information(self.iface.mainWindow(), "No points to process", "No NULL points were found.")
                return

        layer_crs = self.point_layer.crs()
        transform = None
        if layer_crs.authid() != "EPSG:4326":
            transform = QgsCoordinateTransform(
                layer_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )

        processed = 0
        ok_count = 0
        skipped_count = 0
        no_data_count = 0

        overpass_timeout = normal_timeout
        if analyze_only_null:
            overpass_timeout = null_only_timeout

        overpass_cache = {}
        log_file = None
        if log_to_file:
            log_dir = os.path.join(self.plugin_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_name = datetime.now().strftime("qres_run_%Y%m%d_%H%M%S.log")
            log_file = open(os.path.join(log_dir, log_name), "a", encoding="utf-8")

        def log_message(message: str):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{timestamp}] {message}"
            self._log_message(line)
            if log_file is not None:
                log_file.write(line + "\n")
                log_file.flush()

        try:
            for feature in self.point_layer.getFeatures():
                if progress.wasCanceled():
                    break
                if feature.geometry() is None or feature.geometry().isNull():
                    continue
                if analyze_only_null and not self._should_process_feature(feature, target_field_indices):
                    continue

                pt = feature.geometry().asPoint()
                if transform is not None:
                    pt = transform.transform(pt)

                log_message(f"Processing point {processed + 1} / {total_operations}")
                resilience_dict, had_error, had_no_data = calculate_resilience(
                    pt,
                    FACILITIES,
                    PROFILES,
                    token,
                    overpass_timeout,
                    log_message,
                    overpass_cache,
                    0.2,
                    2,
                    [1, 2],
                )
                if had_error:
                    skipped_count += 1
                    log_message("Overpass or Mapbox error, skipped")
                else:
                    ok_count += 1
                    if had_no_data:
                        no_data_count += 1

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
                progress.setLabelText(f"Processing point {processed} / {total_operations}")

                if processed % 25 == 0:
                    self.point_layer.commitChanges()
                    self.point_layer.startEditing()
        finally:
            if log_file is not None:
                log_file.close()

        # Commit changes
        self.point_layer.commitChanges()
        self.point_layer.triggerRepaint()

        progress.reset()

        if progress.wasCanceled():
            QMessageBox.information(self.iface.mainWindow(), "Canceled", "Processing was canceled.")
            return

        self._log_message(f"Completed: ok = {ok_count}, skipped = {skipped_count}, no_data = {no_data_count}")
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

    def _get_target_field_indices(self):
        fields = self.point_layer.fields()
        field_names = [f"R_{key}" for key in FACILITIES.keys()] + ["R_total"]
        return [fields.indexOf(name) for name in field_names if fields.indexOf(name) >= 0]

    def _should_process_feature(self, feature, target_field_indices):
        for idx in target_field_indices:
            value = feature.attribute(idx)
            if value is None or value == NULL:
                return True
        return False

    def _count_features_to_process(self, target_field_indices):
        count = 0
        for feature in self.point_layer.getFeatures():
            if self._should_process_feature(feature, target_field_indices):
                count += 1
        return count

    def _log_message(self, message: str):
        QgsMessageLog.logMessage(message, "QRES", Qgis.Info)


# ----------------------------
# Core logic
# ----------------------------
def calculate_resilience(
    point,
    facilities,
    profiles,
    token: str,
    overpass_timeout: int,
    log_fn=None,
    overpass_cache=None,
    rate_limit_s: float = 0.0,
    max_retries: int = 0,
    backoff_steps=None,
):
    latitude = point.y()
    longitude = point.x()
    coordinates = [longitude, latitude]

    resilience_dict = {}

    def log_message(text: str):
        if log_fn is not None:
            log_fn(text)

    if overpass_cache is None:
        overpass_cache = {}
    if backoff_steps is None:
        backoff_steps = []

    facility_no_data = []

    for facility_key in facilities.keys():
        profile = profiles[facility_key]["profile"]
        intervals = profiles[facility_key]["intervals"]

        isochrones_features = create_isochrones(token, coordinates, intervals, profile, log_message)
        if isochrones_features is None:
            return _build_null_resilience_dict(facilities), True, False
        isochrone_wkts = [shapely.geometry.shape(f["geometry"]).wkt for f in isochrones_features]

        all_facilities = []
        for wkt_poly in isochrone_wkts:
            polygon = wkt_polygon_to_overpass_format(wkt_poly)
            for query in facilities[facility_key]:
                result = get_osm_data_within_polygon(
                    polygon,
                    query,
                    overpass_timeout,
                    log_message,
                    overpass_cache,
                    rate_limit_s,
                    max_retries,
                    backoff_steps,
                )
                if result is None:
                    return _build_null_resilience_dict(facilities), True, False
                all_facilities = [set(result)] + all_facilities

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
        facility_no_data.append(sum(counts) == 0)

    if resilience_dict:
        resilience_dict["R_total"] = sum(resilience_dict.values()) / float(len(resilience_dict))
    else:
        resilience_dict["R_total"] = 0.0

    had_no_data = bool(facility_no_data) and all(facility_no_data)
    return resilience_dict, False, had_no_data


def _build_null_resilience_dict(facilities):
    resilience_dict = {f"R_{key}": None for key in facilities.keys()}
    resilience_dict["R_total"] = None
    return resilience_dict


def wkt_polygon_to_overpass_format(wkt_polygon: str) -> str:
    polygon_coordinates_str = wkt_polygon.split("((")[1].split("))")[0]
    polygon_coordinates_pairs = polygon_coordinates_str.split(", ")
    overpass_pairs = [pair.split(" ")[::-1] for pair in polygon_coordinates_pairs]
    return " ".join([" ".join(p) for p in overpass_pairs])


def get_osm_data_within_polygon(
    polygon_wkt: str,
    query: str,
    timeout: int,
    log_fn=None,
    cache=None,
    rate_limit_s: float = 0.0,
    max_retries: int = 0,
    backoff_steps=None,
):
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

    if cache is None:
        cache = {}
    if backoff_steps is None:
        backoff_steps = []

    cache_key = (polygon_wkt, query)
    if cache_key in cache:
        return cache[cache_key]

    attempts = max_retries + 1
    last_error = None
    for attempt in range(attempts):
        if rate_limit_s > 0:
            time.sleep(rate_limit_s)
        try:
            response = requests.get(url, params={"data": data_query}, timeout=timeout)
        except Exception as exc:
            last_error = exc
            if log_fn is not None:
                log_fn(f"Overpass request error (attempt {attempt + 1}/{attempts}): {exc}")
            if attempt < max_retries:
                time.sleep(backoff_steps[min(attempt, len(backoff_steps) - 1)] if backoff_steps else 0)
                continue
            return None

        if response.status_code != 200:
            if log_fn is not None:
                log_fn(
                    f"Overpass request failed with status {response.status_code} (attempt {attempt + 1}/{attempts})"
                )
            if attempt < max_retries:
                time.sleep(backoff_steps[min(attempt, len(backoff_steps) - 1)] if backoff_steps else 0)
                continue
            return None

        try:
            data = response.json()
            names = [
                elem["tags"]["name"]
                for elem in data.get("elements", [])
                if "tags" in elem and "name" in elem["tags"]
            ]
            cache[cache_key] = names
            return names
        except Exception as exc:
            last_error = exc
            if log_fn is not None:
                log_fn(f"Overpass response parse error (attempt {attempt + 1}/{attempts}): {exc}")
            if attempt < max_retries:
                time.sleep(backoff_steps[min(attempt, len(backoff_steps) - 1)] if backoff_steps else 0)
                continue
            return None

    if log_fn is not None and last_error is not None:
        log_fn(f"Overpass request failed after retries: {last_error}")
    return None


def create_isochrones(token: str, coordinates, intervals, profile: str, log_fn=None):
    try:
        url = f"https://api.mapbox.com/isochrone/v1/mapbox/{profile}/{coordinates[0]},{coordinates[1]}"
        url += "?contours_minutes=" + ",".join(map(str, intervals))
        url += "&polygons=true"
        url += f"&access_token={token}"

        response = requests.get(url, timeout=120)
        if response.status_code != 200:
            if log_fn is not None:
                log_fn(f"Mapbox request failed with status {response.status_code}")
            return None
        data = json.loads(response.text)

        if "features" in data:
            return data["features"]

        return []
    except Exception as exc:
        if log_fn is not None:
            log_fn(f"Mapbox request error: {exc}")
        return None
