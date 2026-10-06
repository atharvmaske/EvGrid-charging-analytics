
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import text

from config.cities import LIVE_CITIES
from core.connectors import compatible
from core.scoring import haversine
from ingestion.station_matching import match_records
from ingestion.tomtom_keys import TomTomKeyPool, request_with_rotation

logger = logging.getLogger("evgrid.live")

SEARCH_URL = "https://api.tomtom.com/search/2/poiSearch/.json"
AVAILABILITY_URL = "https://api.tomtom.com/search/2/chargingAvailability.json"

LIVE_TTL = timedelta(minutes=float(os.getenv("LIVE_TTL_MINUTES", "5")))
DEFAULT_CAP = int(os.getenv("LIVE_ON_DEMAND_CAP", "12"))
MAX_WORKERS = 8
SEARCH_LIMIT = 100
MAX_SEARCH_RADIUS_M = 5000

MAP_DDL = """
CREATE TABLE IF NOT EXISTS bronze.tomtom_availability_map (
    station_id BIGINT PRIMARY KEY,
    city TEXT,
    availability_id TEXT,
    matched_at TIMESTAMP
)
"""

_pool = None
_session = None


def _tomtom():
    global _pool, _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {"User-Agent": "evgrid-charging-analytics/1.0", "Accept": "application/json"}
        )
        try:
            _pool = TomTomKeyPool.from_env()
        except RuntimeError:
            _pool = None
    return _pool, _session


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(ts):
    if ts is not None and ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def select_targets(stations, lat, lon, radius_km, limit, car="", now=None):

    now = _naive_utc(now) or utcnow()
    nearby = []

    for s in stations:
        if s.get("latitude") is None or s.get("longitude") is None:
            continue
        dist = haversine(lat, lon, s["latitude"], s["longitude"])
        if dist > radius_km:
            continue
        if not compatible(s.get("connector_details"), car):
            continue
        nearby.append((dist, s))

    nearby.sort(key=lambda pair: pair[0])

    targets = []
    for _dist, s in nearby:
        observed = _naive_utc(s.get("observed_at"))
        if s.get("data_source") == "live" and observed and now - observed <= LIVE_TTL:
            continue
        targets.append(s)
        if len(targets) >= limit:
            break

    return targets


def parse_counts(data):
    groups = (data or {}).get("connectors", [])
    available = sum(g.get("availability", {}).get("current", {}).get("available", 0) for g in groups)
    occupied = sum(g.get("availability", {}).get("current", {}).get("occupied", 0) for g in groups)
    return available, occupied


def poi_to_record(result):
    position = result.get("position", {})
    availability_id = (
        result.get("dataSources", {}).get("chargingAvailability", {}).get("id")
    )
    lat, lon = position.get("lat"), position.get("lon")
    if lat is None or lon is None or not availability_id:
        return None
    return {
        "lat": lat,
        "lon": lon,
        "name": result.get("poi", {}).get("name")
        or result.get("address", {}).get("freeformAddress"),
        "availability_id": availability_id,
    }


def load_mappings(engine, station_ids):
    if not station_ids:
        return {}
    with engine.begin() as conn:
        conn.execute(text(MAP_DDL))
        rows = conn.execute(
            text(
                "SELECT station_id, availability_id FROM bronze.tomtom_availability_map "
                "WHERE station_id = ANY(:ids)"
            ),
            {"ids": list(station_ids)},
        ).fetchall()
    return {sid: aid for sid, aid in rows}


def save_mappings(engine, city, found):
    if not found:
        return
    now = utcnow()
    with engine.begin() as conn:
        for sid, aid in found.items():
            conn.execute(
                text(
                    """
                    INSERT INTO bronze.tomtom_availability_map
                        (station_id, city, availability_id, matched_at)
                    VALUES (:sid, :city, :aid, :ts)
                    ON CONFLICT (station_id) DO UPDATE SET
                        availability_id = EXCLUDED.availability_id,
                        matched_at = EXCLUDED.matched_at
                    """
                ),
                {"sid": sid, "city": city, "aid": aid, "ts": now},
            )


