import logging
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.cities import COUNTRY_BY_CITY
from ingestion.tomtom_keys import TomTomKeyPool, request_with_rotation

logger = logging.getLogger("evgrid.geocode")

router = APIRouter(tags=["TomTom Geocoding"])

try:
    KEY_POOL = TomTomKeyPool.from_env()
except RuntimeError:
    KEY_POOL = None

session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504], 
)

session.mount("https://", HTTPAdapter(max_retries=retry))

COUNTRY_MAP = COUNTRY_BY_CITY

_CACHE = {}
_CACHE_MAX = 500


def _cache_put(key, value):
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = value


QUOTA_DETAIL = "TomTom quota exhausted for all configured API keys. Try again later or add keys."


@router.get("/geocode")
def geocode(
    query: str = Query(...),
    city: str = Query("berlin"),
):

    if not KEY_POOL:
        raise HTTPException(500, "TomTom API key missing.")

    cache_key = ("geocode", query.strip().lower(), city.lower())
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    url = f"https://api.tomtom.com/search/2/geocode/{quote(query, safe='')}.json"

    params = {
        "limit": 5,
        "countrySet": COUNTRY_MAP.get(city.lower(), "DE"),
    }

    try:
        response = request_with_rotation(session, url, params, KEY_POOL, timeout=10)

        if response.status_code in (401, 403, 429):
            logger.error(f"geocode: all TomTom keys rejected (HTTP {response.status_code})")
            raise HTTPException(status_code=429, detail=QUOTA_DETAIL)

        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        if not results:
            raise HTTPException(404, "Location not found.")

        result = results[0]

        payload = {
            "address": result["address"]["freeformAddress"],
            "latitude": result["position"]["lat"],
            "longitude": result["position"]["lon"],
            "city": city.lower(),
        }
        _cache_put(cache_key, payload)
        return payload

    except requests.exceptions.RequestException as exc:
        logger.error(f"geocode: TomTom request failed: {exc}")
        raise HTTPException(
            status_code=503,
            detail="TomTom service temporarily unavailable.",
        )


@router.get("/autocomplete")
def autocomplete(
    query: str = Query(...),
    city: str = Query("berlin"),
):

    if not KEY_POOL:
        raise HTTPException(500, "TomTom API key missing.")

    cache_key = ("autocomplete", query.strip().lower(), city.lower())
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    url = f"https://api.tomtom.com/search/2/search/{quote(query, safe='')}.json"

    params = {
        "limit": 5,
        "typeahead": True,
        "countrySet": COUNTRY_MAP.get(city.lower(), "DE"),
    }

    try:
        response = request_with_rotation(session, url, params, KEY_POOL, timeout=10)

        if response.status_code in (401, 403, 429):
            logger.error(f"autocomplete: all TomTom keys rejected (HTTP {response.status_code})")
            return []

        response.raise_for_status()

        data = response.json()
        results = data.get("results", [])

        places = []

        for result in results:
            places.append(
                {
                    "address": result["address"]["freeformAddress"],
                    "lat": result["position"]["lat"],
                    "lon": result["position"]["lon"],
                    "city": city.lower(),
                }
            )

        _cache_put(cache_key, places)
        return places

    except requests.exceptions.RequestException as exc:
        logger.error(f"autocomplete: TomTom request failed: {exc}")
        return []