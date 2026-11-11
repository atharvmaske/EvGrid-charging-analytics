import * as api from "./api.js";
import * as map from "./map.js";

import {
    getWeather, getAvailability, getMapStations, getRecommendations,
    getRoute, searchLocation, autocompleteLocation, getChargingPlan
} from "./api.js";

import {
    loadMap, showUserLocation, updateUserMarker, enableFollowMode,
    disableFollowMode, centerOnUser, highlightBestStation, drawRoute,
    clearRoute, focusStation, expandMap, restoreSidebar, refreshMapSize
} from "./map.js";

let city = "berlin";
let userLocation = null;
let addressMode = false;

let batteryPercentage = 50;
let carModel = "";

let currentStation = null;
let currentRoute = null;
let navigationActive = false;

const sidebar = document.getElementById("sidebar");
const routeInfo = document.getElementById("routeInfo");
const mapContainer = document.getElementById("map-container");

const toggleSidebarBtn = document.getElementById("toggleSidebarBtn");
const stopNavTopBtn = document.getElementById("stopNavTopBtn");

const batterySlider = document.getElementById("batterySlider");
const batteryValue = document.getElementById("batteryValue");
const carSelect = document.getElementById("carModel");

batterySlider.addEventListener("input", (e) => {

    batteryPercentage = Number(e.target.value);

    batteryValue.textContent = `${batteryPercentage}%`;

    document.getElementById("routeBattery").textContent =
        `${batteryPercentage}%`;

});

carSelect.addEventListener("change", (e) => {

    carModel = e.target.value || "";

    document.getElementById("selectedCar").textContent =
        carModel || "Select EV Model";

    document.getElementById("routeCarModel").textContent =
        carModel || "Not Selected";

    if (addressMode && userLocation && !navigationActive) loadDashboard();

});

function setSidebarAvailable(available) {

    if (available) {
        document.body.classList.remove("sidebar-hidden");
        toggleSidebarBtn.style.display = "";
    } else {
        document.body.classList.add("sidebar-hidden");
        toggleSidebarBtn.style.display = "none";
        document.getElementById("stationList").innerHTML = "";
    }

    setTimeout(() => {
        if (window.evMap) window.evMap.invalidateSize(true);
    }, 300);

}

function enterNavigationMode() {

    navigationActive = true;

    document.body.classList.add("navigation-mode");
    document.body.classList.add("sidebar-hidden");

    routeInfo.classList.remove("hidden");

    stopNavTopBtn.style.display = "inline-flex";

    requestAnimationFrame(() => {
        setTimeout(() => {
            if (window.evMap) {
                window.evMap.invalidateSize(true);
            }
        }, 350);
    });

}

function renderConnectorBadges(connectorDetails) {

    if (!Array.isArray(connectorDetails) || connectorDetails.length === 0) {
        return '<p style="color:#888;font-size:12px;">Connector details not available for this station</p>';
    }

    return connectorDetails.map(conn => {

        const type = conn.type_name || conn.type || "Unknown";
        const current = conn.current_type_name || conn.current || "";
        const isDC = String(current).toUpperCase().includes("DC");
        const kw = Number(conn.power_kw);
        const power = kw > 0 ? ` • ${kw} kW` : "";
        const qty = Number(conn.quantity) > 1 ? ` ×${conn.quantity}` : "";

        return `
            <span class="connector-badge ${isDC ? "connector-dc" : "connector-ac"}">
                <span class="connector-type">${type}${qty}</span>
                <span class="connector-power">${current}${power}</span>
            </span>
        `;

    }).join("");

}

function esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, c => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
}

