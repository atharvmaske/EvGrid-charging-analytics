from fastapi import APIRouter, HTTPException
import requests

from ingestion.tomtom_keys import TomTomKeyPool, request_with_rotation

router = APIRouter(tags=["Routing"])

try:
    KEY_POOL = TomTomKeyPool.from_env()
except RuntimeError:
    KEY_POOL = None

session = requests.Session()


@router.get("/route")
def get_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
):

    if not KEY_POOL:
        raise HTTPException(
            status_code=500,
            detail="TomTom API key missing",
        )

    url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{start_lat},{start_lon}:{end_lat},{end_lon}/json"
    )

    params = {
        "routeType": "fastest",
        "travelMode": "car",
    }

    response = request_with_rotation(session, url, params, KEY_POOL, timeout=20)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    data = response.json()

    summary = data["routes"][0]["summary"]
    points = data["routes"][0]["legs"][0]["points"]

    polyline = [
        [p["latitude"], p["longitude"]]
        for p in points
    ]

    return {
        "distance_km": round(summary["lengthInMeters"] / 1000, 2),
        "eta_minutes": round(summary["travelTimeInSeconds"] / 60),
        "polyline": polyline,
    }