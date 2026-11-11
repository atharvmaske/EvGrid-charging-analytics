import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from config.cities import CITIES, LIVE_CITIES
from ingestion.extract_tomtom import KEY_POOL, create_session, search_stations, fetch_availability
from ingestion.generate_station_availability import get_connection, upsert
from ingestion.station_matching import match_records, format_stats, log_match_stats

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evgrid.live_refresh")

DEFAULT_LOOKUP_CAP = int(os.getenv("TOMTOM_LIVE_LOOKUP_CAP", "150"))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def poi_to_record(result):
    """TomTom poiSearch result -> the {lat, lon, name} shape match_records()
    expects, or None if this POI has no usable coordinates or availability id
    (nothing to match or fetch, so not worth carrying further)."""
    poi = result.get("poi", {})
    address = result.get("address", {})
    position = result.get("position", {})

    availability_id = (
        result.get("dataSources", {})
        .get("chargingAvailability", {})
        .get("id")
    )

    lat, lon = position.get("lat"), position.get("lon")
    if lat is None or lon is None or not availability_id:
        return None

    return {
        "lat": lat,
        "lon": lon,
        "name": poi.get("name") or address.get("freeformAddress"),
        "availability_id": availability_id,
    }


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


def parse_connectors(availability_data):
    groups = (availability_data or {}).get("connectors", [])
    available = sum(g.get("availability", {}).get("current", {}).get("available", 0) for g in groups)
    occupied = sum(g.get("availability", {}).get("current", {}).get("occupied", 0) for g in groups)
    return available, occupied


def refresh_city(session, conn, city, cap):
    cursor = conn.cursor()

    search_results = search_stations(session, city, CITIES[city])
    records = [r for r in (poi_to_record(res) for res in search_results) if r is not None]

    if not records:
        logger.info(f"[{city.upper()}] no usable TomTom records this run")
        return 0

    candidates = load_candidates(cursor, city)
    matches, stats = match_records(records, candidates, one_to_one=True)
    logger.info(f"[{city.upper()}] {format_stats(stats)}")
    log_match_stats(cursor, "refresh_live_availability", city, stats)
    conn.commit()

    if len(matches) > cap:
        logger.info(f"[{city.upper()}] capping availability lookups to {cap} of {len(matches)} matches")
        matches = matches[:cap]

    observed_at = utcnow()
    inserted = 0

    for m in matches:
        availability_id = records[m.record_index]["availability_id"]
        data = fetch_availability(session, availability_id)
        time.sleep(0.05)

        if data is None:
            continue

        available, occupied = parse_connectors(data)
        total = available + occupied
        if total == 0:
            continue

        upsert(cursor, m.candidate_id, city, available, occupied, total, "live", observed_at)
        inserted += 1

    conn.commit()
    logger.info(f"[{city.upper()}] live upserted: {inserted} (of {len(matches)} matched, cap {cap})")
    return inserted


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", help="refresh one city only")
    ap.add_argument("--cap", type=int, default=DEFAULT_LOOKUP_CAP,
                     help=f"max availability credits spent per city (default {DEFAULT_LOOKUP_CAP}, "
                          "env TOMTOM_LIVE_LOOKUP_CAP)")
    args = ap.parse_args()

    if args.city:
        city = args.city.lower()
        if city not in LIVE_CITIES:
            sys.exit(f"{args.city!r} is not a live city. Live cities: {', '.join(sorted(LIVE_CITIES))}")
        cities = [city]
    else:
        cities = sorted(LIVE_CITIES)

    session = create_session()
    conn = get_connection()

    total_inserted = 0
    failures = []

    for city in cities:
        try:
            total_inserted += refresh_city(session, conn, city, args.cap)
        except Exception as exc:
            conn.rollback()
            failures.append(f"{city}: {exc}")
            logger.exception(f"[{city.upper()}] live refresh failed")

        if KEY_POOL.all_exhausted():
            logger.error("All TomTom keys exhausted -- stopping early rather than failing every remaining city")
            failures.append("stopped early: all TomTom keys exhausted")
            break

    conn.close()

    logger.info("=" * 60)
    logger.info(f"Cities processed : {len(cities)}")
    logger.info(f"Total live rows  : {total_inserted}")
    logger.info(f"Failures         : {len(failures)}")
    logger.info("=" * 60)

    if failures:
        sys.exit("live refresh issues: " + "; ".join(failures))


if __name__ == "__main__":
    main()