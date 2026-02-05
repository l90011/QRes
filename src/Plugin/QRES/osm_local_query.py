# -*- coding: utf-8 -*-
"""
Local Query Layer
Provides fast local queries against cached OSM data without HTTP calls.
"""

import logging
from typing import List, Optional, Dict

from qgis.core import (
    QgsVectorLayer,
    QgsGeometry,
    QgsFeatureRequest,
    QgsExpression,
    QgsSpatialIndex,
    QgsRectangle,
    QgsProject,
    QgsPointXY,
)


class LocalOSMQuery:
    """Query local OSM cache layers without any network calls."""
    
    def __init__(self, gpkg_path: str):
        """Initialize local query interface.
        
        Args:
            gpkg_path: Path to GeoPackage with cached OSM data
        """
        self.gpkg_path = gpkg_path
        self._layer_cache = {}
        self._index_cache = {}
    
    def _get_layer(self, category: str) -> Optional[QgsVectorLayer]:
        """Load layer from GeoPackage (with caching).
        
        Args:
            category: Category name (e.g., "schools")
        
        Returns:
            QgsVectorLayer or None if not found
        """
        if category in self._layer_cache:
            return self._layer_cache[category]
        
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
        
        table_name = table_mapping.get(category)
        if not table_name:
            logging.warning(f"Unknown category: {category}")
            return None
        
        # Load layer from GeoPackage
        layer_uri = f"{self.gpkg_path}|layername={table_name}"
        layer = QgsVectorLayer(layer_uri, table_name, "ogr")
        
        if not layer.isValid():
            logging.debug(f"Layer {table_name} not available (no data downloaded)")
            return None
        
        self._layer_cache[category] = layer
        logging.debug(f"Loaded layer {table_name} with {layer.featureCount()} features, CRS: {layer.crs().authid()}")
        return layer
    
    def _get_spatial_index(self, category: str) -> Optional[QgsSpatialIndex]:
        """Get or create spatial index for category.
        
        Args:
            category: Category name
        
        Returns:
            QgsSpatialIndex or None
        """
        if category in self._index_cache:
            return self._index_cache[category]
        
        layer = self._get_layer(category)
        if not layer:
            return None
        
        # Build spatial index
        index = QgsSpatialIndex(layer.getFeatures())
        self._index_cache[category] = index
        logging.debug(f"Built spatial index for {category}")
        return index
    
    def count_features(self, category: str, polygon_wkt: str) -> int:
        """Count features of category within polygon.
        
        Args:
            category: Category name (e.g., "schools")
            polygon_wkt: WKT polygon string
        
        Returns:
            Number of features within polygon
        """
        layer = self._get_layer(category)
        if not layer:
            return 0
        
        index = self._get_spatial_index(category)
        polygon = QgsGeometry.fromWkt(polygon_wkt)
        
        if not polygon.isGeosValid():
            logging.warning(f"Invalid polygon geometry for {category}")
            return 0
        
        # Use spatial index for fast bbox filtering
        bbox = polygon.boundingBox()
        candidate_ids = index.intersects(bbox)
        
        # Precise intersection test
        count = 0
        request = QgsFeatureRequest().setFilterFids(candidate_ids)
        for feature in layer.getFeatures(request):
            geom = feature.geometry()
            if geom.intersects(polygon):
                count += 1
        
        return count
    
    def get_features_within_polygon(
        self,
        category: str,
        polygon_wkt: str
    ) -> List[Dict]:
        """Get all features within polygon with their attributes.
        
        Args:
            category: Category name
            polygon_wkt: WKT polygon string
        
        Returns:
            List of dicts with feature attributes
        """
        layer = self._get_layer(category)
        if not layer:
            return []
        
        index = self._get_spatial_index(category)
        polygon = QgsGeometry.fromWkt(polygon_wkt)
        
        if not polygon.isGeosValid():
            logging.warning(f"Invalid polygon geometry for {category}")
            return []
        
        # Use spatial index for fast filtering
        bbox = polygon.boundingBox()
        candidate_ids = index.intersects(bbox)
        
        logging.debug(f"      Layer {category}: {layer.featureCount()} features, polygon bbox: ({bbox.xMinimum():.2f}, {bbox.yMinimum():.2f}, {bbox.xMaximum():.2f}, {bbox.yMaximum():.2f}), spatial index found {len(candidate_ids)} candidates")
        
        # Collect features
        results = []
        request = QgsFeatureRequest().setFilterFids(candidate_ids)
        for feature in layer.getFeatures(request):
            geom = feature.geometry()
            if geom.intersects(polygon):
                attrs = {
                    "name": feature["name"] if "name" in feature.fields().names() else "",
                    "osm_id": feature["osm_id"] if "osm_id" in feature.fields().names() else "",
                    "osm_type": feature["type"] if "type" in feature.fields().names() else "",
                }
                results.append(attrs)
        
        logging.debug(f"      After intersection test: {len(results)} features match")
        return results
    
    def get_named_features_within_polygon(
        self,
        category: str,
        polygon_wkt: str
    ) -> List[str]:
        """Get names of features within polygon (for backward compatibility).
        
        Args:
            category: Category name
            polygon_wkt: WKT polygon string
        
        Returns:
            List of feature names (excluding empty names)
        """
        features = self.get_features_within_polygon(category, polygon_wkt)
        names = [f["name"] for f in features if f["name"]]
        return names
    
    def nearest_feature(
        self,
        category: str,
        point_x: float,
        point_y: float,
        max_distance: float = None
    ) -> Optional[Dict]:
        """Find nearest feature to a point.
        
        Args:
            category: Category name
            point_x: X coordinate
            point_y: Y coordinate
            max_distance: Maximum search distance (map units)
        
        Returns:
            Dict with feature attributes and distance, or None
        """
        layer = self._get_layer(category)
        if not layer:
            return None
        
        index = self._get_spatial_index(category)
        point = QgsGeometry.fromPointXY(QgsPointXY(point_x, point_y))
        
        # Search in expanding radius if max_distance not specified
        if max_distance is None:
            max_distance = 50000  # 50km default
        
        # Get candidates within max distance
        bbox = QgsRectangle(
            point_x - max_distance,
            point_y - max_distance,
            point_x + max_distance,
            point_y + max_distance
        )
        candidate_ids = index.intersects(bbox)
        
        # Find nearest
        min_distance = float('inf')
        nearest = None
        
        request = QgsFeatureRequest().setFilterFids(candidate_ids)
        for feature in layer.getFeatures(request):
            geom = feature.geometry()
            distance = point.distance(geom)
            
            if distance < min_distance and distance <= max_distance:
                min_distance = distance
                nearest = {
                    "name": feature["name"],
                    "osm_id": feature["osm_id"],
                    "osm_type": feature["osm_type"],
                    "category": feature["category"],
                    "distance": distance,
                }
        
        return nearest
    
    def features_within_bbox(
        self,
        category: str,
        xmin: float,
        ymin: float,
        xmax: float,
        ymax: float
    ) -> List[Dict]:
        """Get all features within bounding box.
        
        Args:
            category: Category name
            xmin, ymin, xmax, ymax: Bounding box coordinates
        
        Returns:
            List of feature dicts
        """
        layer = self._get_layer(category)
        if not layer:
            return []
        
        bbox = QgsRectangle(xmin, ymin, xmax, ymax)
        request = QgsFeatureRequest().setFilterRect(bbox)
        
        results = []
        for feature in layer.getFeatures(request):
            attrs = {
                "name": feature["name"],
                "osm_id": feature["osm_id"],
                "osm_type": feature["osm_type"],
                "category": feature["category"],
            }
            results.append(attrs)
        
        return results
    
    def clear_cache(self):
        """Clear layer and index caches to free memory."""
        self._layer_cache.clear()
        self._index_cache.clear()
        logging.debug("Cleared layer and index caches")
