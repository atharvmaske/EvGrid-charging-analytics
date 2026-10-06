
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import psycopg2
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evgrid.smard")

SMARD_BASE_URL = os.getenv("SMARD_BASE_URL", "https://www.smard.de/app/chart_data")
SMARD_FILTER_ID = os.getenv("SMARD_FILTER_ID", "4169")   
SMARD_REGION = os.getenv("SMARD_REGION", "DE-LU")     
RESOLUTION = "hour"

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


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


def get_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "evgrid-charging-analytics/1.0", "Accept": "application/json"})
    return session


def fetch_index(session):
    url = f"{SMARD_BASE_URL}/{SMARD_FILTER_ID}/{SMARD_REGION}/index_{RESOLUTION}.json"
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.json().get("timestamps", [])


def fetch_series(session, ts_ms):
    url = f"{SMARD_BASE_URL}/{SMARD_FILTER_ID}/{SMARD_REGION}/{SMARD_FILTER_ID}_{SMARD_REGION}_{RESOLUTION}_{ts_ms}.json"
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.json().get("series", [])


def parse_series(series):

    out = []
    for item in series:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue

        ts_ms, price = item
        if price is None:
            continue

        try:
            hour_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(
                minute=0, second=0, microsecond=0, tzinfo=None
            )
            out.append((hour_utc, float(price)))
        except (TypeError, ValueError, OSError):
            continue

    return out


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS bronze;

            CREATE TABLE IF NOT EXISTS bronze.day_ahead_prices_de (
                hour_utc TIMESTAMP PRIMARY KEY,
                price_eur_per_mwh DOUBLE PRECISION,
                price_eur_per_kwh DOUBLE PRECISION,
                source TEXT DEFAULT 'smard',
                ingestion_timestamp TIMESTAMP
            );
        """)
    conn.commit()


def upsert_prices(conn, hourly_prices):
    now = datetime.now(timezone.utc)
    rows = [
        (hour, price_mwh, round(price_mwh / 1000.0, 5), now)
        for hour, price_mwh in hourly_prices
    ]

    with conn.cursor() as cur:
        for hour, price_mwh, price_kwh, ts in rows:
            cur.execute(
                """
                INSERT INTO bronze.day_ahead_prices_de
                    (hour_utc, price_eur_per_mwh, price_eur_per_kwh, source, ingestion_timestamp)
                VALUES (%s, %s, %s, 'smard', %s)
                ON CONFLICT (hour_utc) DO UPDATE SET
                    price_eur_per_mwh = EXCLUDED.price_eur_per_mwh,
                    price_eur_per_kwh = EXCLUDED.price_eur_per_kwh,
                    ingestion_timestamp = EXCLUDED.ingestion_timestamp
                """,
                (hour, price_mwh, price_kwh, ts),
            )
    conn.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=2,
                     help="how many of the most recent index entries to (re)fetch (default 2, to catch late-published hours)")
    args = ap.parse_args()

    session = get_session()

    try:
        timestamps = fetch_index(session)
    except requests.exceptions.RequestException as exc:
        sys.exit(f"SMARD index request failed: {exc}. See this file's docstring if the API has changed.")

    if not timestamps:
        sys.exit("SMARD index returned no timestamps -- check SMARD_* env vars / API shape.")

    recent = sorted(timestamps)[-args.days_back:]
    logger.info(f"Fetching {len(recent)} index file(s) from SMARD ({SMARD_REGION}, filter {SMARD_FILTER_ID})")

    hourly_prices = {}
    for ts in recent:
        try:
            series = fetch_series(session, ts)
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Failed to fetch series for index {ts}: {exc}")
            continue

        for hour, price in parse_series(series):
            hourly_prices[hour] = price

    if not hourly_prices:
        sys.exit("No day-ahead prices parsed from any fetched file -- check SMARD_* env vars / API shape.")

    conn = get_connection()
    create_table(conn)
    n = upsert_prices(conn, sorted(hourly_prices.items()))
    conn.close()

    oldest, newest = min(hourly_prices), max(hourly_prices)
    logger.info(f"Upserted {n} hourly day-ahead prices, {oldest:%Y-%m-%d %H:%M} to {newest:%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
