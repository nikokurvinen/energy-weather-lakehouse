"""Fetch day-ahead electricity prices for Finland from the ENTSO-E Transparency Platform."""

import csv
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv

# ENTSO-E RESTful API endpoint
BASE_URL = "https://web-api.tp.entsoe.eu/api"

# EIC code identifying the Finnish bidding zone
FINLAND_DOMAIN = "10YFI-1--------U"

# A44 is the document type for day-ahead prices
DOCUMENT_TYPE = "A44"

# Minutes per ENTSO-E resolution code
RESOLUTION_MINUTES = {
    "PT15M": 15,
    "PT30M": 30,
    "PT60M": 60,
}


def fetch_price_xml(security_token: str, start: datetime, end: datetime) -> str:
    """Fetch the raw XML price document for a UTC time range."""
    params = {
        "securityToken": security_token,
        "documentType": DOCUMENT_TYPE,
        # For day-ahead prices both domains are the same bidding zone
        "in_Domain": FINLAND_DOMAIN,
        "out_Domain": FINLAND_DOMAIN,
        # ENTSO-E expects yyyyMMddHHmm in UTC, with no timezone suffix
        "periodStart": start.strftime("%Y%m%d%H%M"),
        "periodEnd": end.strftime("%Y%m%d%H%M"),
    }

    response = requests.get(BASE_URL, params=params, timeout=60)
    response.raise_for_status()

    return response.text

def parse_price_xml(xml_text: str) -> list[dict]:
    """Parse an ENTSO-E A44 price document into one row per interval."""
    root = ET.fromstring(xml_text)
    rows = []

    # {*} matches any namespace. ENTSO-E changes its namespace URI between
    # document versions, so matching it literally would be fragile.
    for period in root.findall(".//{*}Period"):
        start_text = period.find("{*}timeInterval/{*}start").text
        end_text = period.find("{*}timeInterval/{*}end").text
        resolution = period.find("{*}resolution").text

        step = RESOLUTION_MINUTES[resolution]
        period_start = datetime.strptime(start_text, "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc
        )
        period_end = datetime.strptime(end_text, "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc
        )

        # How many intervals this period should contain
        total_positions = int((period_end - period_start).total_seconds() / 60 / step)

        # Points are sparse: a missing position repeats the previous price
        prices_by_position = {
            int(point.find("{*}position").text): float(
                point.find("{*}price.amount").text
            )
            for point in period.findall("{*}Point")
        }

        last_price = None
        for position in range(1, total_positions + 1):
            if position in prices_by_position:
                last_price = prices_by_position[position]
            if last_price is None:
                continue

            rows.append(
                {
                    "time": period_start + timedelta(minutes=step * (position - 1)),
                    "price_eur_mwh": last_price,
                    "resolution": resolution,
                }
            )

    return rows

def fetch_price_range(
    security_token: str, start: datetime, end: datetime
) -> list[dict]:
    """Fetch a long range by requesting it in chunks the API will accept."""
    rows = []
    chunk_start = start

    while chunk_start < end:
        # 31 days stays well inside the per-request limit
        chunk_end = min(chunk_start + timedelta(days=31), end)

        xml_text = fetch_price_xml(security_token, chunk_start, chunk_end)
        chunk_rows = parse_price_xml(xml_text)
        rows.extend(chunk_rows)

        print(f"{chunk_start:%Y-%m-%d} to {chunk_end:%Y-%m-%d}: {len(chunk_rows)} rows")

        chunk_start = chunk_end

    return rows

def deduplicate_rows(rows: list[dict]) -> list[dict]:
    """Drop rows repeated across chunk boundaries, keeping the first seen."""
    by_time = {}
    for row in rows:
        by_time.setdefault(row["time"], row)
    return sorted(by_time.values(), key=lambda row: row["time"])

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("ENTSOE_API_TOKEN")
    if not token:
        raise SystemExit("ENTSOE_API_TOKEN not found. Check your .env file.")

    # Same window as the other bronze tables
    start_time = datetime(2025, 9, 17, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 18, tzinfo=timezone.utc)

    rows = fetch_price_range(token, start_time, end_time)
    print("Rows fetched:", len(rows))

    # ENTSO-E returns whole market days, so consecutive chunks overlap by one day
    raw_count = len(rows)
    rows = deduplicate_rows(rows)
    print(f"Deduplicated: {raw_count} -> {len(rows)} rows")

    # Market days start at 22:00 UTC and spill outside the range we asked for
    rows = [row for row in rows if start_time <= row["time"] < end_time]
    print("After trimming to window:", len(rows))

    print("Resolutions seen:", {row["resolution"] for row in rows})
    print("First:", rows[0])
    print("Last:", rows[-1])

    output_path = Path("data/bronze_raw/entsoe_price.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["time", "price_eur_mwh", "resolution"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "time": row["time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "price_eur_mwh": row["price_eur_mwh"],
                    "resolution": row["resolution"],
                }
            )

    print("Written to", output_path)