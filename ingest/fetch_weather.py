"""Fetch historical weather data from the Open-Meteo API.

No API key required.
"""

import csv
import time

import requests

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "temperature_2m,wind_speed_10m"

# Open-Meteo weights each request by the size of the date range, so a full year
# counts as many calls rather than one, and the free tier throttles with HTTP 429.
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 10


def fetch_weather(latitude: float, longitude: float, start_date: str, end_date: str) -> list[dict]:
    """Fetch hourly temperature and wind speed for one point between two dates.

    Retries on HTTP 429 with exponential backoff. Backoff clears a transient burst
    limit; it cannot clear an hourly or daily quota, and it does not try to.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": HOURLY_VARS,
        "timezone": "UTC",
    }

    for attempt in range(MAX_ATTEMPTS):
        response = requests.get(BASE_URL, params=params, timeout=60)

        if response.status_code == 429:
            # Do not sleep after the final attempt: there is nothing left to wait for.
            if attempt == MAX_ATTEMPTS - 1:
                break
            # Wait longer after each failure so the server is given room to recover.
            # Retrying immediately would make the throttling worse, not better.
            wait = BASE_BACKOFF_SECONDS * (2**attempt)
            print(f"Rate limited, waiting {wait}s before attempt {attempt + 2}/{MAX_ATTEMPTS}")
            time.sleep(wait)
            continue

        response.raise_for_status()
        payload = response.json()

        hourly = payload["hourly"]
        # The API returns parallel arrays (all same length); zip combines them
        # into one dict per hour instead of three separate lists
        return [
            {"time": t, "temperature_2m": temp, "wind_speed_10m": wind}
            for t, temp, wind in zip(
                hourly["time"], hourly["temperature_2m"], hourly["wind_speed_10m"]
            )
        ]

    raise RuntimeError(f"Open-Meteo still rate limiting after {MAX_ATTEMPTS} attempts")


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