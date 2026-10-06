from fastapi import APIRouter
from pydantic import BaseModel
import google.generativeai as genai
from dotenv import load_dotenv
import os
import json

load_dotenv()

router = APIRouter(
    prefix="/ai",
    tags=["Gemini AI"]
)

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=API_KEY)

model = genai.GenerativeModel("gemini-3.1-flash-lite")

TARGET_BATTERY = 98


class ChargingRequest(BaseModel):
    car_model: str
    battery: int
    station_name: str
    available_connectors: int
    city: str = "Berlin"
    max_power_kw: float = 0
    connector_types: list[str] = []


CITY_CURRENCY = {
    "berlin": ("€", "EUR", 0.55),
    "munich": ("€", "EUR", 0.55),
    "frankfurt": ("€", "EUR", 0.55),
    "stuttgart": ("€", "EUR", 0.55),
    "hamburg": ("€", "EUR", 0.55),
    "cologne": ("€", "EUR", 0.55),
    "dusseldorf": ("€", "EUR", 0.55),
    "essen": ("€", "EUR", 0.55),
    "leipzig": ("€", "EUR", 0.55),
    "dresden": ("€", "EUR", 0.55),
    "nuremberg": ("€", "EUR", 0.55),
    "vienna": ("€", "EUR", 0.50),
    "linz": ("€", "EUR", 0.50),
    "amsterdam": ("€", "EUR", 0.50),
    "paris": ("€", "EUR", 0.45),
    "london": ("£", "GBP", 0.65),
    "los_angeles": ("$", "USD", 0.45),
    "bengaluru": ("₹", "INR", 18),
    "tokyo": ("¥", "JPY", 55),
    "shanghai": ("¥", "CNY", 1.6),
    "pune": ("₹", "INR", 16.7),
    "hyderabad": ("₹", "INR", 16.25),
    "chennai": ("₹", "INR", 16.5),
    "solapur": ("₹", "INR", 16.0),
    "bern": ("Fr.", "CHF", 0.66),
    "copenhagen": ("kr", "DKK", 4.35),
    "stockholm": ("kr", "SEK", 5.20),
}

NO_DECIMALS = {"JPY"}
BATTERY_KWH = 60


@router.post("/charging-plan")
def charging_plan(request: ChargingRequest):

    currency, code, price_kwh = CITY_CURRENCY.get(
        request.city.lower().replace(" ", "_"),
        ("€", "EUR", 0.55)
    )

    battery_needed = max(TARGET_BATTERY - request.battery, 0)

    prompt = f"""
You are an EV charging planner giving a short, specific recommendation
to a driver who just opened navigation to a charging station.

Car Model: {request.car_model}
Current Battery: {request.battery}%
Target Battery: {TARGET_BATTERY}%
Battery needed: {battery_needed} percentage points

Charging Station: {request.station_name}
Available Connectors: {request.available_connectors}
Fastest connector here: {request.max_power_kw or "unknown"} kW
Connector types: {", ".join(request.connector_types) or "unknown"}
City: {request.city}
Local currency: {code} (typical price about {price_kwh} {code}/kWh)

Estimate realistic EV charging values for THIS specific situation and
write advice that actually reflects the numbers above (battery level,
how much charge is needed, how many connectors are free right now).

Rules:
- Return ONLY valid JSON, no markdown, no code fences.
- "charging_time": time to go from {request.battery}% to {TARGET_BATTERY}%,
  as a string like "26 mins". Limit the speed by the fastest connector
  ({request.max_power_kw or "unknown"} kW) and what this car can accept.
  Scale realistically with battery_needed.
- "charging_cost": a NUMBER ONLY (no currency symbol) in {code},
  scaled to battery_needed and the per-kWh price above.
- "advice": ONE short sentence that references the battery level,
  connector count, or charge needed.

{{
  "charging_time":"26 mins",
  "charging_cost":17.40,
  "advice":"Only {battery_needed} points needed here, and 2 connectors are free."
}}
"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.6,
                top_p=0.95,
                max_output_tokens=2048
            )
        )

        raw = response.text.strip()

        cleaned = (
            raw.replace("```json", "")
            .replace("```", "")
            .strip()
        )

        result = json.loads(cleaned)

        cost = result.get("charging_cost", 0)

        try:
            cost = float(
                str(cost).replace(currency, "").replace(",", "").strip()
            )

            formatted_cost = (
                f"{currency}{cost:,.0f}"
                if code in NO_DECIMALS
                else f"{currency}{cost:,.2f}"
            )
        except Exception:
            formatted_cost = f"{currency}{cost}"

        return {
            "charging_time": result.get("charging_time", "--"),
            "charging_cost": formatted_cost,
            "advice": result.get("advice", "No advice available.")
        }

    except Exception as e:
        print("Gemini Error:", e)

        kwh = battery_needed / 100 * BATTERY_KWH

        if request.max_power_kw:
            kw = min(request.max_power_kw, 120) * 0.85
            minutes = max(10, min(round(kwh / kw * 60), 600))
        else:
            minutes = max(10, min(round(battery_needed * 0.55), 90))

        cost = kwh * price_kwh

        formatted_cost = (
            f"{currency}{cost:,.0f}"
            if code in NO_DECIMALS
            else f"{currency}{cost:,.2f}"
        )

        if battery_needed <= 5:
            advice = (
                f"Battery is already at {request.battery}% — only a brief "
                f"top-up to {TARGET_BATTERY}% is needed here."
            )
        elif request.available_connectors <= 0:
            advice = (
                f"No connectors free right now at {request.station_name}; "
                f"you may need to wait or pick a nearby alternative."
            )
        elif request.available_connectors == 1:
            advice = (
                f"Only 1 connector free — charging {battery_needed}% "
                f"should take about {minutes} mins once you plug in."
            )
        else:
            advice = (
                f"{request.available_connectors} connectors free, "
                f"so you should charge {battery_needed}% up to "
                f"{TARGET_BATTERY}% in around {minutes} mins."
            )

        return {
            "charging_time": f"{minutes} mins",
            "charging_cost": formatted_cost,
            "advice": advice
        }