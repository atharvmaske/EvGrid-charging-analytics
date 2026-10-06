from datetime import datetime, timedelta, timezone

from core.stations import enrich_station

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)

CONNECTORS_DATA = (
    '[{"type_name":"CCS","current_type_name":"DC","power_kw":150,"quantity":2},'
    '{"type_name":"Type 2","current_type_name":"AC","power_kw":22,"quantity":2}]'
)


def _row(**overrides):
    row = {
        "station_id": 1,
        "station_name": "Test Station",
        "city": "berlin",
        "country_code": "DE",
        "latitude": 52.5,
        "longitude": 13.4,
        "connectors": 4,
        "connectors_data": CONNECTORS_DATA,
        "ingestion_timestamp": NOW,
        "db_available": None,
        "db_source": None,
        "db_observed_at": None,
    }
    row.update(overrides)
    return row


def test_fresh_live_row_is_used_as_is():
    row = _row(db_available=3, db_source="live", db_observed_at=NOW - timedelta(hours=1))
    station = enrich_station(row, "berlin", now=NOW)
    assert station["data_source"] == "live"
    assert station["available_connectors"] == 3
    assert station["occupied_connectors"] == 1
    assert station["observed_at"] == NOW - timedelta(hours=1)


def test_stale_live_row_falls_back_to_simulated():
    row = _row(db_available=3, db_source="live", db_observed_at=NOW - timedelta(hours=30))
    station = enrich_station(row, "berlin", now=NOW)
    assert station["data_source"] == "simulated"
    assert station["observed_at"] is None


def test_no_availability_row_is_simulated():
    station = enrich_station(_row(), "berlin", now=NOW)
    assert station["data_source"] == "simulated"
    assert station["available_connectors"] + station["occupied_connectors"] == 4


def test_connector_details_and_max_power_are_parsed():
    station = enrich_station(_row(), "berlin", now=NOW)
    assert station["max_power_kw"] == 150
    assert len(station["connector_details"]) == 2


def test_internal_db_fields_are_not_leaked():
    row = _row(db_source="live", db_available=1, db_observed_at=NOW)
    station = enrich_station(row, "berlin", now=NOW)
    assert "db_available" not in station
    assert "db_source" not in station
    assert "db_observed_at" not in station


def test_status_and_occupancy_rate_when_fully_occupied():
    row = _row(db_available=0, db_source="live", db_observed_at=NOW)
    station = enrich_station(row, "berlin", now=NOW)
    assert station["status"] == "Busy"
    assert station["occupancy_rate"] == 1.0


def test_status_when_connectors_free():
    row = _row(db_available=2, db_source="live", db_observed_at=NOW)
    station = enrich_station(row, "berlin", now=NOW)
    assert station["status"] == "Available"


def test_missing_connector_count_falls_back_to_a_default_rather_than_crashing():
    row = _row(connectors=0, connectors_data=None)
    station = enrich_station(row, "berlin", now=NOW)
    assert station["connectors"] > 0
    assert 0 <= station["occupancy_rate"] <= 1
