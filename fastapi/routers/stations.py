from fastapi import APIRouter

from core.db import get_engine
from core.stations import get_catalog_rows

router = APIRouter(prefix="/stations", tags=["Stations"])
engine = get_engine()


@router.get("/{city}")
def get_city_stations(city: str):
    return get_catalog_rows(engine, city)
