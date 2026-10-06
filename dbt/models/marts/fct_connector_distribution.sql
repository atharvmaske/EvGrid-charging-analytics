
{{ config(
    materialized='table',
    schema='gold'
) }}

WITH connector_explode AS (

    SELECT
        cs.station_id,
        LOWER(cs.city) AS city,
        cs.country_code,
        cs.station_name,

        COALESCE(
            connector.value->>'type_name',
            'Unknown'
        ) AS connector_type,

                CASE
            WHEN connector.value->>'current_type_name' = 'AC' THEN 'AC'
            WHEN connector.value->>'current_type_name' = 'AC (Single-Phase)' THEN 'AC'
            WHEN connector.value->>'current_type_name' = 'AC (Three-Phase)' THEN 'AC'
            WHEN connector.value->>'current_type_name' = 'DC' THEN 'DC'
            ELSE 'AC/DC'
        END AS current_type,

        COALESCE(
            (connector.value->>'power_kw')::NUMERIC,
            0
        ) AS power_kw,

        COALESCE(
            (connector.value->>'quantity')::INTEGER,
            1
        ) AS connector_quantity,

        cs.connectors AS total_connectors,
        CURRENT_TIMESTAMP AS transformation_timestamp

    FROM bronze.charging_stations cs

    CROSS JOIN LATERAL jsonb_array_elements(
        COALESCE(cs.connectors_data, '[]'::jsonb)
    ) AS connector

    WHERE cs.connectors_data IS NOT NULL
),

connector_summary AS (

    SELECT
        city,
        connector_type,
        current_type,

        COUNT(DISTINCT station_id) AS stations_with_type,

        SUM(connector_quantity) AS total_connectors_of_type,

        COALESCE(MIN(power_kw), 0) AS min_power_kw,
        COALESCE(MAX(power_kw), 0) AS max_power_kw,
        COALESCE(ROUND(AVG(power_kw), 1), 0) AS avg_power_kw,

        MAX(transformation_timestamp) AS last_updated

    FROM connector_explode

    GROUP BY
        city,
        connector_type,
        current_type
)

SELECT
    city,
    connector_type,
    current_type,

    stations_with_type,
    total_connectors_of_type,

    ROUND(
        100.0 * total_connectors_of_type
        / SUM(total_connectors_of_type) OVER (PARTITION BY city),
        1
    ) AS pct_of_all_connectors,

    min_power_kw,
    max_power_kw,
    avg_power_kw,

    last_updated

FROM connector_summary

ORDER BY
    city,
    pct_of_all_connectors DESC