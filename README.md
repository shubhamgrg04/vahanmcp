# VAHAN Data

Scrape India's national vehicle registration database ([VAHAN Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/)) and serve it as a Model Context Protocol (MCP) server — queryable by Claude and any MCP-compatible client.

**Data coverage**
- 36 states / union territories (including All India)
- Available for combimations of X-Y axes (year and state level data)
- Available X-Y axes are: 
  - X-axis: Fuel, Maker, Vehicle Class, Vehicle Category Group, Norms, Month Wise
  - Y-axis: Fuel, Maker, Vehicle Class, Vehicle Category Group, Norms

---

## Table of Contents

1. [Setup](#1-setup)
2. [Scraping](#2-scraping)
3. [MCP Server](#3-mcp-server)
4. [Hosting (Direct Domain)](#4-hosting-direct-domain)
5. [Connecting Claude Desktop](#5-connecting-claude-desktop)
6. [MCP Tools Reference](#6-mcp-tools-reference)
7. [MCP Resources Reference](#7-mcp-resources-reference)
8. [Database Schema](#8-database-schema)
9. [Output Files](#9-output-files)
10. [State Codes](#10-state-codes)
11. [Data Caveats](#11-data-caveats)
12. [Deployment (Custom Domain & SSL)](#12-deployment-custom-domain--ssl)

---

## 1. Setup

**Requirements:** Python 3.11+, Node.js (for `mcp-remote` / Claude Desktop)

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

# Bind to localhost only (behind a reverse proxy)
.venv/bin/python3 mcp_server.py --transport http --host 127.0.0.1

# Custom port
.venv/bin/python3 mcp_server.py --transport http --host 127.0.0.1 --port 9000
```

---
## 4. Hosting

The server is configured to run behind an **Nginx** reverse proxy on a custom domain (`vahanmcp.shubhamgrg.com`).

- **Reverse Proxy**: Nginx listens on port 80/443 and forwards traffic to `127.0.0.1:8000`.
- **SSL**: Automated via **Certbot** (Let's Encrypt).

This project includes `vahan-mcp.nginx` and an automated `deploy.sh` script to handle the configuration.

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
      "args": ["mcp-remote", "https://vahanmcp.shubhamgrg.com/mcp"]
    }
  }
}
```

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
## 12. Deployment (Custom Domain & SSL)

The project includes built-in automation for deployment to a Digital Ocean droplet using GitHub Actions, Nginx, and Certbot.

### Droplet Setup (One-time)

1. **DNS Setup**: Point `vahanmcp.shubhamgrg.com` (A record) to your droplet's IP address.
2. **Authorize GitHub SSH Key**: Add your GitHub Action's public SSH key to `/root/.ssh/authorized_keys` on your droplet.
3. **Setup Folder**: The automation expects the project to live at `/root/vahandata`.
4. **Initial Deploy**: Push to the `main` branch.

### GitHub Actions (CI/CD)

The `.github/workflows/deploy.yml` workflow automatically:
- Clones/Pulls the code to `/root/vahandata`.
- Installs `python3-venv`, `nginx`, and `certbot` if missing.
- Sets up the Python virtual environment and dependencies.
- Configures **Nginx** and handles **SSL (Certbot)** for `vahanmcp.shubhamgrg.com`.
- Manages the `vahan-mcp` background service.

### Service & Proxy Management

```bash
# Monitor the MCP Server
systemctl status vahan-mcp
journalctl -u vahan-mcp -f

# Manage Nginx
systemctl status nginx
nginx -t
```

> Automated scraping through github actions don't work as vahan website blocks traffic from github actions. So you need to run the scraper manually.


