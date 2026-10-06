{{ config(
    materialized='table',
    schema='gold'
) }}

SELECT

    city,

    COUNT(*) AS total_stations,

    SUM(total_connectors) AS total_connectors,

    SUM(available_connectors) AS total_available_connectors,

    SUM(occupied_connectors) AS total_occupied_connectors,

    ROUND(AVG(occupancy_rate)::numeric,2) AS avg_occupancy_rate,

    ROUND(AVG(temperature)::numeric,2) AS avg_temperature,

    ROUND(AVG(wind_speed)::numeric,2) AS avg_wind_speed,

    ROUND(AVG(precipitation)::numeric,2) AS avg_precipitation,

    ROUND(AVG(humidity)::numeric,2) AS avg_humidity,

    COUNT(*) FILTER(
        WHERE occupancy_status='Busy'
    ) AS busy_stations,

    COUNT(*) FILTER(
        WHERE occupancy_status='Available'
    ) AS available_stations,

    MAX(ingested_at) AS latest_ingestion,

    CURRENT_TIMESTAMP AS transformation_timestamp

FROM {{ ref('stg_station_metrics') }}

GROUP BY city

ORDER BY city