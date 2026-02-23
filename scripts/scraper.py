"""
VAHAN Dashboard Scraper
Source: https://vahan.parivahan.gov.in/vahan4dashboard/

Scrapes:
  - Year-wise Registrations, Transactions, Revenue, Permits for every state/UT
  - Summary statistics (total states, RTOs, fitness centres)
  - Top-5 rankings per metric

Output: data/
  - registrations.csv
  - transactions.csv
  - revenue.csv
  - permits.csv
  - summary_stats.csv
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/dashboardview.xhtml"
OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)

PAGE_LOAD_TIMEOUT = 120_000   # 2 min for initial load
AJAX_TIMEOUT      = 20_000    # 20 s for filter AJAX

# All states as discovered from the live page
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

# PrimeFaces component IDs discovered from live inspection
COMPONENT_IDS = {
    "view":    "j_idt17",   # Main Page View / Summary / Tabular etc.
    "scale":   "j_idt30",   # Thousand / Lakh / Crore / Actual
    "state":   "j_idt43",   # State/UT selector
    "rto":     "selectedRto",
}

# AJAX update targets for each component (from onchange handlers)
UPDATE_PANELS = {
    "view":  "v4chart comparison dashboardContentsPanel mainpagepnl calendaryear "
             "yearWiseRegnDataTable yearWiseTransDataTable yearWiseRevDataTable yearWisePermitDataTable",
    "state": "comparison dashboardContentsPanel mainpagepnl selectedRto "
             "yearWiseRegnDataTable yearWiseTransDataTable yearWiseRevDataTable yearWisePermitDataTable",
    "rto":   "comparison dashboardContentsPanel mainpagepnl "
             "yearWiseRegnDataTable yearWiseTransDataTable yearWiseRevDataTable yearWisePermitDataTable",
}


def set_filter(page, component_key: str, value: str):
    """Set a PrimeFaces SelectOneMenu value and trigger its AJAX call."""
    comp_id = COMPONENT_IDS[component_key]
    update = UPDATE_PANELS.get(component_key, "mainpagepnl")

    page.evaluate("""({ compId, val }) => {
        const sel = document.getElementById(compId + '_input');
        if (!sel) return;
        sel.value = val;
        // Use eval(onchange) so PrimeFaces sends the correct AJAX with the right parameters.
        const handler = sel.getAttribute('onchange');
        if (handler) { try { eval(handler); } catch(e) {} }
    }""", {"compId": comp_id, "val": value})

    # Give PrimeFaces time to dispatch the HTTP request before checking networkidle.
    # Without this sleep, networkidle fires before the AJAX is actually dispatched
    # (PrimeFaces queues AJAX internally), causing stale data to be read.
    time.sleep(0.3)
    try:
        page.wait_for_load_state("networkidle", timeout=AJAX_TIMEOUT)
    except PlaywrightTimeout:
        time.sleep(2)  # fallback wait


def parse_number(text: str) -> str:
    """Strip formatting from numbers (commas). Keep as string to preserve precision."""
    return text.replace(",", "").strip()


def extract_tables(page) -> dict:
    """
    Extract all data tables from the current dashboard state.

    Returns a dict:
      {
        "registrations": [{"year": ..., "count": ..., "growth_pct": ...}, ...],
        "transactions":  [...],
        "revenue":       [...],
        "permits":       [...],
      }
    """
    raw = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('table').forEach(t => {
            const headers = Array.from(t.querySelectorAll('th')).map(th => th.textContent.trim());
            const rows = Array.from(t.querySelectorAll('tbody tr')).map(tr =>
                Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim())
            ).filter(r => r.length > 0);
            if (rows.length > 0) results.push({ headers, rows });
        });
        return results;
    }""")

    # The 4 data tables appear in order: registrations, transactions, revenue, permits
    metric_names = ["registrations", "transactions", "revenue", "permits"]
    data_tables = [t for t in raw if t["headers"] == ["Year", "Count", "% Growth"]]

    result = {}
    for i, name in enumerate(metric_names):
        if i < len(data_tables):
            result[name] = [
                {
                    "year":       row[0],
                    "count":      parse_number(row[1]) if len(row) > 1 else "",
                    "growth_pct": parse_number(row[2]) if len(row) > 2 else "",
                }
                for row in data_tables[i]["rows"]
            ]
        else:
            result[name] = []

    return result


