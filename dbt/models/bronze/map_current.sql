{{ config(
    materialized='incremental',
    schema='bronze',
    unique_key='station_id',
    post_hook=[
      "CREATE UNIQUE INDEX IF NOT EXISTS map_current_station_id_idx ON {{ this }} (station_id)"
    ]
) }}

SELECT
    station_id, station_name, latitude, longitude, available_connectors,
    occupied_connectors, ingestion_timestamp, ingested_at
FROM {{ source('bronze', 'map') }}

{% if is_incremental() %}
WHERE ingestion_timestamp >
(
    SELECT COALESCE(MAX(ingestion_timestamp), TIMESTAMP '2000-01-01')
    FROM {{ this }}
)
{% endif %}