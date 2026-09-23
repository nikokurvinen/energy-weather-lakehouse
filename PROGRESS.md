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

## Open items going into Day 3

- Consolidate the two weather bronze tables into one canonical source. The direct API path is the
  better default (no manual upload, schedulable as a job), with the local path documented as a
  fallback if Open-Meteo ever stops resolving.
- ENTSO-E price ingestion, blocked on API access approval (requested, ~3 business day SLA).
- `ci.yml`, deleted placeholder, not yet built (planned Day 4: ruff + pytest on every PR).
- Silver layer: harmonise the `time` type and column order, add a local-time column alongside
  UTC (Finland is UTC+3 in summer, UTC+2 in winter), align the 15-minute Fingrid grain with the
  hourly weather grain, handle the 23/24/25-hour DST days, and add SQL data-quality checks.
- PR-based merge workflow, currently doing direct `git merge`; revisit once CI exists so PRs
  have something to gate on.
