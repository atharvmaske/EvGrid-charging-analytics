
{{ config(
    materialized='incremental',
    schema='bronze',
    incremental_strategy='delete+insert',
    full_refresh=false,
    unique_key=['station_id', 'snapshot_hour'],
    post_hook=[
      "CREATE UNIQUE INDEX IF NOT EXISTS availability_history_station_hour_idx ON {{ this }} (station_id, snapshot_hour)",
      "CREATE INDEX IF NOT EXISTS availability_history_city_hour_idx ON {{ this }} (city, snapshot_hour)"
    ]
) }}

WITH newest_hour AS (
    SELECT MAX(DATE_TRUNC('hour', ingestion_timestamp)) AS snapshot_hour
    FROM {{ source('bronze', 'availability') }}
)

SELECT
    CAST(a.station_id AS BIGINT)                AS station_id,
    a.city                                      AS city,
    n.snapshot_hour::timestamp                  AS snapshot_hour,
    a.available_connectors                      AS available_connectors,
    a.occupied_connectors                       AS occupied_connectors,
    COALESCE(
        a.occupancy_rate,
        CASE WHEN a.total_connectors > 0
             THEN a.occupied_connectors::float / a.total_connectors
             ELSE 0
        END
    )                                            AS occupancy_rate,
    a.source                                    AS source,
    a.ingestion_timestamp::timestamp            AS ingestion_timestamp,
    CAST('captured' AS TEXT)                    AS record_origin

FROM {{ source('bronze', 'availability') }} AS a
INNER JOIN newest_hour AS n
    ON DATE_TRUNC('hour', a.ingestion_timestamp) = n.snapshot_hour