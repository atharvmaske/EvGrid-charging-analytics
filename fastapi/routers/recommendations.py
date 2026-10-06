import logging

from fastapi import APIRouter, HTTPException, Query

from config.cities import CITIES
from core.connectors import compatible
from core.db import get_engine
from core.live import refresh_live_near
from core.scoring import haversine, recommendation_score
from core.stations import get_enriched_stations

logger = logging.getLogger("evgrid.recommendations")

router = APIRouter(
    prefix="/recommendations",
    tags=["Recommendations"],
)

engine = get_engine()
MAX_RADIUS_KM = 5


@router.get("/{city}")
def get_recommendations(
    city: str,
    lat: float = Query(...),
    lon: float = Query(...),
    car: str = Query(""),
    live: bool = Query(False, description="Fetch live TomTom availability for the nearest stations now"),
):
    try:
        stations = get_enriched_stations(engine, city)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}",
        )

    if not stations:
        return []

    if live:
        try:
            summary = refresh_live_near(engine, city, lat, lon, stations, car=car)
            if summary["updated"]:
                stations = get_enriched_stations(engine, city)
        except Exception:
            logger.exception("live refresh failed, serving non-live data")

    if car:
        usable = [s for s in stations if compatible(s.get("connector_details"), car)]
        stations = usable or stations

    for station in stations:
        station.pop("observed_at", None)   

        station["distance_km"] = haversine(
            lat, lon, station["latitude"], station["longitude"],
        )
        station["score"] = recommendation_score(
            station["distance_km"],
            station["available_connectors"],
            station["connectors"],
            station["max_power_kw"],
        )
        station["distance_km"] = round(station["distance_km"], 2)

    within_radius = [
        s for s in stations
        if s["distance_km"] <= MAX_RADIUS_KM
    ]

    if not within_radius:
        fallback_km = CITIES.get(
            city.lower(),
            {},
        ).get("radius_km", MAX_RADIUS_KM)

        within_radius = [
            s for s in stations
            if s["distance_km"] <= fallback_km
        ]

    stations = within_radius

    stations.sort(key=lambda s: s["distance_km"])
    stations = stations[:15]

    stations.sort(
        key=lambda s: (
            -s["score"],
            s["distance_km"],
            -s["available_connectors"],
        )
    )

    return stations