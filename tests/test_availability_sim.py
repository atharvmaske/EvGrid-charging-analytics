from datetime import datetime, timezone

from utils.availability_sim import (
    base_demand,
    get_connector_count,
    local_time,
    parse_when,
    simulate_connector_availability,
)


def test_peak_hour_busier_than_night():
    monday_night = datetime(2026, 9, 21, 3, 0)
    monday_peak = datetime(2026, 9, 21, 18, 0)
    assert base_demand(monday_peak) > 3 * base_demand(monday_night)


def test_connector_count_sums_quantities():
    assert get_connector_count([{"quantity": 2}, {"quantity": 3}]) == 5


def test_connector_count_defaults():
    assert get_connector_count(None) == 2
    assert get_connector_count("7") == 7
    assert get_connector_count(0) == 2


def test_naive_datetime_is_treated_as_utc():
    assert parse_when(datetime(2026, 1, 5, 12)).tzinfo == timezone.utc


def test_local_time_berlin_winter():
    when = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    assert local_time("berlin", when).hour == 13


def test_simulation_is_deterministic_and_bounded():
    when = datetime(2026, 9, 21, 16, tzinfo=timezone.utc)
    a, _ = simulate_connector_availability(42, 6, city="berlin", when_utc=when)
    b, _ = simulate_connector_availability(42, 6, city="berlin", when_utc=when)
    assert a == b
    assert 0 <= a <= 6


def test_zero_connectors():
    assert simulate_connector_availability(1, 0)[0] == 0