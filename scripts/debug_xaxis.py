"""
Debug v12: Inspect yearList checkboxes and try firing their change events.
"""
import time
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/dashboardview.xhtml"
AJAX_WAIT = 20_000
post_log = []


def wait_net(page, timeout=AJAX_WAIT):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        pass
    time.sleep(0.4)


def pf(page, comp_id, value):
    r = page.evaluate("""({ id, val }) => {
        const sel = document.getElementById(id + '_input');
        if (!sel) return { error: 'not found: ' + id };
        sel.value = val;
        const h = sel.getAttribute('onchange');
        if (h) eval(h);
        return { ok: true, value: sel.value };
    }""", {"id": comp_id, "val": value})
    time.sleep(0.3)
    wait_net(page)
    return r


def refresh(page):
    page.evaluate("""() => {
        PrimeFaces.ab({
            s:"j_idt67", f:"masterLayout_formlogin", p:"@form",
            u:"VhCatg norms fuel VhClass combTablePnl groupingTable msg vhCatgPnl"
        });
    }""")
    time.sleep(0.3)
    wait_net(page)


def get_leaf_cols(page):
    return page.evaluate("""() => {
        const pnl = document.getElementById('combTablePnl');
        if (!pnl) return ['no combTablePnl'];
        let t = null;
        for (const tbl of pnl.querySelectorAll('table')) {
            if (tbl.querySelectorAll('tbody tr').length > 0) { t = tbl; break; }
        }
        if (!t) return ['no table'];
        const hrows = Array.from(t.querySelectorAll('thead tr'));
        const leafCols = Array.from(hrows[hrows.length-1].querySelectorAll('th')).map(th=>th.textContent.trim());
        return leafCols;
    }""")


