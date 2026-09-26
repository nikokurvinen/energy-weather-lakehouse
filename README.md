# energy-weather-lakehouse

How weather explains Finnish electricity consumption, wind production and price, at hourly
resolution over a full year.

A lakehouse built in Databricks Free Edition: three public APIs, a bronze/silver/gold pipeline on
Delta Lake, quality checks as a gate, a dimensional model with a slowly changing dimension, and a
scheduled refresh that spans two environments.

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

**A cold calm hour costs sixteen times a warm windy one.**

Mean day-ahead price in cents per kWh, by temperature and wind:

| | Calm | Moderate | Windy |
| --- | --- | --- | --- |
| **Cold** (below 0 C) | **14.73** | 8.99 | 4.03 |
| **Mild** (0 to 15 C) | 7.76 | 3.70 | 1.07 |
| **Warm** (above 15 C) | 4.61 | 1.92 | **0.91** |

Nine cells, monotonic in both directions, no exceptions. Both factors act and they compound.

Holding temperature constant at cold and varying only wind, the price falls **73 percent**
(14.73 to 4.03) while consumption barely moves (12,204 to 12,612 MW). Demand is unchanged and
supply increases, which isolates the supply-side effect.

Cold calm hours are **885, about 10 percent of the year**. This is a recurring condition rather
than a rare extreme, which is why a power system is dimensioned for its tightest hours rather than
its average ones. The peak hour reached **61.4 cents per kWh**.

**The first version of this analysis used two temperature bands and hid a real effect.** "Mild"
covered everything above zero, mixing a 5 C autumn evening with a 25 C summer afternoon, whose
prices differ almost twofold. Splitting warm out revealed the full gradient.

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

**February and March are a natural experiment inside the data.** Two winter months with similar
demand and opposite wind:

| | Consumption | Capacity factor | Price |
| --- | --- | --- | --- |
| February 2026 | 13,032 MW | 0.19 | **13.72 c/kWh** |
| March 2026 | 11,056 MW | 0.42 | **2.78 c/kWh** |

Demand differs by 18 percent, wind by 118 percent, price by 393 percent. The same result as the
table above, reached without bucketing anything.

July is the cheapest month at 1.54 c/kWh **despite having the year's lowest wind**, because demand
is lowest too. Price is set by the ratio of demand to supply, not by either alone. That is the most
precise statement this dataset supports.

**Two apparent effects were confounders, and both were tested rather than assumed.** Consumption
appeared to rise again above +20 C, and to rise with wind among mild hours. Controlling for hour
of day removed the first; the second is explained by "mild" being a band wide enough to mix
autumn storms with summer afternoons. Details in [PROGRESS.md](PROGRESS.md).

**The daylight saving handling is verified by the data itself.** Hours per month, grouped by the
local calendar: October 2025 has **745** and March 2026 has **743**, against 744 for a normal
31-day month. The clock changes appear exactly where they should. This only works because
`dim_date` is derived from local time; a UTC-derived date dimension would show 744 for both and
the transitions would be invisible.

---

## Architecture

The pipeline runs across two environments, because one of them cannot reach two of the sources.

```
GitHub Actions, daily at 05:17 UTC
  1. python -m ingest.run_local_fetch    Fingrid + ENTSO-E  ->  CSV
  2. databricks fs cp                    CSV  ->  Unity Catalog volume
  3. databricks jobs run-now             starts the job below

Databricks Job, four tasks, each depending on the one before it
  bronze  ->  silver  ->  quality checks  ->  gold
     |
     +--  Open-Meteo fetched directly here and merged incrementally
```

### Why two environments

Databricks Free Edition restricts outbound internet access to an undocumented set of allowed
domains. All three source hosts were probed from a notebook:

| Host | Result |
| --- | --- |
| `archive-api.open-meteo.com` | reachable |
| `data.fingrid.fi` | DNS resolution fails |
| `web-api.tp.entsoe.eu` | DNS resolution fails |

Weather is therefore fetched inside the notebook, where it can be loaded incrementally. Fingrid
and ENTSO-E are fetched by a GitHub Actions runner, landed as CSV in a Unity Catalog volume, and
read from there.

Separating ingestion from transformation is standard practice regardless of this constraint:
production Spark clusters are frequently network isolated, with a dedicated ingestion layer
landing data first. Here the constraint forced the pattern rather than the other way round.

### Orchestration

The four notebooks run as a Databricks job with an explicit dependency chain, not as four things
someone remembers to run in order. The chain is what makes the quality gate real: if
`03_quality_checks` raises, `04_gold` is never started, and gold keeps its last good contents
rather than being rebuilt on data that failed.

**Retry policy follows the failure mode.** The bronze task retries with exponential backoff,
because it calls a network API and a timeout or a 503 is transient. The quality gate has retries
disabled entirely, because a failure there means the data is bad and the same run would fail
identically. Retrying a deterministic failure only burns quota and blurs the signal.

