{{ config(
    materialized='view',
    schema='silver'
) }}


WITH joined AS (

    SELECT
        CAST(s.station_id AS BIGINT) AS station_id,
        s.station_name,
        s.city,
        s.country_code,
        s.latitude,
        s.longitude,
        COALESCE(NULLIF(s.connectors, 0), a.available_connectors + a.occupied_connectors, 0) AS total_connectors,
        COALESCE(a.available_connectors, 0) AS available_connectors,
        COALESCE(a.occupied_connectors, 0) AS occupied_connectors,
        w.temperature,
        w.wind_speed,
        w.humidity,
        w.precipitation,
        w.weather_code,
        s.ingestion_timestamp AS ingested_at

    FROM {{ source('bronze', 'charging_stations') }} s

    LEFT JOIN {{ source('bronze', 'availability') }} a
        ON CAST(s.station_id AS BIGINT) = a.station_id

    LEFT JOIN {{ source('bronze', 'weather') }} w
        ON LOWER(s.city) = LOWER(w.city)

)

SELECT
    *,

    CASE WHEN total_connectors > 0
         THEN ROUND(occupied_connectors::numeric / total_connectors, 2)
         ELSE 0
    END AS occupancy_rate,

    CASE
        WHEN total_connectors = 0 THEN 'Available'
        WHEN occupied_connectors::numeric / total_connectors >= 0.80 THEN 'Busy'
        WHEN occupied_connectors::numeric / total_connectors >= 0.50 THEN 'Moderate'
        ELSE 'Available'
    END AS occupancy_status,

    CURRENT_TIMESTAMP AS transformed_at

FROM joined
