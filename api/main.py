from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi_project.routers import (
    availability,
    stations,
    map,
    weather,
    geocode,
    gemini,
    route,
    recommendations,
    connectors_analytics,
)

app = FastAPI(
    title="EVGrid Charging Analytics API",
    version="1.0.0",
    description="EV Charging Analytics Platform API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://evgrid.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(availability.router, tags=["Availability"])
app.include_router(stations.router, tags=["Stations"])
app.include_router(map.router, tags=["Map"])
app.include_router(weather.router, tags=["Weather"])
app.include_router(geocode.router, tags=["Geocode"])
app.include_router(gemini.router, tags=["Gemini AI"])
app.include_router(route.router, tags=["Route"])
app.include_router(recommendations.router, tags=["Recommendations"])
app.include_router(connectors_analytics.router, tags=["Connector Analytics"])


@app.get("/")
def root():
    return {
        "message": "EVGrid Charging Analytics API is running",
        "docs": "/docs",
        "version": "1.0.0",
    }


@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "healthy",
        "service": "EVGrid Charging Analytics API",
        "version": "1.0.0",
    }