def extract_summary_stats(page) -> dict:
    """Extract the top-level summary statistics panel."""
    raw = page.evaluate("""() => {
        const stats = {};
        document.querySelectorAll('table').forEach(t => {
            const headers = Array.from(t.querySelectorAll('th')).map(th => th.textContent.trim());
            if (headers.length === 0) {
                // Try summary table (no headers, just key-value rows)
                const rows = Array.from(t.querySelectorAll('tr')).map(tr =>
                    Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim())
                ).filter(r => r.length === 2);
                rows.forEach(r => { stats[r[0]] = r[1]; });
            }
        });
        return stats;
    }""")
    return raw


def extract_top5(page) -> dict:
    """Extract Top-5 rankings for each metric."""
    return page.evaluate("""() => {
        const result = {};
        document.querySelectorAll('.ui-accordion-content').forEach(panel => {
            const heading = panel.closest('.ui-accordion-tab')
                              ?.querySelector('.ui-accordion-header-text')?.textContent?.trim();
            if (!heading) return;
            const rows = Array.from(panel.querySelectorAll('tr')).map(tr =>
                Array.from(tr.querySelectorAll('td,th')).map(c => c.textContent.trim())
            ).filter(r => r.length > 1);
            if (rows.length) result[heading] = rows;
        });
        return result;
    }""")


def scrape_all_states(page) -> list:
    """
    Iterate over all states, collect year-wise data for all 4 metrics.
    Returns a flat list of dicts ready for CSV.
    """
    rows = []

    for state_code, state_name in STATES:
        print(f"  Fetching state: {state_name} ({state_code})")
        set_filter(page, "state", state_code)

        tables = extract_tables(page)
        for metric, records in tables.items():
            for rec in records:
                rows.append({
                    "state_code":  state_code,
                    "state_name":  state_name,
                    "metric":      metric,
                    "year":        rec["year"],
                    "count":       rec["count"],
                    "growth_pct":  rec["growth_pct"],
                })

    return rows


def write_csv(rows: list, filepath: Path, fieldnames: list):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows)} rows → {filepath}")


def scrape_rto_options(page) -> list:
    """After selecting a state, return the RTO options available."""
    return page.evaluate("""() => {
        const sel = document.getElementById('selectedRto_input');
        if (!sel) return [];
        return Array.from(sel.options).map(o => ({ value: o.value, text: o.text.trim() }));
    }""")


