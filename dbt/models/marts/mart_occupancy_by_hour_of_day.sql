
{{ config(
    materialized='table',
    schema='gold'
) }}

SELECT
    city,
    is_weekend,
    local_hour,
    COUNT(*)                                                        AS hours_observed,
    ROUND(100.0 * SUM(occupied_connectors) / NULLIF(SUM(connectors), 0), 1) AS avg_occupancy_pct,
    MIN(occupancy_pct)                                              AS min_occupancy_pct,
    MAX(occupancy_pct)                                              AS max_occupancy_pct,
    MIN(snapshot_hour)                                              AS first_snapshot,
    MAX(snapshot_hour)                                              AS last_snapshot
FROM {{ ref('mart_occupancy_hourly') }}
GROUP BY city, is_weekend, local_hour