function renderStationCard(station) {

    const total = Number(station.connectors) || 0;
    const free  = Number(station.available_connectors) || 0;
    const busy  = Number(station.occupied_connectors) || 0;

    const busyPct = total ? Math.round((busy / total) * 100) : 0;
    const level   = busyPct >= 80 ? "high" : busyPct >= 50 ? "mid" : "low";
    const isLive  = station.data_source === "live";

    return `
        <div class="station-card level-${level}">

            <div class="station-header">
                <div class="station-title">
                    <h3>${esc(station.station_name)}</h3>
                    <div class="station-tags">
                        <span class="tag tag-score">⭐ ${esc(station.score ?? "–")}</span>
                        <span class="tag">📍 ${esc(station.distance_km ?? "0")} km</span>
                        ${station.max_power_kw ? `<span class="tag tag-power">⚡ ${station.max_power_kw} kW</span>` : ""}
                        ${station.compatible === false ? `<span class="tag tag-warn">⚠ No matching plug</span>` : ""}
                        <span class="tag ${isLive ? "tag-live" : "tag-sim"}">${isLive ? "● Live" : "◌ Simulated"}</span>
                    </div>
                </div>

                <div class="busy-badge">
                    <strong>${busyPct}%</strong>
                    <small>busy</small>
                </div>
            </div>

            <div class="occupancy-bar" title="${free} of ${total} free">
                <span style="width:${total ? (free / total) * 100 : 0}%"></span>
            </div>

            <div class="station-availability">
                <div class="stat stat-free"><strong>${free}</strong><small>Free</small></div>
                <div class="stat stat-busy"><strong>${busy}</strong><small>Occupied</small></div>
                <div class="stat"><strong>${total}</strong><small>Total</small></div>
            </div>

            <div class="connector-section">
                <div class="connector-label">🔌 Connectors</div>
                <div class="connector-list">
                    ${renderConnectorBadges(station.connector_details)}
                </div>
            </div>

            <button class="station-action-btn">🧭 Navigate</button>
        </div>
    `;
}

function exitNavigationMode() {

    navigationActive = false;

    document.body.classList.remove("navigation-mode");

    if (addressMode && userLocation) {
        document.body.classList.remove("sidebar-hidden");
    } else {
        document.body.classList.add("sidebar-hidden");
    }

    routeInfo.classList.add("hidden");

    stopNavTopBtn.style.display = "none";

    requestAnimationFrame(() => {
        setTimeout(() => {
            if (window.evMap) {
                window.evMap.invalidateSize(true);
            }
        }, 350);
    });

}

toggleSidebarBtn.addEventListener("click", () => {

    document.body.classList.toggle("sidebar-hidden");

    setTimeout(() => {
        if (window.evMap) {
            window.evMap.invalidateSize();
        }
    }, 250);

});

async function getCurrentLocation() {

    if (!navigator.geolocation) return null;

    return new Promise((resolve) => {

        navigator.geolocation.getCurrentPosition(

            (position) =>
                resolve({
                    lat: position.coords.latitude,
                    lon: position.coords.longitude
                }),

            () => resolve(null),

            {
                enableHighAccuracy: true,
                timeout: 8000
            }

        );

    });

}

function fitToStations(stations) {

    if (!window.evMap || !stations.length) return;

    const pts = stations
        .filter(s => s.latitude != null && s.longitude != null)
        .map(s => [s.latitude, s.longitude]);

    if (pts.length) {
        window.evMap.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
    }

}

async function loadDashboard() {

    try {

        const weather = await getWeather(city).catch(() => ({}));
        const mapData = await getMapStations(city);

        const stations = Array.isArray(mapData)
            ? mapData
            : (mapData.stations || []);

        updateWeather(weather);
        updateAvailability(stations, stations.length);

        loadMap(stations);

        if (!userLocation) fitToStations(stations);

        if (userLocation) {
            showUserLocation(userLocation.lat, userLocation.lon);
        }

        if (addressMode && userLocation) {

            const recData = await getRecommendations(city, userLocation, carModel);

            const recommendations = Array.isArray(recData)
                ? recData
                : (recData.value || recData.stations || []);

            updateRecommendations(recommendations);

        } else {

            setSidebarAvailable(false);

        }

    } catch (err) {

        console.error("Dashboard load failed:", err);

    }

}

setTimeout(() => {
    const zoom = document.querySelector(".leaflet-top.leaflet-left");
    if (zoom) zoom.style.top = "80px";
}, 500);

function updateWeather(weather = {}) {

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    const cityName = (weather.city || city)
        .replaceAll("_", " ")
        .toUpperCase();

    setText("city-name", cityName);

    setText(
        "temperature",
        weather.temperature != null
            ? `${weather.temperature}°C`
            : "--°C"
    );

    setText(
        "rain",
        weather.precipitation != null
            ? `${weather.precipitation} mm`
            : "-- mm"
    );

    setText(
        "humidity",
        weather.humidity != null
            ? `${weather.humidity}%`
            : "--%"
    );

    setText(
        "wind",
        weather.wind_speed != null
            ? `${weather.wind_speed} km/h`
            : "-- km/h"
    );

}

