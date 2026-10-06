from utils.connectors import max_power_kw, parse_connector_details
from utils.scoring import haversine, recommendation_score


def test_haversine_berlin_munich():
    assert 480 < haversine(52.52, 13.405, 48.1351, 11.582) < 530


def test_close_fast_station_beats_far_slow_one():
    close_fast = recommendation_score(0.09, 3, 4, 150)
    far_slow = recommendation_score(0.64, 1, 1, 3.7)
    assert close_fast > far_slow


def test_no_free_connectors_is_penalised():
    assert recommendation_score(0.5, 0, 4, 50) < recommendation_score(0.5, 2, 4, 50)


def test_score_range():
    assert 0 <= recommendation_score(50, 0, 1, 0) <= 100
    assert recommendation_score(0, 8, 8, 350) <= 100


def test_parse_connector_details_handles_bad_input():
    assert parse_connector_details(None) == []
    assert parse_connector_details("not json") == []


def test_parse_and_max_power():
    raw = '[{"type_name":"CCS","power_kw":150.0,"quantity":2},{"type_name":"T2","power_kw":22}]'
    details = parse_connector_details(raw)
    assert details[0]["power_kw"] == 150
    assert max_power_kw(details) == 150