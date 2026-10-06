from datetime import datetime

from ingestion.extract_smard import parse_series


def test_parse_series_converts_ms_to_hour_and_skips_nulls():
    series = [
        [1704067200000, 85.3],   
        [1704070800000, None],   
        [1704074400000, 102.75],
    ]
    rows = parse_series(series)
    assert len(rows) == 2
    assert rows[0] == (datetime(2024, 1, 1, 0, 0), 85.3)
    assert rows[1] == (datetime(2024, 1, 1, 2, 0), 102.75)


def test_parse_series_ignores_malformed_entries():
    assert parse_series([[1, 2, 3], "not a pair", None, [1704067200000, "bad"]]) == []


def test_parse_series_empty_input():
    assert parse_series([]) == []


def test_parse_series_price_kwh_conversion_is_caller_side():
    rows = parse_series([[1704067200000, 1000.0]])
    assert rows[0][1] == 1000.0
