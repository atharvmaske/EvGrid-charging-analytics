import json
import random
import zlib
from datetime import datetime, timezone

from config.cities import TIMEZONE_BY_CITY

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

CITY_TZ = TIMEZONE_BY_CITY

WEEKDAY = [
    0.10, 0.08, 0.07, 0.07, 0.09, 0.15,
    0.28, 0.42, 0.45, 0.38, 0.35, 0.36,
    0.40, 0.40, 0.38, 0.42, 0.55, 0.70,
    0.78, 0.72, 0.58, 0.42, 0.28, 0.16,
]

WEEKEND = [
    0.12, 0.09, 0.08, 0.07, 0.08, 0.10,
    0.14, 0.20, 0.28, 0.38, 0.48, 0.55,
    0.58, 0.56, 0.52, 0.50, 0.50, 0.52,
    0.50, 0.45, 0.36, 0.28, 0.20, 0.15,
]

DEFAULT_CONNECTORS = 2

CONNECTOR_USAGE_PROFILES = {
    "AC": {
        "base_factor": 0.65,
        "by_power": {
            7: 0.75,
            11: 0.68,
            22: 0.60,
        },
    },
    "DC": {
        "base_factor": 0.72,
        "by_power": {
            50: 0.80,
            100: 0.82,
            150: 0.85,
            200: 0.87,
            350: 0.88,
        },
    },
}


def parse_when(at=None) -> datetime:
    when = at if at is not None else datetime.now(timezone.utc)

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    return when.astimezone(timezone.utc)


def local_time(city, when_utc: datetime) -> datetime:
    name = CITY_TZ.get((city or "").lower())

    if ZoneInfo is None or name is None:
        return when_utc

    try:
        return when_utc.astimezone(ZoneInfo(name))
    except Exception:
        return when_utc


def base_demand(local_dt: datetime) -> float:
    profile = WEEKEND if local_dt.weekday() >= 5 else WEEKDAY

    x = local_dt.hour + local_dt.minute / 60
    h0 = int(x) % 24
    h1 = (h0 + 1) % 24
    frac = x - int(x)

    return profile[h0] * (1 - frac) + profile[h1] * frac


def cold_weather_factor(city) -> float:
    try:
        from sqlalchemy import text
        from core.db import engine

        with engine.connect() as conn:
            temperature = conn.execute(
                text(
                    """
                    SELECT AVG((weather_data->'current'->>'temperature_2m')::float)
                    FROM bronze.weather_raw
                    WHERE LOWER(city) = LOWER(:city)
                      AND ingestion_timestamp = (
                          SELECT MAX(ingestion_timestamp)
                          FROM bronze.weather_raw
                          WHERE LOWER(city) = LOWER(:city)
                      )
                    """
                ),
                {"city": city},
            ).scalar()

    except Exception:
        return 1.0

    if temperature is None:
        return 1.0

    return 1.0 + min(
        0.25,
        max(0.0, 10.0 - float(temperature)) * 0.02,
    )


def get_connector_count(raw_connectors, default: int = DEFAULT_CONNECTORS) -> int:
    if raw_connectors is None or isinstance(raw_connectors, bool):
        return default

    if isinstance(raw_connectors, (int, float)):
        n = int(raw_connectors)
        return n if n > 0 else default

    if isinstance(raw_connectors, str):
        text_value = raw_connectors.strip()

        if text_value.isdigit():
            n = int(text_value)
            return n if n > 0 else default

        try:
            parsed = json.loads(text_value)
        except ValueError:
            return default

        if isinstance(parsed, str):
            return default

        return get_connector_count(parsed, default)

    if isinstance(raw_connectors, dict):
        raw_connectors = [raw_connectors]

    if isinstance(raw_connectors, (list, tuple)):
        total = 0

        for item in raw_connectors:
            if isinstance(item, dict):
                quantity = 1

                for key in (
                    "quantity",
                    "Quantity",
                    "count",
                    "Count",
                ):
                    if key in item:
                        try:
                            quantity = int(item[key])
                        except (TypeError, ValueError):
                            quantity = 1
                        break

                total += max(quantity, 1)

            else:
                total += 1

        return total if total > 0 else default

    return default


def get_connector_usage_factor(
    connector_type: str,
    current_type: str,
    power_kw: float,
) -> float:

    current_type = current_type.upper() if current_type else "AC"

    if "DC" in current_type:
        current_type = "DC"
    else:
        current_type = "AC"

    profile = CONNECTOR_USAGE_PROFILES.get(current_type, {})

    if power_kw and current_type in CONNECTOR_USAGE_PROFILES:
        power_usages = CONNECTOR_USAGE_PROFILES[current_type].get(
            "by_power",
            {},
        )

        closest_power = min(
            power_usages.keys(),
            key=lambda x: abs(x - power_kw),
        )

        return power_usages.get(
            closest_power,
            profile.get("base_factor", 0.65),
        )

    return profile.get("base_factor", 0.65)


def simulate_connector_availability(
    station_id,
    total_connectors,
    city=None,
    when_utc=None,
    weather_factor: float = 1.0,
    connector_data: list = None,
):

    connectors = int(total_connectors or 0)

    if connectors <= 0:
        return 0, "simulated"

    when = parse_when(when_utc)

    base = base_demand(local_time(city, when))

    popularity = (
        0.5
        + (zlib.crc32(f"pop|{station_id}".encode()) % 1000) / 1000
    )

    if (
        connector_data
        and isinstance(connector_data, list)
        and len(connector_data) > 0
    ):
        weighted_usage = 0
        total_quantity = 0

        for connector in connector_data:
            if not isinstance(connector, dict):
                continue

            quantity = connector.get("quantity", 1)
            type_name = connector.get("type_name", "Unknown")
            current_type = connector.get("current_type_name", "AC")
            power_kw = connector.get("power_kw", 22)

            type_factor = get_connector_usage_factor(
                type_name,
                current_type,
                power_kw,
            )

            weighted_usage += type_factor * quantity
            total_quantity += quantity

        if total_quantity > 0:
            avg_type_usage = weighted_usage / total_quantity
        else:
            avg_type_usage = 0.65

    else:
        avg_type_usage = 0.65

    utilisation = min(
        0.97,
        max(
            0.02,
            base * popularity * weather_factor * avg_type_usage,
        ),
    )

    bucket = when.strftime("%Y-%m-%dT%H")

    rng = random.Random(
        zlib.crc32(f"{station_id}|{bucket}".encode())
    )

    occupied = sum(
        1
        for _ in range(connectors)
        if rng.random() < utilisation
    )

    return connectors - occupied, "simulated"