
import argparse
import hashlib
import io
import logging
import os
import re
import sys
from datetime import datetime, timezone

import pandas as pd
import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from config.cities import CITIES

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evgrid.bnetza")

BNETZA_PAGE_URL = "https://bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenkarte/start.html"


BNETZA_URL_PATTERN = re.compile(
    r"https://data\.bundesnetzagentur\.de/Bundesnetzagentur/DE/Fachthemen/"
    r"ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_\d{4}-\d{2}-\d{2}\.csv"
)
BNETZA_URL_FALLBACK = (
    "https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/"
    "ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_2026-09-01.csv"
)
BNETZA_URL = os.getenv("BNETZA_URL")  

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}

DE_CITY_ALIASES = {
    "berlin": {"include": ["berlin"], "exclude": []},
    "munich": {"include": ["muenchen", "munchen", "munich"], "exclude": []},
    "frankfurt": {"include": ["frankfurt"], "exclude": ["oder"]},
}

COLUMN_PATTERNS = {
    "operator": ["betreiber"],
    "street": ["strasse", "adresse"],
    "house_number": ["hausnummer"],
    "postcode": ["plz", "postleitzahl"],
    "city": ["ort", "stadt", "gemeinde"],
    "state": ["bundesland"],
    "latitude": ["breitengrad", "latitude"],
    "longitude": ["laengengrad", "longitude"],

    "aggregate_power_kw": ["nennleistung ladeeinrichtung"],
    "num_points": ["anzahl ladepunkte", "anzahl der ladepunkte"],
    "plug_types": ["steckertyp"],   
    "commissioning_date": ["inbetriebnahme"],
}


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


def normalize_col(name):
    s = str(name).strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


COLUMN_POWER_REGEX = re.compile(r"^(p\d+\s*\[kw\]|nennleistung\s*stecker\s*\d+)$")


def map_columns(columns):
    mapping = {}
    for raw in columns:
        norm = normalize_col(raw)
        matched = None

        for canon, patterns in COLUMN_PATTERNS.items():
            if any(p in norm for p in patterns):
                matched = canon
                break

        if matched is None and COLUMN_POWER_REGEX.match(norm):
            matched = "power_kw"

        if matched:
            mapping.setdefault(matched, []).append(raw)

    return mapping


def first(mapping, canon, row=None):
    cols = mapping.get(canon) or []
    if not cols:
        return None
    return row[cols[0]] if row is not None else cols[0]


def parse_power_kw(value):
    if value is None:
        return None

    s = re.sub(r"[^0-9,.\-]", "", str(value).strip())
    if not s or s in {"-", "."}:
        return None

    if "," in s:
        s = s.replace(".", "").replace(",", ".") 

    try:
        v = float(s)
    except ValueError:
        return None

    return v if v > 0 else None


def _is_missing(value):

    return value is None or (isinstance(value, float) and value != value)


def parse_plug_types(raw_values):
    types, seen = [], set()
    for raw in raw_values:
        if _is_missing(raw):
            continue
        for part in re.split(r"[;,/]", str(raw)):
            name = part.strip()
            if not name:
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                types.append(name)
    return types


def match_city(city_raw):
    if _is_missing(city_raw) or not city_raw:
        return None

    tokens = set(re.findall(r"[a-z0-9]+", normalize_col(city_raw)))

    for key, rule in DE_CITY_ALIASES.items():
        if any(a in tokens for a in rule["include"]) and not any(e in tokens for e in rule["exclude"]):
            return key

    return None