**The workflow order is fetch, upload, trigger.** If the fetch fails, nothing is uploaded and the
job is never started, so the volume keeps yesterday's good files and the tables keep their last
good state. Fail before corrupting, rather than half-writing and hoping.

**The job is defined as code.** `databricks.yml` declares the task chain, the dependency order, the
per-task retry policy and the failure notification, and `databricks bundle deploy` makes the
workspace match the file. A job that exists only in a UI cannot be reviewed, diffed or rebuilt, and
a change to it leaves no trace; this one is a pull request like any other. The bundle is deployed
from a laptop rather than from inside the workspace, because `bundle deploy` downloads a Terraform
binary from HashiCorp, which the same outbound allowlist blocks.

### Incremental loading

Every bronze table is written with a Delta `MERGE` on the source's natural key, never with
`mode("overwrite")`. Overwrite is correct only when each run produces the whole dataset; the moment
the fetch window moves, it deletes the year and leaves the window.

| Table | Merge key | Why that key |
| --- | --- | --- |
| `bronze_consumption` | `startTime` | one row per quarter hour |
| `bronze_wind` | `startTime` | one row per quarter hour |
| `bronze_price` | `time` | one row per settlement interval |
| `bronze_weather` | `time, area_id` | the same hour appears once per observation point |

A key that is too broad matches several target rows and Delta refuses the merge outright. A key
that is too narrow matches nothing and inserts duplicates on every run, silently, which is the
worse failure.

**Weather carries a watermark.** It is read from the target table rather than kept in a separate
state file, because a value derived from the table is true by definition, including after a run
that died halfway. The window runs from the watermark minus three days, so values the source later
revised are picked up, to today minus six days, because ERA5 is published daily with a five day
delay and asking for yesterday returns nothing.

**Fingrid and the price use a fixed trailing window** of fourteen days, set by `FETCH_DAYS` in the
workflow. Fourteen days covers the period in which Finnish balance settlement still revises
measured data. A backfill runs the same script with `FETCH_DAYS=365`.

There is no `WHEN NOT MATCHED BY SOURCE THEN DELETE`. A row the source stops delivering is kept,
which is correct for an append-mostly time series and would be wrong for a snapshot of current
state.

The Delta history is the proof, and it also records the change of strategy:

| version | operation | source rows | updated | inserted |
| --- | --- | --- | --- | --- |
| 8 | MERGE | 35,039 | 35,039 | 0 |
| 7 | CREATE OR REPLACE TABLE AS SELECT | | | |

And end to end, on a fourteen day window: **1,343 rows fetched, two rows added.** The other 1,341
were rewritten in place. Two is exactly the number of quarter-hour readings Fingrid had published
since the previous run.

### Layers

**Bronze** accepts data as each source delivers it. Four tables, no transformation.

**Silver** conforms everything to one row per hour on a UTC key, with Finnish local time derived
alongside. Grain alignment, type repair, unit conversion, and incomplete hours dropped.

**Quality checks** run between silver and gold as a gate. Nine checks defined as a view, and a
cell that raises if any fail.

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

## Dashboard

An AI/BI dashboard reads the star schema directly: four headline figures and four charts covering
price by weather conditions, consumption by temperature, the regional wind correlation, and monthly
seasonality. Its definition lives in `dashboards/` and is version controlled with the rest of the
project.

Chart titles state the finding rather than the variables, and each carries a description covering
what the reader cannot infer from the picture: how the wind bands are defined, which bar rests on a
thin sample, and why one apparent anomaly is a confounder.

---

## Data sources

