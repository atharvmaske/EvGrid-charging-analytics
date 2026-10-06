
import logging
import math
import time

from ingestion.station_matching import haversine_m

logger = logging.getLogger("evgrid.ocm")

KM_PER_DEG_LAT = 111.195
MIN_TILE_KM = 1.0
REQUEST_PAUSE_S = 0.3
EDGE_TOLERANCE_KM = 0.05


def split_tile(lat, lon, radius_km):
    dlat = (radius_km / 2) / KM_PER_DEG_LAT
    dlon = (radius_km / 2) / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    child_km = radius_km / math.sqrt(2)
    return [(lat + sy * dlat, lon + sx * dlon, child_km) for sy in (-1, 1) for sx in (-1, 1)]


def _inside(poi, lat, lon, radius_km):
    info = poi.get("AddressInfo") or {}
    p_lat, p_lon = info.get("Latitude"), info.get("Longitude")
    if p_lat is None or p_lon is None:
        return True
    return haversine_m(lat, lon, p_lat, p_lon) / 1000 <= radius_km + EDGE_TOLERANCE_KM


def fetch_city_tiled(session, url, base_params, lat, lon, radius_km, max_results,
                     pause_s=REQUEST_PAUSE_S):
    found = {}
    stats = {"requests": 0, "split": 0, "saturated_at_min": 0}
    stack = [(lat, lon, radius_km)]

    while stack:
        t_lat, t_lon, t_km = stack.pop()
        params = {
            **base_params,
            "latitude": t_lat,
            "longitude": t_lon,
            "distance": round(t_km, 3),
            "maxresults": max_results,
        }

        response = session.get(url, params=params, timeout=(10, 60))
        response.raise_for_status()
        batch = response.json()

        if not isinstance(batch, list):
            raise ValueError(f"Unexpected OCM response: {str(batch)[:200]}")

        stats["requests"] += 1

        if len(batch) >= max_results:
            if t_km / math.sqrt(2) >= MIN_TILE_KM:
                stats["split"] += 1
                stack.extend(split_tile(t_lat, t_lon, t_km))
            else:
                stats["saturated_at_min"] += 1
                logger.warning(
                    "Tile at (%.4f, %.4f) r=%.2f km is still full at minimum size", t_lat, t_lon, t_km
                )

        for poi in batch:
            poi_id = poi.get("ID")
            if poi_id is not None and _inside(poi, lat, lon, radius_km):
                found.setdefault(poi_id, poi)

        if stack and pause_s:
            time.sleep(pause_s)

    return list(found.values()), stats
