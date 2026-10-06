
import argparse
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from config.cities import CITIES
from core.db import get_engine
from core.stations import get_enriched_stations
from core.weather import get_city_weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evgrid.sync_bronze")


def sync_availability(engine, city):
    stations = get_enriched_stations(engine, city)
    if not stations:
        logger.warning(f"[{city.upper()}] no stations in bronze.charging_stations, nothing to sync")
        return 0

    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        for s in stations:
            conn.execute(
                text("""
                    INSERT INTO bronze.availability
                        (station_id, city, available_connectors, occupied_connectors,
                         total_connectors, ingestion_timestamp, observed_at, source)
                    VALUES (:sid, :city, :avail, :occ, :total, :ts, :observed, :source)
                    ON CONFLICT (station_id) DO UPDATE SET
                        city = EXCLUDED.city,
                        available_connectors = EXCLUDED.available_connectors,
                        occupied_connectors = EXCLUDED.occupied_connectors,
                        total_connectors = EXCLUDED.total_connectors,
                        ingestion_timestamp = EXCLUDED.ingestion_timestamp,
                        observed_at = EXCLUDED.observed_at,
                        source = EXCLUDED.source
                """),
                {
                    "sid": s["station_id"], "city": s["city"],
                    "avail": s["available_connectors"], "occ": s["occupied_connectors"],
                    "total": s["connectors"], "ts": now,
                    "observed": s["observed_at"] or now, "source": s["data_source"],
                },
            )

    live = sum(1 for s in stations if s["data_source"] == "live")
    logger.info(f"[{city.upper()}] synced {len(stations)} stations ({live} live, {len(stations) - live} simulated)")
    return len(stations)


def sync_weather(engine, city):
    row = get_city_weather(engine, city)
    if row is None:
        logger.warning(f"[{city.upper()}] no weather_raw data yet, nothing to sync")
        return 0

    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bronze.weather (
                city TEXT, temperature DOUBLE PRECISION, humidity DOUBLE PRECISION,
                wind_speed DOUBLE PRECISION, precipitation DOUBLE PRECISION,
                weather_code INTEGER, ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
            )
        """))
        conn.execute(
            text("DELETE FROM bronze.weather WHERE LOWER(city) = LOWER(:city)"),
            {"city": city},
        )
        conn.execute(
            text("""
                INSERT INTO bronze.weather
                    (city, temperature, humidity, wind_speed, precipitation, ingestion_timestamp, ingested_at)
                VALUES (:city, :temperature, :humidity, :wind_speed, :precipitation, :ts, :ts)
            """),
            {
                "city": city, "temperature": row["temperature"], "humidity": row["humidity"],
                "wind_speed": row["wind_speed"], "precipitation": row["precipitation"], "ts": now,
            },
        )

    logger.info(f"[{city.upper()}] weather synced: {row['temperature']}C")
    return 1


SYNC_FUNCTIONS = {
    "availability": sync_availability,
    "weather": sync_weather,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=sorted(SYNC_FUNCTIONS), required=True)
    ap.add_argument("--city")
    ap.add_argument("--all-cities", action="store_true")
    args = ap.parse_args()

    if bool(args.city) == bool(args.all_cities):
        sys.exit("Give exactly one of --city <name> or --all-cities")

    cities = [args.city.lower()] if args.city else list(CITIES)
    if args.city and args.city.lower() not in CITIES:
        sys.exit(f"Unknown city {args.city!r}. Available: {', '.join(CITIES)}")

    sync_fn = SYNC_FUNCTIONS[args.dataset]
    engine = get_engine()

    total, failures = 0, []
    for city in cities:
        try:
            total += sync_fn(engine, city)
        except Exception as exc:
            failures.append(f"{city}: {exc}")
            logger.exception(f"[{city.upper()}] {args.dataset} sync failed")

    logger.info(f"{args.dataset}: {total} row(s) synced across {len(cities)} city/cities, {len(failures)} failure(s)")

    if failures:
        sys.exit(f"{args.dataset} failed for: " + "; ".join(failures))


if __name__ == "__main__":
    main()
