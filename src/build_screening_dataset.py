#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Optional

import requests


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "data" / "config" / "sources.json"
RULES_PATH = ROOT / "data" / "config" / "classification_rules.json"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "coney-island-buildings.geojson"
METADATA_PATH = PROCESSED_DIR / "coney-island-buildings.metadata.json"

FEET_PER_DEGREE_LATITUDE = 364_000
SPATIAL_CELL_SIZE_DEGREES = 0.004
BFE_LINE_SEARCH_DISTANCE_FT = 800

STATUS_LABELS = {
    "measured_current_standard_screening": "Measured: meets BFE plus freeboard",
    "measured_base_flood_elevation_screening": "Measured: meets base flood elevation",
    "measured_below_base_flood_elevation": "Measured: below base flood elevation",
    "estimated_current_standard_screening": "Estimated: likely meets BFE plus freeboard",
    "estimated_base_flood_elevation_screening": "Estimated: likely meets base flood elevation",
    "estimated_below_base_flood_elevation": "Estimated: likely below base flood elevation",
    "unverified_elevation": "Unverified elevation source",
    "no_flood_context": "Flood context unavailable",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def request_json(url: str, params: Optional[dict[str, Any]] = None) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(4):
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            last_error = error
            if attempt == 3:
                break
            time.sleep(1.5 * (attempt + 1))
    raise last_error if last_error else RuntimeError(f"Unable to fetch {url}")


def bbox_where(bbox: dict[str, float]) -> str:
    return (
        "within_box(the_geom,"
        f"{bbox['north']},{bbox['west']},{bbox['south']},{bbox['east']})"
    )


def fetch_bes_records(url: str, bbox: dict[str, float]) -> dict[str, dict[str, Any]]:
    records_by_bin: dict[str, dict[str, Any]] = {}
    limit = 50000
    offset = 0
    fields = [
        "bin",
        "bbl",
        "address",
        "z_grade",
        "z_floor",
        "subgrade",
        "notes1",
        "latitude",
        "longitude",
        "ntaname",
    ]

    while True:
        params = {
            "$select": ",".join(fields),
            "$where": bbox_where(bbox),
            "$limit": limit,
            "$offset": offset,
            "$order": "bin",
        }
        batch = request_json(url, params)
        if not batch:
            break
        for record in batch:
            bin_value = normalize_bin(record.get("bin"))
            if bin_value:
                records_by_bin[bin_value] = record
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.2)

    return records_by_bin


