import argparse
import json
import os
import sys

from dotenv import load_dotenv
from psycopg2.extras import execute_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

DOCKER_HOSTNAMES = {"postgres", "evgrid-postgres", "db"}


def resolve_host(host):
    if host in DOCKER_HOSTNAMES and not os.path.exists("/.dockerenv"):
        return "localhost"
    return host or "localhost"


def get_engine():
    return create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            host=resolve_host(os.getenv("POSTGRES_HOST", "localhost")),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB"),
        )
    )


def extract_connectors(connections_array):
    if not connections_array or not isinstance(connections_array, list):
        return 0, None

    connectors_list = []

    for conn in connections_array:
        if not isinstance(conn, dict):
            continue

        connector_info = {
            "id": conn.get("ID"),
            "type_id": conn.get("ConnectionTypeID"),
            "type_name": None,
            "current_type": conn.get("CurrentTypeID"),
            "current_type_name": None,
            "power_kw": conn.get("PowerKW"),
            "quantity": conn.get("Quantity", 1),
        }

        connection_type = conn.get("ConnectionType") or {}
        if isinstance(connection_type, dict):
            connector_info["type_name"] = connection_type.get("Title", "Unknown")

        current_type = conn.get("CurrentType") or {}
        if isinstance(current_type, dict):
            connector_info["current_type_name"] = current_type.get("Title", "AC")

        connectors_list.append(connector_info)

    if not connectors_list:
        return 0, None

    def _qty(value):
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return 1

    connector_count = sum(_qty(c.get("quantity")) for c in connectors_list)

    return connector_count, json.dumps(connectors_list)


STATION_SELECT = """
    SELECT DISTINCT ON ((station_data->>'ID')::BIGINT)
        (station_data->>'ID')::BIGINT AS station_id,
        city,
        country_code,
        COALESCE(
            station_data->'AddressInfo'->>'Title',
            station_data->'AddressInfo'->>'AddressLine1',
            station_data->'AddressInfo'->>'AddressLine2',
            ('EV Station ' || (station_data->>'ID'))
        ) AS station_name,
        (station_data->'AddressInfo'->>'Latitude')::DOUBLE PRECISION AS latitude,
        (station_data->'AddressInfo'->>'Longitude')::DOUBLE PRECISION AS longitude,
        COALESCE(jsonb_array_length(station_data->'Connections'), 0) AS connectors,
        station_data->'Connections' AS connectors_data,
        ingestion_timestamp
    FROM bronze.ocm_raw
    WHERE station_data->>'ID' IS NOT NULL
      {city_filter}
    ORDER BY (station_data->>'ID')::BIGINT, ingestion_timestamp DESC
"""

COLUMNS = (
    "station_id, city, country_code, station_name, latitude, longitude, "
    "connectors, connectors_data, ingestion_timestamp"
)


