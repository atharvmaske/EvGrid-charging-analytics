
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE bronze.stations (
    station_id BIGINT PRIMARY KEY, station_name TEXT, city TEXT, country_code TEXT,
    latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, connectors INT,
    available_connectors INT, occupied_connectors INT, occupancy_rate DOUBLE PRECISION,
    ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
);
CREATE TABLE bronze.availability (
    station_id BIGINT PRIMARY KEY, station_name TEXT, city TEXT, total_stations INT,
    total_connectors INT, available_connectors INT, occupied_connectors INT,
    occupancy_rate DOUBLE PRECISION, source TEXT,
    ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
);
CREATE TABLE bronze.weather (
    city TEXT, temperature DOUBLE PRECISION, humidity DOUBLE PRECISION,
    wind_speed DOUBLE PRECISION, precipitation DOUBLE PRECISION, weather_code INT,
    ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
);
CREATE TABLE bronze.weather_raw (
    id SERIAL PRIMARY KEY, city TEXT, country_code TEXT, weather_point TEXT,
    latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
    ingestion_timestamp TIMESTAMP, weather_data JSONB
);
CREATE TABLE bronze.charging_stations (
    station_id BIGINT PRIMARY KEY, city TEXT, country_code TEXT, station_name TEXT,
    latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, connectors INT,
    connectors_data JSONB, ingestion_timestamp TIMESTAMP
);
CREATE TABLE bronze.energy_prices (
    city TEXT, tariff_per_kwh DOUBLE PRECISION, currency TEXT, ingestion_timestamp TIMESTAMP
);
CREATE TABLE bronze.recommendations (
    station_id BIGINT, station_name TEXT, score DOUBLE PRECISION, reason TEXT,
    ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
);
CREATE TABLE bronze.map (
    station_id BIGINT, station_name TEXT, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
    available_connectors INT, occupied_connectors INT,
    ingestion_timestamp TIMESTAMP, ingested_at TIMESTAMP
);

INSERT INTO bronze.stations VALUES
  (1,'CI Station','berlin','DE',52.5,13.4,2,1,1,0.5, now(), now()),
  (2,'CI Station 2','berlin','DE',52.5,13.4,4,4,0,0.0, now(), now());

INSERT INTO bronze.availability
  (station_id, station_name, city, total_stations, total_connectors, available_connectors,
   occupied_connectors, occupancy_rate, source, ingestion_timestamp, ingested_at)
VALUES
  (1,'CI Station','berlin',2,2,1,1,0.5,'simulated', now(), now()),
  (2,'CI Station 2','berlin',2,4,4,0,0.0,'simulated', now(), now());

INSERT INTO bronze.weather VALUES ('berlin', 15.0, 60, 10, 0, 3, now(), now());

INSERT INTO bronze.weather_raw
  (city, country_code, weather_point, latitude, longitude, ingestion_timestamp, weather_data)
VALUES ('berlin','DE','center',52.5,13.4, now(),
  jsonb_build_object('current', jsonb_build_object('time', to_char(now() at time zone 'utc','YYYY-MM-DD"T"HH24:MI'), 'temperature_2m', 15)));

INSERT INTO bronze.charging_stations VALUES
  (1,'berlin','DE','CI Station',52.5,13.4,2,
   '[{"type_name":"CCS","current_type_name":"DC","power_kw":50,"quantity":1},{"type_name":"Type 2","current_type_name":"AC","power_kw":22,"quantity":1}]'::jsonb, now());

INSERT INTO bronze.energy_prices VALUES ('berlin', 0.56, 'EUR', now());
INSERT INTO bronze.recommendations VALUES (1,'CI Station',80.0,'close and free', now(), now());
INSERT INTO bronze.map VALUES (1,'CI Station',52.5,13.4,1,1, now(), now());