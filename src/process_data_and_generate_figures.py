#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon as MplPolygon, Rectangle


ROOT = Path(__file__).resolve().parents[1]
BUILDINGS_PATH = ROOT / "data" / "processed" / "coney-island-buildings.geojson"
METADATA_PATH = ROOT / "data" / "processed" / "coney-island-buildings.metadata.json"
FIGURE_DIR = ROOT / "output" / "figures"
DATA_DIR = ROOT / "data" / "summary"

CENSUS_REPORTER_GEO_URL = "https://api.censusreporter.org/1.0/geo/show/tiger2024"
CENSUS_REPORTER_DATA_URL = "https://api.censusreporter.org/1.0/data/show/acs2024_5yr"
ACS_RELEASE_ID = "acs2024_5yr"
TRACT_PARENT = "05000US36047"
ACS_TABLES = [
    "B01003",  # Total population
    "B03002",  # Hispanic or Latino origin by race
    "B17001",  # Poverty status
    "B19013",  # Median household income
    "B25003",  # Tenure
    "B25064",  # Median gross rent
]

APP_BBOX = {
    "west": -73.999,
    "south": 40.565,
    "east": -73.930,
    "north": 40.595,
}
MAP_CENTER = (
    (APP_BBOX["west"] + APP_BBOX["east"]) / 2,
    (APP_BBOX["south"] + APP_BBOX["north"]) / 2,
)
MILES_PER_LAT_DEGREE = 69.0
MILES_PER_LON_DEGREE = math.cos(math.radians(MAP_CENTER[1])) * MILES_PER_LAT_DEGREE

VULNERABLE_STATUSES = {
    "measured_below_base_flood_elevation",
    "estimated_below_base_flood_elevation",
}


def request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=90)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        preview = response.text[:500].replace("\n", " ")
        raise RuntimeError(f"Expected JSON from {response.url}; got: {preview}") from exc


def iter_geometry_points(geometry: dict[str, Any] | None) -> list[list[float]]:
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


