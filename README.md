# VAHAN Data

Scrape India's national vehicle registration database ([VAHAN Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/)) and serve it as a Model Context Protocol (MCP) server — queryable by Claude and any MCP-compatible client.

**Data coverage**
- 35 states / union territories
- 1,551 Regional Transport Offices (RTOs)
- Year-wise registrations, transactions, revenue, and permits (All India)
- Vehicle class × fuel type breakdown (273,852 rows)
- Vehicle class × emission norms breakdown (205,801 rows)
- Maker/brand-wise registration counts (~1,457 manufacturers per year)
- Historical data from 1970 to present

---

## Table of Contents

1. [Setup](#1-setup)
2. [Scraping](#2-scraping)
   - [scraper.py — dashboard metrics](#scraperpy--dashboard-metrics)
   - [scrape_vehicle_types.py — fuel & norms](#scrape_vehicle_typespy--fuel--norms)
   - [scrape_makers.py — brand registrations](#scrape_makerspy--brand-registrations)
   - [scrape_crosstab.py — generic tabular combinations](#scrape_crosstabpy--generic-tabular-combinations)
3. [MCP Server](#3-mcp-server)
   - [stdio transport (local)](#stdio-transport-local)
   - [HTTP transport (web)](#http-transport-web)
4. [Hosting with Cloudflared](#4-hosting-with-cloudflared)
   - [Quick tunnel (no account)](#quick-tunnel-no-account)
   - [Named tunnel (stable URL)](#named-tunnel-stable-url)
5. [Connecting Claude Desktop](#5-connecting-claude-desktop)
6. [MCP Tools Reference](#6-mcp-tools-reference)
7. [MCP Resources Reference](#7-mcp-resources-reference)
8. [Database Schema](#8-database-schema)
9. [Output Files](#9-output-files)
10. [State Codes](#10-state-codes)
11. [Data Caveats](#11-data-caveats)

---

## 1. Setup

**Requirements:** Python 3.11+, Node.js (for `mcp-remote` / Claude Desktop), Homebrew (macOS, for cloudflared)

```bash
# Clone / enter the project directory
cd vahandata

# Create virtual environment
python3 -m venv .venv

# Install Python dependencies
.venv/bin/pip install playwright>=1.44.0 pandas>=2.2.0 openpyxl mcp uvicorn starlette

# Install Playwright browsers (needed for scraping only)
.venv/bin/playwright install chromium
```

---

## 2. Scraping

Run the scrapers before starting the MCP server. Scraped data is written to `data/` as CSV files, which the server ingests automatically into `db/vahan.db` on first start.

### `scraper.py` — dashboard metrics

Scrapes year-wise registrations, transactions, revenue, permits, and the RTO list.

```bash
# Quick run — state-level data only (~5 min)
.venv/bin/python3 scraper.py --skip-rto

# Full run — includes per-RTO year-wise data (~30–60 min)
.venv/bin/python3 scraper.py
```

| Flag | Description |
|---|---|
| `--skip-rto` | Skip per-RTO scraping (~1,400 AJAX calls). Produces all state-level CSVs only. |

**Output files:**

| File | Contents |
|---|---|
| `data/registrations.csv` | Year-wise registrations per state |
| `data/transactions.csv` | Year-wise transactions per state |
| `data/revenue.csv` | Year-wise revenue per state |
| `data/permits.csv` | Year-wise permits per state |
| `data/all_metrics.csv` | All 4 metrics combined |
| `data/states.csv` | 35 state codes and names |
| `data/rto_list.csv` | 1,551 RTOs with state codes |
| `data/summary_stats.csv` | Top-level VAHAN dashboard stats |
| `data/rto_metrics.csv` | Per-RTO year-wise data *(full run only)* |

---

### `scrape_vehicle_types.py` — fuel & norms

Scrapes vehicle class breakdowns by fuel type and emission norms across all states.

```bash
# Full scrape — all states, fuel + norms (~25 min)
.venv/bin/python3 scrape_vehicle_types.py

# All India totals only — faster (~10 min)
.venv/bin/python3 scrape_vehicle_types.py --all-india-only

# Skip individual sections
.venv/bin/python3 scrape_vehicle_types.py --skip-fuel
.venv/bin/python3 scrape_vehicle_types.py --skip-norms
.venv/bin/python3 scrape_vehicle_types.py --skip-year
```

| Flag | Description |
|---|---|
| `--all-india-only` | Only scrape All India totals, skipping all 35 individual states. Faster. |
| `--skip-fuel` | Skip the vehicle class × fuel type breakdown. |
| `--skip-norms` | Skip the vehicle class × emission norms breakdown. |
| `--skip-year` | Skip the vehicle class × year breakdown. |

**Output files:**

| File | Contents |
|---|---|
| `data/vehicle_class_by_fuel.csv` | Vehicle class × fuel type counts per state (273,852 rows) |
| `data/vehicle_class_by_norms.csv` | Vehicle class × emission norm counts per state (205,801 rows) |
| `data/vehicle_class_by_year.csv` | Vehicle class × year counts per state |

---

### `scrape_makers.py` — brand registrations

Scrapes maker/manufacturer-wise vehicle registration counts via XLSX download from the dashboard.

```bash
# All India totals, default years (2023–2025)
.venv/bin/python3 scrape_makers.py --all-india-only

# Specific years
.venv/bin/python3 scrape_makers.py --all-india-only --years 2025 2024

# Full run — all 37 states × specified years
.venv/bin/python3 scrape_makers.py --years 2025 2024 2023
```

| Flag | Description |
|---|---|
| `--all-india-only` | Only scrape All India totals, skipping all 35 individual states. Much faster. |
| `--years` | Space-separated list of years to scrape (default: `2025 2024 2023`). |

**Output files:**

| File | Contents |
|---|---|
| `data/maker_registrations.csv` | Maker × year registration counts (~1,457 makers per year) |

**Sample data (top 10 makers, 2025 All India):**

| Maker | Registrations |
|---|---|
| HERO MOTOCORP LTD | 492,963 |
| HONDA MOTORCYCLE AND SCOOTER INDIA | 473,900 |
| TVS MOTOR COMPANY LTD | 372,751 |
| BAJAJ AUTO LTD | 241,125 |
| MARUTI SUZUKI INDIA LTD | 223,289 |
| ROYAL-ENFIELD (UNIT OF EICHER LTD) | 107,641 |
| SUZUKI MOTORCYCLE INDIA PVT LTD | 99,286 |
| MAHINDRA & MAHINDRA LIMITED | 91,361 |
| HYUNDAI MOTOR INDIA LTD | 66,781 |
| INDIA YAMAHA MOTOR PVT LTD | 64,549 |

---

### `scrape_crosstab.py` — generic tabular combinations

A generalized scraper that can download any Y-axis × X-axis combination from the VAHAN dashboard's Tabular Summary view.

```bash
# Maker × Fuel (All India, years 2024-2025)
.venv/bin/python3 scrape_crosstab.py --yaxis Maker --xaxis Fuel --all-india-only --years 2025 2024

# Fuel × Calendar Year (all states)
.venv/bin/python3 scrape_crosstab.py --yaxis Fuel --xaxis "Calendar Year" --years 2025

# State × Fuel
.venv/bin/python3 scrape_crosstab.py --yaxis State --xaxis Fuel --all-india-only --years 2025

# Maker × Norms across 3 years
.venv/bin/python3 scrape_crosstab.py --yaxis Maker --xaxis Norms --years 2025 2024 2023
```

| Flag | Description |
|---|---|
| `--yaxis` | **Required.** Y-axis (rows). Valid options: `Vehicle Category`, `Vehicle Class`, `Norms`, `Fuel`, `Maker`, `State`. |
| `--xaxis` | **Required.** X-axis (columns). Valid options: `Vehicle Category`, `Norms`, `Fuel`, `Vehicle Category Group`, `Financial Year`, `Calendar Year`, `Month Wise`. |
| `--all-india-only` | Only scrape All India totals, skipping individual states. |
| `--years` | Space-separated list of years to scrape (default: `2025 2024 2023`). |

**Output files:**

The script generates a CSV named after the provided Y-axis and X-axis.

| File | Contents |
|---|---|
| `data/<yaxis>_by_<xaxis>.csv` | Cross-tabbed counts (e.g. `data/maker_by_fuel.csv`) |

---

## 3. MCP Server

`mcp_server.py` reads the scraped CSVs, builds a SQLite database (`db/vahan.db`) on first run, and exposes the data as MCP tools and resources.

```bash
.venv/bin/python3 mcp_server.py [--transport {stdio|http}] [--host HOST] [--port PORT]
```

| Flag | Default | Description |
|---|---|---|
| `--transport` | `stdio` | Transport to use. `stdio` for local Claude Desktop use; `http` for web hosting. |
| `--host` | `0.0.0.0` | Host to bind to (HTTP transport only). Use `127.0.0.1` when behind a reverse proxy. |
| `--port` | `8000` | Port to listen on (HTTP transport only). |

### stdio transport (local)

Default mode — launched by Claude Desktop directly over stdin/stdout. No network port opened.

```bash
.venv/bin/python3 mcp_server.py
# or explicitly:
.venv/bin/python3 mcp_server.py --transport stdio
```

### HTTP transport (web)

Runs a Streamable HTTP server. MCP endpoint: `http://<host>:<port>/mcp`

```bash
# Bind to all interfaces (public VPS)
.venv/bin/python3 mcp_server.py --transport http

# Bind to localhost only (behind a reverse proxy or cloudflared)
.venv/bin/python3 mcp_server.py --transport http --host 127.0.0.1

# Custom port
.venv/bin/python3 mcp_server.py --transport http --host 127.0.0.1 --port 9000
```

---

## 4. Hosting with Cloudflared

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) exposes the local HTTP server to the public internet without opening firewall ports.

**Install cloudflared (macOS):**
```bash
brew install cloudflared
```

The convenience script `start.sh` starts both the MCP server and the tunnel together.

### Quick tunnel (no account)

No login required. Gives a random `*.trycloudflare.com` URL — valid until the process stops.

```bash
./start.sh
```

Example output:
```
Starting VAHAN MCP server on http://127.0.0.1:8000/mcp ...
MCP server running (PID 12345)
Starting quick tunnel (no login required) ...

+-----------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at:                                 |
|  https://male-steve-surgeons-airline.trycloudflare.com                            |
+-----------------------------------------------------------------------------------+
```

> The URL changes every time. For a stable URL use a named tunnel.

### Named tunnel (stable URL)

One-time setup (requires a [Cloudflare account](https://dash.cloudflare.com/sign-up)):

```bash
# 1. Log in (opens browser)
cloudflared tunnel login

# 2. Create the tunnel (run once — saves credentials to ~/.cloudflared/)
cloudflared tunnel create vahan

# 3. Optional: route a custom domain
cloudflared tunnel route dns vahan mcp.yourdomain.com
```

Then start with:
```bash
./start.sh --named vahan
```

`start.sh` auto-generates `~/.cloudflared/vahan.yml` on first run:
```yaml
tunnel: vahan
credentials-file: ~/.cloudflared/vahan.json

ingress:
  - service: http://localhost:8000
```

| `start.sh` flag | Description |
|---|---|
| *(none)* | Quick tunnel — random `trycloudflare.com` URL, no login needed. |
| `--named <name>` | Named tunnel — stable URL tied to your Cloudflare account. Requires prior `cloudflared tunnel login` + `create`. |

---

## 5. Connecting Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`.

### Option A — stdio (local, no internet)

```json
{
  "mcpServers": {
    "vahan": {
      "command": "/path/to/vahandata/.venv/bin/python3",
      "args": ["/path/to/vahandata/mcp_server.py"]
    }
  }
}
```

### Option B — remote HTTP via mcp-remote

```json
{
  "mcpServers": {
    "vahan": {
      "command": "npx",
      "args": ["mcp-remote", "https://your-tunnel-url.trycloudflare.com/mcp"]
    }
  }
}
```

> `mcp-remote` is a client-side bridge that lets Claude Desktop (stdio-only) connect to remote Streamable HTTP MCP servers. It requires Node.js / npx.

Restart Claude Desktop after editing the config. The `vahan` tools will appear in the tool picker.

---

## 6. MCP Tools Reference

### `get_registrations`

Get year-wise All India vehicle counts for a metric.

> **Note:** The VAHAN dashboard only exposes All India totals in its year-wise tables. All state codes hold identical All India values. Use `state_code="-1"` for the canonical row.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `metric` | string | `"registrations"` | One of `registrations`, `transactions`, `revenue`, `permits` |
| `state_code` | string | *(all)* | Use `"-1"` for All India. Other codes also return All India values. |
| `year` | string | *(all years)* | Calendar year e.g. `"2025:"`, `"2024:"`, or `"Till Today"` for all-time total |
| `limit` | integer | `100` | Maximum rows returned |

**Example:**
```
get_registrations(metric="registrations", state_code="-1", year="2025:")
```

---

### `get_vehicle_class_by_fuel`

Get vehicle class registration counts broken down by fuel type for a state.

Returns one row per `(vehicle_class, fuel_type)` combination, sorted by count descending.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `state_code` | string | Yes | — | State code e.g. `"MH"`, `"DL"`. Use `"-1"` for All India. |
| `fuel_type` | string | No | *(all)* | Filter to one fuel type e.g. `"PETROL"`, `"DIESEL"`, `"PURE EV"`, `"CNG ONLY"`. See `vahan://fuel-types` resource for full list. |
| `limit` | integer | No | `200` | Maximum rows returned |

**Example:**
```
get_vehicle_class_by_fuel(state_code="MH", fuel_type="PURE EV")
```

---

### `get_vehicle_class_by_norms`

Get vehicle class registration counts broken down by emission norm for a state.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `state_code` | string | Yes | — | State code e.g. `"MH"`. Use `"-1"` for All India. |
| `norm` | string | No | *(all)* | Filter to one norm e.g. `"BS VI"`, `"BS IV"`, `"BS III"`. Note: use spaces not hyphens. See `vahan://emission-norms` for full list. |
| `limit` | integer | No | `200` | Maximum rows returned |

**Example:**
```
get_vehicle_class_by_norms(state_code="-1", norm="BS VI")
```

---

### `get_yearly_trend`

Get the All India year-wise trend for a metric with growth percentages.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `metric` | string | `"registrations"` | One of `registrations`, `transactions`, `revenue`, `permits` |
| `limit` | integer | `20` | Number of years to return (most recent first) |

**Example:**
```
get_yearly_trend(metric="registrations", limit=10)
```

---

### `get_ev_breakdown`

Get electric vehicle (EV) registration breakdown.

Covers four EV fuel types: `PURE EV`, `PLUG-IN HYBRID EV`, `STRONG HYBRID EV`, `ELECTRIC(BOV)`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state_code` | string | *(all states)* | Filter to one state e.g. `"KA"`. Omit for all India. |
| `group_by` | string | `"state"` | Dimension to aggregate by. One of `state`, `vehicle_class`, `fuel_type`. |
| `limit` | integer | `50` | Maximum rows returned |

**Examples:**
```
# EV registrations ranked by state
get_ev_breakdown(group_by="state", limit=36)

# EV breakdown by vehicle class in Karnataka
get_ev_breakdown(state_code="KA", group_by="vehicle_class")

# EV sub-type breakdown (pure EV vs hybrid etc.)
get_ev_breakdown(group_by="fuel_type")
```

---

### `search_rtos`

Look up Regional Transport Offices (RTOs) by state or name.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state_code` | string | *(all states)* | Filter by state code e.g. `"MH"` |
| `name_contains` | string | *(no filter)* | Case-insensitive substring match on RTO name e.g. `"mumbai"` |
| `limit` | integer | `50` | Maximum rows returned |

**Examples:**
```
search_rtos(state_code="MH")
search_rtos(name_contains="bangalore")
search_rtos(state_code="DL", limit=100)
```

---

### `get_top_makers`

Get top vehicle manufacturers/brands ranked by registration count.

Data covers ~1,457 makers across all states and years 2024–2026.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `state_code` | string | Yes | — | State code e.g. `"MH"`. Use `"-1"` for All India. |
| `year` | string | No | *(all years)* | Year e.g. `"2025"`. Omit for all years. |
| `limit` | integer | No | `20` | Number of top makers to return |

**Examples:**
```
# Top 10 brands in India for 2025
get_top_makers(state_code="-1", year="2025", limit=10)

# Top makers in Maharashtra
get_top_makers(state_code="MH", year="2025")
```

---

### `search_makers`

Search vehicle manufacturers/brands by name substring.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `name_contains` | string | Yes | — | Case-insensitive substring e.g. `"tata"`, `"hero"`, `"suzuki"` |
| `state_code` | string | No | *(all states)* | Filter by state code |
| `year` | string | No | *(all years)* | Filter by year e.g. `"2025"` |
| `limit` | integer | No | `50` | Maximum rows returned |

**Examples:**
```
# Find all Tata brands
search_makers(name_contains="tata")

# Search Hero brands in All India 2025
search_makers(name_contains="hero", state_code="-1", year="2025")
```

---

### `run_sql`

Run an arbitrary read-only `SELECT` query directly against the SQLite database.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | Yes | — | SQL `SELECT` statement. Only `SELECT` is permitted; write operations are blocked. |
| `limit` | integer | No | `500` | Maximum rows returned |

**Important query notes:**
- `yearly_metrics.year` uses format `"2025:"` (calendar year with trailing colon) or `"Till Today"`.
- `norms.norm` uses spaces: `"BS VI"`, `"BS IV"` (not `"BS-VI"`).
- The `fuel` and `norms` tables store each `(vehicle_class, fuel_type/norm)` row **once per vehicle_group (10 copies)**. Always deduplicate with `MAX(count) GROUP BY (vehicle_class, fuel_type)` or `MAX(count) GROUP BY (vehicle_class, norm)`.

**Example:**
```sql
SELECT vehicle_class, MAX(count) AS count
FROM fuel
WHERE state_code = '-1' AND fuel_type = 'PURE EV'
GROUP BY vehicle_class
ORDER BY count DESC
LIMIT 20
```

---

## 7. MCP Resources Reference

Resources are read-only reference data, fetched with `resources/read`.

| URI | Name | Description |
|---|---|---|
| `vahan://states` | Indian States | All 36 state codes and names |
| `vahan://vehicle-groups` | Vehicle Groups | 10 vehicle category groups |
| `vahan://fuel-types` | Fuel Types | All distinct fuel type strings in the dataset |
| `vahan://emission-norms` | Emission Norms | All distinct emission norm strings in the dataset |
| `vahan://makers` | Vehicle Makers | All ~1,457 vehicle manufacturer/brand names |
| `vahan://summary` | Dashboard Summary | Top-level VAHAN dashboard statistics |

---

## 8. Database Schema

SQLite database at `db/vahan.db`. Auto-built from CSVs on first server start.

```sql
-- Year-wise All India metrics
-- Note: all state_code values hold All India totals (VAHAN dashboard limitation)
CREATE TABLE yearly_metrics (
    state_code TEXT,
    state_name TEXT,
    year       TEXT,   -- e.g. "2025:", "2024:", "Till Today"
    metric     TEXT,   -- "registrations" | "transactions" | "revenue" | "permits"
    count      INTEGER,
    growth_pct REAL,
    PRIMARY KEY (state_code, year, metric)
);

-- Vehicle class × fuel type, per state
-- Each (vehicle_class, fuel_type) row appears 10× (once per vehicle_group) — use MAX(count) GROUP BY to deduplicate
CREATE TABLE fuel (
    state_code    TEXT,
    state_name    TEXT,
    vehicle_group TEXT,
    vehicle_class TEXT,
    fuel_type     TEXT,
    count         INTEGER
);

-- Vehicle class × emission norm, per state
-- Same 10× duplication as fuel table
CREATE TABLE norms (
    state_code    TEXT,
    state_name    TEXT,
    vehicle_group TEXT,
    vehicle_class TEXT,
    norm          TEXT,   -- e.g. "BS VI", "BS IV" (spaces, not hyphens)
    count         INTEGER
);

-- Regional Transport Offices
CREATE TABLE rtos (
    state_code TEXT,
    state_name TEXT,
    rto_code   TEXT PRIMARY KEY,
    rto_name   TEXT
);

-- States / Union Territories
CREATE TABLE states (
    state_code TEXT PRIMARY KEY,
    state_name TEXT
);

-- Maker/brand registrations per state and year
CREATE TABLE makers (
    state_code TEXT,
    state_name TEXT,
    maker      TEXT,   -- e.g. "HERO MOTOCORP LTD", "MARUTI SUZUKI INDIA LTD"
    year       TEXT,   -- e.g. "2025" (no trailing colon, unlike yearly_metrics)
    count      INTEGER
);
```

---

## 9. Output Files

All files are written to `data/`.

| File | Rows (approx.) | Description |
|---|---|---|
| `registrations.csv` | ~864 | Year-wise All India registrations |
| `transactions.csv` | ~864 | Year-wise All India transactions |
| `revenue.csv` | ~864 | Year-wise All India revenue |
| `permits.csv` | ~864 | Year-wise All India permits |
| `all_metrics.csv` | ~3,456 | All 4 metrics combined |
| `states.csv` | 35 | State codes and names |
| `rto_list.csv` | 1,551 | All RTOs with state code and name |
| `summary_stats.csv` | 1 | Top-level dashboard stats |
| `rto_metrics.csv` | varies | Per-RTO year-wise data *(full run only)* |
| `vehicle_class_by_fuel.csv` | 273,852 | Vehicle class × fuel type per state |
| `vehicle_class_by_norms.csv` | 205,801 | Vehicle class × emission norm per state |
| `vehicle_class_by_year.csv` | varies | Vehicle class × year per state |
| `maker_registrations.csv` | ~1,457/yr | Maker/brand registration counts per year |

---

## 10. State Codes

| Code | State / UT | Code | State / UT |
|---|---|---|---|
| `-1` | All India | `MH` | Maharashtra |
| `AN` | Andaman & Nicobar | `ML` | Meghalaya |
| `AP` | Andhra Pradesh | `MN` | Manipur |
| `AR` | Arunachal Pradesh | `MP` | Madhya Pradesh |
| `AS` | Assam | `MZ` | Mizoram |
| `BR` | Bihar | `NL` | Nagaland |
| `CG` | Chhattisgarh | `OR` | Odisha |
| `CH` | Chandigarh | `PB` | Punjab |
| `DD` | Dadra & Nagar Haveli and Daman & Diu | `PY` | Puducherry |
| `DL` | Delhi | `RJ` | Rajasthan |
| `GA` | Goa | `SK` | Sikkim |
| `GJ` | Gujarat | `TN` | Tamil Nadu |
| `HP` | Himachal Pradesh | `TR` | Tripura |
| `HR` | Haryana | `UK` | Uttarakhand |
| `JH` | Jharkhand | `UP` | Uttar Pradesh |
| `JK` | Jammu & Kashmir | `WB` | West Bengal |
| `KA` | Karnataka | `LA` | Ladakh |
| `KL` | Kerala | `LD` | Lakshadweep |

---

## 11. Data Caveats

**Year-wise data is All India only.**
The VAHAN dashboard's state selector does not update the year-wise data tables — they always display All India totals regardless of which state is selected. The `yearly_metrics` table stores state codes but all rows contain identical All India values. Use `state_code="-1"` to get the canonical row.

**`fuel` and `norms` tables have 10× row duplication.**
VAHAN's Tabular Summary cross-tab ignores the vehicle_group selection and returns all vehicle classes for every group. Each `(vehicle_class, fuel_type, count)` combination is stored 10 times (once per vehicle_group). All built-in tools handle this automatically. If writing raw SQL, always use:
```sql
MAX(count) GROUP BY vehicle_class, fuel_type   -- for fuel table
MAX(count) GROUP BY vehicle_class, norm        -- for norms table
```

**Year format has a trailing colon.**
Years are stored as `"2025:"`, `"2024:"` etc. (with a trailing colon, matching the raw VAHAN output). The special value `"Till Today"` is the all-time cumulative total.

**Emission norm values use spaces not hyphens.**
Use `"BS VI"` and `"BS IV"`, not `"BS-VI"` or `"BS-IV"`.

**20 rows of missing norms data for Ladakh / TRAILER.**
The VAHAN server returns fallback VCG data instead of norms for Ladakh's TRAILER category. These 20 rows (0.01% of the norms table) are a server-side limitation and cannot be resolved by re-scraping.

**Maker data is aggregate per year (not month-wise).**
The XLSX download from the Vahan dashboard provides total registration counts per maker per year. Month-wise and fuel-wise breakdowns are not available in the download export. The data covers all vehicle categories combined (not split by vehicle group).