def extract_points(df, mapping):

    power_cols = mapping.get("power_kw", [])
    plug_cols = mapping.get("plug_types", [])
    rows = []

    for _, row in df.iterrows():
        if power_cols:
            points = []
            for i, col in enumerate(power_cols):
                power = parse_power_kw(row.get(col))
                if power is None:
                    continue
                plug_col = plug_cols[i] if i < len(plug_cols) else (plug_cols[0] if plug_cols else None)
                plugs = parse_plug_types([row.get(plug_col)]) if plug_col else []
                points.append({"power_kw": power, "plug_types": plugs})
        else:
            power = parse_power_kw(first(mapping, "aggregate_power_kw", row))
            n_raw = first(mapping, "num_points", row)
            try:
                n = max(int(float(str(n_raw).replace(",", "."))), 1) if n_raw not in (None, "") else 1
            except (TypeError, ValueError):
                n = 1
            plugs = parse_plug_types([row.get(c) for c in plug_cols]) if plug_cols else []
            points = [{"power_kw": power, "plug_types": plugs} for _ in range(n)]

        if not points:
            points = [{"power_kw": None, "plug_types": []}]

        def num(col):
            try:
                return float(str(first(mapping, col, row)).replace(",", "."))
            except (TypeError, ValueError):
                return None

        rows.append({
            "operator": first(mapping, "operator", row),
            "street": first(mapping, "street", row),
            "house_number": first(mapping, "house_number", row),
            "postcode": first(mapping, "postcode", row),
            "city_raw": first(mapping, "city", row),
            "state": first(mapping, "state", row),
            "lat": num("latitude"),
            "lon": num("longitude"),
            "commissioning_date": first(mapping, "commissioning_date", row),
            "points": points,
        })

    return rows


def rows_to_stations(parsed_rows):

    sites = {}

    for row in parsed_rows:
        if row["lat"] is None or row["lon"] is None:
            continue

        key_src = "|".join(str(row.get(f) or "") for f in
                            ("operator", "street", "house_number", "postcode")) + \
            f"|{round(row['lat'], 5)}|{round(row['lon'], 5)}"
        station_key = hashlib.sha1(key_src.encode()).hexdigest()[:16]

        site = sites.setdefault(station_key, {
            "station_key": station_key,
            "operator": row.get("operator"),
            "street": row.get("street"),
            "city_raw": row.get("city_raw"),
            "city_key": match_city(row.get("city_raw")),
            "lat": row["lat"],
            "lon": row["lon"],
            "commissioning_date": row.get("commissioning_date"),
            "powers": [],
            "plug_types": [],
        })

        for point in row["points"]:
            if point["power_kw"] is not None:
                site["powers"].append(point["power_kw"])
            site["plug_types"].extend(point["plug_types"])
        site["_point_count"] = site.get("_point_count", 0) + len(row["points"])

    stations = []
    for site in sites.values():
        powers = site.pop("powers")
        plug_types = parse_plug_types(site.pop("plug_types"))
        stations.append({
            **site,
            "connectors": site.pop("_point_count"),
            "avg_power_kw": round(sum(powers) / len(powers), 1) if powers else None,
            "max_power_kw": max(powers) if powers else None,
            "plug_types": plug_types,
        })

    return stations



def discover_csv_url(session=None):


    try:
        response = (session or requests).get(BNETZA_PAGE_URL, timeout=(10, 30))
        response.raise_for_status()
    except requests.exceptions.RequestException:
        return None

    match = BNETZA_URL_PATTERN.search(response.text)
    return match.group(0) if match else None


def resolve_csv_url(session=None):

    if BNETZA_URL:
        logger.info(f"Using BNETZA_URL override: {BNETZA_URL}")
        return BNETZA_URL

    discovered = discover_csv_url(session)
    if discovered:
        logger.info(f"Discovered current register URL: {discovered}")
        return discovered

    logger.warning(
        f"Could not discover the current register URL from {BNETZA_PAGE_URL}; "
        f"falling back to the last-known-good link, which may now be stale: {BNETZA_URL_FALLBACK}"
    )
    return BNETZA_URL_FALLBACK


def download_csv(url):
    response = requests.get(url, timeout=(10, 120))
    response.raise_for_status()
    return response.content


def find_header_row(text, max_scan=30, min_fields=5):

    for i, line in enumerate(text.splitlines()[:max_scan]):
        cells = [normalize_col(c) for c in line.split(";")]
        if "betreiber" in cells and len(cells) >= min_fields:
            return i
    return 0


