let map = null;
let markersLayer = null;

let userMarker = null;
let bestMarker = null;
let routeLine = null;

let followUser = false;

window.evMap = null;

const blueIcon = L.icon({
    iconUrl:
        "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png",
    shadowUrl:
        "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
    iconSize: [26, 42],
    iconAnchor: [13, 42],
});

const goldIcon = L.icon({
    iconUrl:
        "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-gold.png",
    shadowUrl:
        "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
    iconSize: [32, 50],
    iconAnchor: [16, 50],
});

function initializeMap() {
    if (map) return map;

    map = L.map("map", {
        zoomControl: true,
        attributionControl: true,
    }).setView([52.52, 13.405], 12);

    L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            attribution: "© OpenStreetMap contributors",
            maxZoom: 19,
        }
    ).addTo(map);

    markersLayer = L.layerGroup().addTo(map);

    window.evMap = map;

    return map;
}

export function loadMap(stations = []) {
    initializeMap();

    markersLayer.clearLayers();

    stations.forEach((station) => {
        const marker = L.circleMarker(
            [station.latitude, station.longitude],
            {
                radius: 6,
                fillColor: "#3568F6",
                color: "#ffffff",
                weight: 2,
                fillOpacity: 1,
            }
        );

        const connectors = (
            station.connector_details || []
        ).map((c) => {
            const kw = Number(c.power_kw) > 0
                ? ` ${c.power_kw}kW`
                : "";

            const qty = Number(c.quantity) > 1
                ? ` ×${c.quantity}`
                : "";

            return `${c.type_name || c.type || "Unknown"}${qty} (${c.current_type_name || c.current || ""}${kw})`;
        });

        marker.bindPopup(`
            <b>${station.station_name}</b><br>
            ⚡ ${station.available_connectors}/${station.connectors} Available
            ${
                connectors.length
                    ? "<br>🔌 " + connectors.join("<br>🔌 ")
                    : ""
            }
        `);

        marker.addTo(markersLayer);
    });

    setTimeout(() => map.invalidateSize(), 150);
}

export function showUserLocation(lat, lon) {
    initializeMap();

    if (!userMarker) {
        userMarker = L.marker(
            [lat, lon],
            { icon: blueIcon }
        ).addTo(map);

        userMarker.bindPopup("📍 Your Location");
    } else {
        userMarker.setLatLng([lat, lon]);
    }

    map.flyTo([lat, lon], 15, {
        animate: true,
        duration: 0.8,
    });
}

export function updateUserMarker(lat, lon) {
    initializeMap();

    if (!userMarker) {
        userMarker = L.marker(
            [lat, lon],
            { icon: blueIcon }
        ).addTo(map);
    } else {
        userMarker.setLatLng([lat, lon]);
    }

    if (followUser) {
        map.flyTo([lat, lon], 17, {
            animate: true,
            duration: 0.8,
        });
    }
}

export function enableFollowMode() {
    followUser = true;
}

export function disableFollowMode() {
    followUser = false;
}

export function centerOnUser() {
    if (!userMarker) return;

    followUser = true;

    map.flyTo(userMarker.getLatLng(), 17, {
        animate: true,
        duration: 0.8,
    });
}

export function highlightBestStation(station) {
    initializeMap();

    if (!station) return;

    if (bestMarker) map.removeLayer(bestMarker);

    bestMarker = L.marker(
        [station.latitude, station.longitude],
        { icon: goldIcon }
    ).addTo(map);

    bestMarker.bindPopup(`
        ⭐ <b>Best EVGrid Station</b><br><br>
        ${station.station_name}<br>
        EVGrid Score: <b>${station.score}</b><br>
        ⚡ ${station.available_connectors}/${station.connectors} Available
    `);
}

export function drawRoute(polyline = []) {
    initializeMap();

    if (
        !Array.isArray(polyline)
        || polyline.length === 0
    ) {
        return;
    }

    if (routeLine) map.removeLayer(routeLine);

    routeLine = L.polyline(polyline, {
        color: "#18C964",
        weight: 6,
        opacity: 0.95,
        lineCap: "round",
        lineJoin: "round",
    }).addTo(map);

    map.fitBounds(routeLine.getBounds(), {
        padding: [80, 80],
    });
}

export function clearRoute() {
    if (routeLine) {
        map.removeLayer(routeLine);
        routeLine = null;
    }

    if (bestMarker) {
        map.removeLayer(bestMarker);
        bestMarker = null;
    }
}

export function focusStation(station) {
    initializeMap();

    if (!station || !map) return;

    requestAnimationFrame(() => {
        map.invalidateSize(true);

        map.flyTo(
            [station.latitude, station.longitude],
            16,
            {
                animate: true,
                duration: 1,
            }
        );
    });
}

export function expandMap() {
    document.body.classList.add("navigation-mode");

    requestAnimationFrame(() => {
        setTimeout(() => {
            if (!map) return;

            map.invalidateSize(true);

            map.eachLayer((layer) => {
                if (layer.redraw) layer.redraw();
            });
        }, 450);
    });
}

export function restoreSidebar() {
    document.body.classList.remove("navigation-mode");
    document.body.classList.remove("sidebar-hidden");

    const sidebar = document.getElementById("sidebar");

    if (sidebar) sidebar.classList.remove("hidden");

    requestAnimationFrame(() => {
        setTimeout(() => {
            if (!map) return;

            map.invalidateSize(true);

            map.eachLayer((layer) => {
                if (layer.redraw) layer.redraw();
            });
        }, 350);
    });
}

export function refreshMapSize() {
    if (!map) return;

    setTimeout(() => {
        map.invalidateSize();
    }, 250);
}