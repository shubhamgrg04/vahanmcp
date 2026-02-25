import sys
import os
import argparse
import time
import pandas as pd
from playwright.sync_api import sync_playwright

def select_primefaces_dropdown(page, label_selector, item_text):
    """
    Helper to select an item from a PrimeFaces dropdown.
    """
    print(f"Setting dropdown {label_selector} to '{item_text}'")
    page.wait_for_selector(label_selector)
    page.click(label_selector)
    
    # Wait for the panel's list items to be visible
    # PrimeFaces list items are typically li[id^='dropdown_id_']
    # But we can find them by their text content.
    # We need to wait for the panel to be visible.
    time.sleep(0.5) # Small sleep for animation
    
    # The panel is usually at the end of the body or nearby.
    # We'll look for an li that contains the text and is visible.
    try:
        # Use a locator that finds the li with the exact text
        # PrimeFaces often has the text inside the li or a spans inside it.
        item_locator = page.locator("li").filter(has_text=item_text).filter(has=page.locator("visible=true"))
        
        # If there are multiple, it might be tricky. Let's try to be more specific.
        # But generally, only one panel is open.
        if item_locator.count() == 0:
            # Try partial match or case-insensitive if exact fails?
            # For now, stick to exact text as provided in args.
            print(f"Warning: Item '{item_text}' not found in dropdown. Trying partial match.")
            item_locator = page.locator("li").filter(has_text=item_text)
            
        if item_locator.count() > 0:
            item_locator.first.click()
            # Wait for panel to close
            time.sleep(0.5)
        else:
            print(f"Error: Could not find option '{item_text}' in dropdown.")
    except Exception as e:
        print(f"Exception selecting dropdown: {e}")

def get_all_states(page):
    """
    Returns a list of all state names from the PrimeFaces dropdown.
    """
    print("Fetching list of all states...")
    # The State dropdown label ID can change. Try to find it by text or common IDs.
    label_selectors = ["label#j_idt45_label", "label#j_idt41_label", "label:has-text('Select State')"]
    label_selector = None
    for selector in label_selectors:
        if page.locator(selector).count() > 0:
            label_selector = selector
            break
    
    if not label_selector:
        print("Error: Could not find State dropdown label.")
        return []

    page.click(label_selector)
    time.sleep(1)
    
    # Get all li items in the panel. The panel ID usually ends with _items.
    # We can search for li items that are visible and in a PrimeFaces list.
    states = page.locator("li.ui-selectonemenu-item").all_inner_texts()
    
    # Close the dropdown
    page.click("body")
    time.sleep(0.5)
    
    # Filter out empty or placeholder text if any
    states = [s.strip() for s in states if s.strip() and "Select State" not in s]
    print(f"Found {len(states)} states.")
    return states