function updateAvailability(data = {}, totalStations = 0) {

    let available = 0;
    let occupied = 0;

    if (Array.isArray(data)) {

        data.forEach((station) => {

            available += station.available_connectors || 0;
            occupied += station.occupied_connectors || 0;

        });

    } else {

        available = data.total_available ?? 0;
        occupied = data.total_occupied ?? 0;

    }

    document.getElementById("station-count").textContent = totalStations;
    document.getElementById("available-count").textContent = available;
    document.getElementById("occupied-count").textContent = occupied;

}

function updateRecommendations(stations = []) {

    const list = document.getElementById("stationList");
    list.innerHTML = "";

    const title = document.getElementById("recommendationTitle");
    const subtitle = document.getElementById("recommendationSubtitle");

    if (title) title.style.display = "none";
    if (subtitle) subtitle.style.display = "none";

    setSidebarAvailable(true);

    if (!stations.length) {

        list.innerHTML = `
            <div class="station-empty">
                <h3>No charging stations nearby</h3>
                <p>Try another address or use Current Location.</p>
            </div>
        `;

        return;

    }

    if (!navigationActive) {
        highlightBestStation(stations[0]);
    }

    setSidebarAvailable(true);

    list.innerHTML = `
        <div class="recommendation-header">
            <h2>⭐ ${stations.length} Recommended Stations</h2>
            <p>Ranked by EVGrid Score • Distance • Availability</p>
        </div>
    `;

    stations.forEach((station) => {

        const wrapper = document.createElement("div");
        wrapper.className = "fade-in";
        wrapper.innerHTML = renderStationCard(station);

        wrapper.querySelector(".station-action-btn")
            .addEventListener("click", () => navigateToStation(station));

        list.appendChild(wrapper);

    });

}
async function navigateToStation(station) {

    if (!userLocation) {
        alert("Choose Current Location or search an address first.");
        return;
    }

    if (!carModel) {
        alert("Select your EV Model first.");
        return;
    }

    try {

        currentStation = station;

        enterNavigationMode();

        await new Promise(resolve => setTimeout(resolve, 450));

        if (window.evMap) {
            window.evMap.invalidateSize(true);
        }

        clearRoute();

        highlightBestStation(station);

        currentRoute = await getRoute(userLocation, {
            lat: station.latitude,
            lon: station.longitude
        });

        drawRoute(currentRoute.polyline);

        focusStation(station);

        const ai = await getChargingPlan({
            car_model: carModel,
            battery: batteryPercentage,
            station_name: station.station_name,
            available_connectors: station.available_connectors,
            city,
            max_power_kw: station.max_power_kw || 0,
            connector_types: (station.connector_details || []).map(c => c.type_name)
        });

        routeInfo.classList.remove("hidden");

        const connBox = document.getElementById("routeConnectorDetails");
        const connList = document.getElementById("routeConnectorsList");

        if (station.connector_details && station.connector_details.length) {
            connList.innerHTML = renderConnectorBadges(station.connector_details);
            connBox.style.display = "block";
        } else {
            connBox.style.display = "none";
        }

        document.getElementById("routeStation").textContent =
            station.station_name;

        document.getElementById("routeDistance").textContent =
            `${Number(currentRoute.distance_km).toFixed(2)} km`;

        document.getElementById("routeETA").textContent =
            `${currentRoute.eta_minutes} mins`;

        document.getElementById("routeAvailability").textContent =
            `${station.available_connectors}/${station.connectors}`;

        document.getElementById("routeCost").textContent =
            ai.charging_cost || "--";

        document.getElementById("routeChargeTime").textContent =
            ai.charging_time || "--";

        document.getElementById("routeAdvice").textContent =
            ai.advice || "Fast charger available.";

        document.getElementById("routeCarModel").textContent = carModel;
        document.getElementById("routeBattery").textContent =
            `${batteryPercentage}%`;

    } catch (err) {

        console.error("Navigation Error:", err);

        exitNavigationMode();

        alert("Unable to calculate route.");

    }

}

function startNavigation() {

    if (!currentStation) return;

    const url =
        `https://www.google.com/maps/dir/?api=1` +
        `&destination=${currentStation.latitude},${currentStation.longitude}` +
        `&travelmode=driving`;

    window.open(url, "_blank");

}