def main():
    parser = argparse.ArgumentParser(description="VAHAN Dashboard Scraper")
    parser.add_argument(
        "--skip-rto", action="store_true",
        help="Skip per-RTO scraping (~1400 AJAX calls, takes 30+ min)",
    )
    args = parser.parse_args()

    print("Starting VAHAN dashboard scraper...")

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
        # Use domcontentloaded first, then wait for key element
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        except PlaywrightTimeout:
            print("  networkidle timed out — waiting for page content...")
            page.wait_for_selector("#j_idt43_input", timeout=30000)
        # Ensure actual values (no scaling)
        set_filter(page, "scale", "A")

        # ── 1. Summary statistics ────────────────────────────────────────────
        print("\n[1/5] Extracting summary statistics...")
        summary = extract_summary_stats(page)
        if summary:
            write_csv(
                [summary],
                OUT_DIR / "summary_stats.csv",
                fieldnames=list(summary.keys()),
            )
        else:
            print("  (no summary stats found)")

        # ── 2. Year-wise data for all states ─────────────────────────────────
        print("\n[2/5] Scraping year-wise data for all states...")
        all_rows = scrape_all_states(page)

        # Split into per-metric CSVs
        for metric in ["registrations", "transactions", "revenue", "permits"]:
            metric_rows = [
                {k: v for k, v in r.items() if k != "metric"}
                for r in all_rows if r["metric"] == metric
            ]
            write_csv(
                metric_rows,
                OUT_DIR / f"{metric}.csv",
                fieldnames=["state_code", "state_name", "year", "count", "growth_pct"],
            )

        # Also write a combined file
        write_csv(
            all_rows,
            OUT_DIR / "all_metrics.csv",
            fieldnames=["state_code", "state_name", "metric", "year", "count", "growth_pct"],
        )

        # ── 3. RTO list + per-RTO year-wise data ─────────────────────────────
        if args.skip_rto:
            print("\n[3/5] Skipping RTO scraping (--skip-rto flag set).")
            # Still collect RTO names without per-RTO data
            print("      Collecting RTO names only...")
            rto_rows = []
            for state_code, state_name in STATES:
                if state_code == "-1":
                    continue
                set_filter(page, "state", state_code)
                time.sleep(0.5)
                rtos = scrape_rto_options(page)
                for rto in rtos:
                    if rto["value"] == "-1":
                        continue
                    rto_rows.append({
                        "state_code": state_code,
                        "state_name": state_name,
                        "rto_code":   rto["value"],
                        "rto_name":   rto["text"],
                    })
            write_csv(rto_rows, OUT_DIR / "rto_list.csv",
                      fieldnames=["state_code", "state_name", "rto_code", "rto_name"])
        else:
            print("\n[3/5] Collecting RTOs and per-RTO year-wise data...")
            rto_rows = []
            rto_metric_rows = []

            for state_code, state_name in STATES:
                if state_code == "-1":
                    continue
                print(f"  State: {state_name}")
                set_filter(page, "state", state_code)
                time.sleep(0.5)  # let RTO dropdown populate

                rtos = scrape_rto_options(page)
                for rto in rtos:
                    if rto["value"] == "-1":
                        continue
                    rto_rows.append({
                        "state_code": state_code,
                        "state_name": state_name,
                        "rto_code":   rto["value"],
                        "rto_name":   rto["text"],
                    })

                # Scrape year-wise data for each RTO in this state
                for rto in rtos:
                    if rto["value"] == "-1":
                        continue
                    rto_code = rto["value"]
                    rto_name = rto["text"]

                    # Select the RTO
                    page.evaluate("""({ compId, val, updateStr }) => {
                        const sel = document.getElementById(compId + '_input');
                        if (!sel) return;
                        sel.value = val;
                        PrimeFaces.ab({ s: compId, e: 'change', f: 'masterLayout_formlogin', p: compId, u: updateStr });
                    }""", {
                        "compId": "selectedRto",
                        "val": rto_code,
                        "updateStr": UPDATE_PANELS["rto"],
                    })
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightTimeout:
                        time.sleep(1)

                    tables = extract_tables(page)
                    for metric, records in tables.items():
                        for rec in records:
                            rto_metric_rows.append({
                                "state_code": state_code,
                                "state_name": state_name,
                                "rto_code":   rto_code,
                                "rto_name":   rto_name,
                                "metric":     metric,
                                "year":       rec["year"],
                                "count":      rec["count"],
                                "growth_pct": rec["growth_pct"],
                            })

                # Reset RTO to "All" before next state
                page.evaluate("""({ compId, val, updateStr }) => {
                    const sel = document.getElementById(compId + '_input');
                    if (sel) { sel.value = val; PrimeFaces.ab({ s: compId, e: 'change', f: 'masterLayout_formlogin', p: compId, u: updateStr }); }
                }""", {"compId": "selectedRto", "val": "-1", "updateStr": UPDATE_PANELS["rto"]})
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeout:
                    pass

            write_csv(
                rto_rows,
                OUT_DIR / "rto_list.csv",
                fieldnames=["state_code", "state_name", "rto_code", "rto_name"],
            )
            write_csv(
                rto_metric_rows,
                OUT_DIR / "rto_metrics.csv",
                fieldnames=["state_code", "state_name", "rto_code", "rto_name",
                            "metric", "year", "count", "growth_pct"],
            )

        # ── 4. (Top-5 rankings are rendered as charts — not in HTML tables) ──
        print("\n[4/5] Skipping Top-5 (chart-only, no table DOM).")

        # ── 5. State metadata ─────────────────────────────────────────────────
        print("\n[5/5] Writing state metadata...")
        write_csv(
            [{"state_code": c, "state_name": n} for c, n in STATES if c != "-1"],
            OUT_DIR / "states.csv",
            fieldnames=["state_code", "state_name"],
        )

        browser.close()

    print("\nDone. Output files:")
    for f in sorted(OUT_DIR.iterdir()):
        size = f.stat().st_size
        print(f"  {f.name:<30} {size:>10,} bytes")


if __name__ == "__main__":
    main()
