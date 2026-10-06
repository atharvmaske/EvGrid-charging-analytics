import math

from ingestion.ocm_tiling import KM_PER_DEG_LAT, fetch_city_tiled, split_tile
from ingestion.station_matching import haversine_m


def poi(poi_id, lat=52.52, lon=13.405):
    return {"ID": poi_id, "AddressInfo": {"Latitude": lat, "Longitude": lon}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(params)
        return FakeResponse(self.handler(params))


def test_four_children_cover_the_parent_disc():
    lat, lon, r = 52.52, 13.405, 25.0
    children = split_tile(lat, lon, r)
    assert len(children) == 4

    for bearing in range(0, 360, 5):
        for frac in (0.5, 1.0):
            d = frac * r
            p_lat = lat + d * math.cos(math.radians(bearing)) / KM_PER_DEG_LAT
            p_lon = lon + d * math.sin(math.radians(bearing)) / (
                KM_PER_DEG_LAT * math.cos(math.radians(lat))
            )
            nearest = min(haversine_m(c[0], c[1], p_lat, p_lon) for c in children)
            assert nearest <= children[0][2] * 1000 * 1.02


def test_full_tile_is_split_and_results_are_deduplicated():
    def handler(params):
        if params["distance"] > 20: 
            return [poi(1), poi(2), poi(3)]
        return [poi(1), poi(f"{params['latitude']:.4f},{params['longitude']:.4f}")]

    session = FakeSession(handler)
    stations, stats = fetch_city_tiled(session, "http://x", {}, 52.52, 13.405, 25.0, 3, pause_s=0)

    assert len(session.calls) == 5 
    assert len(stations) == 7 
    assert stats["split"] == 1 and stats["saturated_at_min"] == 0


def test_tile_still_full_at_minimum_size_is_reported():
    session = FakeSession(lambda params: [poi(1), poi(2), poi(3)])
    _, stats = fetch_city_tiled(session, "http://x", {}, 52.52, 13.405, 1.2, 3, pause_s=0)
    assert stats["saturated_at_min"] == 1 and len(session.calls) == 1


def test_pois_outside_the_city_circle_are_dropped():
    session = FakeSession(lambda params: [poi(1), poi(2, lat=53.52)])  
    stations, _ = fetch_city_tiled(session, "http://x", {}, 52.52, 13.405, 25.0, 100, pause_s=0)
    assert [s["ID"] for s in stations] == [1]