def discover_mappings(engine, city, targets, known_ids, lat, lon, radius_km, pool, session):
    params = {
        "lat": lat,
        "lon": lon,
        "radius": int(min(radius_km * 1000, MAX_SEARCH_RADIUS_M)),
        "limit": SEARCH_LIMIT,
        "categorySet": 7309,
    }
    response = request_with_rotation(session, SEARCH_URL, params, pool, timeout=10)
    if not response.ok:
        logger.warning(f"[{city}] poiSearch failed: HTTP {response.status_code}")
        return {}

    records = [
        rec
        for rec in (poi_to_record(r) for r in response.json().get("results", []))
        if rec and rec["availability_id"] not in known_ids
    ]
    candidates = [
        {"id": s["station_id"], "lat": s["latitude"], "lon": s["longitude"], "name": s["station_name"]}
        for s in targets
    ]

    matches, _stats = match_records(records, candidates, one_to_one=True)
    found = {m.candidate_id: records[m.record_index]["availability_id"] for m in matches}
    save_mappings(engine, city, found)
    return found


def _fetch_one(session, pool, station_id, availability_id):
    try:
        response = request_with_rotation(
            session, AVAILABILITY_URL, {"chargingAvailability": availability_id}, pool, timeout=10
        )
        if not response.ok:
            return station_id, None
        available, occupied = parse_counts(response.json())
        if available + occupied == 0:
            return station_id, None
        return station_id, (available, occupied)
    except Exception as exc:  
        logger.warning(f"availability fetch failed for station {station_id}: {exc}")
        return station_id, None


def upsert_live(engine, city, readings, observed_at):
    with engine.begin() as conn:
        for sid, (available, occupied) in readings.items():
            conn.execute(
                text(
                    """
                    INSERT INTO bronze.availability
                        (station_id, city, available_connectors, occupied_connectors,
                         total_connectors, ingestion_timestamp, observed_at, source)
                    VALUES (:sid, :city, :avail, :occ, :total, :ts, :observed, 'live')
                    ON CONFLICT (station_id) DO UPDATE SET
                        city = EXCLUDED.city,
                        available_connectors = EXCLUDED.available_connectors,
                        occupied_connectors = EXCLUDED.occupied_connectors,
                        total_connectors = EXCLUDED.total_connectors,
                        ingestion_timestamp = EXCLUDED.ingestion_timestamp,
                        observed_at = EXCLUDED.observed_at,
                        source = 'live'
                    """
                ),
                {
                    "sid": sid, "city": city, "avail": available, "occ": occupied,
                    "total": available + occupied, "ts": observed_at, "observed": observed_at,
                },
            )


def refresh_live_near(engine, city, lat, lon, stations, car="", radius_km=5.0, limit=DEFAULT_CAP):

    started = time.perf_counter()
    city_key = city.lower()
    summary = {"requested": 0, "reused": 0, "fetched": 0, "updated": 0, "skipped": None}

    if city_key not in LIVE_CITIES:
        summary["skipped"] = "simulator city"
        return summary

    pool, session = _tomtom()
    if pool is None:
        summary["skipped"] = "no TomTom key configured"
        return summary

    targets = select_targets(stations, lat, lon, radius_km, limit, car)
    summary["requested"] = len(targets)
    if not targets:
        summary["skipped"] = "nothing to refresh"
        return summary

    try:
        mapping = load_mappings(engine, [s["station_id"] for s in targets])
        unmapped = [s for s in targets if s["station_id"] not in mapping]
        if unmapped:
            mapping.update(
                discover_mappings(
                    engine, city_key, unmapped, set(mapping.values()),
                    lat, lon, radius_km, pool, session,
                )
            )

        lookups = [(s["station_id"], mapping[s["station_id"]]) for s in targets if s["station_id"] in mapping]
        summary["reused"] = len(targets) - len(unmapped)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            results = list(
                executor.map(lambda item: _fetch_one(session, pool, item[0], item[1]), lookups)
            )

        readings = {sid: counts for sid, counts in results if counts is not None}
        summary["fetched"] = len(lookups)

        if readings:
            upsert_live(engine, city_key, readings, utcnow())
        summary["updated"] = len(readings)
    except Exception as exc:
        logger.exception(f"[{city_key}] on-demand live refresh failed")
        summary["skipped"] = f"error: {exc}"

    summary["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    logger.info(f"[{city_key}] on-demand live refresh: {summary}")
    return summary