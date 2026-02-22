"""
VAHAN MCP Server
Exposes India vehicle registration data (scraped from vahan.parivahan.gov.in)
as MCP tools and resources for use with Claude.

Transports
----------
stdio (default — for Claude Desktop / local use):
  python mcp_server.py

HTTP / Streamable HTTP (for web hosting):
  python mcp_server.py --transport http [--host 0.0.0.0] [--port 8000]

Claude Desktop config for stdio:
  {
    "mcpServers": {
      "vahan": {
        "command": "/path/to/vahandata/.venv/bin/python3",
        "args":    ["/path/to/vahandata/mcp_server.py"]
      }
    }
  }

Claude Desktop config for remote HTTP (via mcp-remote):
  {
    "mcpServers": {
      "vahan": {
        "command": "npx",
        "args":    ["mcp-remote", "http://your-server:8000/mcp"]
      }
    }
  }
"""

import argparse
import contextlib
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
import mcp.types as types

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = BASE_DIR / "db" / "vahan.db"

DB_PATH.parent.mkdir(exist_ok=True)

# ── DB Ingestion ──────────────────────────────────────────────────────────────

def ingest(con: sqlite3.Connection) -> None:
    """Load all CSVs into SQLite tables (run once; idempotent)."""
    cur = con.cursor()

    # yearly_metrics (registrations / transactions / revenue / permits)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS yearly_metrics (
            state_code TEXT,
            state_name TEXT,
            year       TEXT,
            metric     TEXT,
            count      INTEGER,
            growth_pct REAL,
            PRIMARY KEY (state_code, year, metric)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ym_metric ON yearly_metrics(metric, year)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ym_state  ON yearly_metrics(state_code, metric)")

    for metric in ("registrations", "transactions", "revenue", "permits"):
        path = DATA_DIR / f"{metric}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df["metric"] = metric
        df = df.rename(columns={"count": "count"})
        df[["state_code","state_name","year","metric","count","growth_pct"]].to_sql(
            "yearly_metrics", con, if_exists="append", index=False,
            method="multi",
        )

    # fuel
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fuel (
            state_code    TEXT,
            state_name    TEXT,
            vehicle_group TEXT,
            vehicle_class TEXT,
            fuel_type     TEXT,
            count         INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fuel_state ON fuel(state_code, vehicle_group)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fuel_type  ON fuel(fuel_type)")
    fuel_path = DATA_DIR / "vehicle_class_by_fuel.csv"
    if fuel_path.exists():
        pd.read_csv(fuel_path).to_sql("fuel", con, if_exists="replace", index=False)
        # Recreate indices after replace
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fuel_state ON fuel(state_code, vehicle_group)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fuel_type  ON fuel(fuel_type)")

    # norms
    cur.execute("""
        CREATE TABLE IF NOT EXISTS norms (
            state_code    TEXT,
            state_name    TEXT,
            vehicle_group TEXT,
            vehicle_class TEXT,
            norm          TEXT,
            count         INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_norms_state ON norms(state_code, vehicle_group)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_norms_norm  ON norms(norm)")
    norms_path = DATA_DIR / "vehicle_class_by_norms.csv"
    if norms_path.exists():
        pd.read_csv(norms_path).to_sql("norms", con, if_exists="replace", index=False)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_norms_state ON norms(state_code, vehicle_group)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_norms_norm  ON norms(norm)")

    # rtos
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rtos (
            state_code TEXT,
            state_name TEXT,
            rto_code   TEXT PRIMARY KEY,
            rto_name   TEXT
        )
    """)
    rto_path = DATA_DIR / "rto_list.csv"
    if rto_path.exists():
        pd.read_csv(rto_path).to_sql("rtos", con, if_exists="replace", index=False)

    # states
    cur.execute("""
        CREATE TABLE IF NOT EXISTS states (
            state_code TEXT PRIMARY KEY,
            state_name TEXT
        )
    """)
    states_path = DATA_DIR / "states.csv"
    if states_path.exists():
        pd.read_csv(states_path).to_sql("states", con, if_exists="replace", index=False)

    # makers (brand/manufacturer registrations)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS makers (
            state_code TEXT,
            state_name TEXT,
            maker      TEXT,
            year       TEXT,
            count      INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_state ON makers(state_code, year)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_maker ON makers(maker)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_year  ON makers(year)")
    makers_path = DATA_DIR / "maker_registrations.csv"
    if makers_path.exists():
        pd.read_csv(makers_path).to_sql("makers", con, if_exists="replace", index=False)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_state ON makers(state_code, year)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_maker ON makers(maker)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_makers_year  ON makers(year)")

    con.commit()


def open_db() -> sqlite3.Connection:
    """Open DB, ingest from CSVs if tables are missing or empty."""
    first_run = not DB_PATH.exists()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")

    if first_run:
        ingest(con)
    else:
        row = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('fuel','makers')").fetchone()
        if row[0] < 2:
            ingest(con)

    return con


# ── Helpers ───────────────────────────────────────────────────────────────────

def rows_to_text(rows: list[sqlite3.Row], limit: int | None = None) -> str:
    """Format DB rows as a compact text table."""
    if not rows:
        return "No results found."
    if limit and len(rows) > limit:
        rows = rows[:limit]
    keys = rows[0].keys()
    header = " | ".join(keys)
    sep    = "-" * len(header)
    lines  = [header, sep] + [" | ".join(str(r[k]) for k in keys) for r in rows]
    return "\n".join(lines)


def safe_sql(query: str) -> str:
    """Reject any query that isn't a SELECT."""
    cleaned = query.strip().upper()
    if not cleaned.startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted.")
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|PRAGMA)\b")
    if forbidden.search(cleaned):
        raise ValueError("Query contains forbidden SQL keyword.")
    return query


