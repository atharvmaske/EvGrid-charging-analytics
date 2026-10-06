
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

HOUR = 3600
DAY = 24 * HOUR
DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def _resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


def _engine():
    return create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=_resolve_host(os.getenv("POSTGRES_HOST")),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "evgrid"),
        )
    )


def _age(agg, table, where="true"):
    return (
        f"SELECT EXTRACT(EPOCH FROM (now() AT TIME ZONE 'utc') - {agg}) "
        f"FROM {table} WHERE {where}"
    )


CHECKS = [
    ("weather observation, newest",
     _age("max((weather_data->'current'->>'time')::timestamp)", "bronze.weather_raw"),
     6 * HOUR, False),
    ("availability written, newest",
     _age("max(ingestion_timestamp)", "bronze.availability"),
     6 * HOUR, False),
    ("availability history, newest hour",
     _age("max(snapshot_hour)", "bronze.availability_history"),
     6 * HOUR, False),
    ("live-labelled availability, OLDEST row",
     _age("min(observed_at)", "bronze.availability", "source = 'live'"),
     DAY, True),
    ("station catalog, newest (weekly job)",
     _age("max(ingestion_timestamp)", "bronze.charging_stations"),
     8 * DAY, False),
]


def run_freshness_checks(engine=None):
    engine = engine or _engine()
    failures = 0

    for label, sql, max_age, allow_empty in CHECKS:
        try:
            with engine.connect() as conn:
                age = conn.execute(text(sql)).scalar()
        except Exception as exc:
            status, detail = "FAIL", f"query failed: {str(exc).strip().splitlines()[0]}"
        else:
            if age is None:
                status, detail = ("WARN", "no rows") if allow_empty else ("FAIL", "no rows")
            else:
                age = float(age)
                if age > max_age:
                    status, detail = "FAIL", f"{age / HOUR:.1f} h old, limit {max_age / HOUR:.0f} h"
                else:
                    status, detail = "PASS", f"{age / HOUR:.1f} h old"

        failures += status == "FAIL"
        print(f"[freshness] {status}  {label}: {detail}")

    print(f"Freshness: {failures} failed check(s)")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run_freshness_checks() else 0)
