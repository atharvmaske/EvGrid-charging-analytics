{{ config(
    materialized='table',
    schema='gold'
) }}



WITH latest_tariff AS (

    SELECT DISTINCT ON (REPLACE(LOWER(TRIM(city)), ' ', '_'))
        REPLACE(LOWER(TRIM(city)), ' ', '_') AS city,
        tariff_per_kwh,
        currency
    FROM {{ source('bronze', 'energy_prices') }}
    ORDER BY REPLACE(LOWER(TRIM(city)), ' ', '_'), ingestion_timestamp DESC

),

day_ahead AS (

    SELECT hour_utc, price_eur_per_kwh
    FROM {{ ref('day_ahead_prices_de') }}

),

priced AS (

    SELECT
        e.city,
        e.snapshot_hour,
        e.energy_kwh,
        dc.country_code,
        d.price_eur_per_kwh AS de_price_eur_per_kwh,
        t.currency           AS static_currency,
        t.tariff_per_kwh     AS static_tariff_per_kwh

    FROM {{ ref('mart_energy_hourly') }} e

    LEFT JOIN {{ ref('dim_city') }} dc
        ON LOWER(dc.city) = LOWER(e.city)

    LEFT JOIN day_ahead d
        ON d.hour_utc = e.snapshot_hour

    LEFT JOIN latest_tariff t
        ON REPLACE(LOWER(TRIM(e.city)), ' ', '_') = t.city

)

SELECT
    city,
    snapshot_hour,
    energy_kwh,

    CASE WHEN country_code = 'DE' AND de_price_eur_per_kwh IS NOT NULL
         THEN 'EUR' ELSE static_currency
    END AS currency,

    CASE WHEN country_code = 'DE' AND de_price_eur_per_kwh IS NOT NULL
         THEN de_price_eur_per_kwh ELSE static_tariff_per_kwh
    END AS tariff_per_kwh,

    CASE WHEN country_code = 'DE' AND de_price_eur_per_kwh IS NOT NULL
         THEN ROUND((energy_kwh * de_price_eur_per_kwh)::numeric, 2)
         ELSE ROUND((energy_kwh * static_tariff_per_kwh)::numeric, 2)
    END AS revenue_local,

    CASE WHEN country_code = 'DE' AND de_price_eur_per_kwh IS NOT NULL
         THEN 'smard_day_ahead' ELSE 'static_tariff'
    END AS price_source

FROM priced
