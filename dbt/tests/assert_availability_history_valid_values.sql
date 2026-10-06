
SELECT *
FROM {{ ref('availability_history') }}
WHERE occupancy_rate NOT BETWEEN 0 AND 1
   OR available_connectors < 0
   OR occupied_connectors < 0
