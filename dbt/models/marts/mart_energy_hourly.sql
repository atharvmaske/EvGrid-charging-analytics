{{ config(
    materialized='table',
    schema='gold'
) }}


{% set assumed_avg_power_kw = 11.0 %}
{% set min_de_matches = 5 %}

WITH constants AS (
    SELECT {{ assumed_avg_power_kw }} AS avg_power_kw
),

de_power AS (
    SELECT
        city,
        COUNT(*)           AS matched_stations,
        AVG(avg_power_kw)  AS avg_power_kw
    FROM {{ ref('station_power_de') }}
    WHERE avg_power_kw IS NOT NULL
    GROUP BY city
),

cities AS (
    SELECT DISTINCT city FROM {{ ref('availability_history') }}
),

power_by_city AS (
    SELECT
        cities.city AS city,
        CASE WHEN d.matched_stations >= {{ min_de_matches }}
             THEN d.avg_power_kw
             ELSE c.avg_power_kw
        END AS avg_power_kw,
        CASE WHEN d.matched_stations >= {{ min_de_matches }}
             THEN 'bnetza_de'
             ELSE 'assumed_constant'
        END AS avg_power_kw_source
    FROM cities
    LEFT JOIN de_power d ON LOWER(d.city) = LOWER(cities.city)
    CROSS JOIN constants c
)

SELECT
    h.city,
    h.snapshot_hour,
    SUM(h.occupied_connectors)                                       AS occupied_connector_hours,
    ROUND((SUM(h.occupied_connectors) * p.avg_power_kw)::numeric, 1) AS energy_kwh,
    p.avg_power_kw,
    p.avg_power_kw_source
FROM {{ ref('availability_history') }} h
JOIN power_by_city p ON LOWER(p.city) = LOWER(h.city)
GROUP BY h.city, h.snapshot_hour, p.avg_power_kw, p.avg_power_kw_source
