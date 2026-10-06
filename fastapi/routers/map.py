from fastapi import APIRouter, HTTPException

from core.db import get_engine
from core.stations import get_enriched_stations

router = APIRouter(prefix="/map", tags=["Map"])
engine = get_engine()


@router.get("/{city}")
def get_city_map(city: str):
    try:
        stations = get_enriched_stations(engine, city)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )

    if not stations:
        return {
            "city": city.title(),
            "station_count": 0,
            "live_count": 0,
            "simulated_count": 0,
            "stations": []
        }

    for s in stations:
        s.pop("observed_at", None)  

    live_count = sum(1 for s in stations if s["data_source"] == "live")
    simulated_count = sum(1 for s in stations if s["data_source"] == "simulated")

    return {
        "city": city.title(),
        "station_count": len(stations),
        "live_count": live_count,
        "simulated_count": simulated_count,
        "stations": stations
    }
