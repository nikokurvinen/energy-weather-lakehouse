"""Fetch a full year of data locally and save as CSV files, to be
uploaded into a Databricks Volume.

Free Edition serverless compute cannot reach external APIs directly,
so ingestion happens here instead, and only the resulting files are
handed to Databricks. This mirrors a common real-world pattern where
a compute cluster sits behind a network boundary and a separate
extraction step lands the data first.
"""

import csv
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from ingest.fetch_fingrid import fetch_dataset
from ingest.fetch_weather import fetch_weather, load_weather_points

load_dotenv()

OUTPUT_DIR = "data/bronze_raw"


def write_csv(path: str, rows: list[dict]) -> None:
    """Write a list of dicts to CSV, using the first row's keys as the header."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    api_key = os.environ["FINGRID_API_KEY"]

    # Fingrid: 12 months, ending 2 hours ago
    fingrid_end = datetime.now(timezone.utc) - timedelta(hours=2)
    fingrid_start = fingrid_end - timedelta(days=365)
    fingrid_start_str = fingrid_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    fingrid_end_str = fingrid_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("Fetching Fingrid consumption...")
    consumption_rows = fetch_dataset(124, fingrid_start_str, fingrid_end_str, api_key)
    write_csv(f"{OUTPUT_DIR}/fingrid_consumption.csv", consumption_rows)
    print(f"  {len(consumption_rows)} rows saved")

    print("Fetching Fingrid wind production...")
    wind_rows = fetch_dataset(75, fingrid_start_str, fingrid_end_str, api_key)
    write_csv(f"{OUTPUT_DIR}/fingrid_wind.csv", wind_rows)
    print(f"  {len(wind_rows)} rows saved")

    # Open-Meteo archive API has a data lag, so end 6 days ago to be safe
    weather_end = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    weather_start = (datetime.now(timezone.utc) - timedelta(days=371)).strftime("%Y-%m-%d")

    points = load_weather_points("seeds/area_weather_points.csv")
    all_weather_rows = []
    print("Fetching weather...")
    for point in points:
        rows = fetch_weather(
            latitude=float(point["latitude"]),
            longitude=float(point["longitude"]),
            start_date=weather_start,
            end_date=weather_end,
        )
        for row in rows:
            row["area_id"] = point["area_id"]  # tag each row with which point it came from
            row["city"] = point["city"]
        all_weather_rows.extend(rows)
        print(f"  {point['city']}: {len(rows)} rows")

    write_csv(f"{OUTPUT_DIR}/weather.csv", all_weather_rows)
    print(f"Weather total: {len(all_weather_rows)} rows saved")