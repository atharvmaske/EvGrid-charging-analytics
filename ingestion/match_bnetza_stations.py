
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

from config.cities import CITIES
from ingestion.station_matching import MATCH_LOG_DDL, format_stats, log_match_stats, match_records

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evgrid.bnetza_match")

DE_CITIES = sorted(k for k, v in CITIES.items() if v["country_code"] == "DE")
DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}

TABLE_DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.station_power_de (
    station_id BIGINT PRIMARY KEY,
    city TEXT,
    bnetza_station_key TEXT,
    operator TEXT,
    real_connectors INTEGER,
    avg_power_kw DOUBLE PRECISION,
    max_power_kw DOUBLE PRECISION,
    plug_types TEXT,
    match_distance_m DOUBLE PRECISION,
    match_name_sim DOUBLE PRECISION,
    matched_at TIMESTAMP
)
"""


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


DB_CONFIG = {
    "host": resolve_host(os.getenv("POSTGRES_HOST")),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evgrid"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def load_candidates(cursor, city):
    cursor.execute(
        "SELECT station_id, latitude, longitude, station_name FROM bronze.charging_stations "
        "WHERE LOWER(city) = LOWER(%s)",
        (city,),
    )
    return [
        {"id": sid, "lat": lat, "lon": lon, "name": name}
        for sid, lat, lon, name in cursor.fetchall()
        if lat is not None and lon is not None
    ]


def load_records(cursor, city):
    cursor.execute(
        "SELECT station_key, operator, latitude, longitude, street, connectors, "
        "avg_power_kw, max_power_kw, plug_types FROM bronze.bnetza_stations_de "
        "WHERE LOWER(city) = LOWER(%s)",
        (city,),
    )
    return [
        {
            "key": key, "operator": operator, "lat": lat, "lon": lon, "name": street,
            "connectors": connectors, "avg_power_kw": avg_power_kw,
            "max_power_kw": max_power_kw, "plug_types": plug_types,
        }
        for key, operator, lat, lon, street, connectors, avg_power_kw, max_power_kw, plug_types
        in cursor.fetchall()
        if lat is not None and lon is not None
    ]


def match_city(cursor, city):
    candidates = load_candidates(cursor, city)
    records = load_records(cursor, city)

    matches, stats = match_records(records, candidates, one_to_one=True)
    logger.info(f"[{city.upper()}] {format_stats(stats)}")
    log_match_stats(cursor, "match_bnetza_stations", city, stats)

    now = datetime.now(timezone.utc)
    for m in matches:
        rec = records[m.record_index]
        cursor.execute(
            """
            INSERT INTO bronze.station_power_de
                (station_id, city, bnetza_station_key, operator, real_connectors,
                 avg_power_kw, max_power_kw, plug_types, match_distance_m, match_name_sim, matched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (station_id) DO UPDATE SET
                city = EXCLUDED.city,
                bnetza_station_key = EXCLUDED.bnetza_station_key,
                operator = EXCLUDED.operator,
                real_connectors = EXCLUDED.real_connectors,
                avg_power_kw = EXCLUDED.avg_power_kw,
                max_power_kw = EXCLUDED.max_power_kw,
                plug_types = EXCLUDED.plug_types,
                match_distance_m = EXCLUDED.match_distance_m,
                match_name_sim = EXCLUDED.match_name_sim,
                matched_at = EXCLUDED.matched_at
            """,
            (
                m.candidate_id, city, rec["key"], rec["operator"], rec["connectors"],
                rec["avg_power_kw"], rec["max_power_kw"], rec["plug_types"],
                m.distance_m, m.name_sim, now,
            ),
        )

    return stats


def register_table_exists(cursor):
    cursor.execute("SELECT to_regclass('bronze.bnetza_stations_de') IS NOT NULL")
    return cursor.fetchone()[0]


def main():
    conn = get_connection()
    cursor = conn.cursor()

    if not register_table_exists(cursor):
        sys.exit(
            "bronze.bnetza_stations_de does not exist yet. Run "
            "'python -m ingestion.extract_bnetza' first, then run this again."
        )

    cursor.execute(TABLE_DDL)
    cursor.execute(MATCH_LOG_DDL)
    conn.commit()

    for city in DE_CITIES:
        try:
            match_city(cursor, city)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.exception(f"[{city.upper()}] match failed: {exc}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