def goto_with_retry(page, url, timeout=60000, max_retries=3):
    """
    Navigates to a URL with retry logic and exponential backoff.
    """
    for attempt in range(max_retries):
        try:
            print(f"Navigating to {url} (Attempt {attempt + 1}/{max_retries})...")
            page.goto(url, timeout=timeout)
            page.wait_for_load_state("networkidle", timeout=timeout)
            print("Successfully reached dashboard.")
            return True
        except Exception as e:
            print(f"Navigation attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5
                print(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("Max retries reached. Navigation failed.")
                raise e
    return False

def scrape_vahan(states_to_scrape, x_axes, y_axes, year, output_dir):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("Starting Vahan Dashboard session...")
        goto_with_retry(page, "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml", timeout=90000)

        # If no states provided, fetch all from dropdown
        if not states_to_scrape or "ALL" in [s.upper() for s in states_to_scrape]:
            all_states = get_all_states(page)
            if states_to_scrape and "ALL" in [s.upper() for s in states_to_scrape]:
                states_to_scrape = all_states
            else:
                states_to_scrape = all_states

        # Select Year (Global for all axes)
        select_primefaces_dropdown(page, "label#selectedYear_label", str(year))

        for x_axis in x_axes:
            print(f"\n##########################################")
            print(f"USING X-AXIS: {x_axis}")
            print(f"##########################################")
            
            # Select X-Axis
            select_primefaces_dropdown(page, "label#xaxisVar_label", x_axis)
            
            for y_axis in y_axes:
                if y_axis == x_axis:
                    print(f"Skipping combination where Y-Axis == X-Axis ({y_axis})")
                    continue
                    
                print(f"\n==========================================")
                print(f"PROCESSING Y-AXIS: {y_axis}")
                print(f"==========================================")
                
                # Select Y-Axis
                select_primefaces_dropdown(page, "label#yaxisVar_label", y_axis)

                # List to hold data for all states for this X/Y combination
                all_axes_data = []

                for state in states_to_scrape:
                    print(f"\n--- State: {state} ---")
                    
                    try:
                        # Select State - Use a more robust way to find the state dropdown
                        # It's usually the label associated with "State:"
                        state_label_selectors = ["label#j_idt45_label", "label#j_idt41_label", "div:has-text('State:') + div label"]
                        
                        selected_selector = None
                        for selector in state_label_selectors:
                            if page.locator(selector).count() > 0:
                                selected_selector = selector
                                break
                        
                        if selected_selector:
                            select_primefaces_dropdown(page, selected_selector, state)
                        else:
                            print(f"Warning: Could not find State dropdown for {state}")
                            continue

                        # Refresh - use text-based selector for robustness
                        print("Clicking Refresh...")
                        refresh_button_selectors = ["button#j_idt75", "button#j_idt72", "button:has-text('Refresh')"]
                        refresh_selector = None
                        for selector in refresh_button_selectors:
                            if page.locator(selector).count() > 0:
                                refresh_selector = selector
                                break
                        
                        if refresh_selector:
                            page.click(refresh_selector)
                        else:
                            print(f"Error: Could not find Refresh button for {state}")
                            continue
                        
                        page.wait_for_load_state("networkidle")
                        time.sleep(5)  # Buffer for AJAX table update

                        # Download xlsx
                        print(f"Attempting to download XLSX for {state}...")
                        
                        # The ID of the Excel download button can be dynamic based on the view.
                        download_selectors = ["a[id='groupingTable:xls']", "a[id='vchgroupTable:xls']"]
                        
                        download_element = None
                        for selector in download_selectors:
                            if page.locator(selector).count() > 0:
                                download_element = selector
                                break
                        
                        if not download_element:
                            print(f"Warning: Excel download button not found. Waiting longer...")
                            time.sleep(3)
                            for selector in download_selectors:
                                if page.locator(selector).count() > 0:
                                    download_element = selector
                                    break

                        if download_element:
                            with page.expect_download(timeout=30000) as download_info:
                                page.click(download_element)
                            download = download_info.value
                            
                            # Save temp file
                            temp_xlsx = os.path.join(output_dir, f"temp_{int(time.time())}.xlsx")
                            download.save_as(temp_xlsx)

                            # Process Excel
                            df = pd.read_excel(temp_xlsx, header=None)
                            os.remove(temp_xlsx) # Cleanup temp
                            
                            if len(df) > 4:
                                # ... (Header construction logic same as before) ...
                                rows = [
                                    df.iloc[1].fillna("").astype(str).tolist(),
                                    df.iloc[2].fillna("").astype(str).tolist(),
                                    df.iloc[3].fillna("").astype(str).tolist()
                                ]
                                headers = []
                                for i in range(len(rows[0])):
                                    parts = []
                                    for r in rows:
                                        val = r[i].strip()
                                        if val and not val.startswith("Unnamed") and val not in parts:
                                            parts.append(val)
                                    if not parts: headers.append(f"Col_{i}")
                                    else: headers.append("_".join(parts))
                                
                                df_cleaned = df.iloc[4:].copy()
                                df_cleaned.columns = headers
                                df_cleaned = df_cleaned.dropna(how="all", axis=0)
                                
                                total_patterns = ["TOTAL", "GRAND TOTAL", "TOTAL_TOTAL"]
                                total_index = len(headers)
                                for i, h in enumerate(headers):
                                    if i > 1 and any(tp in h.upper() for tp in total_patterns):
                                        total_index = i
                                        break
                                
                                prefixes_to_strip = [
                                    f"{x_axis}_", f"{x_axis.upper()}_", 
                                    "Month Wise_", "Vehicle Category Group_",
                                    "Fuel_", "Maker_", "Norms_", "Vehicle Class_", "Vehicle Category_",
                                    "FOUR WHEELER_", "TWO WHEELER_", "THREE WHEELER_"
                                ]
                                cleaned_headers = []
                                for h in headers:
                                    ch = h
                                    modified = True
                                    while modified:
                                        modified = False
                                        for pref in prefixes_to_strip:
                                            if ch.startswith(pref):
                                                ch = ch.replace(pref, "", 1)
                                                modified = True
                                                break
                                    cleaned_headers.append(ch)
                                
                                df_cleaned.columns = cleaned_headers
                                s_col_clean, y_col_clean = cleaned_headers[0], cleaned_headers[1]
                                x_cols_clean = [c for c in cleaned_headers[2:total_index] if not c.startswith("Col_")]
                                
                                df_long = df_cleaned.melt(
                                    id_vars=[s_col_clean, y_col_clean],
                                    value_vars=x_cols_clean,
                                    var_name=x_axis,
                                    value_name="Value"
                                )
                                df_long["State"] = state
                                df_long["Year"] = str(year)
                                
                                # Reorder columns
                                df_long = df_long[[s_col_clean, y_col_clean, "State", "Year", x_axis, "Value"]]
                                all_axes_data.append(df_long)
                                print(f"--- Collected {len(df_long)} rows for {state} ---")
                            else:
                                print(f"Warning: No data found for {state}")
                        else:
                            print(f"Error: Could not find download button for {state}")
                            
                    except Exception as e:
                        print(f"Failed to process state {state}: {e}")

                # After all states are processed for this X/Y pair, save one file
                if all_axes_data:
                    final_df = pd.concat(all_axes_data, ignore_index=True)
                    
                    def sanitize(text):
                        return "".join(c if c.isalnum() else "_" for c in text).strip("_")

                    safe_x = sanitize(x_axis)
                    safe_y = sanitize(y_axis)
                    csv_filename = f"{safe_x}_{safe_y}_{year}.csv"
                    csv_path = os.path.join(output_dir, csv_filename)
                    
                    final_df.to_csv(csv_path, index=False)
                    print(f"\n+++ SUCCESSFULLY SAVED CONSOLIDATED FILE: {csv_path} ({len(final_df)} total rows) +++")

        browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vahan Dashboard Scraper")
    parser.add_argument("--year", required=True, help="Year (e.g., '2025', '2024')")
    parser.add_argument("--state", nargs="+", default=None, help="State names (e.g., 'DELHI', 'HARYANA'). If omitted, scrapes all states.")
    parser.add_argument("--xaxis", nargs="+", 
                        default=["Month Wise", "Fuel", "Norms", "Vehicle Category", "Vehicle Class"], 
                        help="One or more X-Axis variables")
    parser.add_argument("--yaxis", nargs="+", 
                        default=["Vehicle Class", "Maker", "Fuel", "Norms", "Vehicle Category"], 
                        help="One or more Y-Axis variables")
    parser.add_argument("--out", default="data", help="Output directory")

    args = parser.parse_args()
    
    output_dir = os.path.abspath(args.out)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    scrape_vahan(args.state, args.xaxis, args.yaxis, args.year, output_dir)
