from ingestion.station_matching import MIN_NAME_SIM, haversine_m, match_records, name_similarity

CANDS = [
    {"id": 1, "lat": 52.5200, "lon": 13.4050, "name": "Ionity Berlin Mitte"},
    {"id": 2, "lat": 52.5201, "lon": 13.4051, "name": "Aral Pulse"}, 
]


def test_haversine_is_in_metres():
    assert 105 < haversine_m(52.5, 13.4, 52.501, 13.4) < 117


def test_name_similarity_ignores_accents_and_generic_words():
    assert name_similarity("Ionity Ladestation München", "IONITY Munchen") > 0.8
    assert name_similarity("Aral Pulse", "Lidl Parkplatz") < MIN_NAME_SIM
    assert name_similarity(None, "Ionity") == 0.0


def test_name_breaks_a_tie_between_equally_close_candidates():
    recs = [{"lat": 52.52005, "lon": 13.40505, "name": "IONITY Berlin Mitte"}]
    matches, stats = match_records(recs, CANDS)
    assert [m.candidate_id for m in matches] == [1]
    assert stats["matched"] == 1 and stats["match_rate"] == 1.0


def test_far_apart_never_matches():
    recs = [{"lat": 52.5300, "lon": 13.4050, "name": "Ionity Berlin Mitte"}]  
    matches, stats = match_records(recs, CANDS)
    assert matches == [] and stats["match_rate"] == 0.0


def test_medium_distance_needs_a_matching_name():
    other = {"lat": 52.52081, "lon": 13.4050, "name": "Totally Different"} 
    same = {"lat": 52.52081, "lon": 13.4050, "name": "Ionity Berlin Mitte"}
    assert match_records([other], CANDS)[0] == []
    assert len(match_records([same], CANDS)[0]) == 1


def test_one_to_one_flag():
    recs = [
        {"lat": 52.5200, "lon": 13.4050, "name": "Ionity Berlin Mitte"},
        {"lat": 52.52002, "lon": 13.40502, "name": "Ionity Berlin Mitte"},
    ]
    assert len(match_records(recs, CANDS[:1])[0]) == 1
    assert len(match_records(recs, CANDS[:1], one_to_one=False)[0]) == 2


def test_empty_input():
    matches, stats = match_records([], CANDS)
    assert matches == [] and stats["match_rate"] == 0.0
