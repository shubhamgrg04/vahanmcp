"""
VAHAN Cross-Tab Scraper  (Tabular Summary — XLSX download)
Source: https://vahan.parivahan.gov.in/vahan4dashboard/

Generalized scraper that can download any Y-axis × X-axis combination
from the VAHAN dashboard's Tabular Summary view.

Available axes:
  Y-axis: Vehicle Category, Vehicle Class, Norms, Fuel, Maker, State
  X-axis: Vehicle Category, Norms, Fuel, Vehicle Category Group,
          Financial Year, Calendar Year, Month Wise

Usage:
  # Maker × Fuel (All India, years 2024-2025)
  python scrape_crosstab.py --yaxis Maker --xaxis Fuel --all-india-only --years 2025 2024

  # Fuel × Calendar Year (all states)
  python scrape_crosstab.py --yaxis Fuel --xaxis "Calendar Year" --years 2025

  # State × Fuel
  python scrape_crosstab.py --yaxis State --xaxis Fuel --all-india-only --years 2025

Output: data/<yaxis>_by_<xaxis>.csv
"""

import argparse
import csv
import os
import tempfile
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeout,
    Error as PlaywrightError,
)

BASE_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/dashboardview.xhtml"
OUT_DIR  = Path("data")
OUT_DIR.mkdir(exist_ok=True)

PAGE_LOAD_TIMEOUT = 180_000
AJAX_TIMEOUT      = 30_000

STATES = [
    ("-1", "All India"),
    ("AN", "Andaman & Nicobar Island"),
    ("AP", "Andhra Pradesh"),
    ("AR", "Arunachal Pradesh"),
    ("AS", "Assam"),
    ("BR", "Bihar"),
    ("CG", "Chhattisgarh"),
    ("CH", "Chandigarh"),
    ("DD", "UT of DNH and DD"),
    ("DL", "Delhi"),
    ("GA", "Goa"),
    ("GJ", "Gujarat"),
    ("HP", "Himachal Pradesh"),
    ("HR", "Haryana"),
    ("JH", "Jharkhand"),
    ("JK", "Jammu and Kashmir"),
    ("KA", "Karnataka"),
    ("KL", "Kerala"),
    ("LA", "Ladakh"),
    ("LD", "Lakshadweep"),
    ("MH", "Maharashtra"),
    ("ML", "Meghalaya"),
    ("MN", "Manipur"),
    ("MP", "Madhya Pradesh"),
    ("MZ", "Mizoram"),
    ("NL", "Nagaland"),
    ("OR", "Odisha"),
    ("PB", "Punjab"),
    ("PY", "Puducherry"),
    ("RJ", "Rajasthan"),
    ("SK", "Sikkim"),
    ("TN", "Tamil Nadu"),
    ("TR", "Tripura"),
    ("UK", "Uttarakhand"),
    ("UP", "Uttar Pradesh"),
    ("WB", "West Bengal"),
]

# Default vehicle group (needed to generate the cross-tab table)
DEFAULT_VH_GROUP = "2W|1,2,3,4,5,51,52,53|2WIC,2WN,2WT"

# Valid axis values
VALID_Y_AXES = ["Vehicle Category", "Vehicle Class", "Norms", "Fuel", "Maker", "State"]
VALID_X_AXES = [
    "Vehicle Category", "Norms", "Fuel", "Vehicle Category Group",
    "Financial Year", "Calendar Year", "Month Wise",
]


# ── PrimeFaces helpers ──────────────────────────────────────────────────────

def pf_change(page, comp_id: str, value: str, _update_str: str = ""):
    """Set a PrimeFaces SelectOneMenu value and fire its inline onchange."""
    try:
        page.evaluate("""({ selId, val }) => {
            const sel = document.getElementById(selId + '_input');
            if (!sel) return;
            sel.value = val;
            const handler = sel.getAttribute('onchange');
            if (handler) { try { eval(handler); } catch(e) {} }
        }""", {"selId": comp_id, "val": value})
    except PlaywrightError as e:
        if "context was destroyed" not in str(e) and "Execution context" not in str(e):
            raise
    time.sleep(0.3)
    try:
        page.wait_for_load_state("networkidle", timeout=AJAX_TIMEOUT)
    except PlaywrightTimeout:
        time.sleep(1)


def configure_axes(page, yaxis: str, xaxis: str):
    """Set Y-axis and X-axis via pf_change."""
    pf_change(page, "yaxisVar", yaxis, "xaxisVar")
    pf_change(page, "xaxisVar", xaxis, "multipleYear")
    time.sleep(0.3)


def set_year_checkboxes(page, years: list):
    """Check the given years in the yearList checkbox group; uncheck the rest."""
    page.evaluate("""(years) => {
        document.querySelectorAll('input[name="yearList"]').forEach(cb => {
            cb.checked = years.includes(cb.value);
        });
    }""", years)


