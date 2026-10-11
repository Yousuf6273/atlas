"""Small, dependency-free geometry helpers.

Emirate boundaries in ``data/uae_emirates.geojson.json`` are simplified
OpenStreetMap administrative boundaries (relations 3766481–3766487),
retrieved via Nominatim with ``polygon_threshold=0.003`` (≈300 m tolerance).
© OpenStreetMap contributors, ODbL 1.0. Points within a few hundred metres of a
border may be assigned to the wrong emirate; callers treat that as uncertain.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib import resources

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    s, w, n, e = bbox
    return s <= lat <= n and w <= lon <= e


def _point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]  # GeoJSON is [lon, lat]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_geometry(lat: float, lon: float, geometry: dict) -> bool:
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for poly in polys:
        if not poly:
            continue
        if _point_in_ring(lat, lon, poly[0]) and not any(_point_in_ring(lat, lon, hole) for hole in poly[1:]):
            return True
    return False


@lru_cache(maxsize=1)
def emirate_polygons() -> dict[str, dict]:
    raw = (resources.files("atlas.geo") / "data" / "uae_emirates.geojson.json").read_text(encoding="utf-8")
    return json.loads(raw)


def emirate_of(lat: float | None, lon: float | None) -> str | None:
    """ISO 3166-2 code of the emirate containing the point, or None."""
    if lat is None or lon is None:
        return None
    for code, entry in emirate_polygons().items():
        if point_in_geometry(lat, lon, entry["geometry"]):
            return code
    return None


def tile_bbox(bbox: tuple[float, float, float, float], step: float) -> list[tuple[float, float, float, float]]:
    s, w, n, e = bbox
    tiles = []
    lat = s
    while lat < n - 1e-9:
        lon = w
        while lon < e - 1e-9:
            tiles.append((round(lat, 4), round(lon, 4), round(min(lat + step, n), 4), round(min(lon + step, e), 4)))
            lon += step
        lat += step
    return tiles


def circle_bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
