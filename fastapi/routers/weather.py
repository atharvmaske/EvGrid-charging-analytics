from fastapi import APIRouter, HTTPException

from core.db import get_engine
from core.weather import get_city_weather

router = APIRouter(prefix="/weather", tags=["Weather"])
engine = get_engine()


@router.get("/{city}")
def get_weather(city: str):
    row = get_city_weather(engine, city)

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No weather data found for city '{city}'.",
        )

    return row
