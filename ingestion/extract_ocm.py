import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values
from requests.adapters import HTTPAdapter

from config.cities import CITIES, OCM_EXCLUDED_CITIES
from ingestion.ocm_tiling import MIN_TILE_KM, fetch_city_tiled

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("evgrid.ocm")

API_KEY = os.getenv("OCM_API_KEY")
if not API_KEY:
    raise RuntimeError("OCM_API_KEY not found in .env")

BASE_URL = os.getenv("OCM_BASE_URL", "https://api.openchargemap.io/v3/poi/")
DEFAULT_MAX_RESULTS = 1000

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


DB_CONFIG = {
    "host": resolve_host(os.getenv("POSTGRES_HOST")),
    "port": os.getenv("POSTGRES_PORT"),
    "dbname": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS bronze;

            CREATE TABLE IF NOT EXISTS bronze.ocm_raw (
                id SERIAL PRIMARY KEY,
                city VARCHAR(100),
                country_code VARCHAR(10),
                ingestion_timestamp TIMESTAMP,
                station_data JSONB
            );
        """)
    conn.commit()


def replace_city(conn, city, country_code, stations):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM bronze.ocm_raw WHERE city = %s", (city,))
        execute_values(
            cur,
            "INSERT INTO bronze.ocm_raw (city, country_code, ingestion_timestamp, station_data) VALUES %s",
            [(city, country_code, now, json.dumps(s)) for s in stations],
        )
    conn.commit()


def get_session():
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=2)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "evgrid-charging-analytics/1.0",
        "Accept": "application/json",
    })
    return session


def extract_city(session, conn, city_name, city_config, max_results, distance_km):
    tag = f"[{city_name.upper()}]"
    logger.info("=" * 70)
    logger.info(f"{tag} Starting Open Charge Map ingestion (adaptive tiles, FULL CONNECTOR DATA)")

    radius_km = distance_km or city_config["radius_km"]
    base_params = {
        "key": API_KEY,
        "countrycode": city_config["country_code"],
        "distanceunit": "KM",
        "compact": False,
        "verbose": False,
    }

    logger.info(f"{tag} radius {radius_km} km, up to {max_results} stations per request")

    try:
        stations, stats = fetch_city_tiled(
            session,
            BASE_URL,
            base_params,
            city_config["latitude"],
            city_config["longitude"],
            radius_km,
            max_results,
        )

        logger.info(
            f"{tag} {len(stations)} unique stations from {stats['requests']} requests "
            f"({stats['split']} tile splits)"
        )

        if stats["saturated_at_min"]:
            logger.warning(
                f"{tag} {stats['saturated_at_min']} tile(s) still full at {MIN_TILE_KM} km; "
                "this city may still be truncated."
            )

        if not stations:
            logger.warning(
                f"{tag} No stations found. Existing rows are left untouched."
            )
            return 0

        sample_connectors = stations[0].get("Connections") or []
        if sample_connectors:
            logger.info(
                f"{tag} Sample connectors (real data): {json.dumps(sample_connectors[:2], indent=2)}"
            )

        replace_city(conn, city_name, city_config["country_code"], stations)

        logger.info(
            f"{tag} Replaced this city's rows in bronze.ocm_raw with {len(stations)} stations."
        )

        return len(stations)

    except requests.exceptions.RequestException as e:
        conn.rollback()
        logger.error(f"{tag} Request failed : {e}")
        return 0

    except Exception as e:
        conn.rollback()
        logger.exception(f"{tag} Unexpected error : {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract Open Charge Map data into PostgreSQL Bronze (with real connector data)."
    )
    parser.add_argument("--city", type=str, help="Extract one city only.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument(
        "--distance",
        type=float,
        help="search radius in km (default: radius_km from config)",
    )

    args = parser.parse_args()

    if args.city and args.city.lower() not in CITIES:
        logger.error(
            f"Invalid city: {args.city}. Available: {', '.join(CITIES)}"
        )
        return 2

    if args.city and args.city.lower() in OCM_EXCLUDED_CITIES:
        logger.error(
            f"{args.city} is sourced from TomTom only (OCM coverage unreliable/absent there). "
            "Run ingestion.extract_tomtom + ingestion.merge_tomtom_stations instead."
        )
        return 2

    if args.city:
        cities = {args.city.lower(): CITIES[args.city.lower()]}
    else:
        skipped = sorted(c for c in CITIES if c in OCM_EXCLUDED_CITIES)
        if skipped:
            logger.info(
                f"Skipping OCM-excluded cities (TomTom-only): {', '.join(skipped)}"
            )
        cities = {k: v for k, v in CITIES.items() if k not in OCM_EXCLUDED_CITIES}

    session = get_session()
    conn = get_connection()
    create_table(conn)

    ok = []
    failed = []
    total = 0

    for i, (name, cfg) in enumerate(cities.items()):
        if i > 0:
            time.sleep(1)

        count = extract_city(
            session,
            conn,
            name,
            cfg,
            args.max_results,
            args.distance,
        )

        if count:
            ok.append(name)
        else:
            failed.append(name)

        total += count
        print(f"[{i+1}/{len(cities)}] {name}: {count} stations", flush=True)

    conn.close()

    logger.info("=" * 70)
    logger.info("OPEN CHARGE MAP INGESTION SUMMARY (FULL CONNECTOR DATA)")
    logger.info("=" * 70)
    logger.info(f"Successful Cities : {len(ok)} {ok}")
    logger.info(f"Failed Cities     : {len(failed)} {failed}")
    logger.info(f"Total Stations    : {total}")
    logger.info(
        "Output            : PostgreSQL bronze.ocm_raw (with Connections array)"
    )
    logger.info("=" * 70)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())