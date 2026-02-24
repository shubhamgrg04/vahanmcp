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
    label_selector = "label#j_idt36_label"
    page.wait_for_selector(label_selector)
    page.click(label_selector)
    time.sleep(1)
    
    # Get all li items in the panel
    states = page.locator("li[id^='j_idt36_']").all_inner_texts()
    
    # Close the dropdown by clicking somewhere else or the label again
    page.click("body")
    time.sleep(0.5)
    
    # Filter out empty or placeholder text if any
    states = [s.strip() for s in states if s.strip() and "Select State" not in s]
    print(f"Found {len(states)} states.")
    return states

def scrape_vahan(states_to_scrape, y_axes, year, output_dir):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("Navigating to Vahan Dashboard...")
        page.goto("https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml", timeout=60000)
        page.wait_for_load_state("networkidle")

        # If no states provided, fetch all from dropdown
        if not states_to_scrape or "ALL" in [s.upper() for s in states_to_scrape]:
            all_states = get_all_states(page)
            if states_to_scrape and "ALL" in [s.upper() for s in states_to_scrape]:
                states_to_scrape = all_states
            else:
                states_to_scrape = all_states

        # 1. Selection logic for shared options
        # Select X-Axis (Hardcoded to Month Wise)
        select_primefaces_dropdown(page, "label#xaxisVar_label", "Month Wise")
        # Select Year
        select_primefaces_dropdown(page, "label#selectedYear_label", str(year))

        for y_axis in y_axes:
            print(f"\n==========================================")
            print(f"PROCESSING Y-AXIS: {y_axis}")
            print(f"==========================================")
            
            # Select Y-Axis
            select_primefaces_dropdown(page, "label#yaxisVar_label", y_axis)
            
            consolidated_df = pd.DataFrame()
            safe_y_axis = y_axis.replace("/", "_").replace(" ", "_")
            csv_path = os.path.join(output_dir, f"{safe_y_axis}_{year}.csv")

            for state in states_to_scrape:
                print(f"\n--- State: {state} ---")
                
                try:
                    # Select State
                    select_primefaces_dropdown(page, "label#j_idt36_label", state)

                    # Refresh
                    print("Clicking Refresh...")
                    page.click("button#j_idt73")
                    
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
                            # Construct headers
                            row1 = df.iloc[1].fillna("").astype(str).tolist()
                            row3 = df.iloc[3].fillna("").astype(str).tolist()
                            
                            headers = []
                            for i in range(len(row1)):
                                r1 = row1[i].strip()
                                r3 = row3[i].strip()
                                if "Unnamed" in r1 or not r1:
                                    h = r3 if r3 else f"Col_{i}"
                                else:
                                    h = r1 if not r3 else f"{r1}_{r3}"
                                headers.append(h)
                            
                            df_cleaned = df.iloc[4:].copy()
                            df_cleaned.columns = headers
                            df_cleaned = df_cleaned.dropna(how="all", axis=0)
                            
                            # Transform to Long Format
                            y_axis_col_name = headers[1]
                            month_cols = [h for h in headers if h in ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"] or "Month Wise_" in h]
                            
                            df_cleaned.columns = [h.replace("Month Wise_", "") for h in df_cleaned.columns]
                            month_cols = [h.replace("Month Wise_", "") for h in month_cols]
                            
                            df_long = df_cleaned.melt(
                                id_vars=["S No", y_axis_col_name],
                                value_vars=month_cols,
                                var_name="Month",
                                value_name="Value"
                            )
                            
                            df_long["State"] = state
                            df_long["Year"] = str(year)
                            
                            # S No, [Y-Axis], State, Year, Month, Value
                            final_cols = ["S No", y_axis_col_name, "State", "Year", "Month", "Value"]
                            df_long = df_long[final_cols]
                            
                            consolidated_df = pd.concat([consolidated_df, df_long], ignore_index=True)
                            print(f"Added {len(df_long)} rows for {state}")
                        else:
                            print(f"Warning: No data found for {state}")
                    else:
                        print(f"Error: Could not find download button for {state}")
                        
                except Exception as e:
                    print(f"Failed to process state {state}: {e}")

            # Save consolidated CSV for this Y-Axis
            if not consolidated_df.empty:
                consolidated_df.to_csv(csv_path, index=False)
                print(f"\n+++ SUCCESSFULLY SAVED CONSOLIDATED CSV: {csv_path} +++")

        browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vahan Dashboard Scraper")
    parser.add_argument("--state", nargs="+", default=None, help="State names (e.g., 'DELHI', 'HARYANA'). If omitted, scrapes all states.")
    parser.add_argument("--yaxis", nargs="+", default=["Vehicle Class"], help="One or more Y-Axis variables")
    parser.add_argument("--year", default="2025", help="Year (e.g., '2025', '2024')")
    parser.add_argument("--out", default="data", help="Output directory")

    args = parser.parse_args()
    
    output_dir = os.path.abspath(args.out)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    scrape_vahan(args.state, args.yaxis, args.year, output_dir)
