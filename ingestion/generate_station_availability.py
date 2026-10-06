import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from config.cities import LIVE_CITIES, SIMULATOR_CITIES
from ingestion.station_matching import (
    MATCH_LOG_DDL,
    format_stats,
    log_match_stats,
    match_records,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("evgrid.availability")

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST"),
    "port": os.getenv("POSTGRES_PORT"),
    "dbname": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}

BRONZE_TOMTOM_DIR = Path("data/bronze/tomtom")

LIVE_MAX_AGE = timedelta(hours=float(os.getenv("LIVE_MAX_AGE_HOURS", "24")))

LIMIT_PER_CITY = None
RANDOM_SEED = 42


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def load_ocm_stations(cursor, city, limit=None):

    sql = """
        SELECT station_id, connectors
        FROM bronze.charging_stations
        WHERE LOWER(city)=LOWER(%s)
        ORDER BY station_id
    """

    params = [city]

    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    cursor.execute(sql, tuple(params))
    return cursor.fetchall()


def load_missing(cursor, city):

    cursor.execute(
        """
        SELECT cs.station_id, cs.connectors
        FROM bronze.charging_stations cs
        LEFT JOIN bronze.availability a
             ON cs.station_id = a.station_id
        WHERE LOWER(cs.city)=LOWER(%s)
          AND a.station_id IS NULL;
        """,
        (city,),
    )

    return cursor.fetchall()