def fetch_footprint_features(url: str, bbox: dict[str, float]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    page_size = 2000
    offset = 0
    geometry = {
        "xmin": bbox["west"],
        "ymin": bbox["south"],
        "xmax": bbox["east"],
        "ymax": bbox["north"],
        "spatialReference": {"wkid": 4326},
    }

    while True:
        params = {
            "f": "geojson",
            "where": "1=1",
            "outFields": "BIN,BASE_BBL,MPLUTO_BBL,CNSTRCT_YR,HEIGHTROOF,GROUNDELEV",
            "geometry": json.dumps(geometry, separators=(",", ":")),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outSR": 4326,
            "returnGeometry": "true",
            "geometryPrecision": 6,
            "resultRecordCount": page_size,
            "resultOffset": offset,
        }
        collection = request_json(url, params)
        batch = collection.get("features", [])
        if not batch:
            break
        features.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(0.2)

    return features


def fetch_arcgis_layer_features(
    service_url: str,
    layer_id: int,
    bbox: dict[str, float],
    out_fields: str,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    page_size = 2000
    offset = 0
    geometry = {
        "xmin": bbox["west"],
        "ymin": bbox["south"],
        "xmax": bbox["east"],
        "ymax": bbox["north"],
        "spatialReference": {"wkid": 4326},
    }
    url = f"{service_url.rstrip('/')}/{layer_id}/query"

    while True:
        params = {
            "f": "geojson",
            "where": "1=1",
            "outFields": out_fields,
            "geometry": json.dumps(geometry, separators=(",", ":")),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outSR": 4326,
            "returnGeometry": "true",
            "geometryPrecision": 6,
            "resultRecordCount": page_size,
            "resultOffset": offset,
        }
        collection = request_json(url, params)
        batch = collection.get("features", [])
        if not batch:
            break
        features.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(0.2)

    return features


def normalize_bin(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value).strip()


def normalize_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def parse_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_positive_float(value: Any) -> Optional[float]:
    number = parse_float(value)
    if number is None or number <= -999:
        return None
    return number


def note_quality_flags(notes: Optional[str]) -> list[str]:
    if not notes:
        return []
    normalized = notes.lower()
    flags: list[str] = []
    if "vacant lot" in normalized:
        flags.append("bes_note_vacant_lot")
    if "not visible" in normalized or "not visible from street" in normalized:
        flags.append("bes_note_property_not_visible")
    if "obstruction" in normalized or "obstructed" in normalized:
        flags.append("bes_note_obstruction")
    if "construction" in normalized:
        flags.append("bes_note_construction")
    if "no address plate" in normalized or "no address" in normalized:
        flags.append("bes_note_no_address")
    return flags


def bes_quality(
    bes: dict[str, Any],
    z_grade: Optional[float],
    z_floor: Optional[float],
    first_floor: Optional[float],
) -> tuple[list[str], bool]:
    flags: list[str] = []
    if not bes:
        return ["missing_bes_match"], False

    notes = normalize_text(bes.get("notes1"))
    flags.extend(note_quality_flags(notes))

    if z_grade is None or z_floor is None:
        flags.append("missing_bes_elevation")
    if z_grade == 0 and z_floor == 0:
        flags.append("suspect_zero_elevation")
    elif first_floor == 0:
        flags.append("at_grade_first_floor")
    if first_floor is not None and first_floor < 0:
        flags.append("negative_first_floor_height")
    if first_floor is not None and first_floor > 35:
        flags.append("extreme_first_floor_height")

    blocking_flags = {
        "missing_bes_elevation",
        "suspect_zero_elevation",
        "negative_first_floor_height",
        "extreme_first_floor_height",
    }
    is_measured = first_floor is not None and not any(flag in blocking_flags for flag in flags)
    return sorted(set(flags)), is_measured


def polygon_ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    if len(ring) < 3:
        lon = sum(point[0] for point in ring) / max(len(ring), 1)
        lat = sum(point[1] for point in ring) / max(len(ring), 1)
        return lon, lat

    area_twice = 0.0
    centroid_lon = 0.0
    centroid_lat = 0.0
    for index in range(len(ring) - 1):
        lon1, lat1 = ring[index][:2]
        lon2, lat2 = ring[index + 1][:2]
        cross = lon1 * lat2 - lon2 * lat1
        area_twice += cross
        centroid_lon += (lon1 + lon2) * cross
        centroid_lat += (lat1 + lat2) * cross

    if abs(area_twice) < 1e-12:
        lon = sum(point[0] for point in ring) / len(ring)
        lat = sum(point[1] for point in ring) / len(ring)
        return lon, lat

    return centroid_lon / (3 * area_twice), centroid_lat / (3 * area_twice)


def polygon_area_abs(ring: list[list[float]]) -> float:
    area_twice = 0.0
    for index in range(len(ring) - 1):
        lon1, lat1 = ring[index][:2]
        lon2, lat2 = ring[index + 1][:2]
        area_twice += lon1 * lat2 - lon2 * lat1
    return abs(area_twice) / 2


def geometry_centroid(geometry: Optional[dict[str, Any]]) -> Optional[tuple[float, float]]:
    if not geometry:
        return None
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and coordinates:
        return polygon_ring_centroid(coordinates[0])
    if geometry_type == "MultiPolygon" and coordinates:
        largest_polygon = max(coordinates, key=lambda polygon: polygon_area_abs(polygon[0]))
        return polygon_ring_centroid(largest_polygon[0])
    if geometry_type == "Point" and coordinates:
        return coordinates[0], coordinates[1]
    return None


def iter_geometry_points(geometry: Optional[dict[str, Any]]) -> list[list[float]]:
    if not geometry:
        return []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Point":
        return [coordinates]
    if geometry_type == "LineString":
        return coordinates
    if geometry_type == "MultiLineString":
        return [point for line in coordinates for point in line]
    if geometry_type == "Polygon":
        return [point for ring in coordinates for point in ring]
    if geometry_type == "MultiPolygon":
        return [point for polygon in coordinates for ring in polygon for point in ring]
    return []


def geometry_bbox(geometry: Optional[dict[str, Any]]) -> Optional[tuple[float, float, float, float]]:
    points = iter_geometry_points(geometry)
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def bbox_contains_point(
    bbox: Optional[tuple[float, float, float, float]],
    point: tuple[float, float],
) -> bool:
    if bbox is None:
        return True
    lon, lat = point
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


def attach_geometry_bboxes(features: list[dict[str, Any]]) -> None:
    for feature in features:
        feature["_bbox"] = geometry_bbox(feature.get("geometry"))


def cells_for_bbox(bbox: Optional[tuple[float, float, float, float]]) -> list[tuple[int, int]]:
    if bbox is None:
        return []
    west, south, east, north = bbox
    west_cell = int(math.floor(west / SPATIAL_CELL_SIZE_DEGREES))
    east_cell = int(math.floor(east / SPATIAL_CELL_SIZE_DEGREES))
    south_cell = int(math.floor(south / SPATIAL_CELL_SIZE_DEGREES))
    north_cell = int(math.floor(north / SPATIAL_CELL_SIZE_DEGREES))
    return [
        (cell_x, cell_y)
        for cell_x in range(west_cell, east_cell + 1)
        for cell_y in range(south_cell, north_cell + 1)
    ]


def build_feature_spatial_index(features: list[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    index: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for feature in features:
        bbox = feature.get("_bbox") or geometry_bbox(feature.get("geometry"))
        for cell in cells_for_bbox(bbox):
            index[cell].append(feature)
    return index


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    previous_lon, previous_lat = ring[-1][:2]
    for point in ring:
        current_lon, current_lat = point[:2]
        intersects = (current_lat > lat) != (previous_lat > lat)
        if intersects:
            projected_lon = (previous_lon - current_lon) * (lat - current_lat) / (
                previous_lat - current_lat
            ) + current_lon
            if lon < projected_lon:
                inside = not inside
        previous_lon, previous_lat = current_lon, current_lat
    return inside


def point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    if not polygon or not point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in polygon[1:])


def point_in_geometry(point: tuple[float, float], geometry: Optional[dict[str, Any]]) -> bool:
    if not geometry:
        return False
    lon, lat = point
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return point_in_polygon(lon, lat, coordinates)
    if geometry_type == "MultiPolygon":
        return any(point_in_polygon(lon, lat, polygon) for polygon in coordinates)
    return False


def project_to_feet(point: tuple[float, float], reference_lat: float) -> tuple[float, float]:
    lon, lat = point
    return (
        lon * FEET_PER_DEGREE_LATITUDE * math.cos(math.radians(reference_lat)),
        lat * FEET_PER_DEGREE_LATITUDE,
    )


def point_to_segment_distance_ft(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    reference_lat = point[1]
    px, py = project_to_feet(point, reference_lat)
    sx, sy = project_to_feet(start, reference_lat)
    ex, ey = project_to_feet(end, reference_lat)
    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    closest_x = sx + t * dx
    closest_y = sy + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def line_distance_ft(point: tuple[float, float], coordinates: list[list[float]]) -> float:
    if len(coordinates) < 2:
        return math.inf
    distance = math.inf
    for index in range(len(coordinates) - 1):
        start = tuple(coordinates[index][:2])
        end = tuple(coordinates[index + 1][:2])
        distance = min(distance, point_to_segment_distance_ft(point, start, end))
    return distance


def geometry_distance_ft(point: tuple[float, float], geometry: Optional[dict[str, Any]]) -> float:
    if not geometry:
        return math.inf
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "LineString":
        return line_distance_ft(point, coordinates)
    if geometry_type == "MultiLineString":
        return min((line_distance_ft(point, line) for line in coordinates), default=math.inf)
    return math.inf


def find_flood_zone(
    point: Optional[tuple[float, float]],
    flood_zone_features: list[dict[str, Any]] | dict[tuple[int, int], list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    if point is None:
        return None

    candidates: list[dict[str, Any]]
    if isinstance(flood_zone_features, dict):
        candidates = flood_zone_features.get(spatial_cell(point), [])
    else:
        candidates = flood_zone_features

    matches = [
        feature
        for feature in candidates
        if bbox_contains_point(feature.get("_bbox") or geometry_bbox(feature.get("geometry")), point)
        if point_in_geometry(point, feature.get("geometry"))
    ]
    if not matches:
        return None
    sfha_matches = [
        feature for feature in matches if normalize_text(feature.get("properties", {}).get("SFHA_TF")) == "T"
    ]
    return sfha_matches[0] if sfha_matches else matches[0]


def find_nearest_bfe_line(
    point: Optional[tuple[float, float]],
    bfe_line_features: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[float]]:
    if point is None:
        return None, None

    closest_feature = None
    closest_distance = math.inf
    for feature in bfe_line_features:
        distance = geometry_distance_ft(point, feature.get("geometry"))
        if distance < closest_distance:
            closest_feature = feature
            closest_distance = distance

    if closest_feature is None or closest_distance > BFE_LINE_SEARCH_DISTANCE_FT:
        return None, None
    return closest_feature, round(closest_distance, 1)


def bfe_context(
    point: Optional[tuple[float, float]],
    ground_elevation_ft: Optional[float],
    flood_zone_features: list[dict[str, Any]] | dict[tuple[int, int], list[dict[str, Any]]],
    bfe_line_features: list[dict[str, Any]],
) -> dict[str, Any]:
    zone = find_flood_zone(point, flood_zone_features)
    zone_props = zone.get("properties", {}) if zone else {}

    bfe_value = parse_positive_float(zone_props.get("STATIC_BFE"))
    bfe_source = "FEMA NFHL Flood Hazard Zones STATIC_BFE" if bfe_value is not None else None
    bfe_datum = normalize_text(zone_props.get("V_DATUM"))
    nearest_distance = None

    if bfe_value is None:
        line, nearest_distance = find_nearest_bfe_line(point, bfe_line_features)
        line_props = line.get("properties", {}) if line else {}
        bfe_value = parse_positive_float(line_props.get("ELEV"))
        if bfe_value is not None:
            bfe_source = "FEMA NFHL Base Flood Elevations line"
            bfe_datum = normalize_text(line_props.get("V_DATUM"))

    bfe_above_grade = None
    if bfe_value is not None and ground_elevation_ft is not None:
        bfe_above_grade = round(bfe_value - ground_elevation_ft, 2)

    return {
        "flood_zone": normalize_text(zone_props.get("FLD_ZONE")),
        "flood_zone_subtype": normalize_text(zone_props.get("ZONE_SUBTY")),
        "special_flood_hazard_area": normalize_text(zone_props.get("SFHA_TF")),
        "base_flood_elevation_ft": bfe_value,
        "base_flood_elevation_datum": bfe_datum,
        "base_flood_elevation_source": bfe_source,
        "base_flood_elevation_line_distance_ft": nearest_distance,
        "bfe_above_grade_ft": bfe_above_grade,
    }


def classify(
    display_height_ft: Optional[float],
    elevation_confidence: str,
    bfe_above_grade_ft: Optional[float],
    rules: dict[str, Any],
) -> tuple[str, str]:
    if display_height_ft is None or elevation_confidence == "unverified":
        status = "unverified_elevation"
        return status, STATUS_LABELS[status]
    if bfe_above_grade_ft is None:
        status = "no_flood_context"
        return status, STATUS_LABELS[status]

    current_freeboard = float(rules["current_freeboard_ft"])
    current_target = bfe_above_grade_ft + current_freeboard
    bfe_target = bfe_above_grade_ft
    prefix = "estimated" if elevation_confidence == "estimated" else "measured"

    if display_height_ft >= current_target:
        status = f"{prefix}_current_standard_screening"
    elif display_height_ft >= bfe_target:
        status = f"{prefix}_base_flood_elevation_screening"
    else:
        status = f"{prefix}_below_base_flood_elevation"
    return status, STATUS_LABELS[status]


def construction_year_bucket(value: Any) -> str:
    year = parse_float(value)
    if year is None or year <= 0:
        return "unknown_year"
    year_int = int(year)
    if year_int < 1920:
        return "pre_1920"
    if year_int < 1946:
        return "1920_1945"
    if year_int < 1984:
        return "1946_1983"
    if year_int < 2008:
        return "1984_2007"
    return "2008_plus"


def height_bucket(value: Any) -> str:
    height = parse_float(value)
    if height is None or height <= 0:
        return "unknown_roof"
    if height < 18:
        return "low_roof"
    if height < 35:
        return "mid_roof"
    if height < 65:
        return "tall_roof"
    return "tower_roof"


def ground_bucket(value: Any) -> str:
    ground = parse_float(value)
    if ground is None:
        return "unknown_ground"
    return f"ground_{int(math.floor(ground / 2) * 2)}"


def spatial_cell(point: Optional[tuple[float, float]]) -> Optional[tuple[int, int]]:
    if point is None:
        return None
    lon, lat = point
    return (
        int(math.floor(lon / SPATIAL_CELL_SIZE_DEGREES)),
        int(math.floor(lat / SPATIAL_CELL_SIZE_DEGREES)),
    )


def estimate_sample(feature: dict[str, Any]) -> Optional[dict[str, Any]]:
    props = feature["properties"]
    measured = props.get("measured_first_floor_above_grade_ft")
    point = geometry_centroid(feature.get("geometry"))
    if measured is None or point is None:
        return None

    return {
        "height": measured,
        "cell": spatial_cell(point),
        "year": construction_year_bucket(props.get("construction_year")),
        "roof": height_bucket(props.get("height_roof_ft")),
        "ground": ground_bucket(props.get("ground_elevation_footprint_ft")),
        "subgrade": normalize_text(props.get("subgrade")) or "unknown_subgrade",
        "ntaname": normalize_text(props.get("ntaname")) or "unknown_nta",
        "flood_zone": normalize_text(props.get("flood_zone")) or "unknown_zone",
    }


def add_index_value(index: dict[tuple[Any, ...], list[float]], key: tuple[Any, ...], value: float) -> None:
    index[key].append(value)


def build_estimate_index(features: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[float]]:
    index: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for feature in features:
        sample = estimate_sample(feature)
        if not sample:
            continue

        height = sample["height"]
        cell = sample["cell"]
        if cell:
            cell_x, cell_y = cell
            add_index_value(
                index,
                ("cell_full", cell_x, cell_y, sample["year"], sample["roof"], sample["ground"], sample["subgrade"]),
                height,
            )
            add_index_value(
                index,
                ("cell_attrs", cell_x, cell_y, sample["year"], sample["roof"], sample["ground"]),
                height,
            )
            add_index_value(index, ("cell_roof_ground", cell_x, cell_y, sample["roof"], sample["ground"]), height)

        add_index_value(
            index,
            ("nta_attrs", sample["ntaname"], sample["year"], sample["roof"], sample["ground"]),
            height,
        )
        add_index_value(
            index,
            ("zone_attrs", sample["flood_zone"], sample["year"], sample["roof"], sample["ground"]),
            height,
        )
        add_index_value(index, ("attrs", sample["year"], sample["roof"], sample["ground"]), height)
        add_index_value(index, ("roof_ground", sample["roof"], sample["ground"]), height)
        add_index_value(index, ("ground", sample["ground"]), height)
        add_index_value(index, ("global",), height)
    return index


def build_estimate_stats(
    index: dict[tuple[Any, ...], list[float]],
) -> dict[tuple[Any, ...], tuple[float, int]]:
    return {
        key: (round(float(median(values)), 2), len(values))
        for key, values in index.items()
        if values
    }


def neighbor_cell_keys(
    prefix: str,
    cell: Optional[tuple[int, int]],
    radius: int,
    extra_values: tuple[Any, ...],
) -> list[tuple[Any, ...]]:
    if cell is None:
        return []
    cell_x, cell_y = cell
    return [
        (prefix, cell_x + dx, cell_y + dy, *extra_values)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
    ]


def collect_values(index: dict[tuple[Any, ...], list[float]], keys: list[tuple[Any, ...]]) -> list[float]:
    values: list[float] = []
    for key in keys:
        values.extend(index.get(key, []))
    return values


def estimate_height(
    feature: dict[str, Any],
    index: dict[tuple[Any, ...], list[float]],
    stats: dict[tuple[Any, ...], tuple[float, int]],
) -> tuple[Optional[float], Optional[int], Optional[str]]:
    props = feature["properties"]
    point = geometry_centroid(feature.get("geometry"))
    cell = spatial_cell(point)
    year = construction_year_bucket(props.get("construction_year"))
    roof = height_bucket(props.get("height_roof_ft"))
    ground = ground_bucket(props.get("ground_elevation_footprint_ft"))
    subgrade = normalize_text(props.get("subgrade")) or "unknown_subgrade"
    ntaname = normalize_text(props.get("ntaname")) or "unknown_nta"
    flood_zone = normalize_text(props.get("flood_zone")) or "unknown_zone"

    strategies = [
        (
            neighbor_cell_keys("cell_full", cell, 1, (year, roof, ground, subgrade)),
            3,
            "nearby buildings with matching year, roof, ground, and subgrade buckets",
        ),
        (
            neighbor_cell_keys("cell_attrs", cell, 2, (year, roof, ground)),
            3,
            "nearby buildings with matching year, roof, and ground buckets",
        ),
        (
            neighbor_cell_keys("cell_roof_ground", cell, 2, (roof, ground)),
            3,
            "nearby buildings with matching roof and ground buckets",
        ),
        (
            [("nta_attrs", ntaname, year, roof, ground)],
            3,
            "same neighborhood with matching official attribute buckets",
        ),
        (
            [("zone_attrs", flood_zone, year, roof, ground)],
            3,
            "same FEMA flood zone with matching official attribute buckets",
        ),
        (
            [("attrs", year, roof, ground)],
            5,
            "citywide matching official attribute buckets",
        ),
        (
            [("roof_ground", roof, ground)],
            8,
            "citywide matching roof and ground buckets",
        ),
        (
            [("ground", ground)],
            10,
            "citywide matching ground-elevation bucket",
        ),
        ([("global",)], 25, "citywide measured BES median"),
    ]

    for keys, minimum_count, method in strategies:
        if len(keys) == 1 and keys[0] in stats:
            value, count = stats[keys[0]]
            if count >= minimum_count:
                return value, count, method
            continue

        values = collect_values(index, keys)
        if len(values) >= minimum_count:
            return round(float(median(values)), 2), len(values), method
    return None, None, None


def apply_estimates(features: list[dict[str, Any]], rules: dict[str, Any]) -> None:
    index = build_estimate_index(features)
    stats = build_estimate_stats(index)
    for feature in features:
        props = feature["properties"]
        if props["measured_first_floor_above_grade_ft"] is not None:
            update_classification(feature, rules)
            continue

        estimate, sample_count, method = estimate_height(feature, index, stats)
        if estimate is None:
            props["elevation_confidence"] = "unverified"
            props["elevation_source_label"] = "BES value missing or failed quality checks; no reliable official-data estimate"
            update_classification(feature, rules)
            continue

        props["estimated_first_floor_above_grade_ft"] = estimate
        props["display_first_floor_above_grade_ft"] = estimate
        props["elevation_confidence"] = "estimated"
        props["elevation_estimate_sample_count"] = sample_count
        props["elevation_estimate_method"] = method
        props["elevation_source_label"] = "Estimated from nearby or similar official-source buildings"
        props["data_quality_flags"] = sorted(set([*props["data_quality_flags"], "estimated_from_official_sources"]))
        update_classification(feature, rules)


def update_classification(feature: dict[str, Any], rules: dict[str, Any]) -> None:
    props = feature["properties"]
    status, label = classify(
        props.get("display_first_floor_above_grade_ft"),
        props.get("elevation_confidence"),
        props.get("bfe_above_grade_ft"),
        rules,
    )
    props["flood_readiness_status"] = status
    props["flood_readiness_label"] = label
    props["current_screening_height_above_grade_ft"] = (
        round(props["bfe_above_grade_ft"] + float(rules["current_freeboard_ft"]), 2)
        if props.get("bfe_above_grade_ft") is not None
        else None
    )
    props["base_screening_height_above_grade_ft"] = props.get("bfe_above_grade_ft")


def build_feature(
    footprint: dict[str, Any],
    bes_by_bin: dict[str, dict[str, Any]],
    rules: dict[str, Any],
    flood_zone_features: Optional[list[dict[str, Any]] | dict[tuple[int, int], list[dict[str, Any]]]] = None,
    bfe_line_features: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    flood_zone_features = flood_zone_features or []
    bfe_line_features = bfe_line_features or []

    props = footprint.get("properties", {})
    bin_value = normalize_bin(props.get("BIN"))
    bes = bes_by_bin.get(bin_value or "", {})

    z_grade = parse_float(bes.get("z_grade"))
    z_floor = parse_float(bes.get("z_floor"))
    first_floor_raw = None
    if z_grade is not None and z_floor is not None:
        first_floor_raw = round(z_floor - z_grade, 2)

    quality_flags, has_trusted_measurement = bes_quality(bes, z_grade, z_floor, first_floor_raw)
    measured_first_floor = first_floor_raw if has_trusted_measurement else None
    elevation_confidence = "measured" if measured_first_floor is not None else "unverified"
    source_label = (
        "NYC BES z_floor - z_grade"
        if measured_first_floor is not None
        else "BES value missing or failed quality checks"
    )

    geometry = footprint.get("geometry")
    centroid = geometry_centroid(geometry)
    ground_elevation = parse_float(props.get("GROUNDELEV"))
    flood_context = bfe_context(centroid, ground_elevation, flood_zone_features, bfe_line_features)

    feature = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "bin": bin_value,
            "bbl": bes.get("bbl"),
            "base_bbl": props.get("BASE_BBL"),
            "mpluto_bbl": props.get("MPLUTO_BBL"),
            "address": bes.get("address"),
            "construction_year": props.get("CNSTRCT_YR"),
            "height_roof_ft": props.get("HEIGHTROOF"),
            "ground_elevation_footprint_ft": ground_elevation,
            "z_grade_navd88_ft": z_grade,
            "z_floor_navd88_ft": z_floor,
            "first_floor_above_grade_ft": measured_first_floor,
            "measured_first_floor_above_grade_ft": measured_first_floor,
            "estimated_first_floor_above_grade_ft": None,
            "display_first_floor_above_grade_ft": measured_first_floor,
            "raw_first_floor_above_grade_ft": first_floor_raw,
            "elevation_confidence": elevation_confidence,
            "elevation_source_label": source_label,
            "elevation_estimate_sample_count": None,
            "elevation_estimate_method": None,
            "data_quality_flags": quality_flags,
            "bes_notes": normalize_text(bes.get("notes1")),
            "subgrade": bes.get("subgrade"),
            "ntaname": bes.get("ntaname"),
            "classification_basis": rules["basis"],
            **flood_context,
            "current_screening_height_above_grade_ft": None,
            "base_screening_height_above_grade_ft": None,
            "flood_readiness_status": "unverified_elevation",
            "flood_readiness_label": STATUS_LABELS["unverified_elevation"],
        },
    }
    update_classification(feature, rules)
    return feature


def status_counts(features: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        status = feature["properties"]["flood_readiness_status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def confidence_counts(features: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"measured": 0, "estimated": 0, "unverified": 0}
    for feature in features:
        confidence = feature["properties"]["elevation_confidence"]
        counts[confidence] = counts.get(confidence, 0) + 1
    return counts


def qa_counts(features: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "feature_count": len(features),
        "measured_count": sum(
            1 for feature in features if feature["properties"]["elevation_confidence"] == "measured"
        ),
        "estimated_count": sum(
            1 for feature in features if feature["properties"]["elevation_confidence"] == "estimated"
        ),
        "unverified_count": sum(
            1 for feature in features if feature["properties"]["elevation_confidence"] == "unverified"
        ),
        "suspect_zero_count": sum(
            1
            for feature in features
            if "suspect_zero_elevation" in feature["properties"]["data_quality_flags"]
        ),
        "fema_matched_count": sum(1 for feature in features if feature["properties"]["flood_zone"] is not None),
        "fema_bfe_count": sum(
            1 for feature in features if feature["properties"]["base_flood_elevation_ft"] is not None
        ),
    }


def assert_dataset_quality(features: list[dict[str, Any]]) -> None:
    trusted_suspect_zero = [
        feature["properties"].get("bin")
        for feature in features
        if feature["properties"]["elevation_confidence"] == "measured"
        and "suspect_zero_elevation" in feature["properties"]["data_quality_flags"]
    ]
    if trusted_suspect_zero:
        sample = ", ".join(str(value) for value in trusted_suspect_zero[:10])
        raise ValueError(f"Suspect BES 0/0 records were treated as measured: {sample}")


def build_metadata(
    features: list[dict[str, Any]],
    sources: dict[str, Any],
    rules: dict[str, Any],
    area: dict[str, Any],
    raw_counts: dict[str, int],
) -> dict[str, Any]:
    qa = qa_counts(features)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "area": area,
        "model_version": rules["model_version"],
        "raw_source_counts": raw_counts,
        "feature_count": len(features),
        "measured_count": qa["measured_count"],
        "estimated_count": qa["estimated_count"],
        "unverified_count": qa["unverified_count"],
        "suspect_zero_count": qa["suspect_zero_count"],
        "fema_matched_count": qa["fema_matched_count"],
        "fema_bfe_count": qa["fema_bfe_count"],
        "status_counts": status_counts(features),
        "confidence_counts": confidence_counts(features),
        "qa_counts": qa,
        "sources": sources,
    }


def main() -> int:
    sources = load_json(SOURCES_PATH)
    rules = load_json(RULES_PATH)
    bbox = sources["area"]["bbox_wgs84"]
    source_config = sources["sources"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    bes_by_bin = fetch_bes_records(source_config["nyc_building_elevation_subgrade"]["url"], bbox)
    footprints = fetch_footprint_features(source_config["nyc_building_footprints"]["url"], bbox)
    fema_config = source_config["fema_nfhl"]
    flood_zones = fetch_arcgis_layer_features(
        fema_config["url"],
        fema_config["flood_hazard_zones_layer"],
        bbox,
        "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,V_DATUM,DEPTH,LEN_UNIT,SOURCE_CIT",
    )
    bfe_lines = fetch_arcgis_layer_features(
        fema_config["url"],
        fema_config["base_flood_elevations_layer"],
        bbox,
        "ELEV,LEN_UNIT,V_DATUM,SOURCE_CIT",
    )
    raw_counts = {
        "nyc_bes_records": len(bes_by_bin),
        "nyc_building_footprints": len(footprints),
        "fema_flood_hazard_zone_features": len(flood_zones),
        "fema_base_flood_elevation_line_features": len(bfe_lines),
    }
    attach_geometry_bboxes(flood_zones)
    attach_geometry_bboxes(bfe_lines)
    flood_zone_index = build_feature_spatial_index(flood_zones)

    features = [
        build_feature(feature, bes_by_bin, rules, flood_zone_index, bfe_lines)
        for feature in footprints
    ]
    apply_estimates(features, rules)
    assert_dataset_quality(features)

    collection = {
        "type": "FeatureCollection",
        "name": "coney-island-buildings",
        "features": features,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    metadata = build_metadata(features, source_config, rules, sources["area"], raw_counts)
    with METADATA_PATH.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)}")
    print(f"Wrote {METADATA_PATH.relative_to(ROOT)}")
    print(json.dumps(metadata["raw_source_counts"], indent=2))
    print(json.dumps(metadata["qa_counts"], indent=2))
    print(json.dumps(metadata["status_counts"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
