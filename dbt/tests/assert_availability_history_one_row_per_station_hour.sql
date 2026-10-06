
SELECT station_id, snapshot_hour, COUNT(*) AS row_count
FROM {{ ref('availability_history') }}
GROUP BY station_id, snapshot_hour
HAVING COUNT(*) > 1
