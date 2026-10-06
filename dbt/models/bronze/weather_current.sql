{{ config(
    materialized='incremental',
    schema='bronze',
    unique_key='city',
    post_hook=[
      "CREATE UNIQUE INDEX IF NOT EXISTS weather_current_city_idx ON {{ this }} (city)"
    ]
) }}


SELECT
    city, temperature, humidity, wind_speed, weather_code, precipitation,
    ingestion_timestamp, ingested_at
FROM {{ source('bronze', 'weather') }}

{% if is_incremental() %}
WHERE ingestion_timestamp >
(
    SELECT COALESCE(MAX(ingestion_timestamp), TIMESTAMP '2000-01-01')
    FROM {{ this }}
)
{% endif %}