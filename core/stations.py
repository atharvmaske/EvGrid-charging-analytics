
import json

from sqlalchemy import text

from core.availability_resolver import resolve_availability
from core.availability_sim import cold_weather_factor, get_connector_count
from core.connectors import max_power_kw, parse_connector_details

CATALOG_WITH_AVAILABILITY_QUERY = text("""
    SELECT
        cs.station_id,
        cs.station_name,
        cs.city,
        cs.country_code,
        cs.latitude,
        cs.longitude,
        cs.connectors,
        cs.connectors_data,
        cs.ingestion_timestamp,
        a.available_connectors AS db_available,
        a.source               AS db_source,
        a.observed_at          AS db_observed_at
    FROM bronze.charging_stations cs
    LEFT JOIN bronze.availability a
        ON cs.station_id = a.station_id
    WHERE LOWER(cs.city) = LOWER(:city)
    ORDER BY cs.station_name
""")

CATALOG_QUERY = text("""
    SELECT
        station_id, station_name, city, country_code, latitude, longitude,
        connectors, connectors_data, ingestion_timestamp
    FROM bronze.charging_stations
    WHERE LOWER(city) = LOWER(:city)
    ORDER BY connectors DESC, station_name
""")


def fetch_city_rows(engine, city):
    with engine.connect() as conn:
        rows = conn.execute(CATALOG_WITH_AVAILABILITY_QUERY, {"city": city}).mappings().all()
    return [dict(r) for r in rows]


def enrich_station(row, city, weather_factor=1.0, now=None):

    station = dict(row)

    raw_connectors = station.get("connectors")
    if isinstance(raw_connectors, str):
        try:
            raw_connectors = json.loads(raw_connectors)
        except Exception:
            raw_connectors = []
    total_connectors = get_connector_count(raw_connectors)
    station["connectors"] = total_connectors

    station["connector_details"] = parse_connector_details(station.pop("connectors_data", None))
    station["max_power_kw"] = max_power_kw(station["connector_details"])

    available, occupied, source = resolve_availability(
        station, total_connectors, city=station.get("city") or city,
        weather_factor=weather_factor, now=now,
    )


    observed_at = station.get("db_observed_at") if source == "live" else None

    station.pop("db_available", None)
    station.pop("db_source", None)
    station.pop("db_observed_at", None)

    station["data_source"] = source
    station["observed_at"] = observed_at
    station["available_connectors"] = available
    station["occupied_connectors"] = occupied
    station["occupancy_rate"] = round(occupied / total_connectors, 2) if total_connectors else 0
    station["status"] = "Available" if available > 0 else "Busy"

    return station


def get_enriched_stations(engine, city, now=None):

    rows = fetch_city_rows(engine, city)
    if not rows:
        return []

    weather_factor = cold_weather_factor(city)
    return [enrich_station(row, city, weather_factor, now=now) for row in rows]


def get_catalog_rows(engine, city):

    with engine.connect() as conn:
        rows = conn.execute(CATALOG_QUERY, {"city": city}).mappings().all()

    stations = []
    for row in rows:
        station = dict(row)
        if station.get("connectors_data"):
            try:
                station["connectors_data"] = (
                    json.loads(station["connectors_data"])
                    if isinstance(station["connectors_data"], str)
                    else station["connectors_data"]
                )
            except Exception:
                station["connectors_data"] = []
        else:
            station["connectors_data"] = []
        stations.append(station)
    return stations
