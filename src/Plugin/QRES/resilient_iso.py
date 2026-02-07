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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

# Plugin dialogs
from .resilient_iso_dialog import ResilientIsochronesDialog

# Plugin resources (Qt .qrc compiled into resources.py)
from .resources import *  # noqa: F401,F403

# OSM cache modules
from .osm_cache_manager import OSMCacheManager
from .osm_downloader import OSMDownloader
from .osm_local_query import LocalOSMQuery


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
# Used for isochrone polygons to reduce coordinate count
POLYGON_SIMPLIFICATION_TOLERANCE = 0.002

# Maximum concurrent Mapbox API calls
# Limits parallel requests to avoid overwhelming the API or network
MAX_CONCURRENT_MAPBOX_CALLS = 9

# Delay between Overpass API requests to avoid rate limiting (seconds)
# Helps prevent HTTP 429 (Too Many Requests) errors
OVERPASS_REQUEST_DELAY = 3

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
    "airports": {"profile": "driving", "intervals": [20, 40, 60]},
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
        
        # Set callbacks
        self.dlg.set_refresh_osm_callback(self._refresh_osm_cache)
        self.dlg.set_configure_token_callback(self._configure_mapbox_token)

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
        self.osm_feature_counts = {}  # Track OSM features downloaded/cached

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

        # Setup logging early
        log_file = self._setup_logging()
        operation_start_time = time.time()
        logging.info("="*60)
        logging.info(f"QRES Plugin started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Layer: {selected_layer_name}")

        # Initialize OSM cache in system directory
        cache_manager = OSMCacheManager()
        
        # Get study area from layer extent
        geometry_wkt, crs_string, bbox = cache_manager.get_study_area_from_layer(self.point_layer)
        logging.info(f"Study area CRS: {crs_string}")
        logging.info(f"Study area bbox: {bbox}")
        
        # Check if cache exists and is valid
        if not cache_manager.is_valid_for_geometry(geometry_wkt, crs_string, bbox):
            logging.info("OSM cache not found or invalid for this geometry, prompting for download...")
            
            reply = QMessageBox.question(
                self.iface.mainWindow(),
                "Download OSM Data",
                "OSM data cache is not available for this study area.\n\n"
                "This will download OpenStreetMap data once and store it locally.\n"
                "Future analyses will use the cached data.\n\n"
                "Download now?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                QMessageBox.information(
                    self.iface.mainWindow(),
                    "Operation Canceled",
                    "Cannot proceed without OSM data cache."
                )
                return
            
            # Transform bbox to EPSG:4326 for Overpass
            layer_crs = self.point_layer.crs()
            if layer_crs.authid() != "EPSG:4326":
                transform = QgsCoordinateTransform(
                    layer_crs,
                    QgsCoordinateReferenceSystem("EPSG:4326"),
                    QgsProject.instance()
                )
                rect = QgsGeometry.fromRect(self.point_layer.extent())
                rect.transform(transform)
                extent_4326 = rect.boundingBox()
                bbox_4326 = {
                    "xmin": extent_4326.xMinimum(),
                    "ymin": extent_4326.yMinimum(),
                    "xmax": extent_4326.xMaximum(),
                    "ymax": extent_4326.yMaximum(),
                }
            else:
                bbox_4326 = bbox
            
            logging.info(f"Study area bbox (EPSG:4326): {bbox_4326}")
            logging.info(f"Downloading OSM data for {len(selected_facility_keys)} categories...")
            
            # Download OSM data
            downloader = OSMDownloader()
            
            download_progress = QProgressDialog(
                "Downloading OSM data...",
                "Cancel",
                0,
                len(selected_facility_keys),
                self.iface.mainWindow()
            )
            download_progress.setWindowModality(Qt.WindowModal)
            download_progress.setWindowTitle("Downloading OSM Data")
            
            def progress_callback(current, total, message):
                download_progress.setValue(current)
                download_progress.setLabelText(message)
                QCoreApplication.processEvents()
                if download_progress.wasCanceled():
                    raise Exception("Download canceled by user")
            
            try:
                feature_counts = downloader.download_and_cache(
                    bbox_4326,
                    cache_manager.gpkg_file,
                    self.point_layer.crs(),
                    list(selected_facility_keys),
                    progress_callback,
                    delay_between_requests=OVERPASS_REQUEST_DELAY
                )
                
                download_progress.reset()
                
                # Check if any data was successfully downloaded
                total_features = sum(feature_counts.values())
                if total_features == 0:
                    QMessageBox.critical(
                        self.iface.mainWindow(),
                        "Download Failed",
                        f"Failed to download OSM data.\n\n"
                        f"Feature counts: {feature_counts}\n\n"
                        f"Check the log file for details:\n{log_file}"
                    )
                    logging.error("Download failed - no features downloaded")
                    return
                
                # Save metadata
                osm_timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
                cache_manager.save_metadata(
                    geometry_wkt,
                    crs_string,
                    bbox,
                    osm_timestamp,
                    list(feature_counts.keys())  # Only categories with data
                )
                
                # Store feature counts for display in results
                self.osm_feature_counts = feature_counts
                
                # Log download summary
                summary = "OSM data downloaded successfully: "
                summary += ", ".join([f"{cat}={count}" for cat, count in feature_counts.items()])
                logging.info(f"OSM cache created: {feature_counts}")
                logging.info(summary)
                
            except Exception as e:
                download_progress.reset()
                logging.error(f"Failed to download OSM data: {e}")
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Download Failed",
                    f"Failed to download OSM data:\n{e}"
                )
                return
        else:
            # Cache exists and is valid
            cache_info = cache_manager.get_cache_info()
            logging.info(f"Using existing OSM cache: {cache_info}")
            
            # Get feature counts from cached data
            self.osm_feature_counts = self._get_cached_feature_counts(
                cache_manager.gpkg_file,
                selected_facility_keys
            )
        
        # Initialize local query layer
        local_query = LocalOSMQuery(cache_manager.gpkg_file)

        logging.info("="*60)
        logging.info("Starting resilience calculation...")

        # Start editing
        self.point_layer.startEditing()

        # Ensure fields exist
        self._ensure_fields()

        # Filter features (skip null geometries)
        features_to_process = [
            f for f in self.point_layer.getFeatures()
            if f.geometry() is not None and not f.geometry().isNull()
        ]

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

            resilience_dict = calculate_resilience(pt, selected_facilities, selected_profiles, token, local_query, self.point_layer.crs())

            # Update fields
            changes = {}
            
            # Update resilience fields
            for key, value in resilience_dict.items():
                idx = self.point_layer.fields().indexOf(key)
                if idx >= 0:
                    changes[idx] = value
            
            logging.debug(f"  Updating {len(changes)} fields for feature {feature_id}: {list(changes.keys())}")
                    
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
                for field_idx, value in changes.items():
                    success = self.point_layer.changeAttributeValue(feature_id, field_idx, value)
                    if not success:
                        logging.error(f"  Failed to change field {field_idx} to {value} for feature {feature_id}")
                logging.debug(f"  Changed {len(changes)} attribute values")

            processed += 1
            progress.setValue(processed)
            QCoreApplication.processEvents()

        # Commit changes
        commit_success = self.point_layer.commitChanges()
        if commit_success:
            logging.info(f"Successfully committed changes to layer")
        else:
            logging.error(f"Failed to commit changes: {self.point_layer.commitErrors()}")
        self.point_layer.triggerRepaint()

        progress.reset()

        # Summary
        operation_elapsed = time.time() - operation_start_time
        summary_msg = f"\n{'='*60}\n"
        summary_msg += f"Processing complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        summary_msg += f"Total operation time: {operation_elapsed:.1f} seconds ({operation_elapsed/60:.1f} minutes)\n"
        summary_msg += f"Total processed: {processed}\n"
        summary_msg += f"Succeeded: {succeeded}\n"
        summary_msg += f"Skipped (failed): {skipped}\n"
        summary_msg += f"Log file: {log_file}\n"
        summary_msg += f"{'='*60}"
        
        logging.info(summary_msg)

        if progress.wasCanceled():
            QMessageBox.information(self.iface.mainWindow(), "Canceled", 
                f"Processing was canceled.\n\n"
                f"Time elapsed: {operation_elapsed/60:.1f} minutes ({operation_elapsed:.0f}s)\n"
                f"Completed: {succeeded}, Failed: {skipped}\n\n")
            return

        # Build OSM features summary
        osm_summary = ""
        if self.osm_feature_counts:
            total_osm_features = sum(self.osm_feature_counts.values())
            osm_summary = f"\n\nOSM Features Used ({total_osm_features} total):\n"
            # Create a nice formatted list
            facility_labels = {
                "schools": "Schools",
                "kindergarden": "Kindergartens",
                "transportation": "Transportation",
                "airports": "Airports",
                "leisure_and_parks": "Leisure & Parks",
                "shops": "Shops",
                "higher_education": "Higher Education",
                "further_education": "Further Education",
                "hospitals": "Hospitals"
            }
            for cat, count in sorted(self.osm_feature_counts.items()):
                label = facility_labels.get(cat, cat)
                osm_summary += f"  • {label}: {count}\n"
        
        QMessageBox.information(self.iface.mainWindow(), "Operation Complete", 
            f"Processing complete!\n\n"
            f"Time elapsed: {operation_elapsed/60:.1f} minutes ({operation_elapsed:.0f}s)\n"
            f"Succeeded: {succeeded}\nFailed: {skipped}"
            f"{osm_summary}\n")

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
    
    def _configure_mapbox_token(self):
        """Allow user to reconfigure/update the Mapbox token."""
        settings = QSettings()
        current_token = settings.value(self.SETTINGS_KEY, "", type=str).strip()
        
        dialog = MapboxTokenDialog(self.iface.mainWindow())
        
        # Pre-fill with current token if it exists
        if current_token:
            dialog.lineEdit.setText(current_token)
        
        result = dialog.exec_()
        if result != QDialog.Accepted:
            return
        
        new_token = dialog.lineEdit.text().strip()
        if not new_token:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Invalid Token",
                "Please enter a valid MapBox Token.",
            )
            return
        
        settings.setValue(self.SETTINGS_KEY, new_token)
        QMessageBox.information(
            self.iface.mainWindow(),
            "Token Updated",
            "Your Mapbox token has been successfully updated.",
        )

    def _populate_point_layers_combo(self):
        self.dlg.layersComboBox.clear()

        layer_names = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PointGeometry:
                layer_names.append(layer.name())

        layer_names.sort(key=lambda s: s.lower())
        self.dlg.layersComboBox.addItems(layer_names)

    def _get_cached_feature_counts(self, gpkg_path: str, categories: list) -> dict:
        """Get feature counts from cached OSM data.
        
        Args:
            gpkg_path: Path to the GeoPackage file
            categories: List of category keys to check
            
        Returns:
            Dict mapping category name to feature count
        """
        from qgis.core import QgsVectorLayer
        
        feature_counts = {}
        
        # Map category to table name
        table_mapping = {
            "schools": "osm_schools",
            "kindergarden": "osm_kindergarden",
            "transportation": "osm_transportation",
            "airports": "osm_airports",
            "leisure_and_parks": "osm_leisure_parks",
            "shops": "osm_shops",
            "higher_education": "osm_higher_education",
            "further_education": "osm_further_education",
            "hospitals": "osm_hospitals",
        }
        
        for category in categories:
            table_name = table_mapping.get(category)
            if not table_name:
                continue
            
            # Load layer from GeoPackage
            layer_uri = f"{gpkg_path}|layername={table_name}"
            layer = QgsVectorLayer(layer_uri, table_name, "ogr")
            
            if layer.isValid():
                feature_counts[category] = layer.featureCount()
            else:
                feature_counts[category] = 0
        
        return feature_counts

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
    
    def _refresh_osm_cache(self):
        """Refresh OSM cache by deleting existing cache and prompting for re-download."""
        cache_manager = OSMCacheManager()
        
        # Check if cache exists
        if not cache_manager.cache_exists():
            QMessageBox.information(
                self.iface.mainWindow(),
                "No Cache Found",
                "No OSM cache exists yet. Cache will be created when you run the analysis."
            )
            return
        
        # Get cache info to show user
        cache_info = cache_manager.get_cache_info()
        if cache_info:
            info_text = (
                f"Current cache information:\n\n"
                f"Created: {cache_info['created']}\n"
                f"OSM Timestamp: {cache_info['osm_timestamp']}\n"
                f"Categories: {', '.join(cache_info['categories'])}\n"
                f"Size: {cache_info['cache_size_mb']:.2f} MB\n\n"
                f"Are you sure you want to delete this cache?\n"
                f"You will need to download OSM data again on the next analysis."
            )
        else:
            info_text = (
                "Delete the existing OSM cache?\n\n"
                "You will need to download OSM data again on the next analysis."
            )
        
        reply = QMessageBox.question(
            self.iface.mainWindow(),
            "Refresh OSM Cache",
            info_text,
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            cache_manager.clear_cache()
            QMessageBox.information(
                self.iface.mainWindow(),
                "Cache Cleared",
                "OSM cache has been deleted successfully.\n\n"
                "Fresh data will be downloaded on the next analysis."
            )
            logging.info("OSM cache cleared by user")
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Error",
                f"Failed to clear OSM cache:\n{e}"
            )
            logging.error(f"Failed to clear OSM cache: {e}")

    def _setup_logging(self) -> str:
        """Setup logging to file and return log file path."""
        # Use platform-specific per-user directory for logging
        import platform
        system = platform.system()
        if system == "Windows":
            log_dir = os.path.join(os.environ.get('PROGRAMDATA', 'C:\\ProgramData'), 'QRes', 'logs')
        elif system == "Darwin":  # macOS
            log_dir = os.path.expanduser('~/Library/Logs/QRes')
        else:  # Linux and others
            log_dir = os.path.expanduser('~/.cache/QRes/logs')
        
        try:
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
        except Exception as e:
            # Fallback to plugin directory if ProgramData not accessible
            log_dir = os.path.join(self.plugin_dir, "logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"resilience_calc_{timestamp}.log")
        
        # Clear any existing handlers and close them properly
        for handler in logging.root.handlers[:]:
            handler.close()
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
def _fetch_isochrones_for_facility(facility_key: str, coordinates, profile: str, intervals, token: str) -> tuple:
    """Fetch isochrones for a single facility (designed for parallel execution).
    
    Args:
        facility_key: Facility identifier
        coordinates: [longitude, latitude]
        profile: Mapbox profile (walking/driving)
        intervals: List of time intervals
        token: Mapbox API token
    
    Returns:
        tuple: (facility_key, isochrones_features, elapsed_time)
    """
    start_time = time.time()
    isochrones_features = create_isochrones(token, coordinates, intervals, profile)
    elapsed_time = time.time() - start_time
    return (facility_key, isochrones_features, elapsed_time)


def calculate_resilience(point, facilities, profiles, token: str, local_query: LocalOSMQuery, layer_crs: QgsCoordinateReferenceSystem) -> dict:
    """Calculate resilience values for a point using local OSM cache.
    
    Args:
        point: QgsPoint with coordinates
        facilities: Dict of facility definitions
        profiles: Dict of profile configurations
        token: Mapbox API token
        local_query: LocalOSMQuery instance for querying cached data
    
    Returns:
        dict: Resilience values
    """
    latitude = point.y()
    longitude = point.x()
    coordinates = [longitude, latitude]

    resilience_dict = {}
    point_start_time = time.time()

    try:
        # Step 1: Fetch all isochrones in parallel
        logging.info(f"  → Fetching isochrones for {len(facilities)} facilities in parallel...")
        parallel_start = time.time()
        
        futures_to_facility = {}
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_MAPBOX_CALLS) as executor:
            for facility_key in facilities.keys():
                profile = profiles[facility_key]["profile"]
                intervals = profiles[facility_key]["intervals"]
                
                future = executor.submit(
                    _fetch_isochrones_for_facility,
                    facility_key, coordinates, profile, intervals, token
                )
                futures_to_facility[future] = facility_key
        
            # Collect results as they complete
            facility_isochrones = {}
            for future in as_completed(futures_to_facility):
                facility_key = futures_to_facility[future]
                try:
                    fac_key, isochrones_features, mapbox_elapsed = future.result()
                    facility_isochrones[fac_key] = isochrones_features
                    logging.info(f"    ✓ {fac_key}: {mapbox_elapsed:.1f}s")
                except Exception as e:
                    logging.error(f"    ✗ {facility_key} failed: {e}")
                    facility_isochrones[facility_key] = []
        
        parallel_elapsed = time.time() - parallel_start
        logging.info(f"  Parallel fetch complete: {parallel_elapsed:.1f}s (avg {parallel_elapsed/len(facilities):.1f}s per facility)")
        
        # Step 2: Process each facility's isochrones sequentially
        for facility_key in facilities.keys():
            facility_start_time = time.time()
            profile = profiles[facility_key]["profile"]
            intervals = profiles[facility_key]["intervals"]
            
            logging.info(f"  → Processing {facility_key} ({profile}, {intervals} min)")
            
            isochrones_features = facility_isochrones.get(facility_key, [])
            
            if not isochrones_features:
                logging.warning(f"No isochrone features returned for {facility_key}, skipping")
                resilience_dict[f"R_{facility_key}"] = 0.0
                continue

            # Simplify polygons for faster local queries
            # Tolerance configured in POLYGON_SIMPLIFICATION_TOLERANCE
            isochrone_wkts = []
            total_coords_before = 0
            total_coords_after = 0
            
            # Setup CRS transformation: Mapbox returns EPSG:4326, transform to layer CRS
            crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform_to_layer_crs = QgsCoordinateTransform(crs_4326, layer_crs, QgsProject.instance())
            
            for idx, f in enumerate(isochrones_features):
                geom = shapely.geometry.shape(f["geometry"])
                original_coords = len(geom.exterior.coords)
                total_coords_before += original_coords
                # Simplify to reduce coordinate count
                simplified = geom.simplify(tolerance=POLYGON_SIMPLIFICATION_TOLERANCE, preserve_topology=True)
                simplified_coords = len(simplified.exterior.coords)
                total_coords_after += simplified_coords
                logging.debug(f"{facility_key} isochrone {idx+1}: {original_coords} -> {simplified_coords} coords")
                
                # Transform from EPSG:4326 to layer CRS before querying OSM cache
                qgs_geom = QgsGeometry.fromWkt(simplified.wkt)
                bbox_before = qgs_geom.boundingBox()
                logging.debug(f"    Isochrone {idx+1} bbox BEFORE transform (EPSG:4326): {bbox_before.xMinimum():.4f}, {bbox_before.yMinimum():.4f}, {bbox_before.xMaximum():.4f}, {bbox_before.yMaximum():.4f}")
                
                qgs_geom.transform(transform_to_layer_crs)
                bbox_after = qgs_geom.boundingBox()
                logging.debug(f"    Isochrone {idx+1} bbox AFTER transform ({layer_crs.authid()}): {bbox_after.xMinimum():.2f}, {bbox_after.yMinimum():.2f}, {bbox_after.xMaximum():.2f}, {bbox_after.yMaximum():.2f}")
                
                isochrone_wkts.append(qgs_geom.asWkt())
            
            logging.info(f"    Simplified {len(isochrones_features)} polygons: {total_coords_before} -> {total_coords_after} coords ({100*(1-total_coords_after/total_coords_before):.1f}% reduction)")

            # Query local cache for each isochrone band
            all_facilities = []
            query_total_time = 0
            for band_idx, wkt_poly in enumerate(isochrone_wkts):
                query_start = time.time()
                
                # Query local cache (no HTTP calls!)
                osm_names = local_query.get_named_features_within_polygon(facility_key, wkt_poly)
                
                query_elapsed = time.time() - query_start
                query_total_time += query_elapsed
                logging.info(f"    Local query {band_idx+1}: {query_elapsed:.3f}s, {len(osm_names)} results")
                
                all_facilities.append(set(osm_names))

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
            logging.info(f"    R_{facility_key} = {r_value:.2f} (counts: {counts})")
            
            facility_elapsed = time.time() - facility_start_time
            logging.info(f"    {facility_key} complete: {facility_elapsed:.1f}s total ({len(isochrone_wkts)} local queries, {query_total_time:.3f}s)")

        if resilience_dict:
            resilience_dict["R_total"] = sum(resilience_dict.values()) / float(len(resilience_dict))
        else:
            resilience_dict["R_total"] = 0.0

        point_elapsed = time.time() - point_start_time
        logging.debug(f"  Resilience values: {resilience_dict}")
        logging.info(f"  Point complete: {point_elapsed:.1f}s total")

        return resilience_dict
    
    except Exception as e:
        logging.error(f"Unexpected error in calculate_resilience: {e}")
        # Return dict with zeros instead of None to avoid complete failure
        result = {f"R_{k}": 0.0 for k in facilities.keys()}
        result["R_total"] = 0.0
        return result


def wkt_polygon_to_overpass_format(wkt_polygon: str) -> str:
    """DEPRECATED: Convert WKT polygon to Overpass format.
    
    This function is no longer used as the plugin now uses local OSM cache
    instead of making Overpass API calls during analysis.
    Kept for backward compatibility only.
    """
    polygon_coordinates_str = wkt_polygon.split("((")[1].split("))")[0]
    polygon_coordinates_pairs = polygon_coordinates_str.split(", ")
    overpass_pairs = [pair.split(" ")[::-1] for pair in polygon_coordinates_pairs]
    return " ".join([" ".join(p) for p in overpass_pairs])


def get_osm_data_within_polygon(polygon_wkt: str, query: str, timeout: int = 240):
    """DEPRECATED: Query Overpass API for OSM data within a polygon.
    
    This function is no longer used as the plugin now uses local OSM cache
    instead of making Overpass API calls during analysis.
    Kept for backward compatibility only.
    
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
        logging.warning(f"Response status: {response.status_code}, body: {response.text[:500]}")
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
