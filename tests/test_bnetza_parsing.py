import pandas as pd

import math

from ingestion.extract_bnetza import (
    BNETZA_URL_PATTERN,
    discover_csv_url,
    extract_points,
    find_header_row,
    map_columns,
    match_city,
    normalize_col,
    parse_plug_types,
    parse_power_kw,
    rows_to_stations,
)


class _FakeResponse:
    def __init__(self, text, ok=True):
        self.text = text
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            import requests
            raise requests.exceptions.RequestException("boom")


class _FakeSession:
    def __init__(self, text, ok=True):
        self._resp = _FakeResponse(text, ok)

    def get(self, url, timeout):
        return self._resp


def test_url_pattern_matches_real_dated_filename():
    html = '<a href="https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_2026-09-01.csv">CSV</a>'
    match = BNETZA_URL_PATTERN.search(html)
    assert match and match.group(0).endswith("2026-09-01.csv")


def test_discover_csv_url_finds_the_link_in_page_html():
    html = (
        '<a href="https://data.bundesnetzagentur.de/Bundesnetzagentur/DE/Fachthemen/'
        'ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenregister_BNetzA_2027-01-01.csv">CSV</a>'
    )
    url = discover_csv_url(_FakeSession(html))
    assert url.endswith("2027-01-01.csv")


def test_discover_csv_url_returns_none_when_pattern_not_found():
    assert discover_csv_url(_FakeSession("<html>nothing relevant here</html>")) is None


def test_discover_csv_url_returns_none_on_request_failure():
    assert discover_csv_url(_FakeSession("irrelevant", ok=False)) is None


def test_find_header_row_skips_the_title_line():

    text = "Ladesaeulenregister Bundesnetzagentur\nBetreiber;Ort;Breitengrad;Laengengrad;Steckertypen1\nACME;Berlin;52,5;13,4;Typ 2"
    assert find_header_row(text) == 1


def test_find_header_row_skips_a_prose_description_mentioning_betreiber():

    text = (
        "Ladesaeulenregister Bundesnetzagentur\n"
        "Die Liste beinhaltet die Ladeeinrichtungen aller Betreiberinnen und Betreiber, die das Anzeigeverfahren der Bundesnetzagentur\n"
        "Betreiber;Ort;Breitengrad;Laengengrad;Steckertypen1\n"
        "ACME;Berlin;52,5;13,4;Typ 2"
    )
    assert find_header_row(text) == 2


def test_find_header_row_handles_a_blank_line_too():
    text = "Ladesaeulenregister Bundesnetzagentur\n\nBetreiber;Ort;Breitengrad;Laengengrad;Steckertypen1\nACME;Berlin;52,5;13,4;Typ 2"
    assert find_header_row(text) == 2


def test_find_header_row_returns_zero_when_header_is_already_first():
    text = "Betreiber;Ort;Breitengrad;Laengengrad;Steckertypen1\nACME;Berlin;52,5;13,4;Typ 2"
    assert find_header_row(text) == 0


def test_find_header_row_requires_enough_fields_not_just_the_word():
    text = "Betreiber\nActualHeaderRow;Ort;Breitengrad;Laengengrad;Steckertypen1"
    assert find_header_row(text) == 0 


def test_find_header_row_falls_back_to_zero_if_betreiber_never_appears():
    text = "Something;Else;Entirely\nrow1;row2;row3"
    assert find_header_row(text) == 0


def test_normalize_col_strips_umlauts_and_case():
    assert normalize_col("Längengrad") == "laengengrad"
    assert normalize_col("  Straße  ") == "strasse"


def test_map_columns_aggregate_only_layout():
    cols = ["Betreiber", "Ort", "Breitengrad", "Längengrad",
            "Nennleistung Ladeeinrichtung [kW]", "Anzahl Ladepunkte"]
    mapping = map_columns(cols)
    assert mapping["operator"] == ["Betreiber"]
    assert mapping["aggregate_power_kw"] == ["Nennleistung Ladeeinrichtung [kW]"]
    assert "power_kw" not in mapping
    assert mapping["num_points"] == ["Anzahl Ladepunkte"]


