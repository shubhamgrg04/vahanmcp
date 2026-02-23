"""
VAHAN Maker/Brand Scraper  (Tabular Summary view — XLSX download mode)
Source: https://vahan.parivahan.gov.in/vahan4dashboard/

Scrapes brand/manufacturer-wise registration data by downloading XLSX exports
from the Vahan dashboard.

The download provides:
  - All makers (manufacturers) with registration counts
  - One download per (state, year) combination
  - Data includes VCG (Vehicle Category Group) breakdown columns

Output: data/
  - maker_registrations.csv  (Maker × Year total registrations)
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

PAGE_LOAD_TIMEOUT = 120_000
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

# Vehicle Category Groups — used as the first vhGroup setting to generate the table
DEFAULT_VH_GROUP = "2W|1,2,3,4,5,51,52,53|2WIC,2WN,2WT"


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
    """Set Y-axis and X-axis via pf_change (hidden input + onchange)."""
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
    print(f"    [nav] Page at {page.url[-60:]!r}, re-initializing...")
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
    print("    [reinit] Full page reload to reset JSF state...")
    try:
        page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
    except PlaywrightTimeout:
        page.wait_for_selector("#j_idt17_input", timeout=30_000)
    time.sleep(1)
    switch_to_tabular_summary(page)
    pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")


# ── Download helpers ────────────────────────────────────────────────────────

def trigger_xlsx_download(page) -> str | None:
    """
    Click the XLSX export button and capture the downloaded file.
    Returns path to downloaded temp file, or None on failure.
    """
    tmp_dir = tempfile.mkdtemp(prefix="vahan_dl_")
    try:
        with page.expect_download(timeout=60_000) as download_info:
            page.evaluate("""() => {
                const btn = document.getElementById('groupingTable:xls')
                          || document.getElementById('vchgroupTable:xls');
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


def parse_xlsx(filepath: str) -> pd.DataFrame:
    """
    Parse a Vahan XLSX export file.

    The XLSX structure:
      Row 0: Report title (e.g. "Maker Wise Vehicle Category Group Data For All State (2025)")
      Row 1: Column headers (S No, Maker, <column names...>, TOTAL)
      Row 2+: Data rows
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(filepath, engine="openpyxl", header=None)

    if df.empty:
        return pd.DataFrame()

    # Find header row — contains "S No" or "Maker"
    header_idx = 0
    for i in range(min(5, len(df))):
        row_vals = [str(v).strip() for v in df.iloc[i].values if pd.notna(v)]
        if any("S No" in v or "Maker" in v for v in row_vals):
            header_idx = i
            break

    # Get title from row before header
    title = ""
    if header_idx > 0:
        title_vals = [str(v).strip() for v in df.iloc[0].values if pd.notna(v)]
        title = " ".join(title_vals)

    # Set up proper DataFrame
    headers = [str(v).strip() if pd.notna(v) else f"col_{i}"
               for i, v in enumerate(df.iloc[header_idx].values)]
    data = df.iloc[header_idx + 1:].copy()
    data.columns = headers[:len(data.columns)]
    data = data.reset_index(drop=True)

    # Clean up: remove rows where Maker is empty/NaN
    if "Maker" in data.columns:
        data = data[data["Maker"].notna() & (data["Maker"].astype(str).str.strip() != "")]

    return data


# ── Scraping function ───────────────────────────────────────────────────────

def scrape_maker_data(page, states=None, years=None) -> list:
    """
    Scrape maker/brand registration data via XLSX download.

    Downloads one XLSX per (state, year) combination.
    Each XLSX contains all makers with their registration counts.

    Returns flat list of dicts ready for CSV.
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
                    # Configure for Maker Y-axis
                    configure_axes(page, "Maker", "Month Wise")

                    # Set a vhGroup to generate the table
                    pf_change(page, "vchgroupTable:selectCatgGrp", DEFAULT_VH_GROUP,
                              "vchgroupTable VhCatg norms fuel VhClass")
                    recover_if_navigated(page, state_code)

                    # Re-apply axes after vhGroup change
                    configure_axes(page, "Maker", "Month Wise")

                    # Select year
                    set_year_checkboxes(page, [year])

                    # Refresh the table
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
                    df = parse_xlsx(filepath)

                    # Cleanup temp file
                    try:
                        os.unlink(filepath)
                        os.rmdir(os.path.dirname(filepath))
                    except Exception:
                        pass

                    if df.empty:
                        print(f"    [warn] empty XLSX")
                        continue

                    # Identify data columns (everything except S No, Maker, TOTAL)
                    skip_cols = {"S No", "Maker", "TOTAL", ""}
                    data_cols = [c for c in df.columns if c not in skip_cols
                                 and not c.startswith("col_")]

                    print(f"    ✓ {len(df)} makers, columns: {data_cols[:6]}{'...' if len(data_cols) > 6 else ''}")

                    for _, row in df.iterrows():
                        maker = str(row.get("Maker", "")).strip()
                        if not maker or maker.upper() == "TOTAL":
                            continue

                        # Get the registration count:
                        # - If we have month columns (JAN, FEB, ...), sum them
                        # - Otherwise use the first data column (usually "Vehicle Category Group")
                        count = 0
                        if data_cols:
                            for col in data_cols:
                                val = row.get(col, 0)
                                if pd.notna(val):
                                    try:
                                        count += int(float(str(val).replace(",", "")))
                                    except (ValueError, TypeError):
                                        pass

                        # Fallback to TOTAL column
                        if count == 0 and "TOTAL" in df.columns:
                            total_val = row.get("TOTAL", 0)
                            if pd.notna(total_val):
                                try:
                                    count = int(float(str(total_val).replace(",", "")))
                                except (ValueError, TypeError):
                                    pass

                        all_rows.append({
                            "state_code":  state_code,
                            "state_name":  state_name,
                            "maker":       maker,
                            "year":        year,
                            "count":       count,
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

def write_csv(rows: list, filepath: Path, fieldnames: list):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows):,} rows → {filepath}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VAHAN Maker/Brand Scraper (XLSX Download)")
    parser.add_argument("--all-india-only", action="store_true",
                        help="Only scrape All India totals (faster)")
    parser.add_argument("--years", nargs="+", default=["2025", "2024", "2023"],
                        help="Years to scrape (default: 2025 2024 2023)")
    args = parser.parse_args()

    states = [("-1", "All India")] if args.all_india_only else STATES

    print("Starting maker/brand scraper (XLSX download mode)...")
    print(f"States: {len(states)}, Years: {args.years}")

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
            page.wait_for_selector("#j_idt17_input", timeout=30000)

        switch_to_tabular_summary(page)

        # Scrape maker data
        print(f"\nScraping Maker registrations ({args.years})...")
        rows = scrape_maker_data(page, states, args.years)
        write_csv(rows, OUT_DIR / "maker_registrations.csv",
                  fieldnames=["state_code", "state_name", "maker",
                              "year", "count"])

        browser.close()

    print("\nDone. Output files:")
    for f in sorted(OUT_DIR.glob("maker_*.csv")):
        print(f"  {f.name:<45} {f.stat().st_size:>10,} bytes")


if __name__ == "__main__":
    main()
