
SELECT city, is_weekend, local_hour, COUNT(*) AS row_count
FROM {{ ref('mart_occupancy_by_hour_of_day') }}
GROUP BY city, is_weekend, local_hour
HAVING COUNT(*) > 1