function stopNavigation() {

    currentStation = null;
    currentRoute = null;
    navigationActive = false;

    clearRoute();

    exitNavigationMode();

    setTimeout(() => {
        if (window.evMap) {
            window.evMap.invalidateSize();
        }
    }, 300);

}

function centerMapOnUser() {

    if (!userLocation) return;

    centerOnUser();

}

document.getElementById("startNavBtn")
    .addEventListener("click", startNavigation);

document.getElementById("stopNavBtn")
    .addEventListener("click", stopNavigation);

document.getElementById("centerNavBtn")
    .addEventListener("click", centerMapOnUser);

stopNavTopBtn.addEventListener("click", stopNavigation);

document.getElementById("citySelect").addEventListener("change", async (e) => {

    city = e.target.value;

    userLocation = null;
    addressMode = false;
    currentStation = null;
    currentRoute = null;

    clearRoute();
    exitNavigationMode();

    document.getElementById("customLocation").value = "";

    document.getElementById("locationStatus").textContent =
        `📍 Manual city selected: ${city.replaceAll("_", " ")}`;

    await loadDashboard();

});

document.getElementById("locationBtn")
    .addEventListener("click", async () => {

        const loc = await getCurrentLocation();

        if (!loc) {
            alert("Unable to get GPS location.");
            return;
        }

        userLocation = loc;
        addressMode = true;

        showUserLocation(loc.lat, loc.lon);

        document.getElementById("locationStatus").textContent =
            "📍 Using Current GPS Location";

        await loadDashboard();

    });

document.getElementById("searchLocationBtn")
    .addEventListener("click", async () => {

        const query =
            document.getElementById("customLocation").value.trim();

        if (!query) return;

        try {

            const result = await searchLocation(query, city);

            userLocation = {
                lat: result.latitude,
                lon: result.longitude
            };

            addressMode = true;

            showUserLocation(result.latitude, result.longitude);

            document.getElementById("locationStatus").textContent =
                `📍 Using address: ${result.address}`;

            await loadDashboard();

        } catch {

            alert("Address not found.");

        }

    });

const addressInput =
    document.getElementById("customLocation");

const suggestionBox =
    document.getElementById("addressSuggestions");

addressInput.addEventListener("input", async () => {

    const query = addressInput.value.trim();

    if (query.length < 3) {

        suggestionBox.innerHTML = "";
        suggestionBox.style.display = "none";
        return;

    }

    try {

        const places =
            await autocompleteLocation(query, city);

        suggestionBox.innerHTML = "";

        if (!places.length) {
            suggestionBox.style.display = "none";
            return;
        }

        suggestionBox.style.display = "block";

        places.forEach((place) => {

            const item = document.createElement("div");
            item.className = "suggestion-item";
            item.textContent = place.address;

            item.onclick = async () => {

                addressInput.value = place.address;

                suggestionBox.innerHTML = "";
                suggestionBox.style.display = "none";

                userLocation = {
                    lat: place.lat,
                    lon: place.lon
                };

                addressMode = true;

                if (place.city) {
                    city = place.city;
                    document.getElementById("citySelect").value = city;
                }

                showUserLocation(place.lat, place.lon);

                document.getElementById("locationStatus").textContent =
                    `📍 Using address: ${place.address}`;

                await loadDashboard();

            };

            suggestionBox.appendChild(item);

        });

    } catch {

        suggestionBox.innerHTML = "";
        suggestionBox.style.display = "none";

    }

});

document.addEventListener("click", (e) => {

    if (!e.target.closest(".search-wrapper")) {

        suggestionBox.innerHTML = "";
        suggestionBox.style.display = "none";

    }

});

document.getElementById("refreshBtn")
    .addEventListener("click", async () => {

        if (navigationActive && currentStation) {
            await navigateToStation(currentStation);
        } else {
            await loadDashboard();
        }

    });

document.getElementById("adminBtn")
    .addEventListener("click", () => {
        window.open("http://localhost:3000", "_blank");
    });

document.addEventListener("keydown", (e) => {

    if (e.key === "Escape") {
        stopNavigation();
    }

});

routeInfo.classList.add("hidden");
sidebar.classList.remove("hidden");
setSidebarAvailable(false);

loadDashboard();