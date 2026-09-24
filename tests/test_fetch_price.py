"""Tests for the ENTSO-E price document parser."""

from datetime import datetime, timezone

from ingest.fetch_price import deduplicate_rows, parse_price_xml

# A minimal A44 document covering one 24-hour period at hourly resolution.
# Positions 2 and 4 through 24 are deliberately absent: ENTSO-E omits points
# whose price is unchanged, which is what the forward fill has to reconstruct.
SPARSE_DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<Publication_MarketDocument
    xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
  <TimeSeries>
    <Period>
      <timeInterval>
        <start>2026-09-19T22:00Z</start>
        <end>2026-09-20T22:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>10.0</price.amount></Point>
      <Point><position>3</position><price.amount>20.0</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>
"""


def test_row_count_follows_the_period_not_the_points():
    """A 24-hour period at PT60M yields 24 rows, even from 2 points."""
    rows = parse_price_xml(SPARSE_DOCUMENT)

    assert len(rows) == 23


def test_missing_positions_repeat_the_previous_price():
    """Position 2 is absent, so it must carry position 1's price."""
    rows = parse_price_xml(SPARSE_DOCUMENT)

    assert rows[0]["price_eur_mwh"] == 10.0  # position 1, given
    assert rows[1]["price_eur_mwh"] == 10.0  # position 2, filled
    assert rows[2]["price_eur_mwh"] == 20.0  # position 3, given
    assert rows[3]["price_eur_mwh"] == 20.0  # position 4, filled


def test_timestamps_step_by_the_declared_resolution():
    """Hourly resolution means consecutive rows are one hour apart."""
    rows = parse_price_xml(SPARSE_DOCUMENT)

    assert rows[0]["time"] == datetime(2026, 9, 19, 22, tzinfo=timezone.utc)
    assert rows[1]["time"] == datetime(2026, 9, 19, 23, tzinfo=timezone.utc)
    assert rows[23]["time"] == datetime(2026, 9, 20, 21, tzinfo=timezone.utc)


def test_deduplicate_keeps_the_first_occurrence():
    """Chunked requests overlap, so the same hour arrives twice."""
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)

    rows = [
        {"time": second, "price_eur_mwh": 2.0, "resolution": "PT60M"},
        {"time": first, "price_eur_mwh": 1.0, "resolution": "PT60M"},
        {"time": first, "price_eur_mwh": 99.0, "resolution": "PT60M"},
    ]

    result = deduplicate_rows(rows)

    assert len(result) == 2
    assert result[0]["price_eur_mwh"] == 1.0  # first occurrence, not 99.0
    assert result[0]["time"] == first  # output is sorted by time
    assert result[1]["time"] == second