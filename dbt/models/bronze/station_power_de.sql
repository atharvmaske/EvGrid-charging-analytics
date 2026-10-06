{{ config(materialized='table', schema='bronze') }}


SELECT
    station_id,
    city,
    bnetza_station_key,
    operator,
    real_connectors,
    avg_power_kw,
    max_power_kw,
    plug_types,
    match_distance_m,
    match_name_sim,
    matched_at
FROM {{ source('bronze', 'station_power_de') }}
