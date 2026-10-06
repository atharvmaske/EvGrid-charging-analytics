
import math
import re
import statistics
import unicodedata
from difflib import SequenceMatcher
from typing import NamedTuple

EARTH_RADIUS_M = 6_371_000
MAX_MATCH_M = 150    
CLOSE_MATCH_M = 40   
MIN_NAME_SIM = 0.5  
_PREFILTER_M = MAX_MATCH_M * 1.2

_STOP_TOKENS = {
    "charging", "charge", "station", "stations", "ev", "point", "points",
    "charger", "chargers", "parking", "parkplatz", "ladestation", "ladesaule",
    "laden", "car", "park", "the", "der", "die", "das", "de", "at", "and", "und",
}

MATCH_LOG_DDL = """
CREATE TABLE IF NOT EXISTS bronze.station_match_log (
    id SERIAL PRIMARY KEY,
    run_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    script TEXT,
    city TEXT,
    records INTEGER,
    matched INTEGER,
    match_rate DOUBLE PRECISION,
    median_dist_m DOUBLE PRECISION,
    mean_name_sim DOUBLE PRECISION,
    source_observed_at TIMESTAMP
)
"""


class Match(NamedTuple):
    record_index: int
    candidate_id: object
    distance_m: float
    name_sim: float
    rule: str  


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _tokens(name):
    if not name:
        return set()
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if t not in _STOP_TOKENS}


def name_similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jaccard, ratio), 3)


def summarize(total, matches):
    n = len(matches)
    return {
        "records": total,
        "matched": n,
        "unmatched": total - n,
        "match_rate": round(n / total, 3) if total else 0.0,
        "median_dist_m": round(statistics.median(m.distance_m for m in matches), 1) if n else None,
        "mean_name_sim": round(statistics.fmean(m.name_sim for m in matches), 3) if n else None,
        "by_rule": {r: sum(1 for m in matches if m.rule == r) for r in ("close", "name")},
    }


def match_records(records, candidates, one_to_one=True):

    m_per_deg = math.pi * EARTH_RADIUS_M / 180
    pairs = []

    for i, rec in enumerate(records):
        lat, lon = rec["lat"], rec["lon"]
        cos_lat = math.cos(math.radians(lat))

        for j, cand in enumerate(candidates):
            if abs(cand["lat"] - lat) * m_per_deg > _PREFILTER_M:
                continue
            if abs(cand["lon"] - lon) * m_per_deg * cos_lat > _PREFILTER_M:
                continue

            dist = haversine_m(lat, lon, cand["lat"], cand["lon"])
            if dist > MAX_MATCH_M:
                continue

            sim = name_similarity(rec.get("name"), cand.get("name"))
            if dist <= CLOSE_MATCH_M:
                rule = "close"
            elif sim >= MIN_NAME_SIM:
                rule = "name"
            else:
                continue

            pairs.append((sim + (1 - dist / MAX_MATCH_M), i, j, dist, sim, rule))

    pairs.sort(key=lambda p: p[0], reverse=True)

    used_records, used_candidates, matches = set(), set(), []
    for _score, i, j, dist, sim, rule in pairs:
        if i in used_records or (one_to_one and j in used_candidates):
            continue
        used_records.add(i)
        used_candidates.add(j)
        matches.append(Match(i, candidates[j]["id"], round(dist, 1), sim, rule))

    matches.sort(key=lambda m: m.record_index)
    return matches, summarize(len(records), matches)


def format_stats(stats):
    if not stats["records"]:
        return "no records to match"
    return (
        f"matched {stats['matched']}/{stats['records']} ({stats['match_rate']:.1%}), "
        f"median distance {stats['median_dist_m']} m, "
        f"mean name similarity {stats['mean_name_sim']}, rules {stats['by_rule']}"
    )


def log_match_stats(cursor, script, city, stats, observed_at=None):
    cursor.execute(MATCH_LOG_DDL)
    cursor.execute(
        "INSERT INTO bronze.station_match_log "
        "(script, city, records, matched, match_rate, median_dist_m, mean_name_sim, source_observed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            script, city, stats["records"], stats["matched"], stats["match_rate"],
            stats["median_dist_m"], stats["mean_name_sim"], observed_at,
        ),
    )
