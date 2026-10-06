
from sqlalchemy import text

WEATHER_QUERY = text("""
    SELECT
        MIN(city) AS city,
        ROUND(AVG((weather_data->'current'->>'temperature_2m')::float)::numeric, 1)::float AS temperature,
        ROUND(AVG((weather_data->'current'->>'relative_humidity_2m')::float)::numeric, 1)::float AS humidity,
        ROUND(AVG((weather_data->'current'->>'wind_speed_10m')::float)::numeric, 1)::float AS wind_speed,
        ROUND(AVG((weather_data->'current'->>'precipitation')::float)::numeric, 1)::float AS precipitation,
        MAX(ingestion_timestamp) AS ingestion_timestamp
    FROM bronze.weather_raw
    WHERE LOWER(city) = LOWER(:city)
      AND ingestion_timestamp = (
          SELECT MAX(ingestion_timestamp)
          FROM bronze.weather_raw
          WHERE LOWER(city) = LOWER(:city)
      )
    GROUP BY LOWER(city)
""")


def get_city_weather(engine, city):
    with engine.connect() as conn:
        row = conn.execute(WEATHER_QUERY, {"city": city}).mappings().first()
    return dict(row) if row else None
