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

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = BASE_DIR / "db" / "vahan.db"

DB_PATH.parent.mkdir(exist_ok=True)

# ── DB Ingestion ──────────────────────────────────────────────────────────────

def ingest(con: sqlite3.Connection) -> None:
    """Load all CSVs into SQLite tables."""
    cur = con.cursor()

    # Unified vahan_data table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vahan_data (
            category   TEXT,
            item_value TEXT,
            state      TEXT,
            year       TEXT,
            month      TEXT,
            count      INTEGER
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_cat ON vahan_data(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_state ON vahan_data(state)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vahan_year ON vahan_data(year)")

    # Scan for [Category]_[Year].csv
    for file in os.listdir(DATA_DIR):
        if file.endswith(".csv") and "_" in file:
            parts = file.replace(".csv", "").split("_")
            if len(parts) >= 2:
                # Last part is usually the year
                year = parts[-1]
                category = " ".join(parts[:-1]).replace("_", " ")
                
                path = DATA_DIR / file
                print(f"Ingesting {file} as category '{category}', year '{year}'...")
                
                try:
                    df = pd.read_csv(path)
                    # Expected columns in CSV: S No, [Y-Axis], State, Year, Month, Value
                    # We map [Y-Axis] (which is the 2nd column) to item_value
                    y_axis_col = df.columns[1]
                    
                    # Handle commas in numeric values
                    count_series = df["Value"].astype(str).str.replace(",", "")
                    
                    ingest_df = pd.DataFrame({
                        "category":   category,
                        "item_value": df[y_axis_col].astype(str),
                        "state":      df["State"].astype(str),
                        "year":       df["Year"].astype(str),
                        "month":      df["Month"].astype(str),
                        "count":      pd.to_numeric(count_series, errors="coerce").fillna(0).astype(int)
                    })
                    
                    ingest_df.to_sql("vahan_data", con, if_exists="append", index=False)
                except Exception as e:
                    print(f"Error ingesting {file}: {e}")

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

    con.commit()


def open_db() -> sqlite3.Connection:
    """Always rebuild DB for now as requested by user."""
    if DB_PATH.exists():
        os.remove(DB_PATH)
        
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")

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

# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("vahan")
DB: sqlite3.Connection = None 

# ── Resources ─────────────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(uri="vahan://states",     name="Indian States",     description="All state codes and names",          mimeType="text/plain"),
        Resource(uri="vahan://categories", name="Data Categories",   description="Available Y-axis variables",         mimeType="text/plain"),
        Resource(uri="vahan://fuel-types",  name="Fuel Types",        description="Available fuel types",               mimeType="text/plain"),
        Resource(uri="vahan://summary",     name="Dashboard Summary", description="Top-level VAHAN dashboard statistics", mimeType="text/plain"),
    ]


