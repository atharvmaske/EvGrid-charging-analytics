import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from config.cities import CITIES, LIVE_CITIES
from ingestion.station_matching import (
    CLOSE_MATCH_M, MAX_MATCH_M, MIN_NAME_SIM,
    format_stats, haversine_m, log_match_stats, match_records, name_similarity,
)


def dedupe_new(usable, matched):
    """Collapse TomTom listings of the same site among the records about to be
    inserted (the catalog check can't see them yet). Richest listing wins."""
    order = sorted(
        (i for i in range(len(usable)) if i not in matched),
        key=lambda i: -connector_count(usable[i]["rec"].get("availability")),
    )
    kept = []
    for i in order:
        u = usable[i]
        for k in kept:
            o = usable[k]
            d = haversine_m(u["lat"], u["lon"], o["lat"], o["lon"])
            if d <= CLOSE_MATCH_M or (
                d <= MAX_MATCH_M and name_similarity(u["name"], o["name"]) >= MIN_NAME_SIM
            ):
                break
        else:
            kept.append(i)
    return sorted(kept), len(order) - len(kept)

load_dotenv()

BRONZE_TOMTOM_DIR = Path("data/bronze/tomtom")
DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


DB_CONFIG = {
    "host": resolve_host(os.getenv("POSTGRES_HOST")),
    "port": os.getenv("POSTGRES_PORT"),
    "dbname": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def tomtom_station_id(tomtom_id: str) -> int:
    h = int(hashlib.sha1(f"tomtom:{tomtom_id}".encode()).hexdigest()[:15], 16)
    return 900_000_000_000 + (h % 100_000_000_000)


def latest_file(city):
    city_dir = BRONZE_TOMTOM_DIR / f"city={city}"
    if not city_dir.exists():
        return None
    for folder in sorted(city_dir.glob("date=*"), reverse=True):
        file = folder / "availability.json"
        if file.exists():
            return file
    return None


def connector_count(availability):
    groups = (availability or {}).get("connectors", [])
    total = sum(
        g.get("availability", {}).get("current", {}).get("available", 0)
        + g.get("availability", {}).get("current", {}).get("occupied", 0)
        for g in groups
    )
    return max(total, 1)


def build_connectors_data(availability):
    groups = (availability or {}).get("connectors", [])
    if not groups:
        return None
    out = []
    for g in groups:
        cur = g.get("availability", {}).get("current", {})
        qty = max(cur.get("available", 0) + cur.get("occupied", 0), 1)
        out.append({
            "id": None,
            "type_id": None,
            "type_name": g.get("type") or "Unknown",
            "current_type": None,
            "current_type_name": None,
            "power_kw": None,
            "quantity": qty,
            "source": "tomtom",
        })
    return json.dumps(out)


def merge_city(conn, city, city_config):
    tag = f"[{city.upper()}]"
    file = latest_file(city)
    if file is None:
        print(f"{tag} No TomTom availability.json found")
        return 0

    with open(file, "r", encoding="utf-8") as f:
        records = json.load(f).get("data", [])
    print(f"{tag} TomTom records: {len(records)}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT station_id, latitude, longitude, station_name FROM bronze.charging_stations "
            "WHERE LOWER(city)=LOWER(%s)", (city,),
        )
        candidates = [
            {"id": sid, "lat": lat, "lon": lon, "name": name}
            for sid, lat, lon, name in cur.fetchall()
            if lat is not None and lon is not None
        ]

    usable, skipped = [], 0
    for rec in records:
        lat, lon, tid = rec.get("latitude"), rec.get("longitude"), rec.get("tomtom_id")
        if lat is None or lon is None or not tid:
            skipped += 1
            continue
        usable.append({
            "lat": lat, "lon": lon, "tid": tid, "rec": rec,
            "name": rec.get("station_name") or rec.get("address"),
        })

    matches, stats = match_records(usable, candidates, one_to_one=False)
    matched = {m.record_index for m in matches}
    print(f"{tag} {format_stats(stats)}")

    now = datetime.now(timezone.utc)
    kept, collapsed = dedupe_new(usable, matched)
    new_rows = [
        (
            tomtom_station_id(usable[i]["tid"]), city, city_config["country_code"],
            usable[i]["name"] or f"TomTom Station {usable[i]['tid']}",
            usable[i]["lat"], usable[i]["lon"],
            connector_count(usable[i]["rec"].get("availability")),
            build_connectors_data(usable[i]["rec"].get("availability")),
            now,
        )
        for i in kept
    ]
    print(f"{tag} Collapsed {collapsed} duplicate TomTom listing(s)")

    with conn.cursor() as cur:
        log_match_stats(cur, "merge_tomtom_stations", city, stats)
        if new_rows:
            execute_values(cur, """
                INSERT INTO bronze.charging_stations
                    (station_id, city, country_code, station_name, latitude,
                     longitude, connectors, connectors_data, ingestion_timestamp)
                VALUES %s
                ON CONFLICT (station_id) DO NOTHING
            """, new_rows)
    conn.commit()

    print(f"{tag} New TomTom-only stations added: {len(new_rows)} | Unusable records: {skipped}")
    return len(new_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city")
    ap.add_argument("--all", action="store_true", help="every live (TomTom) city")
    args = ap.parse_args()

    if not args.city and not args.all:
        sys.exit("Give --city <name> or --all")

    if args.city:
        key = args.city.lower()
        if key not in CITIES:
            sys.exit(f"Unknown city {args.city!r}. Available: {', '.join(CITIES)}")
        cities = {key: CITIES[key]}
    else:
        cities = {name: CITIES[name] for name in sorted(LIVE_CITIES)}

    conn = get_connection()
    total = sum(merge_city(conn, name, cfg) for name, cfg in cities.items())
    conn.close()
    print(f"\nTotal TomTom-only stations added: {total}")


if __name__ == "__main__":
    main()
