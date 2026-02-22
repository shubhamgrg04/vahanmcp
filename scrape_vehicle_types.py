"""
VAHAN Vehicle-Type Scraper  (Tabular Summary view)
Source: https://vahan.parivahan.gov.in/vahan4dashboard/

Scrapes the cross-tab of:
  Vehicle Class × Calendar Year  (registrations by vehicle type per year)
  Vehicle Class × Fuel            (registrations by vehicle type per fuel type)

Filters iterated:
  - Vehicle Category Group (2W, 3W, 4W, GV, PS, CE, AH, SC, TL, TR)
  - State / All India

Output: data/
  - vehicle_class_by_year.csv
  - vehicle_class_by_fuel.csv
"""

import argparse
import csv
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, Error as PlaywrightError

BASE_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/dashboardview.xhtml"
OUT_DIR  = Path("data")
OUT_DIR.mkdir(exist_ok=True)

PAGE_LOAD_TIMEOUT = 120_000
AJAX_TIMEOUT      = 20_000

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

# Vehicle Category Groups  (value: display_name)
VH_GROUPS = [
    ("2W|1,2,3,4,5,51,52,53|2WIC,2WN,2WT",                   "TWO WHEELER"),
    ("3W|6,54,55,57,58|3WN,3WT",                               "THREE WHEELER"),
    ("4W|5,7,15,23,31,56,70,71,93|4WIC,LMV,MMV,HMV",          "FOUR WHEELER"),
    ("AH|76,77,85|2WT,LPV,MPV,HPV,LGV,MGV,HGV",               "AMBULANCE/HEARSES"),
    ("CE|22,25,26,29,87,88,92|LMV,MMV,HMV,LGV,MGV,HGV,OTH",  "CONSTRUCTION EQUIPMENT"),
    ("GV|59,64,79,84|LGV,MGV,HGV",                             "GOODS VEHICLES"),
    ("PS|69,73,75,78,86|LPV,MPV,HPV",                          "PUBLIC SERVICE VEHICLE"),
    ("SC|8,9,10,11,12,14,17,18,19,20,21,24,27,32,62,65,66,67,68,80,81,83|LMV,MMV,HMV,LGV,MGV,HGV,OTH",
                                                                "SPECIAL CATEGORY"),
    ("TL|16,28,30,82,89,91,94|LMV,HMV,MMV,LGV,MGV,HGV",       "TRAILER"),
    ("TR|13,63,90|LMV,MMV,LGV,MGV,HGV,OTH",                   "TRACTOR"),
]

# Years available on the dashboard
YEARS = ["2026","2025","2024","2023","2022","2021","2020","2019","2018","2017",
         "2016","2015","2014","2013","2012","2011","2010","2009","2008","2007",
         "2006","2005","2004","2003"]


def pf_change(page, comp_id: str, value: str, _update_str: str = ""):
    """
    Set a PrimeFaces SelectOneMenu value and fire its inline onchange handler.

    Uses eval(onchange) so PrimeFaces processes the real AJAX with the correct
    component-level execute parameter — avoiding JSF ViewState stale-read issues.

    If the onchange triggers a full-page navigation (JSF redirect), the evaluate
    call raises 'Execution context was destroyed'. We catch that and fall through
    to wait_for_load_state so the new page settles before we continue.
    """
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
        # Navigation triggered mid-eval — that's fine, fall through to wait below
    time.sleep(0.3)  # give PrimeFaces time to dispatch HTTP request before networkidle check
    try:
        page.wait_for_load_state("networkidle", timeout=AJAX_TIMEOUT)
    except PlaywrightTimeout:
        time.sleep(1)


def set_year_checkboxes(page, years: list):
    """Check the given years in the yearList checkbox group; uncheck the rest."""
    page.evaluate("""(years) => {
        document.querySelectorAll('input[name="yearList"]').forEach(cb => {
            cb.checked = years.includes(cb.value);
        });
    }""", years)


def click_refresh(page):
    """
    Click the main Refresh button to regenerate combTablePnl.

    Includes xaxisVar and yearList in the execute (p) list so JSF reads the
    current DOM values instead of restoring stale state from ViewState.
    """
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
    time.sleep(0.3)  # give PrimeFaces time to dispatch HTTP request before networkidle check
    try:
        page.wait_for_load_state("networkidle", timeout=AJAX_TIMEOUT)
    except PlaywrightTimeout:
        time.sleep(2)


