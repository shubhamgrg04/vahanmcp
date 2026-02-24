"""
VAHAN MCP Server
Exposes India vehicle registration data (scraped from vahan.parivahan.gov.in)
as MCP tools and resources for use with Claude.

This version supports the consolidated long-format CSVs (Monthly data).
"""

import argparse
import contextlib
import json
import re
import sqlite3
import os
from pathlib import Path

import pandas as pd
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
import mcp.types as types

# Optional Turso support
try:
    import libsql
    HAS_LIBSQL = True
except ImportError:
    HAS_LIBSQL = False

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = BASE_DIR / "db" / "vahan.db"

DB_PATH.parent.mkdir(exist_ok=True)

DB: sqlite3.Connection = None

# ── DB Ingestion ──────────────────────────────────────────────────────────────

def ingest(con: sqlite3.Connection) -> None:
    """Load new or updated CSVs into SQLite tables."""
    cur = con.cursor()

    # Generic vahan_data table for multi-axis support
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vahan_data (
            yaxis_name  TEXT,
            yaxis_value TEXT,
            xaxis_name  TEXT,
            xaxis_value TEXT,
            state       TEXT,
            year        INTEGER,
            count       INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_yaxis ON vahan_data(yaxis_name, yaxis_value)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_xaxis ON vahan_data(xaxis_name, xaxis_value)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_state ON vahan_data(state)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_year ON vahan_data(year)")

    # File tracking table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_log (
            filename      TEXT PRIMARY KEY,
            last_modified REAL,
            ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Scan for [xaxis]_[yaxis]_[year].csv
    if not DATA_DIR.exists():
        return

    for file in os.listdir(DATA_DIR):
        if not file.endswith(".csv") or file.count("_") < 2:
            continue
            
        path = DATA_DIR / file
        mtime = os.path.getmtime(path)
        
        # Check if already ingested and unchanged
        cur.execute("SELECT last_modified FROM ingestion_log WHERE filename = ?", (file,))
        row = cur.fetchone()
        if row and row[0] >= mtime:
            continue

        print(f"Syncing {file}...")
        
        try:
            df = pd.read_csv(path)
            # Standard consolidated columns: S No, [Y-Axis], State, Year, [X-Axis], Value
            if len(df.columns) < 6:
                print(f"Skipping {file}: Invalid column count")
                continue

            y_axis_name = df.columns[1]
            x_axis_name = df.columns[4]
            
            # Clean existing data for this file to allow updates
            parts = file.replace(".csv", "").split("_")
            year_val = parts[-1]
            
            # Clear old records for this specific combination
            cur.execute("""
                DELETE FROM vahan_data 
                WHERE xaxis_name = ? AND yaxis_name = ? AND year = ?
            """, (x_axis_name, y_axis_name, int(year_val)))

            # Handle commas in numeric values
            count_series = df["Value"].astype(str).str.replace(",", "")
            
            ingest_df = pd.DataFrame({
                "yaxis_name":  y_axis_name,
                "yaxis_value": df[y_axis_name].astype(str),
                "xaxis_name":  x_axis_name,
                "xaxis_value": df[x_axis_name].astype(str),
                "state":       df["State"].astype(str),
                "year":        df["Year"].astype(int),
                "count":       pd.to_numeric(count_series, errors="coerce").fillna(0).astype(int)
            })
            
            ingest_df.to_sql("vahan_data", con, if_exists="append", index=False)
            
            # Update log
            cur.execute("""
                INSERT OR REPLACE INTO ingestion_log (filename, last_modified, ingested_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (file, mtime))
            
        except Exception as e:
            print(f"Error ingesting {file}: {e}")

    # Standard tables (rtos, states)
    _ingest_lookup(con, "rtos", "rto_list.csv")
    _ingest_lookup(con, "states", "states.csv")

    con.commit()

def _ingest_lookup(con: sqlite3.Connection, table: str, filename: str):
    path = DATA_DIR / filename
    if path.exists():
        try:
            pd.read_csv(path).to_sql(table, con, if_exists="replace", index=False)
        except Exception as e:
            print(f"Error ingesting {filename}: {e}")

def open_db():
    """Connect to DB (Local SQLite or Turso) and ensure data is synced."""
    global DB
    url   = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    
    if url and HAS_LIBSQL:
        print(f"Connecting to Turso: {url}")
        con = libsql.connect(url, auth_token=token)
    else:
        print(f"Connecting to local SQLite: {DB_PATH}")
        con = sqlite3.connect(DB_PATH, check_same_thread=False)
        con.row_factory = sqlite3.Row
        
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    ingest(con)
    DB = con
    return con


# Initialize DB globally
DB = open_db()


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

# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("vahan")

# ── Resources ─────────────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(uri="vahan://states",     name="Indian States",     description="All state codes and names",          mimeType="text/plain"),
        Resource(uri="vahan://dimensions", name="Available Dimensions", description="List of available X and Y axis variables in the DB", mimeType="text/plain"),
        Resource(uri="vahan://summary",    name="Dashboard Summary", description="Top-level VAHAN dashboard statistics", mimeType="text/plain"),
    ]


@server.read_resource()
async def read_resource(uri: types.AnyUrl) -> str:
    uri_str = str(uri)

    if uri_str == "vahan://states":
        rows = DB.execute("SELECT state_code, state_name FROM states ORDER BY state_name").fetchall()
        return "\n".join(f"{r['state_code']}: {r['state_name']}" for r in rows)

    if uri_str == "vahan://dimensions":
        rows = DB.execute("""
            SELECT DISTINCT name FROM (
                SELECT DISTINCT yaxis_name as name FROM vahan_data
                UNION
                SELECT DISTINCT xaxis_name as name FROM vahan_data
            ) ORDER BY name
        """).fetchall()
        return "\n".join(r["name"] for r in rows)

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
            name="get_vahan_metrics",
            description=(
                "Flexible tool to fetch vehicle registration counts across any combination of dimensions. "
                "Common dimensions: 'Maker', 'Fuel', 'Norms', 'Vehicle Class', 'Vehicle Category', 'Month Wise'. "
                "You can filter by Y-axis and/or X-axis values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "yaxis_name":  {"type": "string", "description": "e.g., 'Maker'"},
                    "yaxis_value": {"type": "string", "description": "e.g., 'MARUTI SUZUKI'"},
                    "xaxis_name":  {"type": "string", "description": "e.g., 'Fuel' or 'Month Wise'"},
                    "xaxis_value": {"type": "string", "description": "e.g., 'PETROL' or 'JAN'"},
                    "state":       {"type": "string", "description": "Filter by state name"},
                    "year":        {"type": "integer"},
                    "limit":       {"type": "integer", "default": 500},
                },
            },
        ),
        Tool(
            name="get_top_performers",
            description="Identify top values in a dimension (e.g., top makers) based on registration volume.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dimension_name": {"type": "string", "description": "Dimension to rank (e.g., 'Maker', 'Vehicle Class')"},
                    "filter_dim":     {"type": "string", "description": "Optional dimension to filter by (e.g., 'Fuel')"},
                    "filter_val":     {"type": "string", "description": "Optional value for filter (e.g., 'ELECTRIC(BOV)')"},
                    "state":          {"type": "string"},
                    "year":           {"type": "integer"},
                    "limit":          {"type": "integer", "default": 20},
                },
                "required": ["dimension_name"],
            },
        ),
        Tool(
            name="search_dimension_values",
            description="Search for specific values within a dimension (e.g., check for a specific manufacturer name).",
            inputSchema={
                "type": "object",
                "properties": {
                    "dimension_name": {"type": "string", "description": "e.g. 'Maker'"},
                    "name_contains":  {"type": "string", "description": "Substring to search for"},
                    "limit":          {"type": "integer", "default": 50},
                },
                "required": ["dimension_name", "name_contains"],
            },
        ),
        Tool(
            name="get_ev_stats",
            description="Analyze Electric Vehicle (EV) registrations across states and categories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state":    {"type": "string"},
                    "year":     {"type": "integer"},
                    "group_by": {"type": "string", "enum": ["state", "xaxis_value", "yaxis_value"], "default": "state"},
                },
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
                "Run a read-only SQL SELECT query against the VAHAN database. "
                "Table: vahan_data(yaxis_name, yaxis_value, xaxis_name, xaxis_value, state, year, count). "
                "Other tables: rtos(state_code, state_name, rto_code, rto_name), states(state_code, state_name)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL SELECT query."},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["query"],
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
    if name == "get_vahan_metrics":
        return _get_vahan_metrics(args)
    if name == "get_top_performers":
        return _get_top_performers(args)
    if name == "search_dimension_values":
        return _search_dimension_values(args)
    if name == "get_ev_stats":
        return _get_ev_stats(args)
    if name == "search_rtos":
        return _search_rtos(args)
    if name == "run_sql":
        return _run_sql(args)
    raise ValueError(f"Unknown tool: {name}")

# ── Tool implementations ───────────────────────────────────────────────────────

def _get_vahan_metrics(args: dict) -> str:
    limit = int(args.get("limit", 500))
    conditions = []
    params = []

    for key in ["yaxis_name", "yaxis_value", "xaxis_name", "xaxis_value", "state", "year"]:
        if args.get(key):
            conditions.append(f"{key} = ?")
            params.append(args[key])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT * FROM vahan_data {where} ORDER BY year DESC, count DESC LIMIT {limit}"
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _get_top_performers(args: dict) -> str:
    dim_name   = args["dimension_name"]
    filter_dim = args.get("filter_dim")
    filter_val = args.get("filter_val")
    state      = args.get("state")
    year       = args.get("year")
    limit      = int(args.get("limit", 20))

    conditions = ["yaxis_name = ?"]
    params = [dim_name]

    if filter_dim and filter_val:
        conditions.append("xaxis_name = ? AND xaxis_value = ?")
        params.extend([filter_dim, filter_val])
    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT yaxis_value, SUM(count) as total_registrations
        FROM vahan_data
        WHERE {where}
        GROUP BY yaxis_value
        ORDER BY total_registrations DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _search_dimension_values(args: dict) -> str:
    dim_name      = args["dimension_name"]
    name_contains = args["name_contains"]
    limit         = int(args.get("limit", 50))

    query = """
        SELECT DISTINCT val FROM (
            SELECT DISTINCT yaxis_value as val FROM vahan_data WHERE yaxis_name = ? AND yaxis_value LIKE ?
            UNION
            SELECT DISTINCT xaxis_value as val FROM vahan_data WHERE xaxis_name = ? AND xaxis_value LIKE ?
        ) ORDER BY val LIMIT ?
    """
    pattern = f"%{name_contains}%"
    rows = DB.execute(query, (dim_name, pattern, dim_name, pattern, limit)).fetchall()
    return "\n".join(r["val"] for r in rows) if rows else "No matches found."

def _get_ev_stats(args: dict) -> str:
    state    = args.get("state")
    year     = args.get("year")
    group_by = args.get("group_by", "state")

    ev_placeholders = ",".join("?" * len(EV_FUEL_TYPES))
    
    query = f"""
        SELECT {group_by}, SUM(count) as ev_registrations
        FROM vahan_data
        WHERE (
            (xaxis_name = 'Fuel' AND xaxis_value IN ({ev_placeholders}))
            OR 
            (yaxis_name = 'Fuel' AND yaxis_value IN ({ev_placeholders}))
        )
    """
    params = list(EV_FUEL_TYPES) + list(EV_FUEL_TYPES)
    
    conditions = []
    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)

    if conditions:
        query += " AND " + " AND ".join(conditions)
    
    query += f" GROUP BY {group_by} ORDER BY ev_registrations DESC"
    
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

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
    query = f"SELECT * FROM rtos {where} LIMIT {limit}"
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _run_sql(args: dict) -> str:
    query = safe_sql(args["query"])
    limit = int(args.get("limit", 500))
    rows = DB.execute(query).fetchmany(limit)
    return rows_to_text(rows)

# ── Entry point ───────────────────────────────────────────────────────────────

async def run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

async def run_http(host: str, port: int):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.fastmcp.server import StreamableHTTPASGIApp

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=False,
        stateless=True,
    )
    asgi_app = StreamableHTTPASGIApp(session_manager)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[Route("/mcp", endpoint=asgi_app)],
    )

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    userver = uvicorn.Server(config)
    await userver.serve()

if __name__ == "__main__":
    import asyncio
    parser = argparse.ArgumentParser(description="VAHAN MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "http":
        asyncio.run(run_http(args.host, args.port))
    else:
        asyncio.run(run_stdio())