def click_refresh(page):
    """Click refresh and wait for table to load."""
    page.evaluate("""() => {
        PrimeFaces.ab({
            s: "j_idt67",
            f: "masterLayout_formlogin",
            p: "@form",
            u: "VhCatg norms fuel VhClass combTablePnl groupingTable msg vhCatgPnl",
            onst: function(cfg){ PF('blockpnlCombTable').show(); },
            onco: function(xhr,status,args,data){ PF('blockpnlCombTable').hide(); }
        });
    }""")
    time.sleep(0.3)
    try:
        page.wait_for_load_state("networkidle", timeout=AJAX_TIMEOUT)
    except PlaywrightTimeout:
        time.sleep(2)


def switch_to_tabular_summary(page):
    """Switch the main view to Tabular Summary mode."""
    pf_change(page, "j_idt17", "R",
              "v4chart comparison dashboardContentsPanel mainpagepnl calendaryear "
              "yearWiseRegnDataTable yearWiseTransDataTable yearWiseRevDataTable yearWisePermitDataTable")
    time.sleep(1)


def recover_if_navigated(page, state_code: str):
    """If page navigated away, switch back to Tabular Summary."""
    if "reportview.xhtml" in page.url:
        return False
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeout:
        pass
    if "dashboardview.xhtml" not in page.url and "reportview.xhtml" not in page.url:
        page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        time.sleep(1)
    switch_to_tabular_summary(page)
    pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")
    return True


def reinit_session(page, state_code: str):
    """Full page reload to reset JSF viewstate."""
    print("    [reinit] Full page reload...")
    try:
        page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
    except PlaywrightTimeout:
        pass
    time.sleep(1)
    switch_to_tabular_summary(page)
    pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")


# ── Download helpers ────────────────────────────────────────────────────────

def trigger_xlsx_download(page) -> str | None:
    """Click the XLSX export button and capture the downloaded file."""
    # Wait for any pending AJAX to settle
    time.sleep(2)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass

    tmp_dir = tempfile.mkdtemp(prefix="vahan_dl_")
    try:
        with page.expect_download(timeout=180_000) as download_info:
            page.evaluate("""() => {
                var btn = document.getElementById('vchgroupTable:xls');
                if (btn) { btn.click(); return; }
                btn = document.getElementById('groupingTable:xls');
                if (btn) btn.click();
            }""")
        download = download_info.value
        save_path = os.path.join(tmp_dir, download.suggested_filename or "export.xlsx")
        download.save_as(save_path)
        return save_path
    except PlaywrightTimeout:
        print("    [warn] Download timed out")
        return None
    except Exception as e:
        print(f"    [warn] Download failed: {str(e)[:80]}")
        return None


