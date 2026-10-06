
SELECT *
FROM {{ ref('mart_occupancy_hourly') }}
WHERE occupancy_pct < 0 OR occupancy_pct > 100
