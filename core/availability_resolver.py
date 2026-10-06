import os
from datetime import datetime, timedelta, timezone

from utils.availability_sim import simulate_connector_availability

LIVE_MAX_AGE = timedelta(hours=float(os.getenv("LIVE_MAX_AGE_HOURS", "24")))


def is_fresh(observed_at, now=None, max_age=None):
    if observed_at is None:
        return False

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    limit = LIVE_MAX_AGE if max_age is None else max_age

    return now - observed_at <= limit



def resolve_availability(row, total, city, weather_factor=1.0, now=None, connector_data=None):
    total = int(total or 0)
    src = str(row.get("db_source") or "").lower()
    db_available = row.get("db_available")
    real_now = datetime.now(timezone.utc)
    when = now or real_now
    is_present = abs(when - real_now) <= timedelta(minutes=5)  # live data only describes "now"

    if (is_present and src == "live" and db_available is not None
            and total > 0 and is_fresh(row.get("db_observed_at"), when)):
        available = max(0, min(int(db_available), total))
        return available, total - available, "live"

    available, _ = simulate_connector_availability(
        row["station_id"], total, city=city,
        when_utc=when, weather_factor=weather_factor,
        connector_data=connector_data,
    )
    available = max(0, min(int(available), total))
    return available, total - available, "simulated"