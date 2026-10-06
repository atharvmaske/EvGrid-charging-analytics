
{{ config(
    materialized='table',
    schema='gold'
) }}

WITH hourly AS (
    SELECT
        city,
        snapshot_hour,
        COUNT(*)                                        AS stations,
        SUM(occupied_connectors + available_connectors) AS connectors,
        SUM(occupied_connectors)                        AS occupied_connectors
    FROM {{ ref('availability_history') }}
    GROUP BY city, snapshot_hour
),

localised AS (
    SELECT
        h.*,
        (h.snapshot_hour AT TIME ZONE 'UTC') AT TIME ZONE COALESCE(c.timezone, 'UTC') AS local_hour_start
    FROM hourly AS h
    LEFT JOIN {{ ref('dim_city') }} AS c
        ON LOWER(c.city) = LOWER(h.city)
)

SELECT
    city,
    snapshot_hour,
    local_hour_start,
    EXTRACT(HOUR FROM local_hour_start)::int           AS local_hour,
    EXTRACT(ISODOW FROM local_hour_start) IN (6, 7)    AS is_weekend,
    stations,
    connectors,
    occupied_connectors,
    ROUND(100.0 * occupied_connectors / NULLIF(connectors, 0), 1) AS occupancy_pct
FROM localised
