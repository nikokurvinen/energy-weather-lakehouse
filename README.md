# energy-weather-lakehouse

How weather explains Finnish electricity consumption, wind production and price, at hourly
resolution over a full year.

A lakehouse built in Databricks Free Edition: three public APIs, a bronze/silver/gold pipeline on
Delta Lake, quality checks as a gate, and a dimensional model with a slowly changing dimension.

---

## The question

Does weather explain what the Finnish power system does, and by how much?

Three sub-questions, plus the one that needs all three sources at once:

1. Does temperature explain consumption?
2. Which region's wind speed explains national wind production, and does location matter?
3. Does wind production explain the day-ahead price?
4. What happens when it is cold and calm at the same time?

---

## Findings

**A cold calm hour costs fourteen times a mild windy one.**

| Temperature | Wind | Hours | Consumption | Mean price |
| --- | --- | --- | --- | --- |
| cold | calm | 885 | 12,204 MW | **147.29 EUR/MWh** |
| cold | windy | 396 | 12,612 MW | 40.32 |
| mild | calm | 2,215 | 8,831 MW | 66.72 |
| mild | windy | 1,428 | 10,221 MW | **10.64 EUR/MWh** |

Holding temperature constant at cold and varying only wind, the price falls **73 percent** while
consumption barely moves. Demand is unchanged and supply increases, which isolates the supply-side
effect.

Cold calm hours are **885, about 10 percent of the year**. This is a recurring condition rather
than a rare extreme, which is why a power system is dimensioned for its tightest hours rather than
its average ones.

**Temperature drives consumption, strongly and monotonically.** 14,525 MW below -20 C against
8,515 MW between +10 and +20, roughly 70 percent more in the cold.

**Location matters, and the data proves it.** Correlation between local wind speed and national
capacity factor:

| City | Region | Correlation |
| --- | --- | --- |
| Vaasa | West coast | **0.761** |
| Oulu | North | 0.726 |
| Kuopio | East | 0.634 |
| Helsinki | South | 0.523 |

The ranking reconstructs the geography of Finnish wind capacity from the data alone: concentrated
along the west coast from Vaasa northward, sparse inland and in the south. Nothing in the model
was told where the turbines are. This is why the pipeline deliberately does not average the four
observation points into a national figure.

**Two apparent effects were confounders, and both were tested rather than assumed.** Consumption
appeared to rise again above +20 C, and to rise with wind among mild hours. Controlling for hour
of day removed the first; the second is explained by "mild" being a band wide enough to mix
autumn storms with summer afternoons. Details in [PROGRESS.md](PROGRESS.md).

---

## Architecture

```
Fingrid API  ─┐
ENTSO-E API  ─┴─> local fetch ─> CSV ─> UC Volume ─┐
                                                   ├─> BRONZE ─> SILVER ─> checks ─> GOLD
Open-Meteo API ────────────> direct from notebook ─┘
```

### Two ingestion paths, for a documented reason

Databricks Free Edition restricts outbound internet access to an undocumented set of allowed
domains. All three source hosts were probed from the notebook:

| Host | Result |
| --- | --- |
| `archive-api.open-meteo.com` | reachable |
| `data.fingrid.fi` | DNS resolution fails |
| `web-api.tp.entsoe.eu` | DNS resolution fails |

Weather is therefore fetched directly from the notebook. Fingrid and ENTSO-E are fetched by local
scripts in `ingest/`, landed as CSV in a Unity Catalog volume, and read from there.

Separating ingestion from transformation is standard practice regardless of this constraint:
production Spark clusters are frequently network isolated, with a dedicated ingestion layer
landing data first.

### Layers

**Bronze** accepts data as each source delivers it. Four tables, no transformation.

**Silver** conforms everything to one row per hour on a UTC key, with Finnish local time derived
alongside. Grain alignment, type repair, unit conversion, and incomplete hours dropped.

**Quality checks** run between silver and gold as a gate. Nine checks defined as a view, and a
cell that raises if any fail, so gold is never rebuilt on data that did not pass.

**Gold** in two shapes: a wide `gold_hourly` table that feeds a dashboard with no joins, and a
star schema for questions that need stated grain, conformed dimensions and history.

### Dimensional model

Two facts, because the grain differs. `fact_power_hour` is one row per hour; `fact_weather_hour`
is one row per hour per area. They are not merged, because that would repeat every national figure
four times.

Three dimensions: `dim_date` keyed on a `yyyyMMdd` integer and derived from local time,
`dim_area` as SCD1, and `dim_wind_capacity` as **SCD2**.