def test_map_columns_numbered_power_columns():
    cols = ["Nennleistung Stecker1", "Nennleistung Stecker2",
            "Steckertypen1", "Steckertypen2"]
    mapping = map_columns(cols)
    assert len(mapping["power_kw"]) == 2
    assert len(mapping["plug_types"]) == 2


def test_parse_power_kw_handles_german_decimal_comma():
    assert parse_power_kw("22,0 kW") == 22.0
    assert parse_power_kw("150") == 150.0
    assert parse_power_kw("-") is None
    assert parse_power_kw(None) is None
    assert parse_power_kw("0") is None


def test_parse_power_kw_thousands_separator():
    assert parse_power_kw("1.234,5") == 1234.5


def test_parse_plug_types_dedupes_and_splits():
    assert parse_plug_types(["CCS; Type 2", "ccs"]) == ["CCS", "Type 2"]
    assert parse_plug_types([None, ""]) == []


def test_parse_plug_types_filters_pandas_nan_not_just_none():
    assert parse_plug_types(["AC Typ 2 Steckdose", float("nan")]) == ["AC Typ 2 Steckdose"]
    assert parse_plug_types([float("nan"), float("nan")]) == []


def test_match_city_handles_aliases_and_accents():
    assert match_city("München") == "munich"
    assert match_city("Frankfurt am Main") == "frankfurt"
    assert match_city("Hamburg") is None
    assert match_city(None) is None


def test_match_city_does_not_match_a_town_that_merely_contains_the_name():
    assert match_city("Überlingen") is None
    assert match_city("Marienberg") is None
    assert match_city("Münchenbernsdorf") is None


def test_match_city_excludes_the_wrong_frankfurt():
    assert match_city("Frankfurt (Oder)") is None
    assert match_city("Frankfurt an der Oder") is None
    assert match_city("Frankfurt am Main") == "frankfurt"


def test_match_city_handles_nan():
    assert match_city(float("nan")) is None


def test_extract_points_single_power_column_replicated_by_num_points():
    df = pd.DataFrame([{
        "Nennleistung [kW]": "22,0",
        "Anzahl Ladepunkte": "2",
        "Steckertypen1": "Typ 2",
        "Ort": "Berlin",
        "Breitengrad": "52,52",
        "Längengrad": "13,40",
    }])
    mapping = map_columns(df.columns)
    rows = extract_points(df, mapping)
    assert len(rows[0]["points"]) == 2
    assert all(p["power_kw"] == 22.0 for p in rows[0]["points"])
    assert rows[0]["lat"] == 52.52


def test_extract_points_numbered_columns():
    df = pd.DataFrame([{
        "Nennleistung Ladepunkt 1 [kW]": "22,0",
        "Nennleistung Ladepunkt 2 [kW]": "50,0",
        "Steckertypen1": "Typ 2",
        "Steckertypen2": "CCS",
        "Ort": "Berlin",
        "Breitengrad": "52,52",
        "Längengrad": "13,40",
    }])
    mapping = map_columns(df.columns)
    rows = extract_points(df, mapping)
    powers = sorted(p["power_kw"] for p in rows[0]["points"])
    assert powers == [22.0, 50.0]


def test_aggregate_power_column_is_kept_separate_from_per_point_power():
    cols = [
        "Betreiber", "Nennleistung Ladeeinrichtung [kW]", "Ort", "Breitengrad", "Längengrad",
        "Steckertypen1", "Nennleistung Stecker1", "EVSE-ID1", "Public Key1",
        "Steckertypen2", "Nennleistung Stecker2", "EVSE-ID2", "Public Key2",
    ]
    mapping = map_columns(cols)
    assert mapping["aggregate_power_kw"] == ["Nennleistung Ladeeinrichtung [kW]"]
    assert mapping["power_kw"] == ["Nennleistung Stecker1", "Nennleistung Stecker2"]