def parse_xlsx(filepath: str, yaxis: str) -> pd.DataFrame:
    """Parse a Vahan XLSX export file into a clean DataFrame."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(filepath, engine="openpyxl", header=None)

    if df.empty:
        return pd.DataFrame()

    # Find header row — contains "S No" or the Y-axis name
    header_idx = 0
    for i in range(min(5, len(df))):
        row_vals = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if any("S No" in v or yaxis in v for v in row_vals):
            header_idx = i
            break

    # Build DataFrame with proper headers
    headers = [str(v).strip() if pd.notna(v) else f"col_{i}"
               for i, v in enumerate(df.iloc[header_idx].values)]
    data = df.iloc[header_idx + 1:].copy()
    data.columns = headers[:len(data.columns)]
    data = data.reset_index(drop=True)

    # Remove empty/total rows
    y_col = yaxis
    if y_col in data.columns:
        data = data[data[y_col].notna() & (data[y_col].astype(str).str.strip() != "")]

    return data


# ── Main scraping ───────────────────────────────────────────────────────────

def scrape_crosstab(page, yaxis: str, xaxis: str, states=None, years=None) -> list:
    """
    Download XLSX for every (state, year) and parse into flat rows.

    Each XLSX contains a cross-tab of yaxis × xaxis values.
    Output: list of dicts with keys [state_code, state_name, <yaxis>, <column>, value].
    """
    if states is None:
        states = STATES
    if years is None:
        years = ["2025", "2024", "2023"]

    all_rows = []
    total = len(states) * len(years)
    done = 0

    for state_code, state_name in states:
        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

        for year in years:
            done += 1
            print(f"  [{done}/{total}] {state_name} | {year}")

            for attempt in range(3):
                try:
                    # Set axes
                    configure_axes(page, yaxis, xaxis)

                    # Set vehicle group (needed to generate table)
                    pf_change(page, "vchgroupTable:selectCatgGrp", DEFAULT_VH_GROUP,
                              "vchgroupTable VhCatg norms fuel VhClass")
                    recover_if_navigated(page, state_code)

                    # Re-apply axes after vhGroup change
                    configure_axes(page, yaxis, xaxis)

                    # Select year
                    set_year_checkboxes(page, [year])

                    # Refresh table
                    click_refresh(page)
                    time.sleep(1)

                    # Download XLSX
                    filepath = trigger_xlsx_download(page)
                    if not filepath:
                        if attempt < 2:
                            print(f"    [attempt {attempt+1}] download failed, retrying...")
                            if attempt == 1:
                                reinit_session(page, state_code)
                        continue

                    # Parse
                    df = parse_xlsx(filepath, yaxis)

                    # Cleanup temp file
                    try:
                        os.unlink(filepath)
                        os.rmdir(os.path.dirname(filepath))
                    except Exception:
                        pass

                    if df.empty:
                        print(f"    [warn] empty XLSX")
                        continue

                    # Identify Y-column and data columns
                    y_col = yaxis
                    if y_col not in df.columns:
                        # Fallback: second column is usually the Y-axis
                        non_sno = [c for c in df.columns if c != "S No" and not c.startswith("col_")]
                        y_col = non_sno[0] if non_sno else df.columns[1]

                    skip_cols = {"S No", y_col, "TOTAL", ""}
                    data_cols = [c for c in df.columns if c not in skip_cols
                                 and not c.startswith("col_")]

                    print(f"    ✓ {len(df)} rows, Y={y_col}, X-cols: {data_cols[:6]}{'...' if len(data_cols) > 6 else ''}")

                    for _, row in df.iterrows():
                        y_value = str(row.get(y_col, "")).strip()
                        if not y_value or y_value.upper() == "TOTAL":
                            continue

                        # One output row per data column
                        for col in data_cols:
                            val = row.get(col, 0)
                            if pd.isna(val):
                                val = 0
                            try:
                                val = int(float(str(val).replace(",", "")))
                            except (ValueError, TypeError):
                                val = 0

                            all_rows.append({
                                "state_code":  state_code,
                                "state_name":  state_name,
                                "y_value":     y_value,
                                "year":        year,
                                "x_column":    col,
                                "count":       val,
                            })

                    break  # success

                except Exception as e:
                    print(f"    [error] attempt {attempt+1}: {str(e)[:80]}")
                    try:
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    except Exception:
                        pass
                    recover_if_navigated(page, state_code)
                    pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

    return all_rows


# ── Utilities ────────────────────────────────────────────────────────────────

def write_csv(rows: list, filepath: Path, yaxis: str):
    if not rows:
        print(f"  No rows to write for {filepath.name}")
        return
    fieldnames = ["state_code", "state_name", yaxis.lower().replace(" ", "_"),
                  "year", "x_column", "count"]
    # Rename y_value to the actual axis name
    for row in rows:
        row[yaxis.lower().replace(" ", "_")] = row.pop("y_value")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows):,} rows → {filepath}")


def make_output_name(yaxis: str, xaxis: str) -> str:
    """Generate output filename from axis names."""
    y = yaxis.lower().replace(" ", "_")
    x = xaxis.lower().replace(" ", "_")
    return f"{y}_by_{x}.csv"


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VAHAN Cross-Tab Scraper (XLSX Download)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --yaxis Maker --xaxis Fuel --all-india-only --years 2025
  %(prog)s --yaxis Fuel --xaxis "Calendar Year" --years 2025 2024
  %(prog)s --yaxis State --xaxis Fuel --all-india-only --years 2025
  %(prog)s --yaxis Maker --xaxis Norms --years 2025 2024 2023
""")
    parser.add_argument("--yaxis", required=True, choices=VALID_Y_AXES,
                        help="Y-axis (rows) for the cross-tab")
    parser.add_argument("--xaxis", required=True, choices=VALID_X_AXES,
                        help="X-axis (columns) for the cross-tab")
    parser.add_argument("--all-india-only", action="store_true",
                        help="Only scrape All India totals")
    parser.add_argument("--years", nargs="+", default=["2025", "2024", "2023"],
                        help="Years to scrape (default: 2025 2024 2023)")
    args = parser.parse_args()

    states = [("-1", "All India")] if args.all_india_only else STATES
    out_file = OUT_DIR / make_output_name(args.yaxis, args.xaxis)

    print(f"Scraping {args.yaxis} × {args.xaxis}")
    print(f"States: {len(states)}, Years: {args.years}")
    print(f"Output: {out_file}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
        )
        page = context.new_page()

        print("Loading dashboard...")
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        except PlaywrightTimeout:
            # networkidle can be flaky; check if page actually loaded
            try:
                page.wait_for_selector("#j_idt17_input", timeout=10_000)
            except PlaywrightTimeout:
                print("ERROR: Could not load Vahan dashboard (site may be down)")
                browser.close()
                return
        time.sleep(2)

        switch_to_tabular_summary(page)

        rows = scrape_crosstab(page, args.yaxis, args.xaxis, states, args.years)
        write_csv(rows, out_file, args.yaxis)

        browser.close()

    print("\nDone.")
    if out_file.exists():
        print(f"  {out_file.name:<45} {out_file.stat().st_size:>10,} bytes")


if __name__ == "__main__":
    main()
