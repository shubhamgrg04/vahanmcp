# VAHAN Data

Scrape India's national vehicle registration database ([VAHAN Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/)) and serve it as a Model Context Protocol (MCP) server — queryable by Claude and any MCP-compatible client.

**Data coverage**
- 36 states / union territories (including All India)
- Consolidated registration data by **Fuel**, **Maker**, **Vehicle Class**, **Category**, and **Norms**
- Month-wise and state-wise granularity
- Data available for years 2024–2026 (customizable via scraper)

---

## Table of Contents

1. [Setup](#1-setup)
2. [Scraping](#2-scraping)
3. [MCP Server](#3-mcp-server)
4. [Hosting with Cloudflared](#4-hosting-with-cloudflared)
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

The project uses a unified Playwright-based scraper to collect data from the [VAHAN Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/).

### `vahan_scraper.py` — Multi-axis Scraper

### Scraper Usage

The unified scraper supports flexible extraction with broad defaults. **`--year` is the only mandatory parameter.**

```bash
# Full extraction (Broad defaults: Multiple X/Y combinations, all states)
.venv/bin/python scraping/vahan_scraper.py --year 2025

# Targeted extraction: Maker x Fuel breakdown for specific states
.venv/bin/python scraping/vahan_scraper.py \
  --year 2025 \
  --state "DELHI" "HARYANA" \
  --xaxis "Fuel" \
  --yaxis "Maker"
```

**Naming Scheme**: Generated files follow the pattern `data/[xaxis]_[yaxis]_[state]_[year].csv`.

**Defaults for maximum coverage (if omitted)**:
- **X-Axis**: `Month Wise`, `Fuel`, `Norms`, `Vehicle Category`, `Vehicle Class`
- **Y-Axis**: `Vehicle Class`, `Maker`, `Fuel`, `Norms`, `Vehicle Category`
- **States**: All Indian States/UTs

| Flag | Default | Description |
|---|---|---|
| `--state` | `ALL` | List of states to scrape. If `ALL` or omitted, it fetches all ~36 available states. |
| `--yaxis` | `["Vehicle Class"]` | One or more Y-Axis variables to scrape (e.g., `Fuel`, `Maker`, `Vehicle Category`). |
| `--year` | `2025` | Calendar year to scrape. |
| `--out` | `data` | Output directory where CSVs are saved. |

**Key Features:**
- **Auto-Aggregation**: Automatically iterates through all selected states and combines them into one file.
- **Data Transformation**: Converts wide-format Vahan exports into a clean long-format with columns: `S No`, `[Y-Axis]`, `State`, `Year`, `Month`, `Value`.
- **Organized Output**: Files are saved in the `data/` folder with naming pattern `[Y-Axis]_[Year].csv`.
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

### `get_vahan_data`

Query vehicle registration data for any category (e.g., `Vehicle Class`, `Fuel`, `Maker`, `Norms`). Supports month-wise details and flexible filtering.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | Yes | e.g. `"Vehicle Class"`, `"Fuel"`, `"Maker"`, `"Norms"` |
| `item_value` | string | No | Filter by value e.g. `"PETROL"`, `"TATA MOTORS LTD"` |
| `state` | string | No | State name e.g. `"DELHI"`, `"MAHARASHTRA"` |
| `year` | string | No | Year e.g. `"2025"`, `"2026"` |
| `month` | string | No | Month abbreviation e.g. `"JAN"`, `"FEB"` |
| `limit` | integer | No | Maximum rows (default: 200) |

---

### `get_top_items`

Get top items in a category (e.g., top makers or fuel types) ranked by registration count.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | Yes | e.g. `"Maker"`, `"Fuel"`, `"Vehicle Class"` |
| `state` | string | No | Filter by state name |
| `year` | string | No | Filter by year e.g. `"2025"` |
| `limit` | integer | No | Number of top items (default: 20) |

---

### `search_items`

Search for items in a category by name substring (e.g., finding a specific manufacturer).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | Yes | e.g. `"Maker"`, `"Vehicle Class"` |
| `name_contains` | string | Yes | Case-insensitive substring |
| `state` | string | No | Filter by state name |
| `year` | string | No | Filter by year |

---

### `get_ev_breakdown`

Get electric vehicle (EV) registration breakdown across states, months, or fuel sub-types.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state` | string | — | Filter by state name |
| `year` | string | — | Filter by year |
| `group_by` | string | `"state"` | One of `state`, `month`, `item_value` |

---

### `search_rtos`

Look up Regional Transport Offices (RTOs) by state code or name.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state_code` | string | — | Filter by state code e.g. `"MH"` |
| `name_contains` | string | — | Case-insensitive name substring |

---

### `run_sql`

Run an arbitrary read-only `SELECT` query against the SQLite database.

**Tables:**
- `vahan_data(category, item_value, state, year, month, count)`
- `rtos(state_code, state_name, rto_code, rto_name)`
- `states(state_code, state_name)`

---

## 7. MCP Resources Reference

| URI | Name | Description |
|---|---|---|
| `vahan://states` | Indian States | All state codes and names |
| `vahan://categories` | Data Categories | Available Y-axis variables (Fuel, Maker, etc.) |
| `vahan://fuel-types` | Fuel Types | List of all fuel type names |
| `vahan://summary` | Dashboard Summary | Top-level VAHAN dashboard statistics |

---

## 8. Database Schema

SQLite database at `db/vahan.db`. Rebuilt from CSVs on server start.

```sql
CREATE TABLE vahan_data (
    category   TEXT,   -- e.g. "Vehicle Class", "Fuel", "Maker", "Norms"
    item_value TEXT,   -- e.g. "PETROL", "TATA MOTORS LTD"
    state      TEXT,   -- e.g. "DELHI", "MAHARASHTRA"
    year       TEXT,   -- e.g. "2025", "2026"
    month      TEXT,   -- e.g. "JAN", "FEB"
    count      INTEGER
);

CREATE TABLE rtos (
    state_code TEXT,
    state_name TEXT,
    rto_code   TEXT PRIMARY KEY,
    rto_name   TEXT
);

CREATE TABLE states (
    state_code TEXT PRIMARY KEY,
    state_name TEXT
);
```

---

## 9. Output Files

All raw scraped data is stored in the `data/` directory.

| File | Description |
|---|---|
| `data/[Category]_[Year].csv` | Monthly registration data per state |
| `data/states.csv` | List of state codes and names |
| `data/rto_list.csv` | List of all RTOs |
| `data/summary_stats.csv` | Dashboard summary statistics |

---

## 10. State Codes

| Code | State / UT | Code | State / UT |
|---|---|---|---|
| `AN` | Andaman & Nicobar | `LD` | Lakshadweep |
| `AP` | Andhra Pradesh | `MH` | Maharashtra |
| `AR` | Arunachal Pradesh | `ML` | Meghalaya |
| `AS` | Assam | `MN` | Manipur |
| `BR` | Bihar | `MP` | Madhya Pradesh |
| `CG` | Chhattisgarh | `MZ` | Mizoram |
| `CH` | Chandigarh | `NL` | Nagaland |
| `DD` | Dadra & Nagar Haveli | `OR` | Odisha |
| `DL` | Delhi | `PB` | Punjab |
| `GA` | Goa | `PY` | Puducherry |
| `GJ` | Gujarat | `RJ` | Rajasthan |
| `HP` | Himachal Pradesh | `SK` | Sikkim |
| `HR` | Haryana | `TN` | Tamil Nadu |
| `JH` | Jharkhand | `TR` | Tripura |
| `JK` | Jammu & Kashmir | `UK` | Uttarakhand |
| `KA` | Karnataka | `UP` | Uttar Pradesh |
| `KL` | Kerala | `WB` | West Bengal |
| `LA` | Ladakh | | |

---

## 11. Data Caveats

**All India data is captured as a "state" value.**
When scraping "All India" totals, the result is stored with state name "All India".

**Clean Data Transformation.**
Unlike legacy versions, this scraper performs automatic cleaning and transformation to a standardized long format. Numeric counts are cleaned of commas and non-numeric characters during ingestion.

**High Performance Querying.**
The unified `vahan_data` table is indexed by category, state, and year for fast retrieval of trends and rankings.
