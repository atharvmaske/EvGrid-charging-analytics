{{ config(materialized='table', schema='gold') }}
SELECT city, country_code, timezone
FROM {{ ref('cities') }}