EV_FUEL_TYPES = (
    "PURE EV", "PLUG-IN HYBRID EV", "STRONG HYBRID EV", "ELECTRIC(BOV)"
)

VH_GROUPS = [
    "TWO WHEELER", "THREE WHEELER", "FOUR WHEELER",
    "AMBULANCE/HEARSES", "CONSTRUCTION EQUIPMENT", "GOODS VEHICLES",
    "PUBLIC SERVICE VEHICLE", "SPECIAL CATEGORY", "TRAILER", "TRACTOR",
]


# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("vahan")
DB: sqlite3.Connection = None  # set in main()


# ── Resources ─────────────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(uri="vahan://states",         name="Indian States",        description="All 36 state codes and names",          mimeType="text/plain"),
        Resource(uri="vahan://vehicle-groups",  name="Vehicle Groups",       description="10 vehicle category groups",            mimeType="text/plain"),
        Resource(uri="vahan://fuel-types",      name="Fuel Types",           description="All fuel types in the dataset",         mimeType="text/plain"),
        Resource(uri="vahan://emission-norms",  name="Emission Norms",       description="All emission norm names",               mimeType="text/plain"),
        Resource(uri="vahan://makers",          name="Vehicle Makers",       description="All vehicle manufacturer/brand names",  mimeType="text/plain"),
        Resource(uri="vahan://summary",         name="Dashboard Summary",    description="Top-level VAHAN dashboard statistics",  mimeType="text/plain"),
    ]


@server.read_resource()
async def read_resource(uri: types.AnyUrl) -> str:
    uri_str = str(uri)

    if uri_str == "vahan://states":
        rows = DB.execute("SELECT state_code, state_name FROM states ORDER BY state_name").fetchall()
        return "\n".join(f"{r['state_code']}: {r['state_name']}" for r in rows)

    if uri_str == "vahan://vehicle-groups":
        return "\n".join(VH_GROUPS)

    if uri_str == "vahan://fuel-types":
        rows = DB.execute("SELECT DISTINCT fuel_type FROM fuel ORDER BY fuel_type").fetchall()
        return "\n".join(r["fuel_type"] for r in rows)

    if uri_str == "vahan://emission-norms":
        rows = DB.execute("SELECT DISTINCT norm FROM norms ORDER BY norm").fetchall()
        return "\n".join(r["norm"] for r in rows)

    if uri_str == "vahan://makers":
        rows = DB.execute("SELECT DISTINCT maker FROM makers ORDER BY maker").fetchall()
        return "\n".join(r["maker"] for r in rows)

    if uri_str == "vahan://summary":
        path = DATA_DIR / "summary_stats.csv"
        if path.exists():
            df = pd.read_csv(path)
            return df.to_string(index=False)
        return "Summary stats not available."

    raise ValueError(f"Unknown resource: {uri_str}")


