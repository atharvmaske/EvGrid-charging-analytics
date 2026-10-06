import { getRoute } from "./api.js";
import {
    updateUserMarker,
    drawRoute,
} from "./map.js";

const START = {
    lat: 52.512301,
    lon: 13.326966,
};

const DESTINATION = {
    lat: 52.510533,
    lon: 13.330307,
};

const DEMO_ROUTE = [
    { lat: 52.512301, lon: 13.326966 },
    { lat: 52.512180, lon: 13.327180 },
    { lat: 52.512020, lon: 13.327520 },
    { lat: 52.511850, lon: 13.327920 },
    { lat: 52.511650, lon: 13.328400 },
    { lat: 52.511420, lon: 13.328900 },
    { lat: 52.511120, lon: 13.329450 },
    { lat: 52.510850, lon: 13.329900 },
    { lat: 52.510533, lon: 13.330307 },
];

let index = 0;
let timer = null;

export async function startNavigationTest() {
    console.log(" Starting Berlin navigation demo...");

    index = 0;

    updateUserMarker(START.lat, START.lon);

    timer = setInterval(async () => {
        if (index >= DEMO_ROUTE.length) {
            clearInterval(timer);
            console.log(" Destination reached.");
            return;
        }

        const point = DEMO_ROUTE[index];

        updateUserMarker(point.lat, point.lon);

        try {
            const route = await getRoute(
                point,
                DESTINATION
            );

            drawRoute(route.polyline);

            console.log(
                ` Step ${index + 1}`,
                route.distance_km.toFixed(2) + " km",
                route.eta_minutes + " mins",
            );
        } catch (err) {
            console.error("Route error:", err);
        }

        index++;
    }, 2000);
}

export function stopNavigationTest() {
    if (timer) {
        clearInterval(timer);
        timer = null;
    }

    console.log(" Demo stopped.");
}