from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from core.availability_sim import parse_when
from core.db import get_engine
from core.stations import get_enriched_stations

router = APIRouter(prefix="/availability", tags=["Availability"])
engine = get_engine()


@router.get("/{city}")
def get_live_availability(city: str, at: Optional[datetime] = None):
    when_utc = parse_when(at)

    try:
        stations = get_enriched_stations(engine, city, now=when_utc)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}",
        )

    if not stations:
        return {
            "city": city,
            "total_stations": 0,
            "total_available": 0,
            "total_occupied": 0,
            "stations": [],
        }

    out_stations = [
        {
            "station_id": s["station_id"],
            "station_name": s["station_name"],
            "city": s["city"],
            "available_connectors": s["available_connectors"],
            "occupied_connectors": s["occupied_connectors"],
            "total_connectors": s["connectors"],
            "occupancy_rate": s["occupancy_rate"],
            "source": s["data_source"],
            "ingestion_timestamp": (s["observed_at"] or s["ingestion_timestamp"]),
        }
        for s in stations
    ]

    return {
        "city": stations[0]["city"],
        "total_stations": len(out_stations),
        "total_available": sum(s["available_connectors"] for s in out_stations),
        "total_occupied": sum(s["occupied_connectors"] for s in out_stations),
        "stations": out_stations,
    }
