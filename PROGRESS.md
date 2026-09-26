# Progress Log

Chronological, honest notes on what was built, decided, and debugged during this project,
written while working alongside an AI assistant (Claude). See `README.md` for the polished,
final description of the project; this file is the working journal behind it.

---

## Day 1 (Tue)

- Databricks Free Edition workspace created.
- GitHub repository `energy-weather-lakehouse` created and cloned locally.

*(Brief notes only, most of Day 1 predates this log.)*

---

## Day 2 (Wed 2026-09-23)

### Environment setup

- Created a Python virtual environment (`.venv`) and installed `requests`, `pytest`, `ruff`,
  `python-dotenv` from `requirements-dev.txt`.
- Hit a minor Homebrew issue installing the Databricks CLI later in the day: `brew tap
  databricks/tap` refused to load the formula from an "untrusted tap." Resolved with
  `brew trust databricks/tap`.
- Adopted a branch-per-task Git convention (`feat/<task-name>`). The original plan was to merge
  via GitHub Pull Requests so that CI would run against every PR (per the project's CI/CD design).
  In practice, merges were done directly from the terminal (`git merge`) for speed, since the
  GitHub CLI (`gh`) wasn't installed and PRs were adding friction without CI to actually gate
  them yet. **This is a known gap against the original CI/CD plan**, worth reinstating
  PR-based merges once `ci.yml` actually runs checks (Day 4), so CI has something to block on.

### Data sources

- **Fingrid Open Data API** (`data.fingrid.fi`): registered, subscribed to the "Open Data
  starter" product (10,000 requests/day, 1 request per 2 seconds). Found the two dataset IDs
  needed by browsing the dataset catalog:
  - `124` for electricity consumption in Finland, 15-minute resolution
  - `75` for wind power generation, 15-minute resolution (hourly before 2023-06-13)
- **Price data gap**: Fingrid does not publish day-ahead spot price, only balancing-market
  prices (production imbalance price, up/down-regulation bid prices), which answer a different
  question than "what does electricity cost." Added the **ENTSO-E Transparency Platform** as a
  third source: it's the official EU-wide market data platform and covers Finland's day-ahead
  price with multi-year history. Fingrid itself links to it as the authoritative source for
  market data.
  - Registered an ENTSO-E account. The platform's UI has apparently changed recently (a
    "modernised" version rolled out ~Oct 2025), so the documented self-service token generation
    flow ("My Account" then "Generate token") wasn't visible.
  - Found the real current process: email `transparency@entsoe.eu` with subject line
    **"RESTful API access"** and the registered email address in the body; approval takes up to
    3 working days, after which the token-generation option appears.
  - **Sent the access request; price ingestion is blocked on this approval and deferred to a
    later day.**
