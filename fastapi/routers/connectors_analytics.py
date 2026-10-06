from fastapi import APIRouter, HTTPException
from sqlalchemy import text
import json

from services.db import engine

router = APIRouter(prefix="/analytics/connectors", tags=["Connector Analytics"])


@router.get("/{city}/summary")
def get_connector_summary(city: str):

    query = text("""
        SELECT
            city,
            connector_type,
            current_type,
            stations_with_type,
            total_connectors_of_type,
            pct_of_all_connectors,
            avg_power_kw
        FROM gold.fct_connector_distribution
        WHERE LOWER(city) = LOWER(:city)
        ORDER BY pct_of_all_connectors DESC
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"city": city}).mappings().all()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    if not rows:
        return {
            "city": city.title(),
            "total_stations": 0,
            "total_connectors": 0,
            "connector_types": [],
            "ac_percentage": 0,
            "dc_percentage": 0,
        }

    connector_types = [dict(row) for row in rows]

    total_connectors = sum(
        ct["total_connectors_of_type"] for ct in connector_types
    )

    with engine.connect() as conn:
        total_stations = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM bronze.charging_stations
                WHERE LOWER(city) = LOWER(:city)
                AND connectors_data IS NOT NULL
            """),
            {"city": city},
        ).scalar()

    ac_total = sum(
        ct["total_connectors_of_type"]
        for ct in connector_types
        if ct["current_type"] == "AC"
    )

    dc_total = sum(
        ct["total_connectors_of_type"]
        for ct in connector_types
        if ct["current_type"] == "DC"
    )

    ac_pct = round(100 * ac_total / total_connectors, 1) if total_connectors > 0 else 0
    dc_pct = round(100 * dc_total / total_connectors, 1) if total_connectors > 0 else 0

    return {
        "city": city.title(),
        "total_stations": total_stations,
        "total_connectors": total_connectors,
        "ac_percentage": ac_pct,
        "dc_percentage": dc_pct,
        "connector_types": connector_types,
    }


@router.get("/{city}/power-distribution")
def get_power_distribution(city: str):

    query = text("""
        SELECT
            avg_power_kw,
            current_type,
            total_connectors_of_type,
            stations_with_type,
            ROUND(
                100.0 * total_connectors_of_type /
                SUM(total_connectors_of_type) OVER (),
                1
            ) AS percentage
        FROM gold.fct_connector_distribution
        WHERE LOWER(city) = LOWER(:city)
        ORDER BY avg_power_kw DESC
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"city": city}).mappings().all()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    power_distribution = [dict(row) for row in rows]

    categories = {
        "slow": [],
        "fast": [],
        "rapid": [],
    }

    for item in power_distribution:
        power = item.get("avg_power_kw", 0)

        if power < 11:
            categories["slow"].append(item)
        elif power <= 50:
            categories["fast"].append(item)
        else:
            categories["rapid"].append(item)

    return {
        "city": city.title(),
        "categories": {
            "slow_charging": {
                "connectors": sum(
                    i["total_connectors_of_type"]
                    for i in categories["slow"]
                ),
                "stations": sum(
                    i["stations_with_type"]
                    for i in categories["slow"]
                ),
                "details": categories["slow"],
            },
            "fast_charging": {
                "connectors": sum(
                    i["total_connectors_of_type"]
                    for i in categories["fast"]
                ),
                "stations": sum(
                    i["stations_with_type"]
                    for i in categories["fast"]
                ),
                "details": categories["fast"],
            },
            "rapid_charging": {
                "connectors": sum(
                    i["total_connectors_of_type"]
                    for i in categories["rapid"]
                ),
                "stations": sum(
                    i["stations_with_type"]
                    for i in categories["rapid"]
                ),
                "details": categories["rapid"],
            },
        },
    }


@router.get("/global/comparison")
def get_global_connector_comparison():

    query = text("""
        SELECT
            city,
            COUNT(DISTINCT CASE WHEN current_type = 'AC'
                THEN connector_type END) AS ac_types,
            COUNT(DISTINCT CASE WHEN current_type = 'DC'
                THEN connector_type END) AS dc_types,
            SUM(CASE WHEN current_type = 'AC'
                THEN total_connectors_of_type ELSE 0 END) AS ac_connectors,
            SUM(CASE WHEN current_type = 'DC'
                THEN total_connectors_of_type ELSE 0 END) AS dc_connectors,
            SUM(total_connectors_of_type) AS total_connectors,
            COUNT(DISTINCT stations_with_type) AS total_stations,
            ROUND(AVG(avg_power_kw), 1) AS avg_power_kw,
            MAX(max_power_kw) AS max_power_kw
        FROM gold.fct_connector_distribution
        GROUP BY city
        ORDER BY total_connectors DESC
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    cities = [dict(row) for row in rows]

    return {
        "cities_compared": len(cities),
        "total_global_connectors": sum(
            c["total_connectors"] for c in cities
        ),
        "cities": cities,
    }


@router.get("/station/{station_id}")
def get_station_connectors(station_id: int):

    query = text("""
        SELECT
            station_id,
            station_name,
            city,
            connectors,
            connectors_data
        FROM bronze.charging_stations
        WHERE station_id = :sid
    """)

    try:
        with engine.connect() as conn:
            row = conn.execute(
                query,
                {"sid": station_id},
            ).mappings().first()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Station {station_id} not found"
        )

    station = dict(row)

    if station.get("connectors_data"):
        try:
            connectors = (
                json.loads(station["connectors_data"])
                if isinstance(station["connectors_data"], str)
                else station["connectors_data"]
            )
        except Exception:
            connectors = []
    else:
        connectors = []

    ac_count = sum(
        c.get("quantity", 1)
        for c in connectors
        if c.get("current_type_name") == "AC"
    )

    dc_count = sum(
        c.get("quantity", 1)
        for c in connectors
        if c.get("current_type_name") == "DC"
    )

    power_ratings = [
        c.get("power_kw")
        for c in connectors
        if c.get("power_kw")
    ]

    avg_power = (
        sum(power_ratings) / len(power_ratings)
        if power_ratings
        else 0
    )

    return {
        "station_id": station["station_id"],
        "station_name": station["station_name"],
        "city": station["city"],
        "total_connectors": station["connectors"],
        "ac_connectors": ac_count,
        "dc_connectors": dc_count,
        "average_power_kw": round(avg_power, 1),
        "connector_types": connectors,
    }


@router.get("/{city}/ac-dc-ratio")
def get_ac_dc_ratio(city: str):

    query = text("""
        SELECT
            current_type,
            SUM(total_connectors_of_type) AS total_connectors,
            COUNT(DISTINCT connector_type) AS unique_types,
            COUNT(DISTINCT stations_with_type) AS stations_offering,
            ROUND(AVG(avg_power_kw), 1) AS avg_power_kw,
            MAX(max_power_kw) AS max_power_kw
        FROM gold.fct_connector_distribution
        WHERE LOWER(city) = LOWER(:city)
        GROUP BY current_type
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"city": city}).mappings().all()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    data = {
        row["current_type"]: dict(row)
        for row in rows
    }

    total = sum(d["total_connectors"] for d in data.values())

    return {
        "city": city.title(),
        "total_connectors": total,
        "by_current_type": {
            current_type: {
                **details,
                "percentage": (
                    round(
                        100 * details["total_connectors"] / total,
                        1,
                    )
                    if total > 0
                    else 0
                ),
            }
            for current_type, details in data.items()
        },
    }