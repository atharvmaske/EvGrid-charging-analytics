
CREATE UNIQUE INDEX IF NOT EXISTS stations_station_id_idx
    ON bronze.stations (station_id);

CREATE UNIQUE INDEX IF NOT EXISTS availability_station_id_idx
    ON bronze.availability (station_id);
