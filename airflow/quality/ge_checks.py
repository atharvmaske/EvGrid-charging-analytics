
import os
import sys
from urllib.parse import quote_plus

import great_expectations as gx
from great_expectations import expectations as gxe


def connection_string() -> str:
    user = os.environ["POSTGRES_USER"]
    password = quote_plus(os.environ["POSTGRES_PASSWORD"])
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "evgrid")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


WEATHER_QUERY = """
SELECT
    city,
    country_code,
    (weather_data->'current'->>'temperature_2m')::double precision       AS temperature_c,
    (weather_data->'current'->>'relative_humidity_2m')::double precision AS humidity_percent,
    (weather_data->'current'->>'wind_speed_10m')::double precision       AS wind_speed_kmh,
    COALESCE((weather_data->'current'->>'precipitation')::double precision, 0) AS precipitation_mm
FROM bronze.weather_raw
WHERE ingestion_timestamp >= now() - interval '6 hours'
"""


def _between(col, lo=None, hi=None):
    return gxe.ExpectColumnValuesToBeBetween(column=col, min_value=lo, max_value=hi)


def _not_null(col):
    return gxe.ExpectColumnValuesToNotBeNull(column=col)


CHECKS = {
    "charging_stations": {
        "table": ("bronze", "charging_stations"),
        "expectations": [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            _not_null("station_id"),
            gxe.ExpectColumnValuesToBeUnique(column="station_id"),
            _not_null("city"),
            _between("latitude", -90, 90),
            _between("longitude", -180, 180),
            _between("connectors", 0),
        ],
    },
    "availability": {
        "table": ("bronze", "availability"),
        "expectations": [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            _not_null("station_id"),
            gxe.ExpectColumnValuesToBeUnique(column="station_id"),
            _between("available_connectors", 0),
            _between("occupied_connectors", 0),
        ],
    },
    "weather": {
        "query": WEATHER_QUERY,
        "expectations": [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            _not_null("city"),
            _not_null("temperature_c"),
            _not_null("humidity_percent"),
            _not_null("wind_speed_kmh"),
            _between("temperature_c", -50, 60),
            _between("humidity_percent", 0, 100),
            _between("wind_speed_kmh", 0, 300),
            _between("precipitation_mm", 0, 500),
        ],
    },
}


def run_check(name: str) -> int:
    spec = CHECKS[name]
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_postgres(
        name=f"evgrid_{name}", connection_string=connection_string()
    )
    if "query" in spec:
        asset = source.add_query_asset(name=f"{name}_asset", query=spec["query"])
    else:
        schema, table = spec["table"]
        asset = source.add_table_asset(
            name=f"{name}_asset", table_name=table, schema_name=schema
        )
    batch = asset.add_batch_definition_whole_table("whole").get_batch()

    failures = 0
    for exp in spec["expectations"]:
        result = batch.validate(exp)
        ok = bool(result.success)
        failures += 0 if ok else 1
        col = getattr(exp, "column", "")
        print(f"[{name}] {'PASS' if ok else 'FAIL'}  {type(exp).__name__} {col}".rstrip())
    return failures


def run_all(names=None) -> int:
    failures = sum(run_check(n) for n in (names or CHECKS))
    print(f"Great Expectations: {failures} failed expectation(s)")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run_all(sys.argv[1:] or None) else 0)