_VCG_MARKERS = {"2WIC", "2WN", "2WT", "4WIC", "LMV", "MMV", "HMV", "TRACTOR", "E-CART"}


def is_vcg_result(col_names: list) -> bool:
    """Return True if col_names look like VCG breakdown instead of the intended axis."""
    return bool(_VCG_MARKERS.intersection(col_names))


def parse_number(text: str) -> str:
    return text.replace(",", "").strip()


def extract_crosstab(page) -> dict:
    """
    Extract the cross-tab table from combTablePnl.

    The table uses a multi-level header:
      Row 1: S No (rowspan=3) | Vehicle Class (rowspan=3) | group name (colspan=N)
      Row 2: sub-group (colspan=N)  [optional]
      Row 3 (leaf): individual column names (col1, col2, ..., TOTAL)

    Returns:
      {
        "col_names": [...leaf column names...],   # e.g. fuel types or years
        "rows": [[vehicle_class, val1, val2, ...], ...]
      }
    """
    return page.evaluate("""() => {
        const pnl = document.getElementById('combTablePnl');
        if (!pnl) return null;

        // Find the data table (has tbody rows)
        let dataTable = null;
        for (const t of pnl.querySelectorAll('table')) {
            if (t.querySelectorAll('tbody tr').length > 0) { dataTable = t; break; }
        }
        if (!dataTable) return null;

        // Get leaf-level column names: the LAST header row's th elements
        // (these are the individual columns, e.g. fuel types, years, VCG subcategories)
        const headerRows = Array.from(dataTable.querySelectorAll('thead tr'));
        const leafRow = headerRows[headerRows.length - 1];
        const colNames = leafRow
            ? Array.from(leafRow.querySelectorAll('th')).map(th => th.textContent.trim())
            : [];

        // Data rows: each row has [S No, Vehicle Class, val1, val2, ...]
        // We take col idx 1 (Vehicle Class) and everything from idx 2 onward
        const rows = Array.from(dataTable.querySelectorAll('tbody tr')).map(tr => {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim());
            return cells;
        }).filter(r => r.length > 2 && r[1]);  // must have vehicle class

        return { col_names: colNames, rows };
    }""")


def switch_to_tabular_summary(page):
    """Switch the main view to Tabular Summary mode."""
    pf_change(page, "j_idt17", "R",
              "v4chart comparison dashboardContentsPanel mainpagepnl calendaryear "
              "yearWiseRegnDataTable yearWiseTransDataTable yearWiseRevDataTable yearWisePermitDataTable")
    time.sleep(1)


def recover_if_navigated(page, state_code: str):
    """
    If the page navigated away from reportview.xhtml (e.g. due to a JSF redirect
    triggered by a vhGroup change), switch back to Tabular Summary and re-apply
    the current state filter so the next iteration can continue cleanly.

    Returns True if recovery was needed.
    """
    if "reportview.xhtml" in page.url:
        return False
    print(f"    [nav] Page at {page.url[-60:]!r}, re-initializing...")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeout:
        pass
    # Navigate back to dashboardview if we're somewhere unexpected
    if "dashboardview.xhtml" not in page.url and "reportview.xhtml" not in page.url:
        page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        time.sleep(1)
    switch_to_tabular_summary(page)
    pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")
    return True


def configure_axes(page, yaxis: str, xaxis: str):
    """
    Set yaxisVar then xaxisVar.

    NOTE: call this LAST (after state/vhGroup changes) because state changes
    trigger a yaxisVar panel re-render that can reset the server-side xaxisVar.
    """
    pf_change(page, "yaxisVar", yaxis, "xaxisVar")
    pf_change(page, "xaxisVar", xaxis, "multipleYear")
    time.sleep(0.3)


