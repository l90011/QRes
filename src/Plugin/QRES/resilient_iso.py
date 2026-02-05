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
import logging
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
    import shapely.geometry
except Exception:
    shapely = None
    _missing_deps.append("shapely")


# ----------------------------
# Config: facilities and profiles
# ----------------------------

# Polygon simplification tolerance in degrees
# ~0.002 ≈ 220 meters at equator
# Increase this value if you still get HTTP 504 timeouts
# Higher values = faster queries but slightly less precise boundaries
POLYGON_SIMPLIFICATION_TOLERANCE = 0.002

# Overpass API timeout settings (in seconds)
# Normal mode: 60s (1 minute) - increase if queries are timing out
# NULL-only mode: 180s (3 minutes) - set in run() method
OVERPASS_TIMEOUT_NORMAL = 60

# Rate limiting: delay between Overpass API requests (seconds)
# Helps avoid HTTP 429 (Too Many Requests) errors
OVERPASS_REQUEST_DELAY = 2.0

# Retry settings for rate limiting
OVERPASS_MAX_RETRIES = 3
OVERPASS_RETRY_DELAY = 10  # Initial delay in seconds, doubles on each retry

FACILITIES = {
    "schools": ['"amenity"="school"'],
    "kindergarden": ['"amenity"="kindergarten"', '"amenity"="childcare"'],
    "transportation": ['"highway"="bus_stop"', '"railway"="station"'],
    "airports": ['"aeroway"="terminal"'],
    "leisure_and_parks": [
        '"leisure"~"."',
        '"landuse"~"park|forest|meadow|grass|recreation_ground|village_green"',
        '"natural"~"wood|grassland"',
        '"boundary"="protected_area"'
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

        # Get selected facilities from checkboxes
        selected_facility_keys = self.dlg.get_selected_facilities()
        if not selected_facility_keys:
            QMessageBox.warning(self.iface.mainWindow(), "No facilities selected", 
                "Please select at least one facility to calculate.")
            return
        
        # Filter facilities and profiles based on user selection
        selected_facilities = {k: FACILITIES[k] for k in selected_facility_keys}
        selected_profiles = {k: PROFILES[k] for k in selected_facility_keys}
        
        logging.info(f"Selected facilities: {', '.join(selected_facility_keys)}")

        layers = QgsProject.instance().mapLayersByName(selected_layer_name)
        if not layers:
            QMessageBox.critical(self.iface.mainWindow(), "Layer not found", "Selected layer was not found.")
            return

        self.point_layer = layers[0]
        if not isinstance(self.point_layer, QgsVectorLayer) or self.point_layer.geometryType() != QgsWkbTypes.PointGeometry:
            QMessageBox.critical(self.iface.mainWindow(), "Invalid layer", "Selected layer must be a point vector layer.")
            return

        # Check if we should process only NULL points
        process_only_null = self.dlg.processNullCheckBox.isChecked()
        null_only_timeout = 180 if process_only_null else None

        # Setup logging
        log_file = self._setup_logging()
        logging.info("="*60)
        logging.info(f"Starting resilience calculation at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Layer: {selected_layer_name}")
        logging.info(f"Process only NULL points: {process_only_null}")
        if process_only_null:
            logging.info(f"Using extended timeout: {null_only_timeout}s")

        # Start editing
        self.point_layer.startEditing()

        # Ensure fields exist
        self._ensure_fields()

        # Filter features based on mode
        features_to_process = []
        if process_only_null:
            r_total_idx = self.point_layer.fields().indexOf("R_total")
            for feature in self.point_layer.getFeatures():
                if feature.geometry() is None or feature.geometry().isNull():
                    continue
                if r_total_idx >= 0:
                    r_total_value = feature.attribute(r_total_idx)
                    if r_total_value is None or (isinstance(r_total_value, str) and r_total_value.upper() == "NULL"):
                        features_to_process.append(feature)
                else:
                    features_to_process.append(feature)
        else:
            features_to_process = [f for f in self.point_layer.getFeatures() if f.geometry() is not None and not f.geometry().isNull()]

        total_operations = len(features_to_process)
        if total_operations == 0:
            msg = "No NULL points found to process." if process_only_null else "No valid points found to process."
            QMessageBox.information(self.iface.mainWindow(), "No points to process", msg)
            self.point_layer.rollBack()
            logging.info(msg)
            return

        logging.info(f"Total points to process: {total_operations}")

        progress = QProgressDialog("Processing...", "Cancel", 0, total_operations, self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle("Processing...")
        progress.show()

        layer_crs = self.point_layer.crs()
        transform = None
        if layer_crs.authid() != "EPSG:4326":
            transform = QgsCoordinateTransform(
                layer_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )

        processed = 0
        succeeded = 0
        skipped = 0

        for feature in features_to_process:
            if progress.wasCanceled():
                logging.info("Processing canceled by user")
                break

            feature_id = feature.id()
            pt = feature.geometry().asPoint()
            if transform is not None:
                pt = transform.transform(pt)

            progress.setLabelText(f"Processing point {processed + 1}/{total_operations} (ID: {feature_id})")
            logging.info(f"Processing point {processed + 1}/{total_operations} (ID: {feature_id}, coords: {pt.x():.4f}, {pt.y():.4f})")

            resilience_dict = calculate_resilience(pt, selected_facilities, selected_profiles, token, null_only_timeout)

            # Update fields
            changes = {}
            
            # Update resilience fields
            for key, value in resilience_dict.items():
                idx = self.point_layer.fields().indexOf(key)
                if idx >= 0:
                    changes[idx] = value
                    
            if resilience_dict.get("R_total", 0) > 0:
                succeeded += 1
            else:
                skipped += 1

            # Always update coords
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

        # Commit changes
        self.point_layer.commitChanges()
        self.point_layer.triggerRepaint()

        progress.reset()

        # Summary
        summary_msg = f"\n{'='*60}\n"
        summary_msg += f"Processing complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        summary_msg += f"Total processed: {processed}\n"
        summary_msg += f"Succeeded: {succeeded}\n"
        summary_msg += f"Skipped (failed): {skipped}\n"
        summary_msg += f"Log file: {log_file}\n"
        summary_msg += f"{'='*60}"
        
        logging.info(summary_msg)

        if progress.wasCanceled():
            QMessageBox.information(self.iface.mainWindow(), "Canceled", 
                f"Processing was canceled.\n\nCompleted: {succeeded}, Failed: {skipped}\n\nSee log: {log_file}")
            return

        QMessageBox.information(self.iface.mainWindow(), "Operation Complete", 
            f"Processing complete!\n\nSucceeded: {succeeded}\nFailed: {skipped}\n\nSee log: {log_file}")

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
        """Ensure all possible facility fields exist (not just selected ones)."""
        existing = {f.name() for f in self.point_layer.fields()}

        to_add = []
        if "X_coord" not in existing:
            to_add.append(QgsField("X_coord", QVariant.Double))
        if "Y_coord" not in existing:
            to_add.append(QgsField("Y_coord", QVariant.Double))

        # Add fields for ALL facilities (not just selected) so layer structure is consistent
        for facility_key in FACILITIES.keys():
            field_name = f"R_{facility_key}"
            if field_name not in existing:
                to_add.append(QgsField(field_name, QVariant.Double))

        if "R_total" not in existing:
            to_add.append(QgsField("R_total", QVariant.Double))

        if to_add:
            self.point_layer.dataProvider().addAttributes(to_add)
            self.point_layer.updateFields()

    def _setup_logging(self) -> str:
        """Setup logging to file and return log file path."""
        # Use %LOCALAPPDATA%\QRES for logs (works for any user)
        localappdata = os.getenv('LOCALAPPDATA')
        if localappdata:
            log_dir = os.path.join(localappdata, "QRES")
        else:
            # Fallback to plugin directory if LOCALAPPDATA not available
            log_dir = os.path.join(self.plugin_dir, "logs")
        
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"resilience_calc_{timestamp}.log")
        
        # Clear any existing handlers
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        return log_file


# ----------------------------
# Core logic
# ----------------------------
def calculate_resilience(point, facilities, profiles, token: str, null_only_timeout: int = None) -> dict:
    """Calculate resilience values for a point.
    
    Args:
        point: QgsPoint with coordinates
        facilities: Dict of facility definitions
        profiles: Dict of profile configurations
        token: Mapbox API token
        null_only_timeout: Optional longer timeout for NULL-only mode
    
    Returns:
        dict: Resilience values, or None if calculation failed
    """
    latitude = point.y()
    longitude = point.x()
    coordinates = [longitude, latitude]

    resilience_dict = {}
    overpass_timeout = null_only_timeout if null_only_timeout else 60
    point_start_time = time.time()

    try:
        for facility_key in facilities.keys():
            facility_start_time = time.time()
            profile = profiles[facility_key]["profile"]
            intervals = profiles[facility_key]["intervals"]
            
            logging.info(f"  → Calculating {facility_key} ({profile}, {intervals} min)")

            mapbox_start = time.time()
            isochrones_features = create_isochrones(token, coordinates, intervals, profile)
            mapbox_elapsed = time.time() - mapbox_start
            logging.info(f"    Mapbox API: {mapbox_elapsed:.1f}s")
            
            if not isochrones_features:
                logging.warning(f"No isochrone features returned for {facility_key}, skipping")
                resilience_dict[f"R_{facility_key}"] = 0.0
                continue

            # Simplify polygons to avoid HTTP 414 errors (URI too long)
            # Tolerance configured in POLYGON_SIMPLIFICATION_TOLERANCE
            isochrone_wkts = []
            total_coords_before = 0
            total_coords_after = 0
            for idx, f in enumerate(isochrones_features):
                geom = shapely.geometry.shape(f["geometry"])
                original_coords = len(geom.exterior.coords)
                total_coords_before += original_coords
                # Simplify to reduce coordinate count
                simplified = geom.simplify(tolerance=POLYGON_SIMPLIFICATION_TOLERANCE, preserve_topology=True)
                simplified_coords = len(simplified.exterior.coords)
                total_coords_after += simplified_coords
                logging.debug(f"{facility_key} isochrone {idx+1}: {original_coords} -> {simplified_coords} coords")
                isochrone_wkts.append(simplified.wkt)
            
            logging.info(f"    Simplified {len(isochrones_features)} polygons: {total_coords_before} -> {total_coords_after} coords ({100*(1-total_coords_after/total_coords_before):.1f}% reduction)")

            all_facilities = []
            overpass_total_time = 0
            overpass_call_count = 0
            for band_idx, wkt_poly in enumerate(isochrone_wkts):
                polygon = wkt_polygon_to_overpass_format(wkt_poly)
                poly_length = len(polygon)
                logging.debug(f"{facility_key} band {band_idx+1}: polygon string length = {poly_length}")
                for query in facilities[facility_key]:
                    overpass_start = time.time()
                    osm_data = get_osm_data_within_polygon(polygon, query, timeout=overpass_timeout)
                    overpass_elapsed = time.time() - overpass_start
                    overpass_total_time += overpass_elapsed
                    overpass_call_count += 1
                    logging.info(f"    Overpass query {overpass_call_count}: {overpass_elapsed:.1f}s, {len(osm_data)} results")
                    all_facilities = [set(osm_data)] + all_facilities

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
            
            facility_elapsed = time.time() - facility_start_time
            logging.info(f"    {facility_key} complete: {facility_elapsed:.1f}s total ({overpass_call_count} Overpass calls, {overpass_total_time:.1f}s)")
            
            # Small delay between facilities to reduce rate limiting
            time.sleep(0.5)

        if resilience_dict:
            resilience_dict["R_total"] = sum(resilience_dict.values()) / float(len(resilience_dict))
        else:
            resilience_dict["R_total"] = 0.0

        point_elapsed = time.time() - point_start_time
        logging.info(f"  Point complete: {point_elapsed:.1f}s total")

        return resilience_dict
    
    except Exception as e:
        logging.error(f"Unexpected error in calculate_resilience: {e}")
        # Return dict with zeros instead of None to avoid complete failure
        result = {f"R_{k}": 0.0 for k in facilities.keys()}
        result["R_total"] = 0.0
        return result


def wkt_polygon_to_overpass_format(wkt_polygon: str) -> str:
    polygon_coordinates_str = wkt_polygon.split("((")[1].split("))")[0]
    polygon_coordinates_pairs = polygon_coordinates_str.split(", ")
    overpass_pairs = [pair.split(" ")[::-1] for pair in polygon_coordinates_pairs]
    return " ".join([" ".join(p) for p in overpass_pairs])


def get_osm_data_within_polygon(polygon_wkt: str, query: str, timeout: int = 240):
    """Query Overpass API for OSM data within a polygon.
    
    Returns:
        list: List of facility names, or empty list if request failed
    """
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

    try:
        response = requests.get(url, params={"data": data_query}, timeout=timeout)
        if response.status_code != 200:
            logging.warning(f"Overpass API returned status {response.status_code}")
            return []

        data = response.json()
        names = [
            elem["tags"]["name"]
            for elem in data.get("elements", [])
            if "tags" in elem and "name" in elem["tags"]
        ]
        return names
    except requests.exceptions.Timeout:
        logging.warning(f"Overpass API timeout after {timeout}s")
        return []
    except requests.exceptions.RequestException as e:
        logging.warning(f"Overpass API request failed: {e}")
        return []
    except Exception as e:
        logging.warning(f"Overpass API unexpected error: {e}")
        return []


def create_isochrones(token: str, coordinates, intervals, profile: str):
    """Create isochrones using Mapbox API.
    
    Returns:
        list: List of isochrone features, or empty list if request failed
    """
    try:
        url = f"https://api.mapbox.com/isochrone/v1/mapbox/{profile}/{coordinates[0]},{coordinates[1]}"
        url += "?contours_minutes=" + ",".join(map(str, intervals))
        url += "&polygons=true"
        url += f"&access_token={token}"

        response = requests.get(url, timeout=120)
        data = json.loads(response.text)

        if "features" in data:
            return data["features"]

        logging.warning("Mapbox API response missing 'features'")
        return []
    except requests.exceptions.Timeout:
        logging.warning("Mapbox API timeout")
        return []
    except requests.exceptions.RequestException as e:
        logging.warning(f"Mapbox API request failed: {e}")
        return []
    except Exception as e:
        logging.warning(f"Mapbox API unexpected error: {e}")
        return []
