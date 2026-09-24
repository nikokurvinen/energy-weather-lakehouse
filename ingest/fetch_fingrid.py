"""Fetch time series data from the Fingrid Open Data API.

Standalone module: no Spark dependency, so it can be unit tested
and run locally before being called from a Databricks notebook.
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://data.fingrid.fi/api/datasets/{dataset_id}/data"
PAGE_SIZE = 20000  # API maximum per page
RATE_LIMIT_SECONDS = 2.1  # API allows 1 request / 2s; add margin to be safe


def fetch_dataset(dataset_id: int, start_time: str, end_time: str, api_key: str) -> list[dict]:
    """Fetch all rows for one dataset between start_time and end_time.

    Loops over pages because a year of 15-min data (~35k rows)
    exceeds the API's 20000-row page limit.
    """
    headers = {"x-api-key": api_key}
    all_rows = []
    page = 1

    while True:
        params = {
            "startTime": start_time,
            "endTime": end_time,
            "pageSize": PAGE_SIZE,
            "page": page,
        }
        url = BASE_URL.format(dataset_id=dataset_id)
        response = requests.get(url, headers=headers, params=params, timeout=30)

        # Crash loudly on 4xx/5xx instead of silently returning bad data
        response.raise_for_status()

        payload = response.json()
        all_rows.extend(payload["data"])
        time.sleep(RATE_LIMIT_SECONDS)  # always wait, even if this was the last page

        next_page = payload["pagination"].get("nextPage")
        if next_page is None:
            break

        page = next_page

    return all_rows


if __name__ == "__main__":
    api_key = os.environ["FINGRID_API_KEY"]

    # Small range first: 2 days is enough to prove it works, no need to wait for pagination yet
    start = "2026-09-01T00:00:00Z"
    end = "2026-09-03T00:00:00Z"

    rows = fetch_dataset(dataset_id=124, start_time=start, end_time=end, api_key=api_key)
    print(f"Fetched {len(rows)} rows")
    print(rows[0])
    
    wind_rows = fetch_dataset(dataset_id=75, start_time=start, end_time=end, api_key=api_key)
    print(f"Fetched {len(wind_rows)} rows")
    print(wind_rows[0])