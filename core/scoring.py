import math
from math import radians, sin, cos, sqrt, atan2


def haversine(lat1, lon1, lat2, lon2):
    R = 6371

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1))
        * cos(radians(lat2))
        * sin(dlon / 2) ** 2
    )

    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def availability_score(available, total, source="simulated"):
    if total <= 0:
        return 0

    ratio = available / total

    score = (
        ratio * 65
        + min(total * 2.5, 25)
        + min(available * 2, 10)
    )

    if source == "live":
        score += 3

    return min(score, 100)


def distance_score(distance):
    if distance <= 0.5:
        return 100
    elif distance <= 1:
        return 95
    elif distance <= 2:
        return 90
    elif distance <= 3:
        return 85
    elif distance <= 5:
        return 75
    elif distance <= 8:
        return 60
    elif distance <= 12:
        return 45

    return 25


def charging_time_score(connectors):
    if connectors >= 8:
        return 100
    elif connectors >= 6:
        return 90
    elif connectors >= 4:
        return 80
    elif connectors >= 3:
        return 70
    elif connectors >= 2:
        return 60

    return 45


def cost_score(price):
    if price is None:
        return 70

    if price <= 0.18:
        return 100
    elif price <= 0.25:
        return 90
    elif price <= 0.35:
        return 80
    elif price <= 0.45:
        return 70

    return 55


def weather_score(temp, rain, wind):
    score = 100

    if temp < 5 or temp > 32:
        score -= 10

    if rain >= 5:
        score -= 15
    elif rain >= 2:
        score -= 8

    if wind >= 25:
        score -= 10

    return max(score, 0)


def final_score(A, D, C, T, W):
    return round(
        0.30 * A
        + 0.30 * D
        + 0.15 * C
        + 0.15 * T
        + 0.10 * W,
        2,
    )


def recommendation_score(distance_km, available, connectors, max_power_kw):
    free_ratio = available / max(connectors, 1)
    free_count = min(available, 4) / 4
    avail = 0.5 * free_ratio + 0.5 * free_count
    dist = math.exp(-distance_km / 2)
    power = min((max_power_kw or 0) / 150, 1)
    size = min(connectors, 8) / 8

    score = 100 * (0.40 * avail + 0.35 * dist + 0.15 * power + 0.10 * size)
    if available == 0:
        score *= 0.5
    return round(score, 1)