def scrape_vehicle_class_by_year(page, states=None) -> list:
    """
    Scrape Vehicle Class × Calendar Year cross-tab.

    For each (state, vehicle_group) combination:
      - Rows = Vehicle Class
      - Columns = selected calendar years

    Returns flat list of dicts ready for CSV.
    """
    if states is None:
        states = STATES

    all_rows = []
    total = len(states) * len(VH_GROUPS)
    done = 0

    for state_code, state_name in states:
        # Set state filter
        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

        for vhg_value, vhg_name in VH_GROUPS:
            done += 1
            print(f"  [{done}/{total}] {state_name} | {vhg_name}")
            result = None
            available_years = []

            for attempt in range(2):
                try:
                    pf_change(page, "vchgroupTable:selectCatgGrp", vhg_value,
                              "vchgroupTable VhCatg norms fuel VhClass")
                    recover_if_navigated(page, state_code)
                    configure_axes(page, "Vehicle Class", "Calendar Year")
                    available_years = page.evaluate("""() =>
                        Array.from(document.querySelectorAll('input[name="yearList"]'))
                             .map(cb => cb.value)
                    """)
                    if available_years:
                        set_year_checkboxes(page, available_years)
                    click_refresh(page)
                    result = extract_crosstab(page)
                    if result and result["rows"] and is_vcg_result(result["col_names"]):
                        print(f"    [retry] VCG fallback detected, re-applying axes...")
                        configure_axes(page, "Vehicle Class", "Calendar Year")
                        if available_years:
                            set_year_checkboxes(page, available_years)
                        click_refresh(page)
                        result = extract_crosstab(page)
                    break
                except Exception as e:
                    print(f"    [error] attempt {attempt+1}: {str(e)[:80]}")
                    try:
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    except Exception:
                        pass
                    recover_if_navigated(page, state_code)
                    if attempt == 0:
                        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

            if not result or not result["rows"]:
                continue

            # col_names = leaf-level th row (years, fuel types, etc.)
            # data rows: [S No, Vehicle Class, val1, val2, ...]
            col_names = result["col_names"]  # e.g. ['2026','2025',...]

            for row in result["rows"]:
                vehicle_class = row[1] if len(row) > 1 else ""
                if not vehicle_class:
                    continue
                for i, col_name in enumerate(col_names):
                    val_idx = 2 + i  # skip S No (0) and Vehicle Class (1)
                    if val_idx >= len(row):
                        break
                    all_rows.append({
                        "state_code":    state_code,
                        "state_name":    state_name,
                        "vehicle_group": vhg_name,
                        "vehicle_class": vehicle_class,
                        "year":          col_name,
                        "count":         parse_number(row[val_idx]),
                    })

    return all_rows


def scrape_vehicle_class_by_fuel(page, states=None) -> list:
    """
    Scrape Vehicle Class × Fuel cross-tab.

    For each (state, vehicle_group) combination:
      - Rows = Vehicle Class
      - Columns = Fuel types

    Returns flat list of dicts ready for CSV.
    """
    if states is None:
        states = STATES

    all_rows = []
    total = len(states) * len(VH_GROUPS)
    done = 0

    for state_code, state_name in states:
        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

        for vhg_value, vhg_name in VH_GROUPS:
            done += 1
            print(f"  [{done}/{total}] {state_name} | {vhg_name}")
            result = None

            for attempt in range(2):
                try:
                    pf_change(page, "vchgroupTable:selectCatgGrp", vhg_value,
                              "vchgroupTable VhCatg norms fuel VhClass")
                    recover_if_navigated(page, state_code)
                    configure_axes(page, "Vehicle Class", "Fuel")
                    click_refresh(page)
                    result = extract_crosstab(page)
                    if result and result["rows"] and is_vcg_result(result["col_names"]):
                        print(f"    [retry] VCG fallback detected, re-applying axes...")
                        configure_axes(page, "Vehicle Class", "Fuel")
                        click_refresh(page)
                        result = extract_crosstab(page)
                    break
                except Exception as e:
                    print(f"    [error] attempt {attempt+1}: {str(e)[:80]}")
                    try:
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    except Exception:
                        pass
                    recover_if_navigated(page, state_code)
                    if attempt == 0:
                        # Re-set state before retry
                        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

            if not result or not result["rows"]:
                continue

            col_names = result["col_names"]  # fuel types

            for row in result["rows"]:
                vehicle_class = row[1] if len(row) > 1 else ""
                if not vehicle_class:
                    continue
                for i, col_name in enumerate(col_names):
                    val_idx = 2 + i
                    if val_idx >= len(row):
                        break
                    all_rows.append({
                        "state_code":    state_code,
                        "state_name":    state_name,
                        "vehicle_group": vhg_name,
                        "vehicle_class": vehicle_class,
                        "fuel_type":     col_name,
                        "count":         parse_number(row[val_idx]),
                    })

    return all_rows


