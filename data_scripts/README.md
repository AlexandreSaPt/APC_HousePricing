# data_scripts: installation and usage

Scripts that build the datasets stored in `../data/`. For the overall
repository layout, see the [global README](../README.md).


## Installation

Python 3.10+ is recommended. From the repository root:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install pandas playwright beautifulsoup4 lxml
playwright install chromium
```

`lat_long_finder.py` and `plot_map.py` may need extra packages (for example
`geopy` for geocoding and `folium` for the map). Check the `import` lines at
the top of each file and `pip install` anything missing.

> **Windows / OneDrive:** keep the project outside OneDrive-synced folders
> (e.g. `C:\dev\`) and close CSVs in Excel while scripts run. Otherwise saving
> can fail with `PermissionError: [WinError 5]`.

## Running the scripts

Run everything from inside this folder so the `../data/` paths work:

```bash
cd data_scripts
```

### 1a. `idealista_porto_municipalities.py`: full Porto district scraper (recommended)

Loops through all 18 municipalities (*concelhos*) in the Porto district individually to bypass Idealista's 60-page (~1,800 listings) per-search cap. Uses UI-based `"Seguinte"` pagination, opens detail pages in a separate tab to preserve search state, and includes realistic browser headers/anti-detection tweaks.

```bash
# full Porto district run (buying - all 18 municipalities)
python idealista_porto_municipalities.py --output ../data/porto_district_all.csv

# rent instead of buy
python idealista_porto_municipalities.py --operation arrendar --output ../data/porto_district_rent.csv

# single municipality (e.g. Porto city or Vila Nova de Gaia)
python idealista_porto_municipalities.py --municipality porto --output ../data/porto_city.csv
python idealista_porto_municipalities.py --municipality vila-nova-de-gaia --output ../data/gaia.csv

# quick test (first 2 pages of a single municipality)
python idealista_porto_municipalities.py --municipality matosinhos --max-pages 2 --output ../data/test_matosinhos.csv
```

| Option | Default | Meaning |
|---|---|---|
| `--operation` | `comprar` | `comprar` (buy) or `arrendar` (rent) |
| `--municipality` | All 18 | Scrape a single municipality slug (see list below) |
| `--max-pages` | `60` | Maximum search result pages per municipality |
| `--output` | `porto_district_all.csv` | Output CSV path (includes a `municipality` column) |
| `--min-delay` / `--max-delay` | `12.0` / `30.0` | Random delay range (in seconds) between listing requests |

**Supported `--municipality` values:**
`porto`, `vila-nova-de-gaia`, `matosinhos`, `maia`, `gondomar`, `valongo`, `povoa-de-varzim`, `vila-do-conde`, `penafiel`, `paredes`, `santo-tirso`, `amarante`, `felgueiras`, `lousada`, `pacos-de-ferreira`, `trofa`, `marco-de-canaveses`, `baiao`.

- Rows are flushed immediately after each listing, so interrupting loses nothing.
- Re-running skips listing IDs already present in the CSV (resumable).
- Logs go to the console and `idealista_district.log`.
- Runs in a visible browser window (`headless=False`); if a CAPTCHA appears, solve it in the browser and press `ENTER` in the terminal to continue.

### 1b. `idealista_scraper.py`: single-URL / custom search scraper

Use this script if you want to scrape a specific custom search URL (such as a filtered price range or parish) with batch cool-down pauses and a `--max-listings` cap.

```bash
# small test first
python idealista_scraper.py --max-listings 10 --output ../data/porto_district_all.csv

# full run (single district search, capped at 60 pages by Idealista)
python idealista_scraper.py --output ../data/porto_district_all.csv

# rent instead of buy
python idealista_scraper.py --operation arrendar --output ../data/porto_district_rent.csv

# custom URL
python idealista_scraper.py --url [https://www.idealista.pt/comprar-casas/matosinhos/](https://www.idealista.pt/comprar-casas/matosinhos/)
```

| Option | Default | Meaning |
|---|---|---|
| `--operation` | `comprar` | `comprar` (buy) or `arrendar` (rent) |
| `--url` | Porto district | Start URL, e.g. one municipality or filtered search |
| `--max-pages` | `60` | Idealista caps searches at about 60 pages |
| `--max-listings` | `0` (no limit) | Stop after N saved listings |
| `--output` | `idealista_porto.csv` | Output CSV path |
| `--min-delay` / `--max-delay` | `15` / `45` | Seconds between listing requests |
| `--batch-size` / `--batch-pause` | `10` / `300` | Long rest after N listings (seconds) |

- Rows are written immediately, so interrupting loses nothing.
- Re-running skips IDs already in the CSV (resumable).
- Logs go to the console and `idealista_scraper.log`.
- Idealista's terms restrict automated access and the site uses anti-bot
  protection, so runs may be blocked. Keep volumes small and academic. The
  official API (https://developers.idealista.com) is the legitimate alternative.

### 2. `lat_long_finder.py`: geocode addresses *(verify)*

Reads `../data/porto_district_all.csv`, finds latitude/longitude per address,
and writes `../data/porto_district_all_geocoded.csv`,
`../data/porto_district_all_not_found.csv`, and `geocode_cache.json`.

```bash
python lat_long_finder.py
```

- The cache makes it resumable: re-run after an interruption and it continues.
- Geocoding services rate-limit requests, so thousands of rows take a while.
- Input/output paths are set in the script; confirm they point to `../data/`.

### 3. `plot_map.py`: map the listings *(verify)*

Reads the geocoded CSV and writes the interactive
`porto_district_all_geocoded_map.html` in this folder. Open it in any browser.

```bash
python plot_map.py
```

## Troubleshooting

| Problem | Likely fix |
|---|---|
| Idealista shows a verification / "are you a robot" page | Solve it in the open browser window and press `ENTER` in the terminal. If it keeps recurring, stop, wait a few hours, switch networks, or use the official API. |
| Scraper stops early after 1–2 pages | Anti-bot protection returned an empty shell or HTML selectors changed. Check `idealista_district.log` and inspect `parse_listing_cards()` / `parse_detail_page()`. |
| `PermissionError: [WinError 5]` when saving | File open in Excel or locked by OneDrive. Close it / move the project out of OneDrive. |
| `ModuleNotFoundError` | `pip install` the missing package inside the activated venv. |