def read_register(raw_bytes):
    header_scan_bytes = raw_bytes[:65536]

    last_exc = None
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            header_text = header_scan_bytes.decode(encoding, errors="ignore")
        except UnicodeDecodeError as exc:
            last_exc = exc
            continue

        header_row = find_header_row(header_text)
        try:
            return pd.read_csv(
                io.BytesIO(raw_bytes), sep=";", decimal=",",
                encoding=encoding, dtype=str, skip_blank_lines=True,
                header=header_row,
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_exc = exc

    raise RuntimeError(f"Could not parse the register CSV with any known encoding: {last_exc}")


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS bronze;

            CREATE TABLE IF NOT EXISTS bronze.bnetza_stations_de (
                station_key TEXT PRIMARY KEY,
                city TEXT,
                city_raw TEXT,
                operator TEXT,
                street TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                connectors INTEGER,
                avg_power_kw DOUBLE PRECISION,
                max_power_kw DOUBLE PRECISION,
                plug_types TEXT,
                commissioning_date TEXT,
                ingestion_timestamp TIMESTAMP
            );
        """)
    conn.commit()


def load_stations(conn, stations):
    now = datetime.now(timezone.utc)
    rows = [
        (
            s["station_key"], s["city_key"], s["city_raw"], s["operator"], s["street"],
            s["lat"], s["lon"], s["connectors"], s["avg_power_kw"], s["max_power_kw"],
            ";".join(s["plug_types"]) if s["plug_types"] else None,
            s["commissioning_date"], now,
        )
        for s in stations
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE bronze.bnetza_stations_de")
        execute_values(cur, """
            INSERT INTO bronze.bnetza_stations_de
                (station_key, city, city_raw, operator, street, latitude, longitude,
                 connectors, avg_power_kw, max_power_kw, plug_types,
                 commissioning_date, ingestion_timestamp)
            VALUES %s
        """, rows)
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", action="store_true",
                     help="print the detected column mapping and a sample, then exit without touching the DB")
    ap.add_argument("--city", help="only load this config/cities.py city (default: all configured DE cities)")
    args = ap.parse_args()

    de_cities = [k for k, v in CITIES.items() if v["country_code"] == "DE"]
    if args.city and args.city.lower() not in de_cities:
        sys.exit(f"{args.city!r} is not a DE city in config/cities.py. DE cities: {de_cities}")

    session = requests.Session()
    url = resolve_csv_url(session)

    logger.info(f"Downloading register from {url}")
    try:
        raw = download_csv(url)
    except requests.exceptions.RequestException as exc:
        sys.exit(
            f"Download failed: {exc}\n"
            f"Tried: {url}\n"
            f"The register is republished monthly at a dated URL discovered from "
            f"{BNETZA_PAGE_URL} -- if that page's layout changed, update BNETZA_URL_PATTERN "
            f"in ingestion/extract_bnetza.py, or set BNETZA_URL in .env to a known-good link "
            f"as a temporary workaround."
        )

    df = read_register(raw)
    mapping = map_columns(df.columns)

    logger.info(f"Register columns ({len(df.columns)}): {list(df.columns)}")
    logger.info(f"Detected mapping: { {k: v for k, v in mapping.items()} }")

    required = {"latitude", "longitude", "city"}
    missing = required - mapping.keys()
    if missing:
        sys.exit(
            f"Could not find columns for: {sorted(missing)}. The register's headers have "
            "likely changed -- update COLUMN_PATTERNS in ingestion/extract_bnetza.py "
            "after checking the real column names with --inspect."
        )

    parsed = extract_points(df, mapping)
    stations = rows_to_stations(parsed)
    target_cities = [args.city.lower()] if args.city else de_cities
    kept = [s for s in stations if s["city_key"] in target_cities]

    logger.info(
        f"{len(df)} register rows -> {len(stations)} grouped stations, "
        f"{len(kept)} in configured DE cities ({', '.join(target_cities)})"
    )

    if args.inspect:
        for s in kept[:5]:
            logger.info(f"  sample: {s}")
        logger.info("Inspect mode: nothing written to the database.")
        return

    if not kept:
        sys.exit("No stations matched any configured DE city -- check DE_CITY_ALIASES against --inspect output.")

    conn = get_connection()
    create_table(conn)
    load_stations(conn, kept)
    conn.close()

    for city in target_cities:
        n = sum(1 for s in kept if s["city_key"] == city)
        logger.info(f"  {city:<12}{n:>6} stations")


if __name__ == "__main__":
    main()