def test_extract_points_pairs_real_stecker_columns_correctly():
    df = pd.DataFrame([{
        "Betreiber": "ACME", "Nennleistung Ladeeinrichtung [kW]": "2222,0",
        "Ort": "Berlin", "Breitengrad": "52,52", "Längengrad": "13,40",
        "Steckertypen1": "AC Typ 2 Steckdose", "Nennleistung Stecker1": "22,0",
        "Steckertypen2": "CCS", "Nennleistung Stecker2": "150,0",
    }])
    mapping = map_columns(df.columns)
    rows = extract_points(df, mapping)
    points = sorted(rows[0]["points"], key=lambda p: p["power_kw"])
    assert [p["power_kw"] for p in points] == [22.0, 150.0]
    assert points[0]["plug_types"] == ["AC Typ 2 Steckdose"]
    assert points[1]["plug_types"] == ["CCS"]
    assert 2222.0 not in [p["power_kw"] for p in points]


def test_map_columns_power_regex_is_not_capped_at_four_points():
    cols = [f"P{i} [kW]" for i in range(1, 8)] + [f"Steckertypen{i}" for i in range(1, 8)]
    mapping = map_columns(cols)
    assert len(mapping["power_kw"]) == 7
    assert len(mapping["plug_types"]) == 7


def test_extract_points_real_register_column_layout():
    df = pd.DataFrame([{
        "P1 [kW]": "22,0",
        "P2 [kW]": "150,0",
        "Steckertypen1": "Typ 2",
        "Steckertypen2": "CCS",
        "Public Key1": "ABC123",
        "Public Key2": "ABC124",
        "Ort": "München",
        "Breitengrad": "48,1351",
        "Längengrad": "11,582",
    }])
    mapping = map_columns(df.columns)
    assert len(mapping["power_kw"]) == 2
    rows = extract_points(df, mapping)
    powers = sorted(p["power_kw"] for p in rows[0]["points"])
    assert powers == [22.0, 150.0]
    assert match_city(rows[0]["city_raw"]) == "munich"


def test_rows_to_stations_groups_same_site_and_aggregates():
    parsed = [
        {"operator": "ACME", "street": "Hauptstr. 1", "house_number": None, "postcode": "10115",
         "lat": 52.52, "lon": 13.40, "city_raw": "Berlin", "commissioning_date": "2022-01-01",
         "points": [{"power_kw": 22.0, "plug_types": ["Typ 2"]}]},
        {"operator": "ACME", "street": "Hauptstr. 1", "house_number": None, "postcode": "10115",
         "lat": 52.52, "lon": 13.40, "city_raw": "Berlin", "commissioning_date": "2022-06-01",
         "points": [{"power_kw": 50.0, "plug_types": ["CCS"]}]},
    ]
    stations = rows_to_stations(parsed)
    assert len(stations) == 1

    s = stations[0]
    assert s["connectors"] == 2
    assert s["avg_power_kw"] == 36.0
    assert s["max_power_kw"] == 50.0
    assert set(s["plug_types"]) == {"Typ 2", "CCS"}
    assert s["city_key"] == "berlin"


def test_rows_to_stations_different_sites_stay_separate():
    parsed = [
        {"operator": "A", "street": "Street 1", "house_number": None, "postcode": "1",
         "lat": 52.52, "lon": 13.40, "city_raw": "Berlin", "commissioning_date": None,
         "points": [{"power_kw": 22.0, "plug_types": []}]},
        {"operator": "B", "street": "Street 2", "house_number": None, "postcode": "2",
         "lat": 48.13, "lon": 11.58, "city_raw": "München", "commissioning_date": None,
         "points": [{"power_kw": 150.0, "plug_types": []}]},
    ]
    stations = rows_to_stations(parsed)
    assert len(stations) == 2


def test_rows_to_stations_skips_rows_without_coordinates():
    parsed = [{"operator": "A", "street": "Street", "house_number": None, "postcode": "1",
               "lat": None, "lon": None, "city_raw": "Berlin", "commissioning_date": None,
               "points": [{"power_kw": 22.0, "plug_types": []}]}]
    assert rows_to_stations(parsed) == []