| Source | Data | Format | Resolution | Loaded by |
| --- | --- | --- | --- | --- |
| [Fingrid Open Data](https://data.fingrid.fi) | Consumption, wind production | JSON | 15 min | GitHub Actions |
| [Open-Meteo](https://open-meteo.com) | Temperature, wind speed, 4 locations | JSON | hourly | Databricks, incremental |
| [ENTSO-E Transparency](https://transparency.entsoe.eu) | Day-ahead price, installed capacity | XML | 15/60 min, annual | GitHub Actions |

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

**A table nothing could rebuild.** The bronze weather table had been created by a notebook version
that no longer existed: every later cell read it, none wrote it. The pipeline ran green only
because the table happened to persist between runs. Dropping it would have broken silver. This is
the class of bug that survives orchestration precisely because state carries over, and the only
honest test is to drop the target tables and run from empty.

**A missing hour, traced end to end.** Gold held 8,624 rows where the window implies 8,625. The
gap was traced back through silver to bronze and located at `2026-06-04 12:00` UTC, where Fingrid
is missing one quarter-hour reading. The pipeline dropped the hour rather than averaging three
readings and presenting the result as equal to the rest.

**A quality check that was wrong.** The gate failed on first run with 14 negative wind production
values. They are genuine: turbines draw power for control systems and heating when production is
near zero. The bound was corrected rather than the data clamped, because clamping would have
looked tidier and falsified the energy balance.

**A rate limit that backoff cannot fix.** Open-Meteo weights each request by the size of the date
range, so a full year for four locations counts as hundreds of calls rather than four. The initial
load hit the quota. Exponential backoff was added and is correct for a transient burst limit, but
it retried five times and failed, because an hourly or daily quota does not clear in minutes.
Backoff is the right tool for the wrong failure here; the actual fix is that the initial load
happens once and every later run asks for three days.

**A market structure change inside the data.** The European day-ahead market moved from hourly to
quarter-hourly settlement in October 2025, inside the covered period, so the price series carries
both resolutions. Handled with a single hourly mean, which is correct for either.

---

## Stack

Databricks Free Edition (serverless), Delta Lake, Unity Catalog, PySpark, Spark SQL, Python,
Databricks Jobs, Databricks Asset Bundles, Databricks CLI, Databricks Git folders, GitHub,
GitHub Actions.

## Layout

```
databricks.yml      The job definition as code, deployed with the Databricks CLI
.github/workflows/  ci.yml (ruff + pytest), daily_refresh.yml (scheduled ingestion)
ingest/             Python fetch scripts, one per source, plus the orchestrating entry point
notebooks/          01_bronze, 02_silver, 03_quality_checks, 04_gold
dashboards/         AI/BI dashboard definition
seeds/              Weather observation points
src/, tests/        Transformation helpers and unit tests
```

## Running it

The pipeline runs itself daily. To run it by hand:

**Scheduled path**: GitHub, Actions, Daily refresh, Run workflow. This fetches, uploads and
triggers the Databricks job.

**Locally**, to reproduce the ingestion side only:

1. Copy `.env.example` to `.env` and add a Fingrid API key and an ENTSO-E token.
2. `python -m ingest.run_local_fetch` writes CSVs to `data/bronze_raw/`. `FETCH_DAYS=7` shortens
   the window for a smoke test.
3. Upload them to the Unity Catalog volume `energy_weather.landing`.
4. Run the Databricks job, or the notebooks in order: `01_bronze`, `02_silver`,
   `03_quality_checks`, `04_gold`.

**To change the job**, edit `databricks.yml` and run `databricks bundle validate` and
`databricks bundle deploy`. Do not edit it in the workspace UI: the next deploy would revert it.

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

**Only weather is watermarked.** Every source merges, but Fingrid and the price use a fixed
fourteen day window rather than a watermark derived from the target table. Fourteen days is a
guess at how long settlement keeps revising, checked against nothing. If a run were missed for
longer than that, the gap would not be noticed or filled; a watermark would make the window
self-correcting.

**Failure reaches one place, not everyone who should know.** The workflow waits for the
Databricks run, reads its `result_state` and fails explicitly on anything other than `SUCCESS`,
rather than trusting an exit code, which also covers `SUCCESS_WITH_FAILURES`. The job itself emails
on failure. That is enough for one person; a team would route this to a channel with an on-call
owner, and would alert on data freshness rather than only on the run, because a pipeline that
succeeds every night while its source stops publishing is the failure nobody sees.

**The job runs whatever the Git folder is checked out to.** The bundle owns the job definition,
but the notebooks it runs are read from a workspace path, not from a git ref, so switching the Git
folder's branch silently changes what the scheduled job executes. Pointing the job at a git ref, or
running the notebooks the bundle itself deploys, would close this. The notebooks import `ingest/`
from the repository, so whichever path is used has to keep the repository layout intact.

**The job ID is hard coded in the workflow.** If the bundle ever recreates the job rather than
updating it, the ID changes and the workflow triggers nothing. Reading the ID at run time from
`databricks bundle summary` would remove the coupling, at the cost of another moving part.

**The credentials expire.** The Databricks token used by the workflow is a personal access token
with an expiry date, and it is tied to one person. A service principal with OAuth is the right
mechanism; a personal token was used because it was available.

**The schedule stops itself.** GitHub disables scheduled workflows after 60 days without repository
activity, so the daily refresh will stop on its own if the project is left alone.

**The dashboard cannot be shared publicly.** Free Edition offers only "people with access" or
"anyone in my account", and the account has one user. The link was tested in a private window and
shows a login wall, so the dashboard is included here as an image rather than a live link.

**Prices are day-ahead spot** and exclude transmission, tax and margin.

---

## Working log

[PROGRESS.md](PROGRESS.md) is the chronological record: what was built, what broke, what was
decided and why, including the conclusions that turned out to be wrong.
