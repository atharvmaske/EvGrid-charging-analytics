
SELECT * FROM {{ ref('mart_energy_hourly') }}
WHERE energy_kwh < 0 OR occupied_connector_hours < 0
