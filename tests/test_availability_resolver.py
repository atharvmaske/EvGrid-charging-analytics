from datetime import datetime, timedelta, timezone

from utils.availability_resolver import is_fresh, resolve_availability

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def _row(source="live", available=3, observed_at=None):
    return {
        "station_id": 7,
        "db_source": source,
        "db_available": available,
        "db_observed_at": observed_at,
    }


def test_fresh_live_reading_is_used():
    row = _row(observed_at=NOW - timedelta(hours=2))
    assert resolve_availability(row, 6, "berlin", now=NOW) == (3, 3, "live")


def test_day_old_live_reading_becomes_simulated():
    row = _row(observed_at=NOW - timedelta(hours=25))
    assert resolve_availability(row, 6, "berlin", now=NOW)[2] == "simulated"


def test_live_without_timestamp_is_not_trusted():
    assert resolve_availability(_row(observed_at=None), 6, "berlin", now=NOW)[2] == "simulated"


def test_simulated_row_stays_simulated():
    row = _row(source="simulated", observed_at=NOW)
    assert resolve_availability(row, 6, "berlin", now=NOW)[2] == "simulated"


def test_naive_timestamps_are_treated_as_utc():
    assert is_fresh(datetime(2026, 10, 2, 11), NOW)
    assert not is_fresh(datetime(2026, 10, 1, 11), NOW)


def test_counts_always_add_up():
    row = _row(observed_at=NOW - timedelta(hours=30))
    available, occupied, _ = resolve_availability(row, 6, "berlin", now=NOW)
    assert available + occupied == 6
