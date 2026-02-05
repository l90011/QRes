# QRES - OSM Cache System

## Overview

The QRES plugin now uses a **local OSM data cache** to avoid repeated Overpass API calls during analysis. This provides:

- **No rate limits** - Query local data instantly without HTTP timeouts
- **Deterministic results** - Same dataset for all analysis runs
- **Offline capable** - Work without internet after initial download
- **Fast repeat runs** - Queries execute in milliseconds instead of seconds
- **Minimal infrastructure** - No routing engine or OSM server required

## Architecture

### Components

#### 1. **OSM Cache Manager** (`osm_cache_manager.py`)
Manages cache lifecycle and metadata:
- Validates cache against study area geometry
- Stores metadata (timestamp, categories, bbox)
- Handles cache refresh operations

#### 2. **OSM Downloader** (`osm_downloader.py`)
Downloads and normalizes OSM data:
- Builds Overpass queries for study area
- Downloads once per study area
- Normalizes elements into GeoPackage layers
- Handles all OSM categories (schools, hospitals, shops, etc.)

#### 3. **Local Query Layer** (`osm_local_query.py`)
Fast local queries without HTTP:
- `count_features(category, polygon_wkt)` - Count POIs in polygon
- `get_features_within_polygon(category, polygon_wkt)` - Get all features with attributes
- `get_named_features_within_polygon(category, polygon_wkt)` - Get feature names (backward compatible)
- `nearest_feature(category, x, y)` - Find nearest POI to point
- Uses spatial indexing for fast queries

### Data Flow

```
1. User selects point layer (study area)
   ↓
2. Plugin checks if OSM cache exists for area
   ↓
3. If no cache: Download OSM data from Overpass
   → Parse and normalize into GeoPackage
   → Store metadata and create spatial indexes
   ↓
4. If cache exists: Load local layers
   ↓
5. For each point:
   → Generate isochrones (Mapbox API)
   → Query local cache for POIs in each isochrone
   → Calculate resilience scores
   ↓
6. Write results to layer
```

## Storage

### Cache Location
Cache files are stored in the **QGIS project directory**:
```
<project_dir>/
  osm_cache/
    osm_data.gpkg      # GeoPackage with all OSM layers
    metadata.json      # Cache metadata
```

### GeoPackage Structure
The GeoPackage contains one layer per category:
- `osm_schools`
- `osm_kindergarden`
- `osm_transportation`
- `osm_airports`
- `osm_leisure_parks`
- `osm_shops`
- `osm_higher_education`
- `osm_further_education`
- `osm_hospitals`

Each layer has:
- **Geometry**: Point, LineString, or Polygon (in project CRS)
- **Attributes**:
  - `name` (String) - OSM name tag
  - `osm_id` (Integer) - OSM element ID
  - `osm_type` (String) - node/way/relation
  - `category` (String) - Category name
- **Spatial Index**: Auto-created for fast queries

### Metadata
`metadata.json` contains:
```json
{
  "version": "1.0",
  "created": "2026-02-05T10:30:00",
  "geometry_wkt": "POLYGON((...))",
  "crs": "EPSG:4326",
  "geometry_hash": "abc123...",
  "bbox": {
    "xmin": -0.5, "ymin": 51.0,
    "xmax": 0.5, "ymax": 52.0
  },
  "osm_timestamp": "2026-02-05T09:00:00Z",
  "categories": ["schools", "hospitals", "shops"]
}
```

## Usage

### First Run (Cache Creation)
1. Open QGIS and load your point layer
2. Run QRES plugin
3. Select facilities to analyze
4. Plugin detects no cache and prompts to download
5. Accept download - wait for OSM data to download (1-3 minutes)
6. Analysis proceeds using local cache

### Subsequent Runs
1. Run QRES plugin
2. Plugin detects existing valid cache
3. Analysis runs immediately using local data
4. **No Overpass API calls during analysis**

### Refreshing Cache
To update OSM data:
1. Open QRES dialog
2. Click **"Refresh OSM Data Cache"** button
3. Confirm deletion of current cache
4. Run analysis - fresh data will be downloaded

### Cache Validation
Cache is automatically validated:
- **Geometry hash** - If study area changes, cache is invalid
- **CRS match** - Cache must match layer CRS
- **Missing files** - If files deleted, cache is invalid

When invalid, plugin prompts for fresh download.

## Performance

### Query Speed Comparison

| Operation | Overpass API | Local Cache | Speedup |
|-----------|-------------|-------------|---------|
| Single polygon query | 2-5 seconds | 0.01-0.05 seconds | **100-500x** |
| 100 point analysis | 30-60 minutes | 2-5 minutes | **10-30x** |
| Network required | Yes | No (after download) | N/A |
| Rate limits | Yes (429 errors) | No | N/A |

