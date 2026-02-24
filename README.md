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
12. [Deployment (Digital Ocean)](#12-deployment-digital-ocean)

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

### `vahan_scraper.py` — Unified Scraper

The project uses a unified Playwright-based scraper to collect data from the [VAHAN Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/). It automates selection, extraction, and consolidation of multi-dimensional registration data.

### Scraper Usage

**`--year` is the only mandatory parameter.** By default, the scraper iterates through multiple X/Y axis combinations and all Indian states.

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

**Naming Scheme**: Generated files follow the pattern `data/[xaxis]_[yaxis]_[year].csv` (e.g., `data/Fuel_Maker_2025.csv`). Each file contains consolidated registration data across all states.

| Flag | Default | Description |
|---|---|---|
| `--year` | (Required) | Calendar year to scrape. |
| `--state` | `ALL` | List of states to scrape. Fetches all ~36 states if omitted. |
| `--xaxis` | `["Month Wise", "Fuel", "Norms"]` | One or more X-Axis variables to scrape. |
| `--yaxis` | `["Vehicle Class", "Maker", "Fuel"]` | One or more Y-Axis variables to scrape. |
| `--out` | `data` | Output directory where CSVs are saved. |

**Key Features:**
- **Auto-Aggregation**: Automatically iterates through all selected states and combines them into one consolidated file per X/Y/Year combination.
- **Generic Data Schema**: Supports flexible X and Y axis selections (Maker, Fuel, Norms, Vehicle Class, etc.).
- **Data Transformation**: Converts wide-format Vahan exports into standardized long-format CSVs.
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

### `get_vahan_metrics`

Flexible tool to fetch vehicle registration counts across any combination of dimensions (e.g., Maker, Fuel, Norms, Vehicle Class).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `yaxis_name` | string | No | e.g., `"Maker"`, `"Vehicle Class"` |
| `yaxis_value` | string | No | Filter by value e.g., `"MARUTI SUZUKI"` |
| `xaxis_name` | string | No | e.g., `"Fuel"`, `"Month Wise"` |
| `xaxis_value` | string | No | Filter by value e.g., `"PETROL"`, `"JAN"` |
| `state` | string | No | Filter by state name |
| `year` | integer | No | Calendar year |
| `limit` | integer | No | Maximum rows (default: 500) |

---

### `get_top_performers`

Identify top values in a dimension (e.g., top makers or fuel types) ranked by registration volume.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `dimension_name`| string | Yes | Dimension to rank (e.g., `"Maker"`) |
| `filter_dim` | string | No | Optional dimension to filter by (e.g., `"Fuel"`) |
| `filter_val` | string | No | Optional value for filter (e.g., `"ELECTRIC(BOV)"`) |
| `state` | string | No | Filter by state name |
| `year` | integer | No | Filter by year |
| `limit` | integer | No | Number of top items (default: 20) |

---

### `search_dimension_values`

Search for specific values within a dimension by name substring (e.g., finding a specific manufacturer).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `dimension_name` | string | Yes | e.g. `"Maker"`, `"Vehicle Class"` |
| `name_contains` | string | Yes | Case-insensitive substring |
| `limit` | integer | No | Maximum matches |

---

### `get_ev_stats`

Dedicated analysis for Electric Vehicle (EV) registration adoption trends.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state` | string | — | Filter by state name |
| `year` | integer | — | Filter by year |
| `group_by` | string | `"state"` | One of `state`, `xaxis_value`, `yaxis_value` |

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
- `vahan_data(yaxis_name, yaxis_value, xaxis_name, xaxis_value, state, year, count)`
- `rtos(state_code, state_name, rto_code, rto_name)`
- `states(state_code, state_name)`

---

## 7. MCP Resources Reference

| URI | Name | Description |
|---|---|---|
| `vahan://states` | Indian States | All state codes and names |
| `vahan://dimensions` | Available Dimensions | List of available X and Y axis variables globally |
| `vahan://summary` | Dashboard Summary | Top-level VAHAN dashboard statistics |

---

## 8. Database Schema

SQLite database at `db/vahan.db`. Automatically synced from CSVs in `data/` on server start using a tracking log.

```sql
CREATE TABLE vahan_data (
    yaxis_name  TEXT,    -- e.g. "Maker", "Vehicle Class"
    yaxis_value TEXT,    -- e.g. "TATA MOTORS LTD"
    xaxis_name  TEXT,    -- e.g. "Fuel", "Month Wise"
    xaxis_value TEXT,    -- e.g. "PETROL", "JAN"
    state       TEXT,    -- e.g. "DELHI"
    year        INTEGER, -- e.g. 2025
    count       INTEGER
);

CREATE TABLE ingestion_log (
    filename      TEXT PRIMARY KEY,
    last_modified REAL,
    ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
---
## 12. Deployment (Digital Ocean)

The project includes built-in automation for deployment to a Digital Ocean droplet using GitHub Actions and Systemd.

### Droplet Setup (One-time)

1. **Authorize GitHub SSH Key**: Add your GitHub Action's public SSH key to `/root/.ssh/authorized_keys` on your droplet.
2. **Setup Folder**: The automation expects the project to live at `/root/vahandata`.
3. **Trigger Initial Deploy**: Push to the `main` branch of your GitHub repository.

### GitHub Actions (CI/CD)

Continuous Deployment is handled by `.github/workflows/deploy.yml`. It automatically:
- Clones the repository to your droplet (if missing).
- Installs system dependencies (`python3-venv`).
- Sets up the Python virtual environment.
- Configures and starts the `vahan-mcp` systemd service.

**Required GitHub Secrets:**
- `DO_HOST`: Your droplet's IP address.
- `DO_SSH_KEY`: Your private SSH key.

### Service Management

Once deployed, the MCP server runs as a background service. You can manage it on the droplet using:

```bash
# Check service status
systemctl status vahan-mcp

# Restart manually
systemctl restart vahan-mcp

# View logs
journalctl -u vahan-mcp -f
```