def sync(engine, city=None):
    filt = "AND lower(city) = lower(:city)" if city else ""
    params = {"city": city} if city else {}
    select = STATION_SELECT.format(city_filter=filt)

    with engine.begin() as conn:
        raw = conn.execute(
            text(f"SELECT count(*) FROM bronze.ocm_raw WHERE true {filt}"),
            params,
        ).scalar()

        if not raw:
            sys.exit(
                f"bronze.ocm_raw has no rows{f' for {city!r}' if city else ''}. Run "
                f"'python -m ingestion.extract_ocm{f' --city {city}' if city else ''}' first."
            )

        rows = conn.execute(text(select), params).fetchall()

        stations_data = []

        for row in rows:
            station_id, c, cc, name, lat, lon, conn_count, conn_data, ts = row

            count, connectors_json = extract_connectors(conn_data)

            stations_data.append(
                {
                    "station_id": station_id,
                    "city": c,
                    "country_code": cc,
                    "station_name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "connectors": count,
                    "connectors_data": connectors_json,
                    "ingestion_timestamp": ts,
                }
            )

        updated = 0

        for station in stations_data:
            result = conn.execute(
                text("""
                UPDATE bronze.charging_stations
                SET station_name = :name, latitude = :lat, longitude = :lon,
                    connectors = :count, connectors_data = :conn_data,
                    ingestion_timestamp = :ts
                WHERE station_id = :sid
                  AND (station_name, latitude, longitude, connectors)
                      IS DISTINCT FROM (:name, :lat, :lon, :count)
            """),
                {
                    "sid": station["station_id"],
                    "name": station["station_name"],
                    "lat": station["latitude"],
                    "lon": station["longitude"],
                    "count": station["connectors"],
                    "conn_data": station["connectors_data"],
                    "ts": station["ingestion_timestamp"],
                },
            )

            updated += result.rowcount

        inserted = 0

        for station in stations_data:
            result = conn.execute(
                text("""
                INSERT INTO bronze.charging_stations
                  (station_id, city, country_code, station_name, latitude,
                   longitude, connectors, connectors_data, ingestion_timestamp)
                SELECT :sid, :c, :cc, :name, :lat, :lon, :count, :conn_data, :ts
                WHERE NOT EXISTS (
                    SELECT 1 FROM bronze.charging_stations WHERE station_id = :sid
                )
            """),
                {
                    "sid": station["station_id"],
                    "c": station["city"],
                    "cc": station["country_code"],
                    "name": station["station_name"],
                    "lat": station["latitude"],
                    "lon": station["longitude"],
                    "count": station["connectors"],
                    "conn_data": station["connectors_data"],
                    "ts": station["ingestion_timestamp"],
                },
            )

            inserted += result.rowcount

        rows = conn.execute(
            text(
                "SELECT city, count(*) FROM bronze.charging_stations GROUP BY city ORDER BY city"
            )
        ).fetchall()

    print(f"{raw} raw records -> {inserted} stations added, {updated} updated")
    print("Real connector data now stored for all stations:")

    for c, n in rows:
        print(f"  {c:<14}{n:>6}")


def rebuild(engine):
    print("=" * 60)
    print("REBUILDING bronze.charging_stations FROM bronze.ocm_raw (all cities, with real connector data)")
    print("=" * 60)

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE bronze.availability, bronze.charging_stations RESTART IDENTITY CASCADE"
            )
        )

        rows = conn.execute(text(STATION_SELECT.format(city_filter=""))).fetchall()

        stations_data = []

        for row in rows:
            station_id, c, cc, name, lat, lon, conn_count, conn_data, ts = row

            count, connectors_json = extract_connectors(conn_data)

            stations_data.append(
                {
                    "station_id": station_id,
                    "city": c,
                    "country_code": cc,
                    "station_name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "connectors": count,
                    "connectors_data": connectors_json,
                    "ingestion_timestamp": ts,
                }
            )

        print(f"Stations extracted: {len(stations_data)}")

        raw_conn = conn.connection.dbapi_connection

        with raw_conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO bronze.charging_stations
                    (station_id, city, country_code, station_name, latitude,
                     longitude, connectors, connectors_data, ingestion_timestamp)
                VALUES %s
                """,
                [
                    (
                        s["station_id"],
                        s["city"],
                        s["country_code"],
                        s["station_name"],
                        s["latitude"],
                        s["longitude"],
                        s["connectors"],
                        s["connectors_data"],
                        s["ingestion_timestamp"],
                    )
                    for s in stations_data
                ],
            )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT city, COUNT(*) AS stations FROM bronze.charging_stations GROUP BY city ORDER BY city"
            )
        ).fetchall()

        print("\nCity Summary:")

        for c, n in rows:
            print(f"  {c:<14}{n:>6}")

        result = conn.execute(
            text("""
            SELECT
                COUNT(*) AS total,
                COUNT(connectors_data) AS with_connector_data
            FROM bronze.charging_stations
        """)
        ).fetchone()

        print("\nConnector Data:")
        print(f"  Total stations:       {result[0]}")
        print(f"  With connector data:  {result[1]}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = ap.add_mutually_exclusive_group(required=True)

    group.add_argument(
        "--all",
        action="store_true",
        help="sync every city in bronze.ocm_raw (no truncation)",
    )

    group.add_argument(
        "--city",
        help="sync one city (no truncation)",
    )

    group.add_argument(
        "--rebuild",
        action="store_true",
        help="full rebuild of all cities (truncates!)",
    )

    args = ap.parse_args()

    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text("""
            ALTER TABLE bronze.charging_stations
            ADD COLUMN IF NOT EXISTS connectors_data JSONB
        """)
        )

    if args.rebuild:
        rebuild(engine)
    else:
        sync(engine, None if args.all else args.city.lower())


if __name__ == "__main__":
    main()