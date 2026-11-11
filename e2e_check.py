import argparse
import json
import os
import re
import subprocess
import sys
import time
PG, AF, API_C = "evgrid-postgres", "airflow-webserver", "evgrid-fastapi"
POLL = float(os.environ.get("E2E_POLL_SECONDS", "10"))
WEATHER, INGEST, GOLD = (
    "evgrid_weather_raw_pipeline",
    "evgrid_ingest_pipeline",
    "evgrid_gold_pipeline",
)
EXPECTED_DAGS = {WEATHER, INGEST, GOLD, "evgrid_silver_pipeline"}
RESULTS = []
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def record(status, name, detail=""):
    RESULTS.append((status, name, detail))
    print(f"[{status:<4}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
def run(cmd, timeout=180):
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except FileNotFoundError:
        return 127, "", "docker command not found"
def dexec(container, *args, timeout=180):
    return run(["docker", "exec", container, *args], timeout)
def airflow(*args, timeout=240):
    return dexec(AF, "airflow", *args, timeout=timeout)
def parse_json(text):
    m = re.search(r"\[\s*\{", text)
    if not m:
        return [] if re.search(r"\[\s*\]", text) else None
    try:
        return json.loads(text[m.start(): text.rfind("]") + 1])
    except ValueError:
        return None
def airflow_json(*args):
    rc, out, err = airflow(*args, "-o", "json")
    data = parse_json(out)
    if data is None:
        raise RuntimeError(f"cannot parse Airflow output (rc={rc}): {(out or err)[:300]!r}")
    return data
def sql(query):
    rc, out, err = dexec(PG, "psql", "-U", "evgrid", "-d", "evgrid",
                         "-t", "-A", "-F", "|", "-c", query)
    if rc != 0:
        raise RuntimeError((err or out).strip()[:300])
    return [line.split("|") for line in out.strip().splitlines() if line.strip()]
def check(name, query, evaluate):
    try:
        status, detail = evaluate(sql(query))
    except Exception as exc:
        status, detail = "FAIL", str(exc)[:250]
    record(status, name, detail)
def get_runs(dag):
    return airflow_json("dags", "list-runs", "-d", dag)
def trigger(dag):
    rc, out, err = airflow("dags", "trigger", dag, "-o", "json")
    data = parse_json(out)
    if data:
        run_id = data[0].get("dag_run_id") or data[0].get("run_id")
        if run_id:
            return run_id
    runs = get_runs(dag)
    if not runs:
        raise RuntimeError(f"could not trigger {dag}: {(out or err)[:300]!r}")
    return runs[0]["run_id"]
def wait_run(dag, run_id, timeout_s):
    start, last = time.time(), None
    while time.time() - start < timeout_s:
        state = next((r["state"] for r in get_runs(dag) if r["run_id"] == run_id), "unknown")
        if state != last:
            print(f"   {dag}: {state}  ({int(time.time() - start)}s)", flush=True)
            last = state
        if state in ("success", "failed"):
            return state
        time.sleep(POLL)
    return "timeout"
def task_states(dag, run_id):
    rows = airflow_json("tasks", "states-for-dag-run", dag, run_id)
    return {r["task_id"]: r["state"] for r in rows}
def stage(dag, label, timeout_s, allow_skipped=True):
    print(f"\n== {label}: triggering {dag}", flush=True)
    t0 = time.time()
    try:
        run_id = trigger(dag)
        print(f"   run_id: {run_id}", flush=True)
        state = wait_run(dag, run_id, timeout_s)
        states = task_states(dag, run_id)
    except Exception as exc:
        record("FAIL", label, str(exc)[:250])
        return False
    ok_states = ("success", "skipped") if allow_skipped else ("success",)
    bad = {t: s for t, s in states.items() if s not in ok_states}
    skipped = sum(1 for s in states.values() if s == "skipped")
    secs = int(time.time() - t0)
    if state == "success" and not bad:
        note = f"{len(states)} tasks, {secs}s" + (f", {skipped} skipped (expected gaps)" if skipped else "")
        record("PASS", label, note)
        return True
    detail = f"run {state}, run_id={run_id}"
    if bad:
        detail += "; not OK: " + ", ".join(f"{t}={s}" for t, s in sorted(bad.items())[:8])
    record("FAIL", label, detail)
    return False
def preflight():
    print("== Preflight", flush=True)
    rc, out, err = run(["docker", "ps", "--format", "{{.Names}}"], timeout=60)
    if rc != 0:
        record("FAIL", "docker reachable", (err or out).strip()[:200])
        return False
    names = set(out.split())
    missing = [n for n in (PG, API_C, AF, "airflow-scheduler") if n not in names]
    record("FAIL" if missing else "PASS", "core containers running",
           ("missing: " + ", ".join(missing)) if missing else "")
    if missing:
        return False
    code = ("import requests as r; print([r.get('http://localhost:8000/'+p, timeout=30).status_code "
            "for p in ('weather/berlin','availability/berlin','map/berlin')])")
    rc, out, _ = dexec(API_C, "python", "-c", code)
    record("PASS" if "[200, 200, 200]" in out else "FAIL",
           "API endpoints (weather, availability, map)", out.strip()[-80:])
    for host in ("fastapi", "host.docker.internal"):
        code = f"import requests; print(requests.get('http://{host}:8000/weather/berlin', timeout=15).status_code)"
        rc, out, _ = dexec(AF, "python", "-c", code)
        record("PASS" if out.strip().endswith("200") else "FAIL",
               f"Airflow -> API via '{host}'", out.strip()[-80:] if rc else "")
    rc, out, _ = dexec(AF, "python", "-c", "import socket; print(socket.gethostbyname('postgres'))")
    record("PASS" if re.match(r"^\d+\.\d+\.\d+\.\d+$", out.strip()) else "FAIL",
           "Airflow -> Postgres DNS", out.strip()[-80:])
    rc, out, _ = airflow("dags", "list-import-errors")
    record("PASS" if "No data found" in out else "FAIL", "DAG import errors",
           "" if "No data found" in out else out.strip()[-200:])
    try:
        ids = {d["dag_id"] for d in airflow_json("dags", "list")}
        gone = sorted(EXPECTED_DAGS - ids)
        record("FAIL" if gone else "PASS", "all DAGs registered", ("missing: " + ", ".join(gone)) if gone else "")
    except Exception as exc:
        record("WARN", "DAG list", str(exc)[:200])
    for dag in (WEATHER, INGEST, GOLD):
        airflow("dags", "unpause", dag)
    rc, out, _ = run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"], timeout=60)
    if rc == 0:
        print("   memory: " + "; ".join(l.strip() for l in out.strip().splitlines()), flush=True)
    return True
def run_pipeline():
    stage(WEATHER, "weather DAG (Open-Meteo -> bronze.weather_raw)", 600)
    try:
        gold_before = {r["run_id"] for r in get_runs(GOLD)}
    except Exception:
        gold_before = set()
    ingest_ok = stage(INGEST, "ingest DAG (bronze -> Great Expectations -> dbt run -> dbt test)",
                      1500, allow_skipped=False)
    if ingest_ok:
        print("\n== gold DAG (should be triggered by ingest)", flush=True)
        deadline, gold_run = time.time() + 240, None
        while time.time() < deadline and not gold_run:
            try:
                new = [r for r in get_runs(GOLD) if r["run_id"] not in gold_before]
            except Exception:
                new = []
            if new:
                gold_run = new[0]["run_id"]
            else:
                time.sleep(POLL)
        if not gold_run:
            record("FAIL", "gold DAG", "ingest finished but no new gold run appeared")
        else:
            state = wait_run(GOLD, gold_run, 600)
            record("PASS" if state == "success" else "FAIL", "gold DAG", f"run {state}")
    else:
        record("WARN", "gold DAG", "skipped because ingest did not succeed")
def num(x):
    return float(x)
COVERAGE_SQL = """
SELECT c.city,
  (SELECT count(*) FROM bronze.charging_stations x WHERE lower(x.city) = c.city),
  (SELECT count(*) FROM bronze.charging_stations x WHERE lower(x.city) = c.city),
  (SELECT count(*) FROM bronze.availability x WHERE lower(x.city) = c.city),
  COALESCE((SELECT round(extract(epoch FROM (now() AT TIME ZONE 'utc' - w.ingestion_timestamp::timestamp))/60)
            FROM bronze.weather w WHERE lower(w.city) = c.city), -1),
  (SELECT count(*) FROM bronze.availability_history h
   WHERE lower(h.city) = c.city AND h.record_origin = 'captured'
     AND h.snapshot_hour = (SELECT max(snapshot_hour) FROM bronze.availability_history WHERE record_origin = 'captured'))
FROM gold.dim_city c
ORDER BY c.city
"""
def coverage_check(max_age):
    try:
        rows = sql(COVERAGE_SQL)
    except Exception as exc:
        record("FAIL", "per-city coverage", str(exc)[:250])
        return
    print(f"   {'city':<12}{'source':>8}{'stations':>10}{'avail':>7}{'weather':>10}{'history':>9}", flush=True)
    problems, gaps = [], []
    for city, src, st, av, wage, hist in rows:
        src, st, av, hist, wage = int(src), int(st), int(av), int(hist), num(wage)
        print(f"   {city:<12}{src:>8}{st:>10}{av:>7}{('none' if wage < 0 else f'{wage:.0f}m'):>10}{hist:>9}", flush=True)
        if wage < 0:
            problems.append(f"{city}: no weather row in bronze.weather")
        elif wage > max_age + 5:
            problems.append(f"{city}: weather loaded {wage:.0f} min ago")
        if src == 0:
            gaps.append(f"{city}: no stations in bronze.charging_stations (nothing to ingest)")
        elif st == 0:
            problems.append(f"{city}: {src} source stations but none ingested into bronze.stations")
        elif av == 0:
            problems.append(f"{city}: stations but no availability rows")
        elif hist < av:
            problems.append(f"{city}: history has {hist} of {av} stations for the newest hour")
    for msg in problems:
        record("FAIL", "per-city coverage", msg)
    for msg in gaps:
        record("WARN", "per-city coverage", msg)
    if not problems:
        record("PASS", "per-city coverage", f"{len(rows)} cities checked, {len(gaps)} without station data")
def freshness_check():
    name = "scripts.check_freshness (live-data staleness guard)"
    rc, out, err = dexec(API_C, "python", "-m", "scripts.check_freshness")
    text = (out or err or "").strip()
    detail = text.splitlines()[-1][:200] if text else ""
    record("PASS" if rc == 0 else "FAIL", name, detail)
def data_checks(max_age):
    print("\n== Data checks", flush=True)
    coverage_check(max_age)
    freshness_check()
    def weather_batch(rows):
        complete, total, stale_age = int(rows[0][0]), int(rows[0][1]), num(rows[0][2])
        ok = total >= 12 and complete == total and stale_age <= max_age + 10
        return ("PASS" if ok else "FAIL"), (
            f"{complete} of {total} cities have a complete batch, "
            f"oldest per-city batch {stale_age:.0f} min old"
        )
    check("weather_raw: newest batch is complete and recent", """
        WITH latest AS (
            SELECT city, max(ingestion_timestamp) AS ts
            FROM bronze.weather_raw
            GROUP BY city
        ),
        batch AS (
            SELECT r.city, l.ts, count(*) AS pts
            FROM bronze.weather_raw r
            JOIN latest l ON l.city = r.city AND r.ingestion_timestamp = l.ts
            GROUP BY r.city, l.ts
        )
        SELECT
            count(*) FILTER (WHERE pts = 5),
            count(*),
            round(extract(epoch FROM (now() AT TIME ZONE 'utc' - min(ts)))/60)
        FROM batch
    """, weather_batch)
    def weather_real(rows):
        ok_n, total = int(rows[0][0]), int(rows[0][1])
        return ("PASS" if ok_n == total and total >= 12 else "FAIL"), (
            f"{ok_n} of {total} cities match the real weather that was newest when they were loaded")
    check("bronze.weather comes from real weather (all cities)", """
        WITH w AS (SELECT lower(city) AS city, temperature, ingestion_timestamp::timestamp AS ts FROM bronze.weather),
        b AS (
          SELECT w.city,
                 min((r.weather_data->'current'->>'temperature_2m')::float) AS lo,
                 max((r.weather_data->'current'->>'temperature_2m')::float) AS hi
          FROM w
          JOIN bronze.weather_raw r
            ON lower(r.city) = w.city
           AND r.ingestion_timestamp = (SELECT max(r2.ingestion_timestamp) FROM bronze.weather_raw r2
                                         WHERE lower(r2.city) = w.city AND r2.ingestion_timestamp <= w.ts)
          GROUP BY w.city)
        SELECT count(*) FILTER (WHERE w.temperature BETWEEN b.lo - 0.06 AND b.hi + 0.06), count(*)
        FROM w JOIN b USING (city)
    """, weather_real)
    def silver_rows(rows):
        s, b = int(rows[0][0]), int(rows[0][1])
        return ("PASS" if s == b and s > 0 else "FAIL"), f"silver {s} rows vs bronze.charging_stations {b}"
    check("silver keeps every station (no drops or duplicates)", """
        SELECT (SELECT count(*) FROM silver.stg_station_metrics), (SELECT count(*) FROM bronze.charging_stations)
    """, silver_rows)
    def silver_weather(rows):
        nulls, diff = int(rows[0][0]), int(rows[0][1])
        return ("PASS" if nulls == 0 and diff == 0 else "FAIL"), f"{nulls} stations without weather, {diff} differ from bronze.weather"
    check("silver weather present and equal to bronze (all cities)", """
        SELECT count(*) FILTER (WHERE s.temperature IS NULL),
               count(*) FILTER (WHERE s.temperature IS DISTINCT FROM w.temperature)
        FROM silver.stg_station_metrics s
        LEFT JOIN bronze.weather w ON lower(w.city) = lower(s.city)
    """, silver_weather)
    def silver_gaps(rows):
        n = int(rows[0][0])
        return ("PASS" if n == 0 else "WARN"), (
            "every station has an availability row" if n == 0
            else f"{n} stations have no availability row (silver shows them as 0% occupied)")
    check("silver has no hidden availability gaps", """
        SELECT count(*) FROM silver.stg_station_metrics s
        LEFT JOIN bronze.availability a ON s.station_id = a.station_id::bigint
        WHERE a.station_id IS NULL
    """, silver_gaps)
    def connectors_agree(rows):
        n = int(rows[0][0])
        return ("PASS" if n == 0 else "FAIL"), (
            "connector totals agree between stations and availability" if n == 0
            else f"{n} stations where total_connectors differs from available + occupied")
    check("connector counts are consistent", """
        SELECT count(*) FROM silver.stg_station_metrics
        WHERE total_connectors <> available_connectors + occupied_connectors
    """, connectors_agree)
    def real_connector_data(rows):
        total, with_data, real_types = int(rows[0][0]), int(rows[0][1]), int(rows[0][2])
        ok = total > 0 and with_data >= total * 0.90 and real_types >= total * 0.90
        return ("PASS" if ok else "FAIL"), (
            f"{with_data}/{total} stations have connectors_data, {real_types} with a real (non-'Unknown') type_name")
    check("bronze.charging_stations has real OCM connector data (not just counts)", """
        SELECT count(*),
               count(connectors_data),
               count(*) FILTER (WHERE connectors_data->0->>'type_name' IS NOT NULL
                                  AND connectors_data->0->>'type_name' <> 'Unknown')
        FROM bronze.charging_stations
    """, real_connector_data)
    def connector_distribution_populated(rows):
        try:
            n_rows, n_typed, n_powered = int(rows[0][0]), int(rows[0][1]), int(rows[0][2])
        except (IndexError, ValueError):
            return "WARN", "gold.fct_connector_distribution not installed yet"
        ok = n_rows > 0 and n_typed == n_rows and n_powered >= n_rows * 0.80
        return ("PASS" if ok else "FAIL"), (
            f"{n_rows} rows, {n_typed} with a connector_type, {n_powered} with an avg_power_kw")
    try:
        sql("SELECT 1 FROM gold.fct_connector_distribution LIMIT 1")
        check("gold.fct_connector_distribution has real types and power ratings (not NULL)", """
            SELECT count(*),
                   count(*) FILTER (WHERE connector_type IS NOT NULL AND connector_type <> ''),
                   count(*) FILTER (WHERE avg_power_kw IS NOT NULL AND avg_power_kw > 0)
            FROM gold.fct_connector_distribution
        """, connector_distribution_populated)
    except Exception:
        record("WARN", "gold.fct_connector_distribution has real types and power ratings (not NULL)",
               "model not installed yet (run: dbt run --select fct_connector_distribution --target local)")
    def gold_vs_silver(rows):
        sc, gc, bad, age = int(rows[0][0]), int(rows[0][1]), int(rows[0][2]), num(rows[0][3])
        ok = sc == gc and sc > 0 and bad == 0 and age <= max_age
        return ("PASS" if ok else "FAIL"), (
            f"silver {sc} cities, gold {gc} cities, {bad} cities differ, updated {age:.0f} min ago")
    check("gold_city_summary agrees with silver (every city)", """
        WITH s AS (SELECT lower(city) AS city, count(*) AS n, sum(total_connectors) AS c, avg(occupancy_rate) * 100 AS o
                   FROM silver.stg_station_metrics GROUP BY 1),
             g AS (SELECT lower(city) AS city, total_stations AS n, total_connectors AS c, avg_occupancy AS o
                   FROM gold_city_summary)
        SELECT (SELECT count(*) FROM s), (SELECT count(*) FROM g),
               count(*) FILTER (WHERE s.n <> g.n OR s.c <> g.c OR abs(s.o - g.o) >= 0.05),
               (SELECT round(extract(epoch FROM (now() AT TIME ZONE 'utc' - max(updated_at)))/60) FROM gold_city_summary)
        FROM s JOIN g USING (city)
    """, gold_vs_silver)
    def mart_vs_silver(rows):
        sc, mc, bad = int(rows[0][0]), int(rows[0][1]), int(rows[0][2])
        return ("PASS" if sc == mc and sc > 0 and bad == 0 else "FAIL"), f"silver {sc} cities, mart {mc} cities, {bad} cities differ"
    check("dbt mart (gold.mart_ev_city_summary) agrees with silver", """
        WITH s AS (SELECT lower(city) AS city, count(*) AS n, avg(occupancy_rate) * 100 AS o
                   FROM silver.stg_station_metrics GROUP BY 1)
        SELECT (SELECT count(*) FROM s), (SELECT count(*) FROM gold.mart_ev_city_summary),
               count(*) FILTER (WHERE m.total_stations <> s.n OR abs(m.avg_occupancy_rate * 100 - s.o) >= 0.51)
        FROM gold.mart_ev_city_summary m JOIN s ON lower(m.city) = s.city
    """, mart_vs_silver)
    def history_check():
        name = "availability_history captured the newest hour"
        try:
            rows = sql("""
                WITH h AS (SELECT max(snapshot_hour) AS m FROM bronze.availability_history
                           WHERE record_origin = 'captured')
                SELECT (SELECT count(*) FROM bronze.availability_history, h
                        WHERE snapshot_hour = h.m AND record_origin = 'captured'),
                       (SELECT count(*) FROM bronze.availability),
                       round(extract(epoch FROM (now() AT TIME ZONE 'utc' - (SELECT m FROM h)))/60),
                       (SELECT count(DISTINCT snapshot_hour) FROM bronze.availability_history)
            """)
            n, cur, age, hours = int(rows[0][0]), int(rows[0][1]), num(rows[0][2]), int(rows[0][3])
            ok = n == cur and n > 0 and age <= 60 + max_age
            record("PASS" if ok else "FAIL", name,
                   f"{n} rows in newest hour vs {cur} current stations, {age:.0f} min ago, {hours} hours of history in total")
        except Exception as exc:
            msg = str(exc)
            if "does not exist" in msg:
                record("WARN", name, "history model not installed yet (bronze.availability_history missing)")
            else:
                record("FAIL", name, msg[:250])
    history_check()
    def marts_populated(rows):
        hourly, profile, cities = int(rows[0][0]), int(rows[0][1]), int(rows[0][2])
        return ("PASS" if hourly > 0 and profile > 0 else "FAIL"), f"{hourly} hourly rows, {profile} hour-of-day rows, {cities} cities"
    try:
        sql("SELECT 1 FROM gold.mart_occupancy_hourly LIMIT 1")
        check("occupancy marts are populated",
              "SELECT (SELECT count(*) FROM gold.mart_occupancy_hourly), (SELECT count(*) FROM gold.mart_occupancy_by_hour_of_day), "
              "(SELECT count(DISTINCT city) FROM gold.mart_occupancy_hourly)",
              marts_populated)
    except Exception:
        record("WARN", "occupancy marts are populated", "marts not installed yet")
    code = ("import requests as r; g=lambda h: r.get('http://localhost:8000/availability/berlin', "
            "params={'at': '2026-09-21T'+h+':00:00+00:00'}, timeout=60).json()['total_occupied']; "
            "print(g('01'), g('16'))")
    rc, out, _ = dexec(API_C, "python", "-c", code)
    try:
        night, peak = (int(x) for x in out.split()[-2:])
        ok = peak > 3 * max(night, 1)
        record("PASS" if ok else "FAIL", "occupancy follows a daily pattern",
               f"Monday 03:00 local = {night} occupied, 18:00 local = {peak}")
    except Exception:
        record("FAIL", "occupancy follows a daily pattern", (out or "no output").strip()[-120:])
    check("live availability share (informational)", """
        SELECT count(*) FILTER (WHERE source='live' AND observed_at > now() AT TIME ZONE 'utc' - interval '24 hours'),
               count(*)
        FROM bronze.availability
    """, lambda r: ("PASS", f"{r[0][0]} of {r[0][1]} stations have a fresh live reading"))
def summary():
    counts = {s: sum(1 for r in RESULTS if r[0] == s) for s in ("PASS", "WARN", "FAIL")}
    print("\n" + "=" * 60)
    print(f"PASS {counts['PASS']}   WARN {counts['WARN']}   FAIL {counts['FAIL']}")
    for status, name, detail in RESULTS:
        if status in ("WARN", "FAIL"):
            print(f"  [{status}] {name}: {detail}")
    print("=" * 60)
    if counts["FAIL"]:
        verdict = "SOMETHING FAILED - paste this output"
    elif counts["WARN"]:
        verdict = "NO FAILURES, but review the warnings above"
    else:
        verdict = "ALL GOOD"
    print("RESULT:", verdict)
    return 1 if counts["FAIL"] else 0
def main():
    ap = argparse.ArgumentParser(description="EVGrid end-to-end check")
    ap.add_argument("--checks-only", action="store_true", help="only verify the current database state")
    args = ap.parse_args()
    if not preflight():
        summary()
        return 2
    if not args.checks_only:
        run_pipeline()
    data_checks(max_age=90 if args.checks_only else 25)
    return summary()
if __name__ == "__main__":
    sys.exit(main())