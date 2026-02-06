# -*- coding: utf-8 -*-
"""
OSM Cache Manager
Manages local OSM data cache including metadata, validation, and storage.
"""

import os
import json
import hashlib
import platform
from datetime import datetime
from typing import Optional, Dict, Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsRectangle,
)


class OSMCacheManager:
    """Manages local OSM data cache in per-user directory."""
    
    CACHE_VERSION = "1.0"
    
    @staticmethod
    def _get_base_dir():
        """Get platform-specific per-user base directory for cache storage."""
        system = platform.system()
        if system == "Windows":
            # Use per-user AppData\Local directory
            return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~/AppData/Local')), 'QRes')
        elif system == "Darwin":  # macOS
            # Use per-user Library/Caches directory
            return os.path.expanduser('~/Library/Caches/QRes')
        else:  # Linux and others
            # Use XDG cache directory standard
            return os.path.expanduser('~/.cache/QRes')
    
    BASE_DIR = _get_base_dir.__func__()
    
    def __init__(self):
        """Initialize cache manager using system-level directory."""
        self.base_dir = self.BASE_DIR
        self.cache_dir = os.path.join(self.base_dir, "cache")
        self.metadata_file = os.path.join(self.cache_dir, "metadata.json")
        self.gpkg_file = os.path.join(self.cache_dir, "osm_data.gpkg")
        
        # Ensure cache directory exists
        try:
            if not os.path.exists(self.cache_dir):
                os.makedirs(self.cache_dir)
        except Exception as e:
            raise RuntimeError(f"Cannot create cache directory at {self.cache_dir}: {e}")
    
    def get_geometry_hash(self, geometry_wkt: str, crs: str, bbox: Optional[Dict[str, float]] = None) -> str:
        """Generate hash for geometry + CRS combination.
        
        Uses bbox coordinates if provided (more robust than WKT formatting).
        
        Args:
            geometry_wkt: WKT representation of geometry
            crs: CRS authority string (e.g., "EPSG:4326")
            bbox: Optional bbox dict with xmin, ymin, xmax, ymax
        
        Returns:
            SHA256 hash hexdigest
        """
        if bbox:
            # Use bbox coordinates directly - more robust than WKT formatting
            combined = f"{bbox['xmin']},{bbox['ymin']},{bbox['xmax']},{bbox['ymax']}|{crs}"
        else:
            # Fallback to WKT (normalize by removing all whitespace)
            normalized_wkt = ''.join(geometry_wkt.split())
            combined = f"{normalized_wkt}|{crs}"
        return hashlib.sha256(combined.encode('utf-8')).hexdigest()
    
    def save_metadata(
        self,
        geometry_wkt: str,
        crs: str,
        bbox: Dict[str, float],
        osm_timestamp: str,
        categories: list
    ) -> None:
        """Save cache metadata to disk.
        
        Args:
            geometry_wkt: WKT representation of study area
            crs: CRS authority string
            bbox: Bounding box dict with xmin, ymin, xmax, ymax
            osm_timestamp: OSM data timestamp from Overpass
            categories: List of cached categories
        """
        metadata = {
            "version": self.CACHE_VERSION,
            "created": datetime.now().isoformat(),
            "geometry_wkt": geometry_wkt,
            "crs": crs,
            "geometry_hash": self.get_geometry_hash(geometry_wkt, crs, bbox),
            "bbox": bbox,
            "osm_timestamp": osm_timestamp,
            "categories": categories,
        }
        
        with open(self.metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
    
    def load_metadata(self) -> Optional[Dict[str, Any]]:
        """Load cache metadata from disk.
        
        Returns:
            Metadata dict or None if not found
        """
        if not os.path.exists(self.metadata_file):
            return None
        
        try:
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        except Exception:
            return None
    
    def is_valid_for_geometry(self, geometry_wkt: str, crs: str, bbox: Optional[Dict[str, float]] = None) -> bool:
        """Check if cache is valid for given geometry.
        
        Args:
            geometry_wkt: WKT representation of study area
            crs: CRS authority string
            bbox: Optional bbox dict with xmin, ymin, xmax, ymax
        
        Returns:
            True if cache exists and matches geometry
        """
        metadata = self.load_metadata()
        if not metadata:
            return False
        
        # Try to use bbox from metadata if not provided
        if not bbox:
            bbox = metadata.get("bbox")
        
        current_hash = self.get_geometry_hash(geometry_wkt, crs, bbox)
        return metadata.get("geometry_hash") == current_hash
    
    def get_study_area_from_layer(self, layer) -> tuple:
        """Extract study area geometry and CRS from a layer.
        
        Args:
            layer: QgsVectorLayer
        
        Returns:
            Tuple of (geometry_wkt, crs_string, bbox_dict)
        """
        # Get layer extent
        extent = layer.extent()
        crs = layer.crs()
        
        # Convert extent to WKT polygon
        rect = QgsGeometry.fromRect(extent)
        geometry_wkt = rect.asWkt()
        
        # Create bbox dict
        bbox = {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        }
        
        crs_string = crs.authid()
        
        return geometry_wkt, crs_string, bbox
    
    def clear_cache(self) -> None:
        """Delete all cached data."""
        if os.path.exists(self.gpkg_file):
            os.remove(self.gpkg_file)
        if os.path.exists(self.metadata_file):
            os.remove(self.metadata_file)
    
    def cache_exists(self) -> bool:
        """Check if cache files exist.
        
        Returns:
            True if both metadata and geopackage exist
        """
        return (
            os.path.exists(self.metadata_file) and
            os.path.exists(self.gpkg_file)
        )
    
    def get_cache_info(self) -> Optional[Dict[str, Any]]:
        """Get human-readable cache information.
        
        Returns:
            Dict with cache info or None if no cache exists
        """
        metadata = self.load_metadata()
        if not metadata:
            return None
        
        gpkg_size = 0
        if os.path.exists(self.gpkg_file):
            gpkg_size = os.path.getsize(self.gpkg_file)
        
        return {
            "created": metadata.get("created"),
            "osm_timestamp": metadata.get("osm_timestamp"),
            "categories": metadata.get("categories", []),
            "cache_size_mb": gpkg_size / (1024 * 1024),
            "bbox": metadata.get("bbox"),
        }