def generate_availability(connectors):

    connectors = max(int(connectors or 1), 1)

    if connectors == 1:
        available = 1 if random.random() < 0.70 else 0

    else:
        ratio = random.triangular(0.25, 0.95, 0.68)

        available = round(connectors * ratio)

        if connectors >= 6:

            if available == connectors and random.random() < 0.85:
                available -= random.randint(1, max(1, connectors // 4))

            if available == 0 and random.random() < 0.90:
                available = random.randint(1, max(1, connectors // 3))

        available = max(0, min(available, connectors))

    occupied = connectors - available

    return available, occupied


def utcnow():
    
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upsert(cursor, station_id, city, available, occupied, total, source, observed_at=None):

    cursor.execute(
        """
        INSERT INTO bronze.availability (
            station_id,
            city,
            available_connectors,
            occupied_connectors,
            total_connectors,
            ingestion_timestamp,
            observed_at,
            source
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)

        ON CONFLICT (station_id)
        DO UPDATE SET
            city = EXCLUDED.city,
            available_connectors = EXCLUDED.available_connectors,
            occupied_connectors = EXCLUDED.occupied_connectors,
            total_connectors = EXCLUDED.total_connectors,
            ingestion_timestamp = EXCLUDED.ingestion_timestamp,
            observed_at = EXCLUDED.observed_at,
            source = EXCLUDED.source;
        """,
        (
            station_id,
            city,
            available,
            occupied,
            total,
            utcnow(),
            observed_at or utcnow(),
            source,
        ),
    )


def latest_file(city):

    city_dir = BRONZE_TOMTOM_DIR / f"city={city}"

    if not city_dir.exists():
        return None

    folders = sorted(city_dir.glob("date=*"), reverse=True)

    for folder in folders:
        file = folder / "availability.json"
        if file.exists():
            return file

    return None


def parse_observed_at(value):
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)

    return ts


def insert_live_city(cursor, city):
    file = latest_file(city)

    if file is None:
        logger.warning(f"{city.upper()} | Availability JSON not found.")
        return 0

    with open(file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    observed_at = parse_observed_at(payload.get("ingestion_timestamp"))
    age = utcnow() - observed_at if observed_at else None

    if age is None or age > LIVE_MAX_AGE:
        shown = "of unknown age" if age is None else f"{age.total_seconds() / 3600:.1f} h old"
        logger.warning(
            f"{city.upper()} | {file} is {shown} (limit {LIVE_MAX_AGE}); "
            "not labelling it live, stations fall back to simulated."
        )
        return 0

    logger.info(
        f"{city.upper()} | Using availability file: {file} "
        f"(observed {observed_at:%Y-%m-%d %H:%M} UTC)"
    )

    records = payload["data"]
    usable = []

    for station in records:
        availability = station.get("availability")

        if availability is None:
            continue

        groups = availability.get("connectors", [])

        if not groups:
            continue

        available = sum(
            g.get("availability", {}).get("current", {}).get("available", 0)
            for g in groups
        )

        occupied = sum(
            g.get("availability", {}).get("current", {}).get("occupied", 0)
            for g in groups
        )

        total = available + occupied
        lat = station.get("latitude")
        lon = station.get("longitude")

        if total == 0 or lat is None or lon is None:
            continue

        usable.append(
            {
                "lat": lat,
                "lon": lon,
                "name": station.get("station_name") or station.get("address"),
                "available": available,
                "occupied": occupied,
                "total": total,
            }
        )

    logger.info(
        f"{city.upper()} | Live stations : {len(records)} ({len(usable)} usable)"
    )

    cursor.execute(
        """
        SELECT station_id, latitude, longitude, station_name
        FROM bronze.charging_stations
        WHERE LOWER(city)=LOWER(%s);
        """,
        (city,),
    )

    candidates = [
        {"id": sid, "lat": lat, "lon": lon, "name": name}
        for sid, lat, lon, name in cursor.fetchall()
        if lat is not None and lon is not None
    ]

    matches, stats = match_records(usable, candidates, one_to_one=True)

    logger.info(f"{city.upper()} | {format_stats(stats)}")
    log_match_stats(cursor, "generate_station_availability", city, stats, observed_at)

    for m in matches:
        rec = usable[m.record_index]

        upsert(
            cursor,
            m.candidate_id,
            city,
            rec["available"],
            rec["occupied"],
            rec["total"],
            "live",
            observed_at,
        )

    logger.info(
        f"{city.upper()} | Live inserted : {len(matches)} | Unmatched : {stats['unmatched']}"
    )

    return len(matches)


def backfill_city(cursor, city):
    missing = load_missing(cursor, city)

    inserted = 0

    for station_id, connectors in missing:

        available, occupied = generate_availability(connectors)

        upsert(
            cursor=cursor,
            station_id=station_id,
            city=city,
            available=available,
            occupied=occupied,
            total=connectors,
            source="simulated",
        )

        inserted += 1

    logger.info(f"{city.upper()} | Backfilled with simulated : {inserted}")

    return inserted


def simulator_city(cursor, city):

    stations = load_ocm_stations(cursor, city, LIMIT_PER_CITY)

    inserted = 0

    for station_id, connectors in stations:

        available, occupied = generate_availability(connectors)
        total = available + occupied

        upsert(
            cursor,
            station_id,
            city,
            available,
            occupied,
            total,
            "simulated",
        )

        inserted += 1

    logger.info(f"{city.upper()} | Simulator inserted : {inserted}")

    return inserted


def main():

    random.seed(RANDOM_SEED)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bronze.availability (
        station_id BIGINT PRIMARY KEY,
        city VARCHAR(100),
        available_connectors INTEGER,
        occupied_connectors INTEGER,
        total_connectors INTEGER,
        ingestion_timestamp TIMESTAMP,
        observed_at TIMESTAMP,
        source TEXT
    );

    ALTER TABLE bronze.availability
    ADD COLUMN IF NOT EXISTS total_connectors INTEGER;

    ALTER TABLE bronze.availability
    ADD COLUMN IF NOT EXISTS source TEXT;

    ALTER TABLE bronze.availability
    ADD COLUMN IF NOT EXISTS observed_at TIMESTAMP;
    """)

    cursor.execute(MATCH_LOG_DDL)

    conn.commit()

    cursor.execute("TRUNCATE TABLE bronze.availability;")
    conn.commit()

    total_live = 0
    total_backfill = 0
    total_simulator = 0

    logger.info("=" * 60)
    logger.info("STARTING AVAILABILITY INGESTION")
    logger.info("=" * 60)

    logger.info("Loading Live TomTom Cities...")

    for city in sorted(LIVE_CITIES):

        try:
            total_live += insert_live_city(cursor, city)
            conn.commit()

        except Exception as e:
            conn.rollback()
            logger.exception(e)

    logger.info("-" * 60)
    logger.info("Backfilling Remaining Stations...")

    for city in sorted(LIVE_CITIES):

        try:
            total_backfill += backfill_city(cursor, city)
            conn.commit()

        except Exception as e:
            conn.rollback()
            logger.exception(e)

    logger.info("-" * 60)
    logger.info("Loading Simulator Cities...")

    for city in sorted(SIMULATOR_CITIES):

        try:
            total_simulator += simulator_city(cursor, city)
            conn.commit()

        except Exception as e:
            conn.rollback()
            logger.exception(e)

    cursor.close()
    conn.close()

    logger.info("=" * 60)
    logger.info(f"Live rows loaded        : {total_live}")
    logger.info(f"Simulated backfill rows : {total_backfill}")
    logger.info(f"Simulator-city rows     : {total_simulator}")
    logger.info(f"Total availability rows : {total_live + total_backfill + total_simulator}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
