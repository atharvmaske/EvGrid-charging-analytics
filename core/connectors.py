import json


def parse_connector_details(raw):
    if not raw:
        return []

    for _ in range(2):
        if isinstance(raw, (str, bytes)):
            try:
                raw = json.loads(raw)
            except Exception:
                return []

    if isinstance(raw, dict):
        raw = (
            raw.get("connectors")
            or raw.get("Connections")
            or [raw]
        )

    if not isinstance(raw, list):
        return []

    out = []

    for c in raw:
        if not isinstance(c, dict):
            continue

        power = c.get("power_kw", c.get("PowerKW"))

        try:
            power = (
                round(float(power), 1)
                if power not in (None, "", "Unknown")
                else None
            )
        except (TypeError, ValueError):
            power = None

        if power is not None and power == int(power):
            power = int(power)

        try:
            qty = int(
                c.get("quantity")
                or c.get("Quantity")
                or 1
            )
        except (TypeError, ValueError):
            qty = 1

        out.append(
            {
                "type_name": (
                    c.get("type_name")
                    or c.get("type")
                    or "Unknown"
                ),
                "current_type_name": (
                    c.get("current_type_name")
                    or c.get("current")
                    or ""
                ),
                "power_kw": power,
                "quantity": max(qty, 1),
            }
        )

    return out


def max_power_kw(details):
    powers = [
        c["power_kw"]
        for c in details
        if c.get("power_kw")
    ]

    return max(powers) if powers else 0


_DEFAULT_PLUGS = (
    "ccs",
    "type 2",
    "iec 62196",
)

CAR_PLUGS = {
    "nissan leaf": (
        "chademo",
        "type 2",
        "type 1",
        "j1772",
    ),
    "tesla": (
        "tesla",
        "nacs",
        "ccs",
        "type 2",
    ),
}


def compatible(details, car):
    if not car or not details:
        return True

    plugs = _DEFAULT_PLUGS

    for key, val in CAR_PLUGS.items():
        if key in car.lower():
            plugs = val
            break

    return any(
        any(
            p in str(c.get("type_name", "")).lower()
            for p in plugs
        )
        for c in details
    )


def power_bonus(kw):
    if kw >= 150:
        return 10

    if kw >= 50:
        return 7

    if kw >= 22:
        return 4

    if kw >= 11:
        return 2

    return 0