@server.read_resource()
async def read_resource(uri: types.AnyUrl) -> str:
    uri_str = str(uri)

    if uri_str == "vahan://states":
        rows = DB.execute("SELECT state_code, state_name FROM states ORDER BY state_name").fetchall()
        return "\n".join(f"{r['state_code']}: {r['state_name']}" for r in rows)

    if uri_str == "vahan://categories":
        rows = DB.execute("SELECT DISTINCT category FROM vahan_data ORDER BY category").fetchall()
        return "\n".join(r["category"] for r in rows)

    if uri_str == "vahan://fuel-types":
        rows = DB.execute("SELECT DISTINCT item_value FROM vahan_data WHERE category = 'Fuel' ORDER BY item_value").fetchall()
        return "\n".join(r["item_value"] for r in rows)

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
            name="get_vahan_data",
            description=(
                "Query vehicle registration data for any category (e.g., 'Vehicle Class', 'Fuel', 'Maker', 'Norms'). "
                "Allows filtering by state, year, month, and item_value (e.g., fuel type name or maker name). "
                "Returns month-wise details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category":   {"type": "string", "description": "e.g. 'Vehicle Class', 'Fuel', 'Maker', 'Norms'."},
                    "item_value": {"type": "string", "description": "Specific value to filter for (e.g. 'PETROL' for Fuel category)."},
                    "state":      {"type": "string", "description": "State name (e.g. 'DELHI')."},
                    "year":       {"type": "string", "description": "Year (e.g. '2025')."},
                    "month":      {"type": "string", "description": "Month abbreviation (e.g. 'JAN')."},
                    "limit":      {"type": "integer", "default": 200},
                },
                "required": ["category"],
            },
        ),
        Tool(
            name="get_top_items",
            description="Get top items in a category (e.g., top makers or top fuel types) ranked by registration count.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "e.g. 'Maker', 'Fuel'."},
                    "state":    {"type": "string", "description": "State name."},
                    "year":     {"type": "string", "description": "Year (e.g. '2025')."},
                    "limit":    {"type": "integer", "default": 20},
                },
                "required": ["category"],
            },
        ),
        Tool(
            name="search_items",
            description="Search for items in a category by name substring (e.g., search for a manufacturer).",
            inputSchema={
                "type": "object",
                "properties": {
                    "category":      {"type": "string", "description": "e.g. 'Maker'."},
                    "name_contains": {"type": "string", "description": "Substring to search for."},
                    "state":         {"type": "string"},
                    "year":          {"type": "string"},
                    "limit":         {"type": "integer", "default": 50},
                },
                "required": ["category", "name_contains"],
            },
        ),
        Tool(
            name="get_ev_breakdown",
            description="Get electric vehicle (EV) registration breakdown across states or categories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "state":    {"type": "string", "description": "Filter by state name."},
                    "year":     {"type": "string", "description": "Year (e.g. '2025')."},
                    "group_by": {"type": "string", "enum": ["state", "month", "item_value"], "default": "state"},
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
                "Run a read-only SQL SELECT query against the VAHAN database. "
                "Table: vahan_data(category, item_value, state, year, month, count). "
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
    if name == "get_vahan_data":
        return _get_vahan_data(args)
    if name == "get_top_items":
        return _get_top_items(args)
    if name == "search_items":
        return _search_items(args)
    if name == "get_ev_breakdown":
        return _get_ev_breakdown(args)
    if name == "search_rtos":
        return _search_rtos(args)
    if name == "run_sql":
        return _run_sql(args)
    raise ValueError(f"Unknown tool: {name}")

# ── Tool implementations ───────────────────────────────────────────────────────

def _get_vahan_data(args: dict) -> str:
    category   = args["category"]
    item_value = args.get("item_value")
    state      = args.get("state")
    year       = args.get("year")
    month      = args.get("month")
    limit      = int(args.get("limit", 200))

    conditions = ["category = ?"]
    params: list = [category]
    if item_value:
        conditions.append("item_value = ?")
        params.append(item_value)
    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)
    if month:
        conditions.append("month = ?")
        params.append(month)

    where = " AND ".join(conditions)
    query = f"SELECT * FROM vahan_data WHERE {where} ORDER BY year DESC, month DESC LIMIT {limit}"
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _get_top_items(args: dict) -> str:
    category = args["category"]
    state    = args.get("state")
    year     = args.get("year")
    limit    = int(args.get("limit", 20))

    conditions = ["category = ?"]
    params: list = [category]
    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT item_value, SUM(count) as total_registrations
        FROM vahan_data
        WHERE {where}
        GROUP BY item_value
        ORDER BY total_registrations DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _search_items(args: dict) -> str:
    category      = args["category"]
    name_contains = args["name_contains"]
    state         = args.get("state")
    year          = args.get("year")
    limit         = int(args.get("limit", 50))

    conditions = ["category = ?", "item_value LIKE ?"]
    params: list = [category, f"%{name_contains}%"]
    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    query = f"""
        SELECT item_value, SUM(count) as total_registrations
        FROM vahan_data
        WHERE {where}
        GROUP BY item_value
        ORDER BY total_registrations DESC
        LIMIT {limit}
    """
    rows = DB.execute(query, params).fetchall()
    return rows_to_text(rows)

def _get_ev_breakdown(args: dict) -> str:
    state    = args.get("state")
    year     = args.get("year")
    group_by = args.get("group_by", "state")

    ev_placeholders = ",".join("?" * len(EV_FUEL_TYPES))
    conditions = [f"category = 'Fuel'", f"item_value IN ({ev_placeholders})"]
    params: list = list(EV_FUEL_TYPES)

    if state:
        conditions.append("state = ?")
        params.append(state)
    if year:
        conditions.append("year = ?")
        params.append(year)

    where = " AND ".join(conditions)
    
    group_col = "state"
    if group_by == "month": group_col = "month"
    elif group_by == "item_value": group_col = "item_value"

    query = f"""
        SELECT {group_col}, SUM(count) as ev_count
        FROM vahan_data
        WHERE {where}
        GROUP BY {group_col}
        ORDER BY ev_count DESC
    """
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
    global DB
    DB = open_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

async def run_http(host: str, port: int):
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
