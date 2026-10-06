{{ config(
    materialized='incremental',
    schema='bronze',
    unique_key='hour_utc',
    post_hook=[
      "CREATE UNIQUE INDEX IF NOT EXISTS day_ahead_prices_de_hour_idx ON {{ this }} (hour_utc)"
    ]
) }}


SELECT
    hour_utc,
    price_eur_per_mwh,
    price_eur_per_kwh,
    source,
    ingestion_timestamp
FROM {{ source('bronze', 'day_ahead_prices_de') }}

{% if is_incremental() %}
WHERE ingestion_timestamp >
(
    SELECT COALESCE(MAX(ingestion_timestamp), TIMESTAMP '2000-01-01')
    FROM {{ this }}
)
{% endif %}
