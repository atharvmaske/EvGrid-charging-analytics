const BASE_URL = "http://127.0.0.1:8000";

async function fetchAPI(endpoint) {
    const response = await fetch(`${BASE_URL}${endpoint}`);

    if (!response.ok) {
        const errorText = await response.text();
        console.error(
            "API Error:",
            endpoint,
            response.status,
            errorText
        );
        throw new Error(`API Error ${response.status}`);
    }

    return response.json();
}

export function getWeather(city) {
    return fetchAPI(`/weather/${city}`);
}

export function getAvailability(city) {
    return fetchAPI(`/availability/${city}`);
}

export function getStations(city) {
    return fetchAPI(`/stations/${city}`);
}

export function getMapStations(city) {
    return fetchAPI(`/map/${city}`);
}

export async function getRecommendations(
    city,
    userLocation,
    car = "",
    live = true
) {
    const query = new URLSearchParams({
        lat: userLocation.lat,
        lon: userLocation.lon,
        car,
        live,
    });

    return fetchAPI(`/recommendations/${city}?${query}`);
}

export async function getRoute(start, destination) {
    const query = new URLSearchParams({
        start_lat: start.lat,
        start_lon: start.lon,
        end_lat: destination.lat,
        end_lon: destination.lon,
    });

    return fetchAPI(`/route?${query}`);
}

export async function searchLocation(query, city) {
    const params = new URLSearchParams({
        query,
        city,
    });

    return fetchAPI(`/geocode?${params}`);
}

export async function autocompleteLocation(query, city) {
    const params = new URLSearchParams({
        query,
        city,
    });

    return fetchAPI(`/autocomplete?${params}`);
}

export async function getChargingPlan(data) {
    const response = await fetch(
        `${BASE_URL}/ai/charging-plan`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(data),
        }
    );

    if (!response.ok) {
        const errorText = await response.text();
        console.error("Gemini Error:", errorText);
        throw new Error("Charging planner failed.");
    }

    return response.json();
}