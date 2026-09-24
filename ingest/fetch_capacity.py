"""Fetch installed onshore wind capacity for Finland from ENTSO-E."""

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import xml.etree.ElementTree as ET
from datetime import timedelta

BASE_URL = "https://web-api.tp.entsoe.eu/api"
FINLAND_DOMAIN = "10YFI-1--------U"

# A68 is installed generation capacity, aggregated per production type
DOCUMENT_TYPE = "A68"
# A33 is the "year ahead" process, which is how installed capacity is published
PROCESS_TYPE = "A33"
# B19 is onshore wind
PSR_TYPE_WIND_ONSHORE = "B19"


def fetch_capacity_xml(security_token: str, start: datetime, end: datetime) -> str:
    """Fetch the raw XML installed capacity document for a period."""
    params = {
        "securityToken": security_token,
        "documentType": DOCUMENT_TYPE,
        "processType": PROCESS_TYPE,
        "in_Domain": FINLAND_DOMAIN,
        "psrType": PSR_TYPE_WIND_ONSHORE,
        "periodStart": start.strftime("%Y%m%d%H%M"),
        "periodEnd": end.strftime("%Y%m%d%H%M"),
    }

    response = requests.get(BASE_URL, params=params, timeout=60)
    response.raise_for_status()

    return response.text

def parse_capacity_xml(xml_text: str) -> list[dict]:
    """Parse an A68 installed capacity document into one row per year."""
    root = ET.fromstring(xml_text)
    rows = []

    for period in root.findall(".//{*}Period"):
        start_text = period.find("{*}timeInterval/{*}start").text
        period_start = datetime.strptime(start_text, "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc
        )

        # ENTSO-E year boundaries are CET midnight, so the period for 2025
        # starts at 2024-12-31T22:00Z. Shifting by the CET offset recovers
        # the year the figure actually refers to.
        year = (period_start + timedelta(hours=2)).year

        for point in period.findall("{*}Point"):
            # A68 reports quantity, not price.amount
            quantity = float(point.find("{*}quantity").text)
            rows.append({"year": year, "installed_capacity_mw": quantity})

    return rows

if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("ENTSOE_API_TOKEN")
    if not token:
        raise SystemExit("ENTSOE_API_TOKEN not found. Check your .env file.")

    # A68 allows at most one year per request, so fetch each year separately
    for year in (2025, 2026):
        start_time = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

        try:
            xml_text = fetch_capacity_xml(token, start_time, end_time)
            for row in parse_capacity_xml(xml_text):
                print(row)
        except requests.exceptions.HTTPError as error:
            print(f"--- {year} FAILED: {error.response.status_code} ---")
            print(error.response.text[:800])