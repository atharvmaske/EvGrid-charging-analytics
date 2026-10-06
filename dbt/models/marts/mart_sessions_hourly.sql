{{ config(
    materialized='table',
    schema='gold'
) }}

WITH ordered AS (

    SELECT
        station_id,
        city,
        snapshot_hour,
        occupied_connectors,
        LAG(occupied_connectors) OVER (
            PARTITION BY station_id ORDER BY snapshot_hour
        ) AS prev_occupied
    FROM {{ ref('availability_history') }}

),

station_hour_starts AS (

    SELECT
        station_id,
        city,
        snapshot_hour,
        GREATEST(
            occupied_connectors - COALESCE(prev_occupied, 0),
            0
        ) AS sessions_started
    FROM ordered

)

SELECT
    city,
    snapshot_hour,
    SUM(sessions_started) AS sessions_started,
    COUNT(DISTINCT station_id)
        FILTER (WHERE sessions_started > 0) AS stations_with_a_new_session

FROM station_hour_starts

GROUP BY city, snapshot_hour