def log_posts(tag=""):
    print(f"  [{tag}] {len(post_log)} POST(s)")
    for req in post_log:
        parsed = parse_qs(req['data'])
        xi = parsed.get('xaxisVar_input', ['n/a'])
        yl = parsed.get('yearList', ['n/a'])[:5]
        print(f"    xaxisVar_input={xi}, yearList={yl}")
    post_log.clear()


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
        ).new_page()

        page.on("request", lambda req: (
            post_log.append({"url": req.url[-50:], "data": req.post_data or ""})
            if req.method == "POST" and "vahan4dashboard" in req.url else None
        ))

        print("Loading and navigating to reportview...")
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=120_000)
        except PlaywrightTimeout:
            page.wait_for_selector("#j_idt17_input", timeout=30_000)
        time.sleep(2)
        post_log.clear()

        pf(page, "j_idt17", "R")
        time.sleep(2)
        post_log.clear()

        pf(page, "vchgroupTable:selectCatgGrp", "2W|1,2,3,4,5,51,52,53|2WIC,2WN,2WT")
        time.sleep(4)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:
            pass
        time.sleep(2)
        post_log.clear()
        print(f"On: {page.url[-50:]}")

        pf(page, "yaxisVar", "Vehicle Class")
        post_log.clear()

        # ── Inspect yearList checkbox structure ───────────────────────────────
        print("\n=== Inspecting yearList DOM structure ===")
        pf(page, "xaxisVar", "Financial Year")
        post_log.clear()

        cb_info = page.evaluate("""() => {
            const cbs = Array.from(document.querySelectorAll('input[name="yearList"]'));
            return cbs.slice(0, 5).map(cb => ({
                id: cb.id,
                name: cb.name,
                value: cb.value,
                type: cb.type,
                checked: cb.checked,
                onchange: cb.getAttribute('onchange'),
                onclick: cb.getAttribute('onclick'),
                disabled: cb.disabled,
                // Check for PrimeFaces hidden companion input
                companion: document.getElementById(cb.id + '_hidden') ?
                    document.getElementById(cb.id + '_hidden').value : 'n/a'
            }));
        }""")
        print("yearList checkbox attributes (first 5):")
        for cb in cb_info:
            print(f"  {cb}")

        # Check if there's hidden input for unchecked state
        hidden_inputs = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('input[type="hidden"]'))
                .filter(el => el.name && el.name.includes('yearList'))
                .map(el => ({ id: el.id, name: el.name, value: el.value }));
        }""")
        print(f"Hidden inputs for yearList: {hidden_inputs}")

        # ── Test A: Check boxes by setting .checked AND firing click event ────
        print("\n=== TEST A: Set checked + fire click event ===")
        post_log.clear()
        click_result = page.evaluate("""() => {
            const cbs = Array.from(document.querySelectorAll('input[name="yearList"]'));
            cbs.forEach(cb => {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb.dispatchEvent(new Event('click', { bubbles: true }));
            });
            return { count: cbs.length, checked: cbs.filter(c=>c.checked).length };
        }""")
        time.sleep(0.3)
        wait_net(page)
        print(f"  Click result: {click_result}")
        log_posts("after checkbox clicks")

        post_log.clear()
        refresh(page)
        log_posts("Refresh after click events")
        print(f"  Leaf cols: {get_leaf_cols(page)[:8]}")

        # ── Test B: Fire individual click() per checkbox (real DOM click) ─────
        print("\n=== TEST B: Fire real DOM click() per checkbox ===")
        pf(page, "xaxisVar", "Financial Year")
        post_log.clear()

        # Fire real click on first 3 checkboxes
        click_result2 = page.evaluate("""() => {
            const cbs = Array.from(document.querySelectorAll('input[name="yearList"]')).slice(0,3);
            const results = [];
            cbs.forEach(cb => {
                cb.click();  // real DOM click
                results.push({ v: cb.value, checked: cb.checked });
            });
            return results;
        }""")
        time.sleep(0.3)
        wait_net(page)
        print(f"  Click results: {click_result2}")
        log_posts("after DOM clicks")

        post_log.clear()
        refresh(page)
        log_posts("Refresh")
        print(f"  Leaf cols: {get_leaf_cols(page)[:8]}")

        # ── Test C: Use jQuery to trigger checkbox change via PrimeFaces API ──
        print("\n=== TEST C: Fire jQuery change event on checkboxes ===")
        pf(page, "xaxisVar", "Financial Year")
        post_log.clear()

        jq_result = page.evaluate("""() => {
            const cbs = Array.from(document.querySelectorAll('input[name="yearList"]'));
            cbs.forEach(cb => {
                cb.checked = true;
                $(cb).trigger('change');
            });
            return { count: cbs.length };
        }""")
        time.sleep(0.3)
        wait_net(page)
        print(f"  jQuery trigger result: {jq_result}")
        log_posts("after jQuery trigger")

        post_log.clear()
        refresh(page)
        log_posts("Refresh")
        print(f"  Leaf cols: {get_leaf_cols(page)[:8]}")

        # ── Test D: Inspect if j_idt72 (second Refresh) works differently ─────
        print("\n=== TEST D: Use j_idt72 (second Refresh button) ===")
        pf(page, "xaxisVar", "Financial Year")
        post_log.clear()

        page.evaluate("""() => {
            document.querySelectorAll('input[name="yearList"]').forEach(cb => { cb.checked = true; });
        }""")

        # Use j_idt72 instead of j_idt67
        page.evaluate("""() => {
            PrimeFaces.ab({
                s:"j_idt72", f:"masterLayout_formlogin", p:"@form",
                u:"combTablePnl"
            });
        }""")
        time.sleep(0.3)
        wait_net(page)
        log_posts("j_idt72 Refresh")
        print(f"  Leaf cols: {get_leaf_cols(page)[:8]}")

        # ── Test E: What does the initial reportview table look like by default?
        print("\n=== TEST E: Default table (no changes, what does reportview show?) ===")
        # Navigate fresh to see default state
        pf(page, "xaxisVar", "VCG")  # reset
        post_log.clear()
        refresh(page)
        print(f"  VCG Leaf cols: {get_leaf_cols(page)[:8]}")

        # Now set FY but DON'T click Refresh — what does the current table show?
        pf(page, "xaxisVar", "Financial Year")
        post_log.clear()
        page.evaluate("""() => {
            document.querySelectorAll('input[name="yearList"]').forEach(cb => { cb.checked = true; });
        }""")
        print(f"  After FY xaxis set (before Refresh): {get_leaf_cols(page)[:8]}")

        browser.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
