import logging
import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from config.provider_prices import PROVIDER_PRICES

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("evgrid.energy_prices")

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


DB_CONFIG = {
    "host": resolve_host(os.getenv("POSTGRES_HOST", "localhost")),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evgrid"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS bronze;

            CREATE TABLE IF NOT EXISTS bronze.energy_prices (
                city TEXT,
                tariff_per_kwh DOUBLE PRECISION,
                currency TEXT,
                ingestion_timestamp TIMESTAMP
            );
        """)
    conn.commit()


def main():
    logger.info("Starting Energy Price Bronze ingestion...")

    conn = get_connection()
    create_table(conn)
    cursor = conn.cursor()

    cursor.execute("TRUNCATE TABLE bronze.energy_prices;")

    now = datetime.now(timezone.utc)

    rows = [
        (
            row["city"],
            row["price_per_kwh"],
            row["currency"],
            now,
        )
        for row in PROVIDER_PRICES
    ]

    execute_values(
        cursor,
        "INSERT INTO bronze.energy_prices (city, tariff_per_kwh, currency, ingestion_timestamp) VALUES %s",
        rows,
    )

    conn.commit()

    cursor.close()
    conn.close()

    logger.info("=" * 60)
    logger.info("ENERGY PRICE INGESTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Rows Inserted : {len(rows)}  (table was truncated first)")
    logger.info("Output Table  : bronze.energy_prices")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()