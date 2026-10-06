import json
import time
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from sqlalchemy import text

from config.cities import CITIES

CONN_ID = "evgrid_postgres"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 5

_session = requests.Session()
_retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)
_session.mount("https://", HTTPAdapter(max_retries=_retry))

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,wind_speed_10m,"
    "precipitation,weather_code"
)

DEDUPE_WINDOW = timedelta(minutes=30)


def get_points(info: dict) -> dict:
    return info.get("weather_points") or {
        "center": {"latitude": info["latitude"], "longitude": info["longitude"]}
    }


def fetch_city_points(points: dict) -> list:
    names = list(points)

    params = {
        "latitude": ",".join(str(points[n]["latitude"]) for n in names),
        "longitude": ",".join(str(points[n]["longitude"]) for n in names),
        "current": CURRENT_FIELDS,
    }

    last_exc = None

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            response = _session.get(OPEN_METEO_URL, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            break
        except (requests.exceptions.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < FETCH_ATTEMPTS:
                time.sleep(FETCH_BACKOFF_SECONDS * attempt)
            continue
    else:
        raise last_exc

    if isinstance(payload, dict):
        payload = [payload]

    if len(payload) != len(names):
        raise ValueError(f"Expected {len(names)} points, got {len(payload)}")

    for item in payload:
        if "current" not in item:
            raise ValueError(f"No 'current' block in response: {item}")

    return list(zip(names, payload))


def ingest_weather_raw():
    engine = PostgresHook(postgres_conn_id=CONN_ID).get_sqlalchemy_engine()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ts = now.isoformat()
    cutoff = (now - DEDUPE_WINDOW).isoformat()

    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS bronze")
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS bronze.weather_raw (
                id SERIAL PRIMARY KEY,
                city VARCHAR(100),
                country_code VARCHAR(10),
                weather_point VARCHAR(100),
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                ingestion_timestamp TIMESTAMP,
                weather_data JSONB
            )
            """
        )

        already_loaded = {
            row[0].lower()
            for row in conn.execute(
                text(
                    "SELECT DISTINCT city FROM bronze.weather_raw "
                    "WHERE ingestion_timestamp >= :cutoff AND city IS NOT NULL"
                ),
                {"cutoff": cutoff},
            )
        }

    rows = []
    failures = []

    for city, info in CITIES.items():
        if city.lower() in already_loaded:
            print(f"{city}: already loaded in the last 30 min, skipping")
            continue

        points = get_points(info)

        try:
            results = fetch_city_points(points)
        except Exception as exc:
            failures.append(f"{city}: {exc}")
            continue

        for name, payload in results:
            rows.append(
                {
                    "city": city,
                    "country_code": info.get("country_code"),
                    "weather_point": name,
                    "latitude": points[name]["latitude"],
                    "longitude": points[name]["longitude"],
                    "ts": ts,
                    "data": json.dumps(payload),
                }
            )

    if rows:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO bronze.weather_raw "
                    "(city, country_code, weather_point, latitude, longitude, "
                    "ingestion_timestamp, weather_data) "
                    "VALUES (:city, :country_code, :weather_point, :latitude, "
                    ":longitude, :ts, :data)"
                ),
                rows,
            )

    print(
        f"Inserted {len(rows)} rows, skipped {len(already_loaded)} cities, "
        f"{len(failures)} cities failed"
    )

    if failures:
        raise RuntimeError("Weather fetch failed for: " + "; ".join(failures))


default_args = {
    "owner": "evgrid",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "execution_timeout": timedelta(minutes=10),
}

with DAG(
    dag_id="evgrid_weather_raw_pipeline",
    description="Hourly real weather from Open-Meteo into bronze.weather_raw",
    default_args=default_args,
    start_date=datetime(2026, 9, 1),
    schedule="50 * * * *",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    tags=["evgrid", "bronze", "weather"],
) as dag:

    PythonOperator(
        task_id="ingest_weather_raw",
        python_callable=ingest_weather_raw,
    )