def scrape_vehicle_class_by_norms(page, states=None) -> list:
    """
    Scrape Vehicle Class × Norms (emission standards) cross-tab.
    """
    if states is None:
        states = STATES

    all_rows = []
    total = len(states) * len(VH_GROUPS)
    done = 0

    for state_code, state_name in states:
        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

        for vhg_value, vhg_name in VH_GROUPS:
            done += 1
            print(f"  [{done}/{total}] {state_name} | {vhg_name}")
            result = None

            for attempt in range(2):
                try:
                    pf_change(page, "vchgroupTable:selectCatgGrp", vhg_value,
                              "vchgroupTable VhCatg norms fuel VhClass")
                    recover_if_navigated(page, state_code)
                    configure_axes(page, "Vehicle Class", "Norms")
                    click_refresh(page)
                    result = extract_crosstab(page)
                    if result and result["rows"] and is_vcg_result(result["col_names"]):
                        print(f"    [retry] VCG fallback detected, re-applying axes...")
                        configure_axes(page, "Vehicle Class", "Norms")
                        click_refresh(page)
                        result = extract_crosstab(page)
                    break
                except Exception as e:
                    print(f"    [error] attempt {attempt+1}: {str(e)[:80]}")
                    try:
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    except Exception:
                        pass
                    recover_if_navigated(page, state_code)
                    if attempt == 0:
                        pf_change(page, "j_idt37", state_code, "selectedRto yaxisVar")

            if not result or not result["rows"]:
                continue

            col_names = result["col_names"]  # emission norms

            for row in result["rows"]:
                vehicle_class = row[1] if len(row) > 1 else ""
                if not vehicle_class:
                    continue
                for i, col_name in enumerate(col_names):
                    val_idx = 2 + i
                    if val_idx >= len(row):
                        break
                    all_rows.append({
                        "state_code":    state_code,
                        "state_name":    state_name,
                        "vehicle_group": vhg_name,
                        "vehicle_class": vehicle_class,
                        "norm":          col_name,
                        "count":         parse_number(row[val_idx]),
                    })

    return all_rows


def write_csv(rows: list, filepath: Path, fieldnames: list):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows):,} rows → {filepath}")


def main():
    parser = argparse.ArgumentParser(description="VAHAN Vehicle-Type Scraper")
    parser.add_argument("--all-india-only", action="store_true",
                        help="Only scrape All India totals (faster, ~10 min)")
    parser.add_argument("--skip-fuel",  action="store_true", help="Skip fuel breakdown")
    parser.add_argument("--skip-norms", action="store_true", help="Skip emission norm breakdown")
    parser.add_argument("--skip-year",  action="store_true", help="Skip year breakdown")
    args = parser.parse_args()

    states = [("-1", "All India")] if args.all_india_only else STATES

    print("Starting vehicle-type scraper...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()

        print("Loading dashboard...")
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        except PlaywrightTimeout:
            page.wait_for_selector("#j_idt17_input", timeout=30000)

        switch_to_tabular_summary(page)

        # ── 1. Vehicle Class × Calendar Year ────────────────────────────────
        if not args.skip_year:
            print("\n[1] Scraping Vehicle Class × Calendar Year...")
            rows = scrape_vehicle_class_by_year(page, states)
            write_csv(rows, OUT_DIR / "vehicle_class_by_year.csv",
                      fieldnames=["state_code","state_name","vehicle_group",
                                  "vehicle_class","year","count"])

        # ── 2. Vehicle Class × Fuel Type ─────────────────────────────────────
        if not args.skip_fuel:
            print("\n[2] Scraping Vehicle Class × Fuel Type...")
            rows = scrape_vehicle_class_by_fuel(page, states)
            write_csv(rows, OUT_DIR / "vehicle_class_by_fuel.csv",
                      fieldnames=["state_code","state_name","vehicle_group",
                                  "vehicle_class","fuel_type","count"])

        # ── 3. Vehicle Class × Emission Norms ───────────────────────────────
        if not args.skip_norms:
            print("\n[3] Scraping Vehicle Class × Emission Norms...")
            rows = scrape_vehicle_class_by_norms(page, states)
            write_csv(rows, OUT_DIR / "vehicle_class_by_norms.csv",
                      fieldnames=["state_code","state_name","vehicle_group",
                                  "vehicle_class","norm","count"])

        browser.close()

    print("\nDone. Output files:")
    for f in sorted(OUT_DIR.glob("vehicle_class_*.csv")):
        print(f"  {f.name:<40} {f.stat().st_size:>10,} bytes")


if __name__ == "__main__":
    main()