# ── Tools ─────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_registrations",
            description=(
                "Get year-wise All India vehicle counts for registrations, transactions, revenue, or permits. "
                "NOTE: The VAHAN dashboard only exposes All India totals in its year-wise tables — "
                "state_code is stored but all states hold identical All India values. "
                "Filter with state_code='-1' to get the canonical All India row. "
                "Years are calendar years with a trailing colon e.g. '2025:', '2024:'. "
                "Special value 'Till Today' gives the all-time running total. "
                "Returns rows sorted by year descending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric":     {"type": "string", "enum": ["registrations","transactions","revenue","permits"], "default": "registrations"},
                    "state_code": {"type": "string", "description": "Use '-1' for All India (recommended). Other state codes exist but hold All India values."},
                    "year":       {"type": "string", "description": "Calendar year e.g. '2025:', '2024:', or 'Till Today'. Omit for all years."},
                    "limit":      {"type": "integer", "default": 100},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_vehicle_class_by_fuel",
            description=(
                "Get vehicle class registration counts broken down by fuel type for a given state. "
                "Returns one row per (vehicle_class, fuel_type) combination with a count. "
                "Use fuel_type filter e.g. 'PETROL', 'DIESEL', 'PURE EV', 'CNG ONLY'. "
                "Use the vahan://fuel-types resource for the full list of valid fuel type strings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state_code": {"type": "string", "description": "State code e.g. 'MH'. Use '-1' for All India."},
                    "fuel_type":  {"type": "string", "description": "e.g. 'PETROL', 'DIESEL', 'PURE EV'. Omit for all fuel types."},
                    "limit":      {"type": "integer", "default": 200},
                },
                "required": ["state_code"],
            },
        ),
        Tool(
            name="get_vehicle_class_by_norms",
            description=(
                "Get vehicle class registration counts broken down by emission norm for a given state. "
                "Note: norm values use spaces not hyphens, e.g. 'BS VI' (not 'BS-VI'), 'BS IV', 'BS III'. "
                "Use the vahan://emission-norms resource for the full list of valid norm strings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state_code": {"type": "string", "description": "State code e.g. 'MH'. Use '-1' for All India."},
                    "norm":       {"type": "string", "description": "e.g. 'BS VI', 'BS IV', 'BS III'. Omit for all norms."},
                    "limit":      {"type": "integer", "default": 200},
                },
                "required": ["state_code"],
            },
        ),
        Tool(
            name="get_yearly_trend",
            description=(
                "Get the All India year-wise trend for a metric (registrations / transactions / "
                "revenue / permits). Shows how the metric changed each year with growth percentages. "
                "Year format is calendar year with trailing colon e.g. '2025:', '2024:'. "
                "Use 'Till Today' for the all-time running total."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": ["registrations","transactions","revenue","permits"], "default": "registrations"},
                    "limit":  {"type": "integer", "default": 20},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_ev_breakdown",
            description=(
                "Get electric vehicle (EV) registration breakdown. "
                "Covers PURE EV, PLUG-IN HYBRID EV, STRONG HYBRID EV, ELECTRIC(BOV). "
                "Can group results by state, vehicle_class, or fuel_type (EV sub-type)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state_code": {"type": "string", "description": "Filter to one state. Omit for all states."},
                    "group_by":   {"type": "string", "enum": ["state","vehicle_class","fuel_type"], "default": "state"},
                    "limit":      {"type": "integer", "default": 50},
                },
                "required": [],
            },
        ),
        Tool(
            name="search_rtos",
            description="Look up RTOs (Regional Transport Offices) by state or name substring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state_code":   {"type": "string", "description": "Filter by state code e.g. 'MH'."},
                    "name_contains":{"type": "string", "description": "Case-insensitive substring of RTO name."},
                    "limit":        {"type": "integer", "default": 50},
                },
                "required": [],
            },
        ),
        Tool(
            name="run_sql",
            description=(
                "Run a read-only SQL SELECT query directly against the VAHAN SQLite database. "
                "Tables: yearly_metrics(state_code,state_name,year,metric,count,growth_pct), "
                "fuel(state_code,state_name,vehicle_group,vehicle_class,fuel_type,count), "
                "norms(state_code,state_name,vehicle_group,vehicle_class,norm,count), "
                "makers(state_code,state_name,maker,year,count), "
                "rtos(state_code,state_name,rto_code,rto_name), "
                "states(state_code,state_name). "
                "Note: yearly_metrics.year uses format '2025:' (calendar year, trailing colon) "
                "or 'Till Today'. makers.year uses plain '2025' (no colon). "
                "Norm values use spaces: 'BS VI', 'BS IV' (not 'BS-VI'). "
                "IMPORTANT: fuel and norms tables store each (vehicle_class, fuel_type/norm) row "
                "once per vehicle_group (10 copies). Always use MAX(count) with GROUP BY "
                "(vehicle_class, fuel_type) or (vehicle_class, norm) to deduplicate. "
                "Only SELECT is permitted."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL SELECT query."},
                    "limit": {"type": "integer", "default": 500, "description": "Max rows returned."},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_top_makers",
            description=(
                "Get top vehicle manufacturers/brands ranked by registration count. "
                "Data covers ~1,457 makers across all states and years 2024-2026. "
                "Use state_code='-1' for All India totals. "
                "Use the vahan://makers resource for the full list of maker names."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "state_code": {"type": "string", "description": "State code e.g. 'MH'. Use '-1' for All India."},
                    "year":       {"type": "string", "description": "Year e.g. '2025'. Omit for all years."},
                    "limit":      {"type": "integer", "default": 20, "description": "Number of top makers to return."},
                },
                "required": ["state_code"],
            },
        ),
        Tool(
            name="search_makers",
            description=(
                "Search vehicle manufacturers/brands by name substring. "
                "Returns registration counts per maker, optionally filtered by state and year. "
                "Case-insensitive search, e.g. 'tata' matches 'TATA MOTORS LTD'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name_contains": {"type": "string", "description": "Case-insensitive substring of maker name e.g. 'hero', 'tata', 'suzuki'."},
                    "state_code":    {"type": "string", "description": "State code e.g. 'MH'. Use '-1' for All India. Omit for all states."},
                    "year":          {"type": "string", "description": "Year e.g. '2025'. Omit for all years."},
                    "limit":         {"type": "integer", "default": 50},
                },
                "required": ["name_contains"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
    except Exception as e:
        result = f"Error: {e}"
    return [TextContent(type="text", text=result)]


def _dispatch(name: str, args: dict) -> str:
    if name == "get_registrations":
        return _get_registrations(args)
    if name == "get_vehicle_class_by_fuel":
        return _get_vehicle_class_by_fuel(args)
    if name == "get_vehicle_class_by_norms":
        return _get_vehicle_class_by_norms(args)
    if name == "get_yearly_trend":
        return _get_yearly_trend(args)
    if name == "get_ev_breakdown":
        return _get_ev_breakdown(args)
    if name == "search_rtos":
        return _search_rtos(args)
    if name == "run_sql":
        return _run_sql(args)
    if name == "get_top_makers":
        return _get_top_makers(args)
    if name == "search_makers":
        return _search_makers(args)
    raise ValueError(f"Unknown tool: {name}")


# ── Tool implementations ───────────────────────────────────────────────────────

def _get_registrations(args: dict) -> str:
    metric     = args.get("metric", "registrations")
    state_code = args.get("state_code")
    year       = args.get("year")
    limit      = int(args.get("limit", 100))

    conditions = ["metric = ?"]
    params: list = [metric]
    if state_code:
        conditions.append("state_code = ?")
        params.append(state_code)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT state_code, state_name, year, count, growth_pct
        FROM yearly_metrics
        WHERE {where}
        ORDER BY year DESC, count DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    header = f"Metric: {metric}" + (f" | State: {state_code}" if state_code else "") + (f" | Year: {year}" if year else "")
    return header + "\n\n" + rows_to_text(rows)


def _get_vehicle_class_by_fuel(args: dict) -> str:
    state_code = args["state_code"]
    fuel_type  = args.get("fuel_type")
    limit      = int(args.get("limit", 200))

    conditions = ["state_code = ?"]
    params: list = [state_code]
    if fuel_type:
        conditions.append("fuel_type = ?")
        params.append(fuel_type)

    where = " AND ".join(conditions)
    # Deduplicate: the raw table stores the same (vehicle_class, fuel_type, count) once per
    # vehicle_group (10×). Use MAX(count) GROUP BY to collapse to one row per combination.
    query = f"""
        SELECT vehicle_class, fuel_type, MAX(count) AS count
        FROM fuel
        WHERE {where}
        GROUP BY vehicle_class, fuel_type
        ORDER BY count DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)


def _get_vehicle_class_by_norms(args: dict) -> str:
    state_code = args["state_code"]
    norm       = args.get("norm")
    limit      = int(args.get("limit", 200))

    conditions = ["state_code = ?"]
    params: list = [state_code]
    if norm:
        conditions.append("norm = ?")
        params.append(norm)

    where = " AND ".join(conditions)
    query = f"""
        SELECT vehicle_class, norm, MAX(count) AS count
        FROM norms
        WHERE {where}
        GROUP BY vehicle_class, norm
        ORDER BY count DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)


def _get_yearly_trend(args: dict) -> str:
    metric = args.get("metric", "registrations")
    limit  = int(args.get("limit", 20))

    query = """
        SELECT year, count, growth_pct
        FROM yearly_metrics
        WHERE state_code = '-1' AND metric = ?
        ORDER BY year DESC
        LIMIT ?
    """
    rows = DB.execute(query, [metric, limit]).fetchall()
    header = f"All India yearly trend — {metric}"
    return header + "\n\n" + rows_to_text(rows)


def _get_ev_breakdown(args: dict) -> str:
    state_code = args.get("state_code")
    group_by   = args.get("group_by", "state")
    limit      = int(args.get("limit", 50))

    valid_group_by = {"state", "vehicle_class", "fuel_type"}
    if group_by not in valid_group_by:
        raise ValueError(f"group_by must be one of: {sorted(valid_group_by)}")

    group_col_map = {
        "state":         "state_name",
        "vehicle_class": "vehicle_class",
        "fuel_type":     "fuel_type",
    }
    group_col = group_col_map[group_by]

    ev_placeholders = ",".join("?" * len(EV_FUEL_TYPES))
    conditions = [f"fuel_type IN ({ev_placeholders})"]
    params: list = list(EV_FUEL_TYPES)

    if state_code:
        conditions.append("state_code = ?")
        params.append(state_code)

    where = " AND ".join(conditions)

    # The raw fuel table stores the same (state, vehicle_class, fuel_type, count) once
    # per vehicle_group (10×). We deduplicate first with an inner query using MAX(count)
    # grouped by (state_code, state_name, vehicle_class, fuel_type), then aggregate.
    if group_by == "state":
        query = f"""
            SELECT state_name, SUM(dedup_count) AS ev_count
            FROM (
                SELECT state_code, state_name, vehicle_class, fuel_type,
                       MAX(count) AS dedup_count
                FROM fuel
                WHERE {where}
                GROUP BY state_code, state_name, vehicle_class, fuel_type
            )
            GROUP BY state_name
            ORDER BY ev_count DESC
            LIMIT {limit}
        """
    elif group_by == "vehicle_class":
        state_filter = ("AND state_code = ?" if state_code else "")
        state_params = [state_code] if state_code else []
        query = f"""
            SELECT vehicle_class, SUM(dedup_count) AS ev_count
            FROM (
                SELECT state_code, vehicle_class, fuel_type,
                       MAX(count) AS dedup_count
                FROM fuel
                WHERE {where}
                GROUP BY state_code, vehicle_class, fuel_type
            )
            GROUP BY vehicle_class
            ORDER BY ev_count DESC
            LIMIT {limit}
        """
    else:  # fuel_type
        query = f"""
            SELECT fuel_type, SUM(dedup_count) AS ev_count
            FROM (
                SELECT state_code, vehicle_class, fuel_type,
                       MAX(count) AS dedup_count
                FROM fuel
                WHERE {where}
                GROUP BY state_code, vehicle_class, fuel_type
            )
            GROUP BY fuel_type
            ORDER BY ev_count DESC
            LIMIT {limit}
        """

    rows = DB.execute(query, params).fetchall()
    header = "EV Breakdown" + (f" | State: {state_code}" if state_code else " | All India") + f" | Grouped by: {group_by}"
    return header + "\n\n" + rows_to_text(rows)


def _search_rtos(args: dict) -> str:
    state_code    = args.get("state_code")
    name_contains = args.get("name_contains")
    limit         = int(args.get("limit", 50))

    conditions: list[str] = []
    params: list = []
    if state_code:
        conditions.append("state_code = ?")
        params.append(state_code)
    if name_contains:
        conditions.append("LOWER(rto_name) LIKE ?")
        params.append(f"%{name_contains.lower()}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"""
        SELECT state_code, state_name, rto_code, rto_name
        FROM rtos
        {where}
        ORDER BY state_code, rto_code
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)


def _run_sql(args: dict) -> str:
    query = safe_sql(args["query"])
    limit = int(args.get("limit", 500))
    rows = DB.execute(query).fetchmany(limit)
    if not rows:
        return "No results."
    return rows_to_text(rows)


def _get_top_makers(args: dict) -> str:
    state_code = args["state_code"]
    year       = args.get("year")
    limit      = int(args.get("limit", 20))

    conditions = ["state_code = ?"]
    params: list = [state_code]
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT maker, year, count
        FROM makers
        WHERE {where}
        ORDER BY count DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    header = f"Top makers" + (f" | State: {state_code}" if state_code else "") + (f" | Year: {year}" if year else "")
    return header + "\n\n" + rows_to_text(rows)


def _search_makers(args: dict) -> str:
    name_contains = args["name_contains"]
    state_code    = args.get("state_code")
    year          = args.get("year")
    limit         = int(args.get("limit", 50))

    conditions = ["LOWER(maker) LIKE ?"]
    params: list = [f"%{name_contains.lower()}%"]
    if state_code:
        conditions.append("state_code = ?")
        params.append(state_code)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT maker, state_name, year, count
        FROM makers
        WHERE {where}
        ORDER BY count DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_stdio():
    """Run over stdio (default — for Claude Desktop / local use)."""
    global DB
    DB = open_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def run_http(host: str, port: int):
    """Run as a Streamable HTTP server (for web hosting)."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.fastmcp.server import StreamableHTTPASGIApp

    global DB
    DB = open_db()

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=False,   # SSE streaming (compatible with mcp-remote)
        stateless=True,        # no session state needed for pure query server
    )
    asgi_app = StreamableHTTPASGIApp(session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/mcp", endpoint=asgi_app),
        ],
    )

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    userver = uvicorn.Server(config)
    print(f"VAHAN MCP server listening on http://{host}:{port}/mcp")
    await userver.serve()


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="VAHAN MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport to use: 'stdio' (default, for Claude Desktop) or 'http' (for web hosting)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    args = parser.parse_args()

    if args.transport == "http":
        asyncio.run(run_http(args.host, args.port))
    else:
        asyncio.run(run_stdio())
