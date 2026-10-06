import argparse
import csv
import io
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PG_CONTAINER = "evgrid-postgres"

COLUMNS = (
    "station_id, city, snapshot_hour, available_connectors, occupied_connectors, "
    "occupancy_rate, source, ingestion_timestamp, record_origin"
)


def psql(script, container):
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            "evgrid",
            "-d",
            "evgrid",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-t",
            "-A",
        ],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:500])

    return proc.stdout


def fetch_stations(api, city, when):
    url = f"{api}/availability/{city}?at={urllib.parse.quote(when.isoformat())}"

    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.load(resp).get("stations", [])


def build_script(city, when, stations):
    hour = when.strftime("%Y-%m-%d %H:%M:%S")

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    for s in stations:
        writer.writerow(
            [
                s["station_id"],
                s["city"],
                hour,
                s["available_connectors"],
                s["occupied_connectors"],
                s["occupancy_rate"],
                s.get("source", "simulated"),
                hour,
                "backfilled",
            ]
        )

    return (
        "BEGIN;\n"
        f"DELETE FROM bronze.availability_history WHERE record_origin = 'backfilled' "
        f"AND lower(city) = '{city}' AND snapshot_hour = '{hour}';\n"
        f"COPY bronze.availability_history ({COLUMNS}) FROM STDIN WITH (FORMAT csv);\n"
        f"{buf.getvalue()}\\.\n"
        "COMMIT;\n"
    )


def backfill_city(args, city):
    captured = {
        datetime.fromisoformat(line.strip())
        for line in psql(
            "SELECT DISTINCT snapshot_hour FROM bronze.availability_history "
            f"WHERE record_origin = 'captured' AND lower(city) = '{city}';",
            args.container,
        ).splitlines()
        if line.strip()
    }

    now_hour = datetime.now(timezone.utc).replace(
        minute=0,
        second=0,
        microsecond=0,
    )

    targets = [
        now_hour - timedelta(hours=k)
        for k in range(args.hours, 0, -1)
    ]

    todo = [
        h
        for h in targets
        if h.replace(tzinfo=None) not in captured
    ]

    print(
        f"{city}: {len(targets)} past hours, "
        f"{len(targets) - len(todo)} already captured, "
        f"{len(todo)} to backfill",
        flush=True,
    )

    if args.dry_run or not todo:
        return 0, 0

    done = 0
    rows = 0

    for when in todo:
        stations = fetch_stations(args.api, city, when)

        if not stations:
            print(
                f"  {city} {when:%Y-%m-%d %H}:00Z  API returned no stations - skipped"
            )
            continue

        psql(build_script(city, when, stations), args.container)

        done += 1
        rows += len(stations)

        if done % 12 == 0 or done == len(todo):
            print(f"  {city}: {done}/{len(todo)} hours", flush=True)

    return done, rows


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    ap.add_argument("--city")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--hours", type=int)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--container", default=PG_CONTAINER)
    ap.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    if bool(args.city) == bool(args.all):
        sys.exit("Give exactly one of --city <name> or --all")

    if args.city and not re.fullmatch(r"[a-z_]+", args.city):
        sys.exit("--city must be lowercase letters/underscores, e.g. berlin")

    if args.hours is None:
        args.hours = 72 if args.all else 168

    if (
        psql(
            "SELECT to_regclass('bronze.availability_history') IS NOT NULL;",
            args.container,
        ).strip()
        != "t"
    ):
        sys.exit(
            "bronze.availability_history does not exist yet. "
            "Run the ingest DAG once, then run this script again."
        )

    if args.all:
        cities = [
            c.strip()
            for c in psql(
                "SELECT DISTINCT lower(city) FROM bronze.availability ORDER BY 1;",
                args.container,
            ).splitlines()
            if c.strip()
        ]
    else:
        cities = [args.city]

    print(f"cities: {', '.join(cities)}", flush=True)

    total_hours = 0
    total_rows = 0

    for city in cities:
        done, rows = backfill_city(args, city)
        total_hours += done
        total_rows += rows

    if not args.dry_run:
        print(
            f"Backfilled {total_hours} city-hours, {total_rows} rows. "
            "Now run the ingest DAG (or dbt run --select mart_occupancy_hourly+)."
        )


if __name__ == "__main__":
    main()