"""Fetch source data locally and save as CSV files, to be uploaded into a
Databricks Volume.

Free Edition serverless compute cannot reach data.fingrid.fi or
web-api.tp.entsoe.eu, so ingestion for those two sources happens here instead,
and only the resulting files are handed to Databricks. This mirrors a common
real-world pattern where a compute cluster sits behind a network boundary and a
separate extraction step lands the data first.

Weather is not fetched here: archive-api.open-meteo.com is reachable from the
notebook, so the bronze notebook loads it directly and incrementally.
"""

import csv
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from ingest.fetch_fingrid import fetch_dataset
from ingest.fetch_price import deduplicate_rows, fetch_price_range

load_dotenv()

OUTPUT_DIR = "data/bronze_raw"

# Days of history to fetch. Configurable so a local smoke test can use a short
# window without pulling a full year through the APIs.
FETCH_DAYS = int(os.getenv("FETCH_DAYS", "365"))


def write_csv(path: str, rows: list[dict]) -> None:
    """Write a list of dicts to CSV, using the first row's keys as the header."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    # Indexing rather than .get(): a missing secret must fail loudly here, not
    # produce an empty CSV that later overwrites good data in the Volume.
    api_key = os.environ["FINGRID_API_KEY"]
    entsoe_token = os.environ["ENTSOE_API_TOKEN"]

    print(f"Fetching {FETCH_DAYS} days of history")

    # Fingrid: ending 2 hours ago, because measured data lags real time slightly.
    fingrid_end = datetime.now(timezone.utc) - timedelta(hours=2)
    fingrid_start = fingrid_end - timedelta(days=FETCH_DAYS)
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

    # ENTSO-E: the same window, snapped to day boundaries because the market
    # publishes whole days rather than arbitrary ranges.
    print("Fetching ENTSO-E day-ahead price...")
    price_end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    price_start = price_end - timedelta(days=FETCH_DAYS)

    price_rows = fetch_price_range(entsoe_token, price_start, price_end)
    # ENTSO-E returns whole market days, so consecutive chunks overlap by one day.
    price_rows = deduplicate_rows(price_rows)
    # Market days start at 22:00 UTC and spill outside the range we asked for.
    price_rows = [row for row in price_rows if price_start <= row["time"] < price_end]

    write_csv(
        f"{OUTPUT_DIR}/entsoe_price.csv",
        [
            {
                "time": row["time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "price_eur_mwh": row["price_eur_mwh"],
                "resolution": row["resolution"],
            }
            for row in price_rows
        ],
    )
    print(f"  {len(price_rows)} rows saved")