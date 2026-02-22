# Vahan Scraping Status

## Current Capability: `scrape_crosstab.py`
A new unified scraper (`scrape_crosstab.py`) has been built and tested. It uses a headless browser to download XLSX reports directly from the Tabular Summary view. 

## Key Findings on Axis Configurations

### Y-Axis (Rows)
**Works beautifully.** We can successfully swap the Y-axis to group data by:
*   Fuel
*   Norms (Emission Standards)
*   Maker (Manufacturer)
*   State
*   Vehicle Category
*   Vehicle Class

### X-Axis (Columns)
**Locked to Vehicle Category Group (VCG).** Regardless of what X-axis option is selected in the UI (even simulated by a real browser subagent clicking the dropdowns), the headless XLSX download *always* defaults to outputting columns for the 10 Vehicle Category Groups (2W, 3W, 4W, etc.).
*   *Conclusion:* We cannot currently get cross-tabs like "Maker × Fuel". Every download will inherently be "[Selected Y-Axis] × [Vehicle Category Group]". 

## Reliability and Server Bottlenecks
The Vahan backend is currently struggling with heavy requests. 
*   **All India Queries:** Requests for single, high-level aggregated data points (like "All India 2025" for a specific Y-axis) complete successfully.
*   **State-Level Iteration (The 108 Challenge):** When attempting a full run iterating over all 36 states and 3 years (108 individual XLSX downloads), the Vahan server consistently times out. Even with the download wait extended to a massive **180 seconds** (3 minutes), the server fails to generate and return the file. 

## Data Acquired Today (All India Only)
During testing, we successfully pulled the All-India totals for 2023-2025 across several new Y-axes. This data is now in the `data/` folder:
*   `fuel_registrations.csv` (18 rows: 6 fuel types × 3 years)
*   `norms_registrations.csv` (21 rows: 7 norm types × 3 years)
*   `state_registrations.csv` (117 rows: 39 regional entries × 3 years)
*   `vehicle_category_registrations.csv` (49 rows: various categories × 3 years)
*(Note: These files represent only national totals, not the granular state-by-state data).*

## Next Steps / Options
Because the scraper logic itself is solid, the blocker is entirely on the Vahan server-side capacity. 
1.  **Wait for off-peak hours:** Run the full 108-state scrape later when the Vahan servers are less loaded.
2.  **Settle for All-India:** Limit our new data ingestion to just the national totals if the granular state breakdown isn't strictly necessary.
3.  **Ingest existing Maker data:** We do have the full 157k row dataset for "Maker × VCG", which has already been added to the database with corresponding MCP tools.
