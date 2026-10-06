import logging
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
import json
from dotenv import load_dotenv

from config.cities import CITIES as CONFIG_CITIES

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("evgrid.synthetic_sessions")

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}

def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"

DB_CONFIG = {
    "host": resolve_host(os.getenv("POSTGRES_HOST")),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evgrid"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}

CITIES = list(CONFIG_CITIES)

DEFAULT_CONNECTOR_TYPES = ["CCS", "Type2", "CHAdeMO"]


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def load_city_stations(cursor, city):
    cursor.execute(
        """
        SELECT station_id, station_name, connectors, connectors_data
        FROM bronze.charging_stations
        WHERE LOWER(city)=LOWER(%s)
        ORDER BY station_id;
        """,
        (city,),
    )

    rows = cursor.fetchall()

    stations = []
    for row in rows:
        station_id, name, conn_count, conn_data = row

        connector_types = DEFAULT_CONNECTOR_TYPES
        if conn_data:
            try:
                connectors = json.loads(conn_data) if isinstance(conn_data, str) else conn_data
                if isinstance(connectors, list) and connectors:
                    types_set = set()
                    for c in connectors:
                        if isinstance(c, dict):
                            type_name = c.get("type_name", "Unknown")
                            if type_name and type_name != "Unknown":
                                types_set.add(type_name)
                    if types_set:
                        connector_types = list(types_set)
                        logger.debug(f"Station {station_id}: Real connector types = {connector_types}")
            except Exception as e:
                logger.warning(f"Could not parse connector data for station {station_id}: {e}")

        stations.append(
            {
                "station_id": station_id,
                "station_name": name,
                "connectors": conn_count or 1,
                "connector_types": connector_types,
            }
        )

    return stations


def generate_city_sessions(cursor, city):
    stations = load_city_stations(cursor, city)

    if not stations:
        logger.warning("[%s] No stations found. Skipping.", city.upper())
        return 0

    inserted = 0
    now = datetime.now(timezone.utc)

    for station in stations:

        connector_count = station["connectors"] or 1
        connector_types = station["connector_types"]

        total_sessions = random.randint(
            connector_count * 10,
            connector_count * 30,
        )

        for _ in range(total_sessions):

            duration = random.randint(20, 120)
            energy = round(random.uniform(8, 60), 2)
            price = round(energy * random.uniform(0.22, 0.48), 2)

            start = now - timedelta(
                minutes=random.randint(0, 30 * 24 * 60)
            )

            end = start + timedelta(minutes=duration)

            connector_type = random.choice(connector_types)

            cursor.execute(
                """
                INSERT INTO bronze.synthetic_sessions (
                    station_id,
                    city,
                    connector_type,
                    energy_kwh,
                    duration_minutes,
                    start_time,
                    end_time,
                    cost,
                    ingestion_timestamp
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    station["station_id"],
                    city,
                    connector_type,
                    energy,
                    duration,
                    start,
                    end,
                    price,
                    now,
                ),
            )

            inserted += 1

    return inserted


def main():

    logger.info("Synthetic Session Generation Date : %s", datetime.now().date())
    logger.info("Generating synthetic sessions for %d cities.", len(CITIES))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bronze.synthetic_sessions (
        id SERIAL PRIMARY KEY,
        station_id BIGINT,
        city VARCHAR(100),
        connector_type VARCHAR(100),
        energy_kwh DOUBLE PRECISION,
        duration_minutes INTEGER,
        start_time TIMESTAMP,
        end_time TIMESTAMP,
        cost DOUBLE PRECISION,
        ingestion_timestamp TIMESTAMP
    );
    """)

    conn.commit()

    cursor.execute("TRUNCATE TABLE bronze.synthetic_sessions RESTART IDENTITY;")
    conn.commit()

    total = 0

    logger.info("=" * 70)
    logger.info("STARTING SYNTHETIC SESSION GENERATION")
    logger.info("=" * 70)

    for city in CITIES:

        logger.info("[%s] Generating synthetic charging sessions", city.upper())

        count = generate_city_sessions(cursor, city)

        conn.commit()

        logger.info("[%s] Sessions inserted : %s", city.upper(), count)

        logger.info("=" * 70)

        total += count

    cursor.close()
    conn.close()

    logger.info("")
    logger.info("=" * 70)
    logger.info("SYNTHETIC SESSION BRONZE SUMMARY")
    logger.info("=" * 70)
    logger.info("Processed Cities : %d", len(CITIES))
    logger.info("Failed Cities    : 0")
    logger.info("Total Sessions   : %d", total)
    logger.info("Output Table     : bronze.synthetic_sessions")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()