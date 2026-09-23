"""Fetch historical weather data from the Open-Meteo API.

No API key required.
"""

import csv

import requests

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "temperature_2m,wind_speed_10m"


def fetch_weather(latitude: float, longitude: float, start_date: str, end_date: str) -> list[dict]:
    """Fetch hourly temperature and wind speed for one point between two dates."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": HOURLY_VARS,
        "timezone": "UTC",
    }
    response = requests.get(BASE_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    hourly = payload["hourly"]
    # The API returns parallel arrays (all same length); zip combines them
    # into one dict per hour instead of three separate lists
    rows = [
        {"time": t, "temperature_2m": temp, "wind_speed_10m": wind}
        for t, temp, wind in zip(hourly["time"], hourly["temperature_2m"], hourly["wind_speed_10m"])
    ]
    return rows


def load_weather_points(csv_path: str) -> list[dict]:
    """Read the seed CSV of weather observation points into a list of dicts."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))  # DictReader uses the header row as keys


if __name__ == "__main__":
    points = load_weather_points("seeds/area_weather_points.csv")

    start = "2026-09-01"
    end = "2026-09-03"

    for point in points:  # loop over every point instead of just the first
        rows = fetch_weather(
            latitude=float(point["latitude"]),
            longitude=float(point["longitude"]),
            start_date=start,
            end_date=end,
        )
        print(f"{point['city']}: fetched {len(rows)} rows")