# -*- coding: utf-8 -*-
"""
OSM Data Downloader
Downloads OSM data once per study area and normalizes it into local layers.
"""
from typing import Dict, List, Optional, Any
import time
import logging

try:
    import requests
except ImportError:
    requests = None

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsVectorLayer,
    QgsFeature,
    QgsField,
    QgsProject,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsPointXY,
)
from PyQt5.QtCore import QVariant


class OSMDownloader:
    """Downloads and normalizes OSM data for local storage."""
    
    # Define OSM categories and their queries
    CATEGORIES = {
        "schools": {
            "queries": ['"amenity"="school"'],
            "table": "osm_schools",
        },
        "kindergarden": {
            "queries": ['"amenity"="kindergarten"', '"amenity"="childcare"'],
            "table": "osm_kindergarden",
        },
        "transportation": {
            "queries": ['"highway"="bus_stop"', '"railway"="station"'],
            "table": "osm_transportation",
        },
        "airports": {
            "queries": ['"aeroway"="terminal"'],
            "table": "osm_airports",
        },
        "leisure_and_parks": {
            "queries": [
                '"leisure"~"."',
                '"landuse"~"park|forest|meadow|grass|recreation_ground|village_green"',
                '"natural"~"wood|grassland"',
                '"boundary"="protected_area"'
            ],
            "table": "osm_leisure_parks",
        },
        "shops": {
            "queries": ['"shop"~"."'],
            "table": "osm_shops",
        },
        "higher_education": {
            "queries": ['"amenity"="university"'],
            "table": "osm_higher_education",
        },
        "further_education": {
            "queries": ['"amenity"="college"'],
            "table": "osm_further_education",
        },
        "hospitals": {
            "queries": ['"healthcare"="hospital"'],
            "table": "osm_hospitals",
        },
    }
    
    OVERPASS_URL = "http://overpass-api.de/api/interpreter"
    OVERPASS_TIMEOUT = 180  # 3 minutes for initial download
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def _build_overpass_query(self, bbox: Dict[str, float], queries: List[str]) -> str:
        """Build Overpass QL query for given bbox and OSM tag queries."""
        # bbox format: south,west,north,east (lat,lon,lat,lon)
        bbox_str = f"{bbox['ymin']},{bbox['xmin']},{bbox['ymax']},{bbox['xmax']}"
        
        # Combine all queries with union - compact format to avoid whitespace issues
        union_parts = []
        for query in queries:
            union_parts.append(f"node[{query}]({bbox_str})")
            union_parts.append(f"way[{query}]({bbox_str})")
            union_parts.append(f"relation[{query}]({bbox_str})")
        
        # Build query - semicolons BETWEEN parts, not after each
        query = f"[out:json][timeout:{self.OVERPASS_TIMEOUT}];({';'.join(union_parts)};);out center;"
        return query
    
    def _download_overpass_data(self, query: str, max_retries: int = 3) -> Optional[Dict]:
        """
        Download data from Overpass API with retry logic.
        
        Args:
            query: Overpass QL query string
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dict with 'elements' key, or None on failure
        """
        if requests is None:
            self.logger.error("requests library not available")
            return None
        
        for attempt in range(max_retries):
            try:
                self.logger.info(f"Querying Overpass API (timeout: {self.OVERPASS_TIMEOUT}s, attempt {attempt + 1}/{max_retries})...")
                
                response = requests.post(
                    self.OVERPASS_URL,
                    data={"data": query},
                    timeout=self.OVERPASS_TIMEOUT + 10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    element_count = len(data.get('elements', []))
                    self.logger.info(f"Received {element_count} OSM elements")
                    return data
                    
                elif response.status_code == 429:
                    # Rate limit - check Retry-After header first
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                            self.logger.warning(f"Overpass API rate limit (429). Server says retry after {wait_time}s")
                        except ValueError:
                            # Retry-After might be a date, use exponential backoff
                            wait_time = 30 * (2 ** attempt)  # 30s, 60s, 120s
                            self.logger.warning(f"Overpass API rate limit (429). Exponential backoff: {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    else:
                        # Exponential backoff: 30s, 60s, 120s
                        wait_time = 30 * (2 ** attempt)
                        self.logger.warning(f"Overpass API rate limit (429). Exponential backoff: {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    
                    if attempt < max_retries - 1:
                        time.sleep(wait_time)
                        continue
                    else:
                        self.logger.error(f"Rate limit persists after {max_retries} attempts")
                        return None
                        
                elif response.status_code in [504, 503]:
                    # Server timeout/unavailable - wait before retry
                    wait_time = 30 * (attempt + 1)  # 30s, 60s, 90s
                    self.logger.warning(f"Overpass API timeout ({response.status_code}). Waiting {wait_time}s before retry...")
                    if attempt < max_retries - 1:
                        time.sleep(wait_time)
                        continue
                    else:
                        self.logger.error(f"Server timeout persists after {max_retries} attempts")
                        return None
                else:
                    self.logger.error(f"Overpass API returned status {response.status_code}")
                    if attempt < max_retries - 1:
                        time.sleep(15)  # Short wait for other errors
                        continue
                    return None
                    
            except requests.exceptions.Timeout:
                wait_time = 30 * (attempt + 1)
                self.logger.warning(f"Request timeout. Waiting {wait_time}s before retry...")
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"Timeout persists after {max_retries} attempts")
                    return None
                    
            except Exception as e:
                self.logger.error(f"Error querying Overpass API: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(15)
                    continue
                return None
        
        return None
    
    def _normalize_osm_element(self, element: Dict, nodes: Dict) -> Optional[tuple]:
        """
        Convert OSM element to (geometry, properties) tuple.
        
        Returns:
            (QgsGeometry, dict) or None if element can't be processed
        """
        elem_type = element.get('type')
        tags = element.get('tags', {})
        
        # Extract name
        name = tags.get('name', tags.get('ref', 'Unnamed'))
        
        # Create properties dict
        properties = {
            'osm_id': str(element.get('id', '')),
            'name': name,
            'type': elem_type,
        }
        
        # Add all tags as properties (flatten)
        for key, value in tags.items():
            if key not in ['name', 'ref']:
                properties[f'tag_{key}'] = str(value)
        
        # Create geometry
        geometry = None
        
        if elem_type == 'node':
            lat = element.get('lat')
            lon = element.get('lon')
            if lat and lon:
                geometry = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
                
        elif elem_type == 'way':
            # Use center point
            if 'center' in element:
                lat = element['center'].get('lat')
                lon = element['center'].get('lon')
                if lat and lon:
                    geometry = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
                    
        elif elem_type == 'relation':
            # Use center point
            if 'center' in element:
                lat = element['center'].get('lat')
                lon = element['center'].get('lon')
                if lat and lon:
                    geometry = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
        
        if geometry and not geometry.isNull():
            return (geometry, properties)
        
        return None
    
    def _create_layers_from_data(
        self,
        raw_data: Dict,
        category: str,
        table_name: str,
        gpkg_path: str,
        target_crs: QgsCoordinateReferenceSystem
    ) -> int:
        """
        Convert raw Overpass data to QGIS features and write to GPKG.
        
        Returns:
            Number of features written
        """
        elements = raw_data.get('elements', [])
        
        if not elements:
            self.logger.warning(f"No OSM elements returned for category {category}")
            return 0
        
        self.logger.info(f"Received {len(elements)} elements from Overpass for {category}")
        
        # Build nodes lookup (for way geometry resolution)
        nodes = {}
        for elem in elements:
            if elem.get('type') == 'node':
                nodes[elem['id']] = elem
        
        # Create features
        features = []
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(crs_4326, target_crs, QgsProject.instance())
        
        for element in elements:
            result = self._normalize_osm_element(element, nodes)
            if result:
                geom, props = result
                
                # Transform to target CRS
                geom.transform(transform)
                
                # Create feature
                feature = QgsFeature()
                feature.setGeometry(geom)
                
                # Set attributes (will be created dynamically)
                feature.setAttributes([
                    props.get('osm_id', ''),
                    props.get('name', ''),
                    props.get('type', ''),
                ])
                
                # Store full properties for later
                feature.props = props
                features.append(feature)
        
        if not features:
            self.logger.warning(f"No valid features created for {category} - skipping layer")
            return 0
        
        self.logger.info(f"Created {len(features)} features for {category}")
        
        # Write to GPKG
        return self._write_to_gpkg(features, table_name, gpkg_path, target_crs)
    
    def _write_to_gpkg(
        self,
        features: List[QgsFeature],
        layer_name: str,
        gpkg_path: str,
        crs: QgsCoordinateReferenceSystem
    ) -> int:
        """Write features to GeoPackage layer."""
        import os
        
        # Define fields
        fields = [
            QgsField("osm_id", QVariant.String),
            QgsField("name", QVariant.String),
            QgsField("type", QVariant.String),
        ]
        
        # Create temporary memory layer
        temp_layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "temp", "memory")
        temp_provider = temp_layer.dataProvider()
        temp_provider.addAttributes(fields)
        temp_layer.updateFields()
        
        # Add features
        temp_provider.addFeatures(features)
        
        # Determine action based on whether file exists
        if os.path.exists(gpkg_path):
            action = QgsVectorFileWriter.CreateOrOverwriteLayer
            self.logger.info(f"Writing {len(features)} features to {layer_name} in existing GPKG")
        else:
            action = QgsVectorFileWriter.CreateOrOverwriteFile
            self.logger.info(f"Creating new GPKG and writing {len(features)} features to {layer_name}")
        
        # Write to GPKG
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.actionOnExistingFile = action
        
        # writeAsVectorFormatV3 returns (errorCode, errorMessage, newFilename, newLayer)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            temp_layer,
            gpkg_path,
            QgsProject.instance().transformContext(),
            options
        )
        
        error_code = result[0]
        error_message = result[1] if len(result) > 1 else "Unknown error"
        
        if error_code == QgsVectorFileWriter.NoError:
            self.logger.info(f"Successfully wrote {layer_name} layer to GPKG")
            return len(features)
        else:
            self.logger.error(f"Error writing {layer_name} to GPKG: {error_message}")
            return 0
    
    def download_and_cache(
        self,
        bbox_4326: Dict[str, float],
        gpkg_path: str,
        target_crs: QgsCoordinateReferenceSystem,
        categories: Optional[List[str]] = None,
        progress_callback=None,
        delay_between_requests: float = 0
    ) -> Dict[str, int]:
        """
        Download OSM data for all categories and cache locally.
        
        Args:
            bbox_4326: Bounding box in EPSG:4326 (lat/lon)
            gpkg_path: Path to output GeoPackage
            target_crs: Target coordinate system for cached data
            categories: List of category keys to download (None = all)
            progress_callback: Optional callback(current, total, category_name)
            delay_between_requests: Delay in seconds between category requests (to avoid rate limits)
            
        Returns:
            Dict mapping category name to feature count
        """
        if categories is None:
            categories = list(self.CATEGORIES.keys())
        
        results = {}
        total = len(categories)
        
        for idx, category in enumerate(categories, 1):
            # Add delay between requests to avoid rate limiting (skip for first request)
            if idx > 1 and delay_between_requests > 0:
                self.logger.info(f"Waiting {delay_between_requests}s before next category (rate limit prevention)...")
                time.sleep(delay_between_requests)
            
            if progress_callback:
                progress_callback(idx, total, category)
            
            self.logger.info(f"Downloading category: {category}")
            
            cat_config = self.CATEGORIES[category]
            queries = cat_config['queries']
            table_name = cat_config['table']
            
            # Build and execute query
            self.logger.info(f"Querying Overpass for {category}...")
            query = self._build_overpass_query(bbox_4326, queries)
            raw_data = self._download_overpass_data(query, max_retries=3)
            
            if raw_data is None:
                self.logger.error(f"Failed to download data for {category} - Overpass API error")
                results[category] = 0
                continue
            
            # Normalize and cache
            feature_count = self._create_layers_from_data(
                raw_data, category, table_name, gpkg_path, target_crs
            )
            
            results[category] = feature_count
        
        return results
