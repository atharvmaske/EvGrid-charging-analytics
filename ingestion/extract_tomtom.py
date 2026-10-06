import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests

from config.cities import CITIES, LIVE_CITIES
from ingestion.tomtom_keys import TomTomKeyPool, request_with_rotation

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("evgrid.tomtom")

KEY_POOL = TomTomKeyPool.from_env()

SEARCH_URL = "https://api.tomtom.com/search/2/poiSearch/.json"
AVAILABILITY_URL = "https://api.tomtom.com/search/2/chargingAvailability.json"

BRONZE_OUTPUT_DIR = Path("data/bronze/tomtom")

PAGE_SIZE = 100


def create_session():
    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    session = requests.Session()

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)

    session.headers.update(
        {
            "User-Agent": "evgrid-charging-analytics/1.0",
            "Accept": "application/json",
        }
    )

    return session


def search_stations(session, city_name, city_config):
    logger.info(f"[{city_name.upper()}] Searching charging stations")

    all_results = []
    offset = 0
    total_results = None

    while True:
        params = {
            "lat": city_config["latitude"],
            "lon": city_config["longitude"],
            "radius": city_config["radius_km"] * 1000,
            "limit": PAGE_SIZE,
            "categorySet": 7309,
        }

        if offset > 0:
            params["ofs"] = offset

        response = request_with_rotation(session, SEARCH_URL, params, KEY_POOL, timeout=30)

        response.raise_for_status()

        payload = response.json()

        batch = payload.get("results", [])

        if total_results is None:
            total_results = payload.get(
                "summary", {}
            ).get("totalResults", len(batch))

        if not batch:
            break

        all_results.extend(batch)

        logger.info(
            f"[{city_name.upper()}] Fetched {len(all_results)} / {total_results}"
        )

        offset += PAGE_SIZE

        if offset >= total_results:
            break

        time.sleep(0.2)

    logger.info(
        f"[{city_name.upper()}] Total Stations Found : {len(all_results)}"
    )

    return all_results


def fetch_availability(session, availability_id):
    params = {
        "chargingAvailability": availability_id,
    }

    response = request_with_rotation(session, AVAILABILITY_URL, params, KEY_POOL, timeout=30)

    if not response.ok:
        logger.warning(
            f"Availability API failed | ID={availability_id} | HTTP={response.status_code}"
        )
        return None

    return response.json()


def extract_city(session, city_name, city_config):
    if city_name not in LIVE_CITIES:
        logger.info(f"[{city_name.upper()}] Simulator city. Skipping TomTom.")
        return 0

    logger.info("=" * 70)
    logger.info(f"[{city_name.upper()}] Starting TomTom Bronze ingestion")

    search_results = search_stations(session, city_name, city_config)

    station_records = []

    for result in search_results:
        poi = result.get("poi", {})
        address = result.get("address", {})
        position = result.get("position", {})

        availability_id = (
            result.get("dataSources", {})
            .get("chargingAvailability", {})
            .get("id")
        )

        availability_data = None

        if availability_id:
            availability_data = fetch_availability(
                session,
                availability_id,
            )

            time.sleep(0.05)

        station_records.append(
            {
                "tomtom_id": result.get("id"),
                "station_name": poi.get("name"),
                "address": address.get("freeformAddress"),
                "latitude": position.get("lat"),
                "longitude": position.get("lon"),
                "charging_availability_id": availability_id,
                "availability": availability_data,
            }
        )

    partition_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    bronze_record = {
        "source": "tomtom",
        "api": "chargingAvailability",
        "city": city_name,
        "country_code": city_config["country_code"],
        "record_count": len(station_records),
        "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "data": station_records,
    }

    output_dir = (
        BRONZE_OUTPUT_DIR
        / f"city={city_name}"
        / f"date={partition_date}"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "availability.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(
            bronze_record,
            file,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        f"[{city_name.upper()}] Records Saved : {len(station_records)}"
    )
    logger.info(
        f"[{city_name.upper()}] Bronze File   : {output_file}"
    )
    logger.info("=" * 70)

    return len(station_records)


def main():
    parser = argparse.ArgumentParser(
        description="Extract TomTom EV charging station live availability."
    )

    parser.add_argument(
        "--city",
        type=str,
        help="Extract one city only.",
    )

    args = parser.parse_args()

    session = create_session()

    processed = 0
    skipped = 0
    failed = 0
    total_records = 0

    if args.city:
        city = args.city.lower()

        if city not in CITIES:
            logger.error(f"Invalid city: {city}")
            logger.info(
                f"Available cities: {', '.join(CITIES.keys())}"
            )
            return

        try:
            records = extract_city(
                session,
                city,
                CITIES[city],
            )

            if records:
                processed += 1
                total_records += records
            else:
                skipped += 1

        except Exception as error:
            failed += 1
            logger.exception(
                f"[{city.upper()}] Extraction failed | {error}"
            )

    else:
        logger.info(
            f"Starting TomTom Bronze ingestion for {len(CITIES)} configured cities."
        )

        for city_name, city_config in CITIES.items():
            try:
                records = extract_city(
                    session,
                    city_name,
                    city_config,
                )

                if records:
                    processed += 1
                    total_records += records
                else:
                    skipped += 1

            except Exception as error:
                failed += 1
                logger.exception(
                    f"[{city_name.upper()}] Extraction failed | {error}"
                )

    logger.info("")
    logger.info("=" * 70)
    logger.info("TOMTOM BRONZE INGESTION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Processed Cities : {processed}")
    logger.info(f"Skipped Cities   : {skipped}")
    logger.info(f"Failed Cities    : {failed}")
    logger.info(f"Total Records    : {total_records}")
    logger.info(f"Bronze Output    : {BRONZE_OUTPUT_DIR}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()