Capacity is SCD2 because it genuinely changes: 8,224 MW in 2025 and 9,330 MW in 2026, real figures
from ENTSO-E. Overwriting the old value would silently recompute every 2025 capacity factor
against a capacity that did not exist yet. The fact joins it **point in time**, on a range rather
than on equality, so each hour uses the version in force at that moment.

---

## Data sources

| Source | Data | Format | Resolution |
| --- | --- | --- | --- |
| [Fingrid Open Data](https://data.fingrid.fi) | Consumption, wind production | JSON | 15 min |
| [Open-Meteo](https://open-meteo.com) | Temperature, wind speed, 4 locations | JSON | hourly |
| [ENTSO-E Transparency](https://transparency.entsoe.eu) | Day-ahead price, installed capacity | XML | 15/60 min, annual |

Coverage: `2025-09-23` to `2026-09-17`, **8,624 hours**.

Three source formats, three different time resolutions, and two different ways of expressing time:
Fingrid and Open-Meteo carry timestamps on the row, while ENTSO-E carries a position index that has
to be reconstructed from the period start. Reconciling this is what the silver layer is for.

---

## Notable problems solved

**Sparse XML points.** ENTSO-E omits unchanged price points entirely, so positions 1, 2, 4 appear
with 3 missing. Parsing as given produces gaps; a forward fill over every expected position
reconstructs the series.

**Overlapping API chunks.** Requests are chunked to stay inside a per-request limit, but ENTSO-E
returns whole market days, so consecutive chunks overlapped by one day each. 12 chunks, 11
boundaries, 1,056 duplicate rows, deduplicated on timestamp.

**A missing hour, traced end to end.** Gold held 8,624 rows where the window implies 8,625. The
gap was traced back through silver to bronze and located at `2026-06-04 12:00` UTC, where Fingrid
is missing one quarter-hour reading. The pipeline dropped the hour rather than averaging three
readings and presenting the result as equal to the rest.

**A quality check that was wrong.** The gate failed on first run with 14 negative wind production
values. They are genuine: turbines draw power for control systems and heating when production is
near zero. The bound was corrected rather than the data clamped, because clamping would have
looked tidier and falsified the energy balance.

**A market structure change inside the data.** The European day-ahead market moved from hourly to
quarter-hourly settlement in October 2025, inside the covered period, so the price series carries
both resolutions. Handled with a single hourly mean, which is correct for either.

---

## Stack

Databricks Free Edition (serverless), Delta Lake, Unity Catalog, PySpark, Spark SQL, Python,
Databricks Git folders, GitHub.

## Layout

```
ingest/       Python fetch scripts, one per source, plus a local orchestrator
notebooks/    01_bronze, 02_silver, 03_quality_checks, 04_gold
seeds/        Weather observation points
src/, tests/  Transformation helpers and unit tests
```

## Running it

1. Copy `.env.example` to `.env` and add a Fingrid API key and an ENTSO-E token.
2. `python -m ingest.run_local_fetch` writes CSVs to `data/bronze_raw/`.
3. Upload them to the Unity Catalog volume `energy_weather.landing`.
4. Run the notebooks in order: `01_bronze`, `02_silver`, `03_quality_checks`, `04_gold`.

---

## Honest limitations

**Correlation, not causation**, except where a physical mechanism is known. Wind speed turning a
rotor is causal; the price relationships are not established as causal by this data. Time of day,
day of week, industrial activity and interconnector flows with neighbouring bidding zones all act
at the same time and none of them are in the model. Two findings were shown to be confounded once
one such variable was controlled for, which is reason to assume others are too.

**Four weather points are a simplification.** Finland has 19 regions. Four cities were chosen to
span the north/south temperature range and to put one observation near the west coast wind
capacity. This is not an administrative or population-weighted division.

**Full refresh, not incremental.** Every layer rebuilds with `mode("overwrite")`. This is fine at
this volume but is not production practice. The intended design is a watermark plus a Delta
`MERGE`, with a trailing re-merge window for the Fingrid series because measured data is revised
as settlement completes, and none for the price, because an auction clearing price is final once
published.

**Not scheduled.** A daily refresh cannot run entirely inside Databricks, because two of three
sources are unreachable from it.

**Prices are day-ahead spot** and exclude transmission, tax and margin.

---

## Working log

[PROGRESS.md](PROGRESS.md) is the chronological record: what was built, what broke, what was
decided and why, including the conclusions that turned out to be wrong.