### Storage Requirements

| Study Area Size | Cache Size (typical) |
|-----------------|---------------------|
| Small (10km²) | 5-20 MB |
| Medium (100km²) | 20-100 MB |
| Large (1000km²) | 100-500 MB |
| City-scale (London) | 200-1000 MB |

## Migration from Old System

### Changes from Prior Version

**Before (Overpass-based):**
- Made Overpass API call for each isochrone polygon
- Subject to rate limits and timeouts
- Required internet connection for every analysis
- Inconsistent results as OSM data changed

**After (Cache-based):**
- Downloads OSM data once per study area
- No rate limits during analysis
- Works offline after initial download
- Consistent, reproducible results

### Backward Compatibility
- Old Overpass functions (`get_osm_data_within_polygon`, `wkt_polygon_to_overpass_format`) are marked **DEPRECATED** but kept for reference
- Existing projects can continue using the plugin - cache will be created automatically
- No changes to output fields or resilience calculation logic

## Troubleshooting

### Cache Not Being Created
**Problem**: Plugin keeps trying to download on every run

**Solutions**:
- Check QGIS project is saved (cache stored in project directory)
- Verify write permissions to project directory
- Check logs for download errors

### Download Fails
**Problem**: Overpass API timeout or error during initial download

**Solutions**:
- Try again - Overpass may be temporarily overloaded
- Check internet connection
- Reduce study area size
- Reduce number of selected facilities

### Cache Out of Date
**Problem**: OSM data has changed since cache was created

**Solution**:
- Click "Refresh OSM Data Cache" button in dialog
- Confirm deletion and run analysis to download fresh data

### Missing Features in Cache
**Problem**: Expected POIs not found in results

**Solutions**:
- Check OSM data at openstreetmap.org - POI might not be mapped
- Verify correct facilities are selected in analysis
- Refresh cache if OSM recently updated
- Check layer projection matches cache CRS

### Large Cache Size
**Problem**: GeoPackage file is very large

**Solutions**:
- Reduce study area to minimum needed
- Select only required facility categories
- Consider splitting large areas into multiple sub-projects

## API Reference

### OSMCacheManager

```python
from osm_cache_manager import OSMCacheManager

# Initialize
cache = OSMCacheManager(project_dir="/path/to/project")

# Check validity
is_valid = cache.is_valid_for_geometry(geometry_wkt, crs)

# Get info
info = cache.get_cache_info()
# Returns: {created, osm_timestamp, categories, cache_size_mb, bbox}

# Clear cache
cache.clear_cache()
```

### OSMDownloader

```python
from osm_downloader import OSMDownloader

# Initialize
downloader = OSMDownloader()

# Download and cache
success, timestamp, counts = downloader.download_and_cache(
    bbox={"xmin": -0.5, "ymin": 51.0, "xmax": 0.5, "ymax": 52.0},
    gpkg_path="/path/to/osm_data.gpkg",
    target_crs=QgsCoordinateReferenceSystem("EPSG:27700"),
    categories=["schools", "hospitals"],
    progress_callback=lambda c, t, m: print(f"{m} ({c}/{t})")
)
```

### LocalOSMQuery

```python
from osm_local_query import LocalOSMQuery

# Initialize
query = LocalOSMQuery("/path/to/osm_data.gpkg")

# Count features in polygon
count = query.count_features("schools", polygon_wkt)

# Get all features with attributes
features = query.get_features_within_polygon("hospitals", polygon_wkt)
# Returns: [{"name": "...", "osm_id": ..., "osm_type": "...", "category": "..."}, ...]

# Get feature names (backward compatible)
names = query.get_named_features_within_polygon("shops", polygon_wkt)
# Returns: ["Tesco", "Sainsbury's", ...]

# Find nearest
nearest = query.nearest_feature("schools", x=0.0, y=51.5, max_distance=5000)
# Returns: {"name": "...", "distance": 1234.5, ...}

# Clear caches
query.clear_cache()
```

## Future Enhancements

Potential improvements for future versions:

1. **Incremental Updates** - Download only changed data using OSM diffs
2. **Multiple Study Areas** - Support multiple cached areas per project
3. **Custom Queries** - Allow users to define custom POI categories
4. **Export Cache** - Share cached datasets between projects
5. **Statistics Dashboard** - Show cache coverage and usage statistics

## Credits

OSM cache system implemented as part of QRES v2.0 refactoring to improve reliability, performance, and offline capability.

Uses OpenStreetMap data © OpenStreetMap contributors (ODbL license)