- **Weather points**: chose 4 representative locations, Helsinki (south), Vaasa (west coast,
  where most of Finland's wind capacity sits), Kuopio (east), Oulu (north), instead of a single
  national average point, to capture both the north/south temperature spread and the
  geographic concentration of wind production. This is a deliberate simplification versus
  Finland's official 19-region division (`maakunta`); noted here for transparency rather than
  presented as a real administrative boundary.

### Ingestion scripts (`ingest/`)

- **`fetch_fingrid.py`**: paginated fetch against `GET /api/datasets/{id}/data`, driven by the
  response's `pagination.nextPage` field.
  - *Bug 1 (rate limiting):* the original rate-limit sleep only ran between pages within a
    single `fetch_dataset()` call. Two back-to-back calls (`fetch_dataset(124, ...)` immediately
    followed by `fetch_dataset(75, ...)`) fired two requests inside one second and hit
    Fingrid's 1-request/2-second limit, returning `429 Too Many Requests`. Fixed by moving the
    `time.sleep()` so it runs after *every* request, not only when continuing to a next page.
  - *Bug 2 (undocumented pagination shape):* on a dataset's **last page**, the API's `pagination`
    object omits the `nextPage` key entirely instead of returning it as `null` (contrary to the
    example in the API docs, which only showed single-page responses). Direct dict indexing
    (`payload["pagination"]["nextPage"]`) raised a `KeyError`; fixed by switching to
    `payload["pagination"].get("nextPage")`, which returns `None` either way. Confirmed via a
    temporary debug print that page 1's `pagination` included `nextPage: 2` while page 2's
    omitted the key completely.
- **`fetch_weather.py`**: Open-Meteo historical archive API (`archive-api.open-meteo.com`), no
  API key required. Reads observation points from `seeds/area_weather_points.csv` via
  `csv.DictReader`. Default unit for wind speed is km/h, not m/s, noted for later unit handling
  in silver/gold.

### Databricks integration

- Linked the GitHub account under Databricks **Settings > Linked accounts > Git integration**,
  then cloned the repo into the workspace as a **Git folder**
  (`Workspace > Create > Git folder`).
- First notebook attempt (`01_bronze.py`) was created by simply `touch`-ing an empty file in the
  repo skeleton on Day 1 and opening it in Databricks. This turned out **not** to behave like a
  real multi-cell notebook (no `+ Code` button, mixed SQL/Python landed in a single cell and
  failed) because the file lacked Databricks's notebook header (`# Databricks notebook
  source`). Fixed by deleting that file and creating a proper notebook via
  **Create > Notebook** instead.

### The network restriction, and what it actually is

This took most of the afternoon and the first conclusion drawn was wrong, so the sequence is
recorded here in full.

1. Calling `fetch_dataset()` directly from inside the notebook failed with
   `ConnectionError: ... NameResolutionError: Failed to resolve 'data.fingrid.fi'`. This is a
   DNS resolution failure, occurring *before* any HTTP request is sent, categorically different
   from an API-level error (like the `429` seen earlier locally, which is a real response *from*
   the server). That distinction is what ruled out a bug in the request itself.
2. Tried Databricks's documented LinkedIn identity verification, which is supposed to unlock
   broader "Outbound internet access." Verified successfully, started a fresh compute session to
   pick up the change, and the call to `data.fingrid.fi` failed identically.
3. **First conclusion, which was wrong:** that Free Edition serverless compute has no general
   outbound internet access at all.
4. Researched the problem properly. The official Free Edition limitations page states that
   "outbound internet access is restricted to a limited set of trusted domains," and a Databricks
   employee confirmed in the community forum that this is intentional and cannot be enabled
   through settings or identity verification. No allowlist is published, and it appears to vary
   by region: `pypi.org` is reachable, `wikipedia.org` reportedly works in EU regions but not US
   ones.
5. That "limited set" wording is what prompted an actual test rather than more reasoning. Probed
   `archive-api.open-meteo.com` from the notebook with the same code shape used for Fingrid, and
   it returned **HTTP 200 with real data**. Re-probed `data.fingrid.fi` two minutes later in the
   same session with an identical probe, and it failed with the same DNS error.

**Conclusion, now based on a controlled experiment rather than inference:** the restriction is a
per-domain allowlist, not a blanket block. Same notebook, same session, same library, same code
shape, two minutes apart, only the hostname differed. Open-Meteo is reachable; Fingrid is not.

The lesson worth keeping: the mechanism was reasoned out correctly (DNS-level domain filtering)
but the prediction drawn from it ("therefore nothing external works") was wrong, and only
testing caught it. The earlier version of this log asserted the wrong conclusion.

**Caveat to carry forward:** because the allowlist is undocumented, Open-Meteo's reachability
could disappear without warning and without any code change. The local fetch path remains as a
fallback for weather if that happens.

### Bronze layer

- Created Unity Catalog schema `workspace.energy_weather` and volume
  `workspace.energy_weather.landing` via SQL cells in the notebook.
- **`ingest/run_local_fetch.py`**: local orchestration script (imports the existing
  `ingest.fetch_fingrid` / `ingest.fetch_weather` functions, no duplicated logic) that fetches
  a full 12 months of data and writes 3 CSV files to `data/bronze_raw/`:
  - Fingrid range ends 2 hours before "now" (safety buffer against requesting data that doesn't
    exist yet).
  - Weather range ends 6 days before "now" (Open-Meteo's archive API lags behind real time by a
    few days).
  - Hit a `ModuleNotFoundError: No module named 'ingest'` running it as `python
    ingest/run_local_fetch.py` directly. Python only adds the *script's own directory* to
    `sys.path`, not the project root, so the script couldn't see its own parent package. Fixed
    by running it as a module from the project root instead: `python -m ingest.run_local_fetch`.
  - Added `data/` to `.gitignore` (raw data isn't versioned, only the code that produces it).
    First attempt broke: `echo "data/" >> .gitignore` appended directly onto the previous line
    (`.DS_Store`) because the file didn't end in a newline, producing the silently-wrong pattern
    `.DS_Storedata/`. Fixed by editing the file directly and splitting it onto its own line.
  - Final run: 35,039 consumption rows, 35,038 wind rows, 35,136 weather rows (4 points x 8,784
    hourly rows each), all saved locally in under 30 seconds.
- Uploaded the 3 CSVs to the Volume via the Catalog Explorer's Upload button, then in the
  notebook read them with `spark.read.csv(..., header=True, inferSchema=True)` and wrote 3
  managed Delta tables:
  - `workspace.energy_weather.bronze_consumption`, 35,039 rows
  - `workspace.energy_weather.bronze_wind`, 35,038 rows
  - `workspace.energy_weather.bronze_weather`, 35,136 rows
- Row counts verified against the local fetch's own printed output, exact match on all three,
  confirming nothing was lost or corrupted across the CSV write, Volume upload and Spark read
  round trip.
- Spot-checked `bronze_consumption` with `SELECT * ... LIMIT 5`, columns and types look correct
  (`datasetId` int, `startTime`/`endTime` timestamps, `value` double).

### Hybrid ingestion, added after the Open-Meteo finding

Once Open-Meteo turned out to be reachable, the weather source was also ingested a second way:
directly from the notebook, with no local step and no Volume upload.

- Read the observation points from the Git folder with `load_weather_points()` against
  `/Workspace/Users/<user>/energy-weather-lakehouse/seeds/area_weather_points.csv`. Workspace
  files are readable with ordinary Python file I/O, so the same function works unchanged in
  both environments.
- Looped the 4 points, tagged every hourly row with `area_id` and `city`, and wrote
  `workspace.energy_weather.bronze_weather_api`.
- Result: 8,784 rows per city, 35,136 total. Identical to the volume-based table.
- Completeness was verified arithmetically rather than by eye: 366 days x 24 hours x 4 points =
  35,136 exactly, which proves no missing hours and no duplicates.

**Resulting architecture:** weather ingests natively inside the platform; Fingrid ingests through
an external layer because the platform cannot reach it. This is not purely a workaround.
Production Spark clusters are frequently network isolated by design, with a dedicated ingestion
or extraction service landing data first, and mixed constraints across sources are normal. Worth
stating in interview as a deliberate decision with a documented reason.

### Schema drift between the two paths

The two weather tables hold the same 35,136 rows but do **not** have the same schema. Two
differences, both real and both worth knowing:

1. **Type of `time`.** `timestamp` via the CSV path, `string` via the API path. The CSV read used
   `inferSchema=True`, so Spark recognised the values as timestamps. The API path builds a
   DataFrame from Python dicts parsed out of JSON, and JSON has no timestamp type, so the value
   arrives as text and Spark infers `string`. Sorting still behaves correctly because ISO 8601
   text sorts identically to chronological order, but that is a coincidence of the format, not a
   property that can be relied on. Any date arithmetic or timezone conversion would fail.
2. **Column order.** The CSV table preserves source order (`time, temperature_2m,
   wind_speed_10m, area_id, city`); the API table is alphabetical (`area_id, city,
   temperature_2m, time, wind_speed_10m`), because `spark.createDataFrame()` sorts dict keys for
   determinism. This matters more than it looks: `union()` matches columns **by position** and
   would silently write timestamps into `area_id`. `unionByName()` matches by name and is the
   correct choice whenever schemas may differ.

Both are left unresolved in bronze on purpose. Bronze accepts data as the source delivers it;
harmonising types and names is silver's job. Fixing it at ingestion would hide the fact that the
two sources genuinely differ.

### Tooling / UI notes (minor, but real time sinks)

- Databricks notebook cell insertion: hovering for the documented "+ Code" hover button didn't
  work reliably in practice; the more reliable path turned out to be running the last cell again
  with Shift+Enter, which auto-creates a new empty cell below it.
- A stray Vercel "new project detected" email and repeated failed-CI-run emails both turned out
  to be noise unrelated to the actual work (Vercel auto-watches new GitHub repos; the CI emails
  were from an empty placeholder `ci.yml` that GitHub tried and failed to execute as a workflow,
  deleted until Day 4, when it will be written for real).
- Red squiggles under `from ingest.fetch_weather import ...` were misdiagnosed twice as an
  unresolvable import caused by `sys.path` differences between edit time and run time. Hovering
  the squiggle showed the actual diagnostic: **ruff F401, "imported but unused"**, with the
  symbol fully resolved including its signature, docstring and source path. The editor could
  resolve the import perfectly; the functions simply weren't called yet. The general rule that
  came out of it: read the tooltip and the rule code before theorising about a linter warning.
  Rule codes are unambiguous, and an unused import and an unresolvable import are entirely
  different diagnostics. `dbutils` is a genuine false positive by contrast, since it is injected
  at runtime and has no source file for the editor to resolve.
- Notebook output is not the same thing as notebook state. After editing a cell, the displayed
  output still belonged to the previous version of the code, and deleted imports stayed live in
  the session's memory until a restart. Anything heavily edited needs a clean run from the top
  before it can be trusted.

### Unused leftovers

- A Databricks secret scope (`fingrid`) was created and populated via the CLI before the network
  restriction was understood. It is now unused: Fingrid is fetched locally with a key from
  `.env`, and the notebook never authenticates against anything. Kept rather than deleted, since
  it costs nothing and documents the attempt.

---

## Day 3 (Thu 2026-09-24)

Bronze completed with the third source, then silver, quality checks, gold and a dimensional
model in one long day.

### ENTSO-E price ingestion

API access was approved overnight, so the third source could finally be built. It differs from
the other two in three ways, all of which had to be handled:

- **The response is XML**, not JSON, so `xml.etree.ElementTree` replaces the JSON parser. Element
  paths use the `{*}` namespace wildcard, because ENTSO-E changes its namespace URI between
  document versions and matching it literally would break on the next schema revision.
- **Rows carry no timestamp.** Each price point has a `position` number, and the time has to be
  reconstructed from the period start plus the resolution. This is the real work in the parser.
- **The token goes in a query parameter**, not a header, which turned out to matter later.

*Bug 1 (sparse points):* the document declares `curveType A03`, "variable sized block", meaning a
price holds until the next point changes it. Unchanged positions are simply omitted, so positions
1, 2, 4 appear with 3 missing. Parsing the points as given would have produced gaps in the series
and a wrong row count. Fixed with a forward fill: iterate every position from 1 to the number of
intervals the period should contain, carrying the last seen price.

*Bug 2 (overlapping chunks):* the request is chunked into 31-day windows to stay inside the API's
per-request limit, but ENTSO-E returns whole market days, so each chunk returned 32 days and
overlapped the next by one. 12 chunks means 11 boundaries, and 11 x 96 quarter-hours is exactly
the 1,056 duplicate rows observed. Deduplicated by timestamp, keeping the first occurrence.

*Trimming:* market days start at 22:00 UTC, so the result spilled outside the requested window by
2 rows at the start (hourly resolution) and 88 at the end (22 hours at quarter-hourly), which is
the 90 rows the trim removed. Final table: **34,134 rows**.

**The dataset captures a market structure change.** Resolutions present are both `PT60M` and
`PT15M`: the European day-ahead market moved from hourly to quarter-hourly settlement in early
October 2025, inside the covered period. 334 hours at the old resolution, 8,450 at the new.

**Daylight saving is visible, and the totals are still clean.** Per-chunk row counts showed one
chunk four rows over and another four rows under, which is one hour more in October and one hour
less in March. Yet the total reconciles exactly. The reason is that **UTC has no daylight saving**:
every UTC day has 24 hours, and the clock changes only appear when the data is sliced by local
market day. This is the concrete justification for the rule the whole project follows: store UTC,
compute in UTC, convert to local time only for presentation.

### The network map, completed

All three source hosts were probed from the notebook with an identical request shape:

| Host | Result |
| --- | --- |
| `archive-api.open-meteo.com` | reachable |
| `data.fingrid.fi` | DNS resolution fails |
| `web-api.tp.entsoe.eu` | DNS resolution fails |

Yesterday's conclusion that "the allowlist is domain-specific" holds, and the picture is now
complete rather than inferred from two data points.

The two weather bronze tables were consolidated: the direct API table replaced the volume-based
one and took its name, via `DROP` plus `ALTER TABLE ... RENAME TO`, run in the SQL editor rather
than the notebook because a one-time migration inside a pipeline would break the next full run.

### Silver layer

Four tables, all conformed to one row per hour keyed on `time_utc`, with `time_local` derived
alongside.

- **Average, not sum.** Consumption and wind production are megawatts, a rate of power. The mean
  of four quarter-hour readings is the hourly average power; summing would overstate it fourfold
  and would only be correct if the unit were megawatt hours. This is a domain decision, not a
  technical one, and it is the kind of thing that silently produces plausible wrong answers.
- **Incomplete hours dropped.** The extraction window does not start or end on an exact hour, so
  the edge hours held fewer than four readings and their averages were computed from a different
  sample size than every other hour. Filtered on a reading count of exactly four.
- **Type repair with a null check.** The weather `time` column arrives as text because it is built
  from JSON. `to_timestamp` fails silently and returns null rather than raising, so an unchecked
  cast can produce a table of 35,136 empty timestamps that only surfaces as an empty join much
  later. The null count is asserted to be zero.
- **Unit conversion.** Open-Meteo reports wind in km/h; converted to m/s, the Finnish convention
  and the unit used when discussing turbine output.
- **Weather deliberately keeps four rows per hour.** Averaging the observation points into a
  national figure would destroy the regional signal that the project exists to measure.

### Quality checks

Defined as a view, `quality_check_results`, rather than as notebook cells, so the same definition
can run from a job or from CI. A Python cell reads the view and raises if any check fails, which
makes it a gate rather than a report: printed output nobody reads does not stop a bad load.

Nine checks across three families: duplicate keys, gaps in the hourly series, and implausible
values.

**The gate fired on its first run, and the check was wrong rather than the data.** Fourteen rows in
`silver_wind` failed the bound `wind_mw >= 0`. Investigation showed small negative values between
-0.3 and -33 MW, clustered in calm periods. They are genuine: turbines draw power for control
systems, heating and yaw motors when production is near zero, so measured output can be slightly
negative, exactly as the day-ahead price can be. The bound was corrected to -500 MW rather than
the data being clamped to zero, which would have looked tidier and falsified the energy balance.

A quality rule written without domain knowledge produces false alarms. This one did, immediately,
and the finding is kept rather than quietly edited away.

**One documented exception.** The gap check excludes `2026-06-04 13:00` UTC by exact timestamp with
the reason in a comment, because Fingrid is missing the 12:15 reading that day. A check that is
permanently red trains the reader to ignore it; naming the known exception keeps everything else
meaningful. Note that the excluded timestamp is the hour *after* the gap, because `LAG` flags the
row that finds its predecessor too far back.

### Gold, wide table

`gold_hourly`, one row per hour with weather pivoted into per-location columns, national means,
and the three measures. Inner joins trim the result to the window all four sources cover,
`2025-09-23 15:00` to `2026-09-17 23:00`, without any hand-written date bounds.

**8,624 rows, where the window implies 8,625.** The missing hour was traced end to end: `bronze_wind`
held one reading fewer than `bronze_consumption`, which made one hour incomplete, which silver
dropped, which the inner join then removed from all four sources. A `LAG` window function over
`silver_wind` located it at `2026-06-04 12:00` UTC, and a direct query against bronze confirmed
that hour holds three quarter-hours instead of four.

The pipeline did the right thing without being asked: it dropped the hour rather than averaging
three readings and presenting the result as equal to the other 8,623.

### Dimensional model

A star schema built alongside the wide table, because they serve different readers.

**Two facts, because the grain differs.** `fact_power_hour` is one row per hour; `fact_weather_hour`
is one row per hour per area. Merging them would repeat every national figure four times. When a
question needs both, the finer fact is aggregated to the coarser grain first, in a CTE. Joining
two facts of different grain directly is the classic error known as a fan trap.

**Three dimensions.** `dim_date` keyed on a `yyyyMMdd` integer and derived from *local* time,
because an analyst asking about Monday means the Finnish Monday. `dim_area` as SCD1, because
nothing in it changes historically. `dim_wind_capacity` as SCD2, because installed capacity
genuinely does.

**The capacity figures are real, and nearly were not.** The plan called for a simulated capacity
dimension. Before writing it that way, it was worth checking whether the data actually exists, and
it does: ENTSO-E publishes installed generation capacity as documentType A68, annually per
production type and bidding zone. Finland onshore wind: **8,224 MW in 2025 and 9,330 MW in 2026**.
Fetched by `ingest/fetch_capacity.py`, which is in the repository so the figures can be re-derived
rather than trusted.

Per-area capacity is genuinely unavailable, but that turned out not to matter, because the original
plan had it in the wrong place: capacity is a national quantity and belongs with the national fact.
Attaching it there also makes the **capacity factor** computable, which is the metric that actually
matters for wind and which normalises out the capacity growth inside the series.

**Point-in-time join.** The fact joins the capacity dimension on a range rather than on equality,
so each hour is matched to the version in force at that moment. Joining on `is_current` instead
would have applied 2026 capacity to 2025 hours and understated every early capacity factor. SCD2
without a point-in-time join does nothing useful; the two belong together.

Validated on both failure modes: 8,624 rows means the range join multiplied nothing, and zero
unmatched rows means the validity periods cover the series without gaps.

### Findings

**Temperature explains consumption.** 14,525 MW below -20 C against 8,515 MW between +10 and +20,
roughly 70 percent more in the cold.

**Two apparent effects turned out to be confounded, and both were tested rather than assumed.**
Consumption appeared to rise again above +20 C; holding the hour of day constant at 13:00 removed
the reversal, because those hours are summer afternoons compared against spring and autumn nights.
Consumption also appeared to rise with wind among mild hours, which is not causal: "mild" covers
everything above zero, and windy hours are autumn storms sitting at the cold end of that band.

**Vaasa's wind speed explains national production best**, correlation 0.761 against capacity factor,
followed by Oulu 0.726, Kuopio 0.634 and Helsinki 0.523. The ranking reconstructs the geography of
Finnish wind capacity from the data alone: concentrated on the west coast from Vaasa northward.
Nothing in the model was told where the turbines are. This validates the silver decision not to
average the observation points.

**Cold and calm together is the expensive case.** A cold calm hour averages 14.73 cents per kWh
against 0.91 for a warm windy one, a sixteenfold difference. Holding temperature at cold and
varying only wind, the price falls 73 percent while consumption barely moves, which isolates the
supply-side effect. Cold calm hours are 885, roughly 10 percent of the year, so this is a
recurring condition rather than a rare extreme.

**The first version of this table used two temperature bands and hid a real effect.** "Mild" was
everything above zero, which put a 5 C autumn evening and a 25 C summer afternoon in the same
bucket despite prices differing almost twofold. Splitting warm out at 15 C produced a nine-cell
table that is monotonic in both directions with no exceptions, and moved the headline figure from
fourteenfold to sixteenfold.

The threshold is 15 C rather than 20 C because only 175 hours in the whole series exceed 20 C, and
split three ways by wind those cells would be too thin to report.

**On averaging.** Summed per city there are 492 hours below -20 C; the national average produces
47. Averaging four points discards roughly 90 percent of locally extreme cold, because one mild
city cancels another's extreme.

### Monthly seasonality, and a natural experiment

Aggregating the power fact through `dim_date` gave a monthly series, and it contains a cleaner
demonstration of the thesis than any of the bucketed tables.

February 2026 and March 2026 had similar demand, 13,032 and 11,056 MW, and opposite wind, capacity
factors of 0.19 and 0.42. Prices were 13.72 and 2.78 cents per kWh. Demand differs by 18 percent,
wind by 118 percent, price by 393 percent. Two adjacent winter months, no bucketing, same result.

July is the cheapest month at 1.54 cents **despite having the year's lowest capacity factor**,
0.153, because demand is also at its floor. Price follows the ratio of demand to supply rather
than either one alone, which is the most precise statement this dataset supports.

**And the hour counts verified the timezone design.** Grouped by local calendar month, October 2025
has 745 hours and March 2026 has 743, against 744 for a normal 31-day month. The daylight saving
transitions land exactly where they should. This only works because `dim_date` is derived from
local time; a UTC-derived date dimension would report 744 for both, because UTC has no daylight
saving, and the transitions would be invisible.

That was not designed as a test. It fell out of a routine `COUNT(*)` column added to sanity-check
partial months, which is a reasonable argument for always carrying the row count.

### Dashboard

An AI/BI dashboard over the star schema: four counters and four charts. Built after the notebooks
so the queries were already written and verified.

Several things were wrong on the first pass and were caught by reading the output rather than by
knowing the rules in advance:

- **Stacked bars where grouped were meant.** Adding a colour split stacked cold and mild prices
  into a single bar of 21.4 cents, a number nobody pays. Stacking is correct when the parts sum to
  something meaningful; these are alternatives, not components.
- **A title that claimed a trend the chart did not show.** "Consumption rises 70 percent in the
  cold" sat above bars in alphabetical order, which looked random. The figure was right and the
  axis order was wrong. A title that contradicts its own picture costs more credibility than no
  title at all.
- **Sort prefixes leaking into the display.** Labels read "1 Calm", "2 Moderate", which exposed a
  SQL ordering workaround to the reader. Removed, with custom sort used where alphabetical order
  does not match the natural one.
- **Green and red as adjacent categories**, the most common accessibility failure. Replaced with a
  diverging blue to grey to orange scale, which also encodes the ordering of temperature rather
  than treating the bands as unrelated categories.
- **A title promising interactivity the dashboard does not have.** "Hour by hour" with no filters.
  The root cause is that the datasets aggregate in SQL, so the hour column no longer exists by the
  time the dashboard sees the data. Pre-aggregating trades interactivity for simplicity, which is
  a real design decision rather than an oversight, but the title should not have implied otherwise.

**The dashboard cannot be shared publicly.** Free Edition offers only "people with access" or
"anyone in my account", and the account has one user. Tested by opening the copied link in a
private window: it shows a login wall. A third Free Edition limitation, alongside the network
restriction and the absence of cluster configuration.

### A security incident, and what caused it

**The ENTSO-E API token was exposed in a screenshot** shared while debugging a 400 error. ENTSO-E
takes the token as a query parameter, so it appears in full inside every error message, log line
and stack trace. The token was revoked and regenerated immediately.

Impact was low: the token grants read access to public market data with no billing attached. The
lesson is structural rather than about carelessness. An API that puts credentials in the URL leaks
them into places that are easy to share by accident, which is exactly why Fingrid and most modern
APIs use a header instead. When an API forces the token into the query string, screenshots and log
output need handling with specific care.

### Unused leftovers

Both Databricks secret scopes, `fingrid` and `entsoe`, are now unused: neither source is reachable
from Databricks, so no notebook authenticates against anything. An earlier note here argued they
were worth keeping because they "document the attempt". That reasoning was weak. This file
documents the attempt; a secret store holding a revoked credential documents nothing. Both should
be deleted.

---

## Day 4 (Sat 2026-09-26)

Day 4 was meant to be Friday. It was not used, so this day carried orchestration, incremental
loading and scheduling together, two days before the interview this project was built for.

### Orchestration, moved up the list

The plan had a Databricks job sitting at position seven. That ordering was wrong, and the argument
that moved it was simple: without a job, **the quality gate was decoration**. `03_quality_checks`
raised an exception, but nothing ran it automatically and its failure blocked nothing. Only inside
a dependency chain does a failing gate stop `04_gold` from being rebuilt on bad data.

Four tasks, `bronze -> silver -> quality_checks -> gold`, each depending on the previous one. A
task whose upstream fails is reported as **Upstream failed** rather than failed, which matters when
reading a run: it separates what broke from what merely waited.

### Retry policy, and a default that lies

The retry setting was made per task rather than accepted as a default, on one principle: **a retry
only helps a transient failure.** `01_bronze` calls Open-Meteo over the network, so a timeout or a
503 is worth retrying, with a delay rather than immediately, because an instant retry makes
throttling worse. `02_silver`, `03_quality_checks` and `04_gold` touch no network; a failure there
is deterministic and four attempts produce four identical failures while consuming four times the
quota. Free Edition shuts the workspace down for the rest of the day when quota is exceeded, so
this is not a theoretical concern.

The quality gate in particular must not retry. A failure there means the data is bad; repeating the
run blurs the signal it was built to produce.

Setting "2 retries" on the bronze task produced a task showing **"at most 3x"**. The dialog itself
explains why: "Enable serverless auto-optimization (may include at most 3 retries)" was checked,
and its own text says that disabling all retries requires turning that off. A setting of zero is
not zero until both are set. Worth reading configuration rather than filling it in.

### A table nothing could rebuild

Looking for the weather table's natural key surfaced something larger. Two bronze weather tables
existed, `bronze_weather` and `bronze_weather_api`, identical in row count and range. `02_silver`
read the first. Nothing in `01_bronze` wrote it: one cell read from it, another wrote to the second
table, and the cell that had originally created it belonged to a version of the notebook that no
longer existed.

So the pipeline could not build itself from empty. The job had just passed, and passed only because
the table persisted between runs. Dropping it would have broken silver immediately.

This is the failure mode orchestration hides rather than reveals, because state carries over
between runs. The only honest test is to drop the target tables and run from nothing, which is
exactly what happened next, by necessity rather than by design.

### Incremental weather load

The fix and the next planned feature turned out to be the same work. The API path became the only
weather path, because it is the only one Databricks can refresh by itself, and it was rewritten to
load incrementally.

**The watermark is read from the target table**, not from a state file, because a value derived
from the table cannot disagree with the table, including after a run that died halfway. That is
what makes the load idempotent.

**The window starts three days behind the watermark**, so values the source revised after
publication are re-fetched. Loading strictly forward from the watermark would mean an already
loaded hour could never be corrected.

**The window ends six days back**, because ERA5 publishes daily with a five day delay. The gap in
the data was not a bug; it was the source's publication lag, and a pipeline that asks for yesterday
gets an empty answer and looks broken.

**And this is what forces `MERGE`.** With a fixed window, `mode("overwrite")` is correct, because
every run produces the whole set. The moment the window moves, overwrite would delete the year and
leave four days. Incremental loading and `MERGE` are not two tasks; they are one task in two parts.
That connection had not been stated plainly before and should have been.

The merge key is `(time, area_id)`. `time` alone matches four rows, one per observation point, and
Delta refuses that with "multiple source rows matched the same target row" rather than guessing.
The more dangerous error is the opposite: a key too specific matches nothing and inserts duplicates
on every run, silently.

Proof, from `DESCRIBE HISTORY`:

| version | operation | source rows | updated | inserted | deleted |
| --- | --- | --- | --- | --- | --- |
| 1 | MERGE | 384 | 384 | 0 | 0 |
| 0 | CREATE TABLE AS SELECT | | | | |

384 rows rewritten in place, nothing appended, table unchanged at 35,424 rows.

### A rate limit, and what backoff cannot do

The initial load failed with HTTP 429. Open-Meteo weights requests by date range: two weeks counts
as one call, four weeks as three. A year for four locations is hundreds of calls, not four.

Exponential backoff was added to `fetch_weather`, waiting 10, 20, 40 and 80 seconds. It failed all
five attempts. **Backoff is correct for a transient burst limit and useless against an hourly or
daily quota**, which does not clear in minutes. The code was right and the diagnosis was wrong.

The backoff stayed, because it is the right behaviour for the failure it addresses, and because
every later run asks for three days rather than a year. The load succeeded two days later without
any change, which is the plainest possible confirmation of what the limit actually was.

Two defects in that first version, both visible in its own output. It printed "attempt 6/5",
an off-by-one in the message. Worse, it slept 160 seconds on the final attempt and then gave up:
the wait should only happen when another attempt follows. Ninety seconds of a rate-limited evening
spent waiting for nothing.

### A result that was not a result

The second run of the load reported `Initial load written.` with an unchanged row count. Both
numbers looked right, and the branch was wrong: that message only prints when the table does not
exist, yet the previous cell had read a watermark from it. Two claims that could not both be true.

`DESCRIBE HISTORY` settled it in one query: version 0 only, no `MERGE`. The cell had not been
re-run and its previous output was still on screen. **A notebook shows the last output, not the
current state**, which is exactly the trap a job avoids by keeping each run's output with that run.

The habit worth keeping: when a printed result and a claimed state disagree, ask the store, not the
screen.

### Scheduling, across two environments

Neither Fingrid nor ENTSO-E is reachable from Databricks, so a daily refresh cannot live inside it.
The design that follows is a GitHub Actions workflow that fetches, uploads and triggers.

Before writing any of it, two cheap tests settled whether it was possible at all: Free Edition does
issue personal access tokens, and `databricks fs cp` writes to a Unity Catalog volume from outside
the workspace. Five minutes of testing in place of an hour of speculation.

`run_local_fetch.py` already fetched Fingrid on a rolling twelve-month window. Weather was removed
from it, since the notebook now loads it directly, and the day-ahead price was added. The window
became `FETCH_DAYS`, defaulting to 365, so a smoke test can run seven days without pulling a year
through the APIs.

Because the CSV always holds a full rolling year, `mode("overwrite")` in bronze remains correct for
those two sources. They are not incremental, and saying so is more useful than pretending.

The workflow does three things in a deliberate order: fetch, upload, trigger. A failed fetch
uploads nothing and starts nothing, so the volume keeps yesterday's files and the tables keep their
last good state.

Three things about GitHub Actions scheduling that are easy to discover the hard way: scheduled runs
queue and can be late by tens of minutes, which is why the cron is at minute 17 rather than on the
hour; scheduled workflows are disabled after 60 days of repository inactivity; and `workflow_dispatch`
only appears once the file is on the default branch, so "test it on a branch first" does not work
and the advice given earlier here was wrong.

First manual run: green in 3m23s, 35,039 and 35,038 Fingrid rows, 34,686 price rows from twelve
chunks after deduplication. The job it triggered finished green in 2m20s, down from 3m55s, because
the weather load had nothing left to fetch. Incremental loading visible in the wall clock.

The secrets were checked rather than assumed: `securityToken` does not appear anywhere in the log,
because a successful run prints only row counts. The URL carrying it reaches the log only on an
error, which is how it leaked into a screenshot two days earlier.

### A claim made without checking

Early in the day this log's author asserted that the previous evening's work was not in version
control, based on `git branch -a` in a local clone that had not fetched. The branch was on GitHub,
pushed at 22:55 with CI green. Remote-tracking branches show the last fetch, not the remote. Fetch
first, then claim.

### Where this leaves the pipeline

Running daily, end to end, with a quality gate that can stop it and one source loading
incrementally. The gaps are known and listed below rather than hidden.

---

## Open items

- **Extend `MERGE` to Fingrid and the price.** Both are re-fetched in full for twelve months and
  written with `overwrite`. Correct at 35,000 rows, wrong in principle. The pattern exists in
  `01_bronze` for weather; applying it needs a trailing re-merge window for the two Fingrid series,
  because measured data is revised as settlement completes, and none for the price, because an
  auction clearing price is final once published.
- **Notify on failure.** `databricks jobs run-now` returns immediately, so the workflow is green
  regardless of what the job does afterwards. Either poll the run and propagate its status, or
  configure job notifications. Currently a failed nightly run would go unnoticed.
- **Put the job in version control** as a Databricks Asset Bundle. It exists only in the workspace
  UI. Related: the job points at a workspace path, not a git ref, so switching the Git folder's
  branch changes what the scheduled job runs without touching the job.
- **Replace the personal access token with a service principal.** The current token expires
  2026-10-07 and is tied to one person, so the pipeline stops on that date.
- **Delete the two unused secret scopes** `fingrid` and `entsoe`, the unused `weather.csv` in the
  volume, and the stale feature branches.
- **PR-based merge workflow**, still doing direct `git merge` even though CI now exists to gate on.

### Dashboard, planned additions

- **Hourly-grain datasets and filters.** The current datasets aggregate in SQL, which is why no
  filter is possible. Feeding the dashboard hourly rows and letting it aggregate would make month,
  season and hour-of-day filterable. With an hour-of-day filter the reader could watch the warm
  weather anomaly disappear rather than reading about it in a caption.
- **Heatmap, month by hour of day.** The daily rhythm is entirely absent from the current view and
  `hour_of_day` is already in the fact table.
- **Box plot per month**, replacing the mean line. Means hide the spread, and February's whiskers
  carry more than its average does.
- **Bubble scatter**, temperature against wind speed with price as colour. The thesis in one
  picture, at hourly resolution, without bucketing. Needs transparency or binning at 8,624 points.
- **Price histogram**, to show the skew: many cheap hours and a thin expensive tail.

Considered and rejected: a **dual-axis chart** for a single crisis week. The idea is good but the
form invents correlation, because the alignment of two y-scales is arbitrary. Small multiples
sharing one x-axis carry the same information honestly. Also rejected: a **map** of the four
observation points, whose proposed encoding needs per-area wind capacity that does not exist for
this simplified four-point division, and a **gauge**, which Databricks does not support and which
a counter does better anyway.