def geometry_bbox(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    points = iter_geometry_points(geometry)
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def bbox_contains_point(
    bbox: tuple[float, float, float, float] | None,
    point: tuple[float, float],
) -> bool:
    if bbox is None:
        return True
    lon, lat = point
    west, south, east, north = bbox
    return west <= lon <= east and south <= lat <= north


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


def geometry_centroid(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
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


def point_in_geometry(point: tuple[float, float], geometry: dict[str, Any] | None) -> bool:
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


def bbox_intersects(a: tuple[float, float, float, float] | None, b: dict[str, float]) -> bool:
    if a is None:
        return False
    west, south, east, north = a
    return not (
        east < b["west"]
        or west > b["east"]
        or north < b["south"]
        or south > b["north"]
    )


def project_point(point: list[float] | tuple[float, float]) -> tuple[float, float]:
    lon, lat = point[:2]
    return (
        (lon - MAP_CENTER[0]) * MILES_PER_LON_DEGREE,
        (lat - MAP_CENTER[1]) * MILES_PER_LAT_DEGREE,
    )


def project_bbox(bbox: dict[str, float]) -> tuple[float, float, float, float]:
    west, south = project_point((bbox["west"], bbox["south"]))
    east, north = project_point((bbox["east"], bbox["north"]))
    return west, south, east, north


def clip_ring_to_bbox(ring: list[list[float]], bbox: dict[str, float]) -> list[list[float]]:
    points = ring[:-1] if len(ring) > 1 and ring[0][:2] == ring[-1][:2] else ring[:]

    def clip_edge(
        input_points: list[list[float]],
        inside,
        intersect,
    ) -> list[list[float]]:
        if not input_points:
            return []
        output: list[list[float]] = []
        previous = input_points[-1]
        previous_inside = inside(previous)
        for current in input_points:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
            previous_inside = current_inside
        return output

    def intersect_vertical(x_value: float):
        def _intersect(start: list[float], end: list[float]) -> list[float]:
            x1, y1 = start[:2]
            x2, y2 = end[:2]
            if x2 == x1:
                return [x_value, y1]
            ratio = (x_value - x1) / (x2 - x1)
            return [x_value, y1 + ratio * (y2 - y1)]

        return _intersect

    def intersect_horizontal(y_value: float):
        def _intersect(start: list[float], end: list[float]) -> list[float]:
            x1, y1 = start[:2]
            x2, y2 = end[:2]
            if y2 == y1:
                return [x1, y_value]
            ratio = (y_value - y1) / (y2 - y1)
            return [x1 + ratio * (x2 - x1), y_value]

        return _intersect

    clipped = clip_edge(points, lambda point: point[0] >= bbox["west"], intersect_vertical(bbox["west"]))
    clipped = clip_edge(clipped, lambda point: point[0] <= bbox["east"], intersect_vertical(bbox["east"]))
    clipped = clip_edge(clipped, lambda point: point[1] >= bbox["south"], intersect_horizontal(bbox["south"]))
    clipped = clip_edge(clipped, lambda point: point[1] <= bbox["north"], intersect_horizontal(bbox["north"]))
    if len(clipped) < 3:
        return []
    clipped.append(clipped[0])
    return clipped


def load_census_tracts() -> list[dict[str, Any]]:
    collection = request_json(
        CENSUS_REPORTER_GEO_URL,
        {"geo_ids": f"140|{TRACT_PARENT}"},
    )
    tracts = []
    for feature in collection["features"]:
        bbox = geometry_bbox(feature.get("geometry"))
        if bbox_intersects(bbox, APP_BBOX):
            feature["_bbox"] = bbox
            tracts.append(feature)
    return tracts


def load_acs(geoids: list[str]) -> dict[str, Any]:
    # Use Census Reporter's fixed ACS 2024 release. The official Census API can
    # require a key from some networks; this endpoint keeps the repo reproducible
    # without a private credential while still pinning the ACS vintage.
    payload = request_json(
        CENSUS_REPORTER_DATA_URL,
        {
            "table_ids": ",".join(ACS_TABLES),
            "geo_ids": ",".join(geoids),
        },
    )
    release_id = payload.get("release", {}).get("id")
    if release_id != ACS_RELEASE_ID:
        raise ValueError(f"Expected Census Reporter release {ACS_RELEASE_ID}, got {release_id!r}")
    missing = sorted(set(geoids) - set(payload.get("data", {})))
    if missing:
        raise ValueError(f"ACS 2024 response did not include {len(missing)} requested tracts: {missing[:5]}")
    return payload


def estimate(data: dict[str, Any], geoid: str, table: str, variable: str) -> float | None:
    try:
        value = data["data"][geoid][table]["estimate"][variable]
    except KeyError:
        return None
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number < -100000:
        return None
    return number


def pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return 100.0 * numerator / denominator


def extract_demographics(tract: dict[str, Any], acs: dict[str, Any]) -> dict[str, Any]:
    geoid = tract["properties"]["geoid"]
    population = estimate(acs, geoid, "B01003", "B01003001")
    poverty_universe = estimate(acs, geoid, "B17001", "B17001001")
    poverty_count = estimate(acs, geoid, "B17001", "B17001002")
    occupied_units = estimate(acs, geoid, "B25003", "B25003001")
    owner_units = estimate(acs, geoid, "B25003", "B25003002")
    renter_units = estimate(acs, geoid, "B25003", "B25003003")
    total_race = estimate(acs, geoid, "B03002", "B03002001")
    nh_white = estimate(acs, geoid, "B03002", "B03002003")
    nh_black = estimate(acs, geoid, "B03002", "B03002004")
    nh_asian = estimate(acs, geoid, "B03002", "B03002006")
    hispanic = estimate(acs, geoid, "B03002", "B03002012")

    return {
        "geoid": geoid,
        "name": tract["properties"].get("name", geoid),
        "population": population,
        "median_household_income": estimate(acs, geoid, "B19013", "B19013001"),
        "median_gross_rent": estimate(acs, geoid, "B25064", "B25064001"),
        "poverty_count": poverty_count,
        "poverty_rate": pct(poverty_count, poverty_universe),
        "occupied_units": occupied_units,
        "owner_units": owner_units,
        "renter_units": renter_units,
        "renter_share": pct(renter_units, occupied_units),
        "nh_white_share": pct(nh_white, total_race),
        "nh_black_share": pct(nh_black, total_race),
        "nh_asian_share": pct(nh_asian, total_race),
        "hispanic_share": pct(hispanic, total_race),
    }


def assign_buildings_to_tracts(
    buildings: list[dict[str, Any]],
    tracts: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for building in buildings:
        point = geometry_centroid(building.get("geometry"))
        if point is None:
            continue
        for tract in tracts:
            if not bbox_contains_point(tract.get("_bbox"), point):
                continue
            if point_in_geometry(point, tract.get("geometry")):
                assignments[tract["properties"]["geoid"]].append(building)
                break
    return assignments


def building_metrics(features: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(features)
    status_counts = Counter(feature["properties"].get("flood_readiness_status") for feature in features)
    confidence_counts = Counter(feature["properties"].get("elevation_confidence") for feature in features)
    bfe_context = sum(1 for feature in features if feature["properties"].get("base_flood_elevation_ft") is not None)
    vulnerable = sum(
        1
        for feature in features
        if feature["properties"].get("flood_readiness_status") in VULNERABLE_STATUSES
    )
    measured_vulnerable = status_counts["measured_below_base_flood_elevation"]
    estimated_vulnerable = status_counts["estimated_below_base_flood_elevation"]
    return {
        "building_count": total,
        "bfe_context_count": bfe_context,
        "vulnerable_count": vulnerable,
        "vulnerable_share_all": pct(vulnerable, total),
        "vulnerable_share_bfe_context": pct(vulnerable, bfe_context),
        "measured_count": confidence_counts["measured"],
        "estimated_count": confidence_counts["estimated"],
        "unverified_count": confidence_counts["unverified"],
        "measured_vulnerable_count": measured_vulnerable,
        "estimated_vulnerable_count": estimated_vulnerable,
    }


def tract_patch(feature: dict[str, Any]) -> list[MplPolygon]:
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    patches: list[MplPolygon] = []
    if geometry.get("type") == "Polygon":
        clipped = clip_ring_to_bbox(coords[0], APP_BBOX)
        if clipped:
            patches.append(MplPolygon([project_point(point) for point in clipped], closed=True))
    elif geometry.get("type") == "MultiPolygon":
        for polygon in coords:
            if polygon and polygon[0]:
                clipped = clip_ring_to_bbox(polygon[0], APP_BBOX)
                if clipped:
                    patches.append(MplPolygon([project_point(point) for point in clipped], closed=True))
    return patches


def add_screening_context(ax: plt.Axes) -> None:
    west, south, east, north = project_bbox(APP_BBOX)
    ax.add_patch(
        Rectangle(
            (west, south),
            east - west,
            north - south,
            fill=False,
            linestyle=(0, (4, 3)),
            linewidth=1.2,
            edgecolor="#222222",
            zorder=3,
        )
    )
    ax.text(
        0.02,
        0.04,
        "Census tract portions\nclipped to screening box\nnot ZIP codes",
        transform=ax.transAxes,
        fontsize=7.5,
        color="#333333",
        bbox={"facecolor": "white", "edgecolor": "#888888", "alpha": 0.82, "pad": 3},
    )
    labels = [
        ("Sea Gate", -73.997, 40.576),
        ("Coney Island", -73.982, 40.575),
        ("Brighton Beach", -73.960, 40.577),
        ("Atlantic Ocean", -73.965, 40.567),
        ("Coney Island Creek", -73.975, 40.592),
    ]
    for label, lon, lat in labels:
        x, y = project_point((lon, lat))
        ax.text(x, y, label, fontsize=7, color="#4a4a4a", ha="center", va="center", alpha=0.86)


def add_choropleth(
    ax: plt.Axes,
    tracts: list[dict[str, Any]],
    values: dict[str, float | None],
    title: str,
    cmap: str,
    legend_label: str,
    money: bool = False,
) -> None:
    patches: list[MplPolygon] = []
    patch_values: list[float] = []
    for tract in tracts:
        value = values.get(tract["properties"]["geoid"])
        if value is None:
            continue
        tract_patches = tract_patch(tract)
        patches.extend(tract_patches)
        patch_values.extend([value] * len(tract_patches))

    collection = PatchCollection(
        patches,
        cmap=cmap,
        edgecolor="#ffffff",
        linewidth=0.8,
    )
    collection.set_array(np.asarray(patch_values))
    ax.add_collection(collection)
    west, south, east, north = project_bbox(APP_BBOX)
    margin_x = (east - west) * 0.03
    margin_y = (north - south) * 0.05
    ax.set_xlim(west - margin_x, east + margin_x)
    ax.set_ylim(south - margin_y, north + margin_y)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=13, pad=10, weight="bold")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    add_screening_context(ax)
    cbar = plt.colorbar(collection, ax=ax, fraction=0.045, pad=0.02)
    cbar.ax.tick_params(labelsize=9)
    if money:
        cbar.formatter = plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k")
        cbar.update_ticks()
    cbar.set_label(legend_label, fontsize=9)


def format_money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.0f}"


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def write_outputs(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / "tract_flood_demographic_summary.csv"
    fieldnames = [
        "geoid",
        "name",
        "population",
        "median_household_income",
        "median_gross_rent",
        "poverty_rate",
        "renter_share",
        "building_count",
        "bfe_context_count",
        "vulnerable_count",
        "vulnerable_share_all",
        "vulnerable_share_bfe_context",
        "measured_vulnerable_count",
        "estimated_vulnerable_count",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})

    summary_path = DATA_DIR / "parts_bcd_summary.json"
    summary_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def income_tiers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if row.get("median_household_income") is not None]
    valid.sort(key=lambda row: row["median_household_income"])
    if not valid:
        return []
    tiers: list[dict[str, Any]] = []
    for index, label in enumerate(["Lowest income", "Lower-middle", "Middle", "Upper-middle", "Highest income"]):
        start = round(index * len(valid) / 5)
        end = round((index + 1) * len(valid) / 5)
        chunk = valid[start:end]
        if not chunk:
            continue
        building_count = sum(row["building_count"] for row in chunk)
        vulnerable = sum(row["vulnerable_count"] for row in chunk)
        measured_vulnerable = sum(row["measured_vulnerable_count"] for row in chunk)
        estimated_vulnerable = sum(row["estimated_vulnerable_count"] for row in chunk)
        bfe_context = sum(row["bfe_context_count"] for row in chunk)
        poverty_values = [row["poverty_rate"] for row in chunk if row.get("poverty_rate") is not None]
        tiers.append(
            {
                "label": label,
                "tracts": len(chunk),
                "income_min": min(row["median_household_income"] for row in chunk),
                "income_max": max(row["median_household_income"] for row in chunk),
                "median_income": median(row["median_household_income"] for row in chunk),
                "building_count": building_count,
                "bfe_context_count": bfe_context,
                "vulnerable_count": vulnerable,
                "measured_vulnerable_count": measured_vulnerable,
                "estimated_vulnerable_count": estimated_vulnerable,
                "vulnerable_share_bfe_context": pct(vulnerable, bfe_context),
                "median_poverty_rate": median(poverty_values) if poverty_values else None,
            }
        )
    return tiers


def plot_maps(tracts: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    row_by_geoid = {row["geoid"]: row for row in rows}
    income_values = {
        geoid: row.get("median_household_income")
        for geoid, row in row_by_geoid.items()
        if row.get("building_count", 0) > 0
    }
    vulnerable_values = {
        geoid: row.get("vulnerable_share_bfe_context")
        for geoid, row in row_by_geoid.items()
        if row.get("building_count", 0) > 0
    }
    mapped_tracts = [tract for tract in tracts if tract["properties"]["geoid"] in income_values]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), constrained_layout=True)
    add_choropleth(
        axes[0],
        mapped_tracts,
        income_values,
        "Median household income",
        "YlGnBu",
        "ACS 2024 dollars",
        money=True,
    )
    add_choropleth(
        axes[1],
        mapped_tracts,
        vulnerable_values,
        "Below-BFE building share",
        "OrRd",
        "% of buildings with BFE context",
        money=False,
    )
    fig.suptitle(
        "Coney Island Screening Box: Census Tract Context for Income and Flood-Elevation Vulnerability",
        fontsize=13,
        weight="bold",
    )
    fig.text(
        0.5,
        0.01,
        "Map units are census tract portions clipped to the building-screening bbox. They are not ZIP codes, parcel boundaries, or photo frames. Sources: ACS 2024 5-year tract estimates; NYC BES; NYC Building Footprints; FEMA NFHL.",
        ha="center",
        fontsize=8.4,
        color="#444444",
    )
    fig.savefig(FIGURE_DIR / "fig_income_vs_vulnerability_maps.png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def plot_income_tiers(tiers: list[dict[str, Any]]) -> None:
    labels = [tier["label"].replace(" ", "\n") for tier in tiers]
    measured = np.array([tier["measured_vulnerable_count"] for tier in tiers])
    estimated = np.array([tier["estimated_vulnerable_count"] for tier in tiers])
    shares = np.array([
        tier["vulnerable_share_bfe_context"] or 0
        for tier in tiers
    ])

    fig, ax1 = plt.subplots(figsize=(10, 6.2))
    fig.subplots_adjust(left=0.09, right=0.89, top=0.84, bottom=0.24)
    x = np.arange(len(labels))
    ax1.bar(x, measured, color="#2F6F88", label="Measured below BFE")
    ax1.bar(x, estimated, bottom=measured, color="#E7A23B", label="Estimated below BFE")
    ax1.set_ylabel("Below-BFE building count", fontsize=11)
    ax1.set_xticks(x, labels)
    ax1.set_ylim(0, max((measured + estimated).max() * 1.22, 100))
    ax1.legend(loc="upper left", frameon=False)
    ax1.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax1.set_axisbelow(True)

    ax2 = ax1.twinx()
    ax2.plot(x, shares, color="#9B1C1C", marker="o", linewidth=2.2, label="Below-BFE share")
    ax2.set_ylabel("% of BFE-context buildings below BFE", fontsize=11)
    ax2.set_ylim(0, max(100, shares.max() * 1.2 if len(shares) else 100))

    for index, tier in enumerate(tiers):
        ax1.text(
            index,
            measured[index] + estimated[index] + 25,
            f"MHI {tier['income_min']/1000:.0f}k-{tier['income_max']/1000:.0f}k\n{format_pct(tier['median_poverty_rate'])} poverty",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333333",
        )

    ax1.set_title(
        "Flood-Elevation Vulnerability by Tract Income Tier",
        fontsize=14,
        weight="bold",
        pad=14,
    )
    fig.text(
        0.5,
        0.01,
        "Tier labels sort app-area census tracts by ACS 2024 median household income. Estimates remain screening indicators, not compliance findings.",
        ha="center",
        fontsize=9,
        color="#444444",
    )
    fig.savefig(FIGURE_DIR / "fig_income_tier_vulnerability.png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def plot_data_methods(metadata: dict[str, Any]) -> None:
    labels = [
        "Measured\nBES-derived",
        "Estimated\nofficial-data fallback",
        "Unverified",
        "Suspect BES\n0/0 records",
        "FEMA BFE\ncontext",
    ]
    values = [
        metadata["qa_counts"]["measured_count"],
        metadata["qa_counts"]["estimated_count"],
        metadata["qa_counts"]["unverified_count"],
        metadata["qa_counts"]["suspect_zero_count"],
        metadata["qa_counts"]["fema_bfe_count"],
    ]
    colors = ["#2F6F88", "#E7A23B", "#9B9B9B", "#C84C31", "#4C7F45"]

    fig, ax = plt.subplots(figsize=(10, 5.6))
    fig.subplots_adjust(left=0.09, right=0.98, top=0.84, bottom=0.22)
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel("Building records", fontsize=11)
    ax.set_title("Data Quality Summary for Flood-Readiness Screening Dataset", fontsize=15, weight="bold")
    ax.set_ylim(0, max(values) * 1.22)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(values) * 0.02,
            f"{value:,}",
            ha="center",
            va="bottom",
            fontsize=10,
            weight="bold",
        )
    ax.text(
        0.5,
        -0.18,
        "Trusted display fields suppress suspect BES 0/0 elevations; estimated values are labeled separately from measured values.",
        transform=ax.transAxes,
        ha="center",
        fontsize=9,
        color="#444444",
    )
    fig.savefig(FIGURE_DIR / "fig_data_methods_summary.png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def validate_input_dataset(buildings: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    metadata_qa = metadata.get("qa_counts", {})
    feature_count = len(buildings)
    confidence_counts = Counter(
        feature["properties"].get("elevation_confidence")
        for feature in buildings
    )
    suspect_zero_count = sum(
        1
        for feature in buildings
        if "suspect_zero_elevation" in (feature["properties"].get("data_quality_flags") or [])
    )
    fema_matched_count = sum(
        1
        for feature in buildings
        if feature["properties"].get("flood_zone") is not None
    )
    fema_bfe_count = sum(
        1
        for feature in buildings
        if feature["properties"].get("base_flood_elevation_ft") is not None
    )
    trusted_suspect_zero = [
        feature["properties"].get("bin") or feature["properties"].get("bbl")
        for feature in buildings
        if "suspect_zero_elevation" in (feature["properties"].get("data_quality_flags") or [])
        and feature["properties"].get("elevation_confidence") == "measured"
    ]
    expected = {
        "feature_count": feature_count,
        "measured_count": confidence_counts["measured"],
        "estimated_count": confidence_counts["estimated"],
        "unverified_count": confidence_counts["unverified"],
        "suspect_zero_count": suspect_zero_count,
        "fema_matched_count": fema_matched_count,
        "fema_bfe_count": fema_bfe_count,
    }
    mismatches = {
        key: {"metadata": metadata_qa.get(key), "computed": value}
        for key, value in expected.items()
        if metadata_qa.get(key) != value
    }
    if mismatches:
        raise ValueError(f"Metadata QA counts do not match building features: {mismatches}")
    if trusted_suspect_zero:
        raise ValueError(
            "Suspect BES 0/0 records are incorrectly labeled measured: "
            f"{trusted_suspect_zero[:10]}"
        )


def aggregate_context(rows: list[dict[str, Any]], tiers: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    population = sum(row["population"] or 0 for row in rows)
    poverty_count = sum(row["poverty_count"] or 0 for row in rows)
    poverty_universe = sum(
        (row["poverty_count"] or 0) / (row["poverty_rate"] / 100.0)
        for row in rows
        if row.get("poverty_count") is not None and row.get("poverty_rate")
    )
    renter_units = sum(row["renter_units"] or 0 for row in rows)
    occupied_units = sum(row["occupied_units"] or 0 for row in rows)
    income_values = [
        row["median_household_income"]
        for row in rows
        if row.get("median_household_income") is not None
    ]
    rent_values = [
        row["median_gross_rent"]
        for row in rows
        if row.get("median_gross_rent") is not None
    ]
    vulnerable_total = sum(row["vulnerable_count"] for row in rows)
    bfe_context_total = sum(row["bfe_context_count"] for row in rows)

    lowest_income = min(rows, key=lambda row: row.get("median_household_income") or math.inf)
    highest_vulnerability = max(rows, key=lambda row: row.get("vulnerable_count") or 0)
    return {
        "app_bbox": APP_BBOX,
        "tract_count": len(rows),
        "tracts_with_buildings": sum(1 for row in rows if row["building_count"] > 0),
        "population_screening_area_sum": round(population),
        "median_of_tract_median_household_income": median(income_values) if income_values else None,
        "median_of_tract_median_gross_rent": median(rent_values) if rent_values else None,
        "approx_poverty_rate": pct(poverty_count, poverty_universe),
        "approx_renter_share": pct(renter_units, occupied_units),
        "below_bfe_buildings": vulnerable_total,
        "bfe_context_buildings": bfe_context_total,
        "below_bfe_share_bfe_context": pct(vulnerable_total, bfe_context_total),
        "lowest_income_tract": {
            "name": lowest_income["name"],
            "median_household_income": lowest_income["median_household_income"],
            "poverty_rate": lowest_income["poverty_rate"],
            "vulnerable_count": lowest_income["vulnerable_count"],
        },
        "highest_vulnerability_tract": {
            "name": highest_vulnerability["name"],
            "median_household_income": highest_vulnerability["median_household_income"],
            "poverty_rate": highest_vulnerability["poverty_rate"],
            "vulnerable_count": highest_vulnerability["vulnerable_count"],
        },
        "income_tiers": tiers,
        "dataset_qa": metadata["qa_counts"],
        "source_notes": {
            "acs": "ACS 2024 5-year tract estimates accessed through Census Reporter release acs2024_5yr; tract geometry accessed through Census Reporter TIGER 2024.",
            "flood_dataset": "App screening dataset built from NYC BES, NYC Building Footprints, and FEMA NFHL.",
            "screening_caveat": "Building-level results are presentation screening indicators, not certified engineering, insurance, or code-compliance determinations.",
        },
    }


def main() -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    buildings = json.loads(BUILDINGS_PATH.read_text(encoding="utf-8"))["features"]
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    validate_input_dataset(buildings, metadata)
    tracts = load_census_tracts()
    acs = load_acs([tract["properties"]["geoid"] for tract in tracts])
    assignments = assign_buildings_to_tracts(buildings, tracts)

    rows: list[dict[str, Any]] = []
    for tract in tracts:
        geoid = tract["properties"]["geoid"]
        row = extract_demographics(tract, acs)
        row.update(building_metrics(assignments.get(geoid, [])))
        if row["building_count"] > 0:
            rows.append(row)

    rows.sort(key=lambda row: row["geoid"])
    tiers = income_tiers(rows)
    summary = aggregate_context(rows, tiers, metadata)

    write_outputs(rows, summary)
    plot_maps(tracts, rows)
    plot_income_tiers(tiers)
    plot_data_methods(metadata)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
