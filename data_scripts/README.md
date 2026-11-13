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

pip install pandas playwright beautifulsoup4 lxml folium
playwright install chromium
```

> **Windows / OneDrive:** keep the project outside OneDrive-synced folders
> (e.g. `C:\dev\`) and close CSVs in Excel while scripts run. Otherwise saving
> can fail with `PermissionError: [WinError 5]`.

## Running the scripts

Run everything from inside this folder so the `../data/` paths work:

```bash
cd data_scripts
```

### 1. `idealista_scraper.py`: collect listings (single URL / district search)

```bash
# small test first
python idealista_scraper.py --max-listings 10 --output ../data/porto_district_all.csv

# full run (buying)
python idealista_scraper.py --output ../data/porto_district_all.csv

# rent instead of buy
python idealista_scraper.py --operation arrendar --output ../data/porto_district_rent.csv

# single municipality
python idealista_scraper.py --url [https://www.idealista.pt/comprar-casas/matosinhos/](https://www.idealista.pt/comprar-casas/matosinhos/)
```

| Option | Default | Meaning |
|---|---|---|
| `--operation` | `comprar` | `comprar` (buy) or `arrendar` (rent) |
| `--url` | Porto district | Start URL, e.g. one municipality |
| `--max-pages` | 60 | Idealista caps searches at about 60 pages |
| `--max-listings` | 0 (no limit) | Stop after N saved listings |
| `--output` | `idealista_porto.csv` | Output CSV path |
| `--min-delay` / `--max-delay` | 15 / 45 | Seconds between listing requests |
| `--batch-size` / `--batch-pause` | 10 / 300 | Long rest after N listings (seconds) |

- Rows are written immediately, so interrupting loses nothing.
- Re-running skips IDs already in the CSV (resumable).
- Logs go to the console and `idealista_scraper.log`.
- Idealista's terms restrict automated access and the site uses anti-bot
  protection, so runs may be blocked. Keep volumes small and academic. The
  official API (https://developers.idealista.com) is the legitimate alternative.

### 2. `lat_long_finder.py`: scrape & geocode listings across Porto municipalities

Loops through all 18 municipalities (*concelhos*) in the Porto district individually to bypass Idealista's 60-page (~1,800 listings) per-search cap, extracting listing details along with their embedded latitude/longitude coordinates. Uses UI-based `"Seguinte"` pagination, opens detail pages in a separate tab to preserve search state, and includes realistic browser headers and anti-detection tweaks.

```bash
# full Porto district run (buying - all 18 municipalities)
python lat_long_finder.py --output ../data/porto_district_all.csv

# rent instead of buy
python lat_long_finder.py --operation arrendar --output ../data/porto_district_rent.csv

# single municipality (e.g. Porto city or Vila Nova de Gaia)
python lat_long_finder.py --municipality porto --output ../data/porto_city.csv
python lat_long_finder.py --municipality vila-nova-de-gaia --output ../data/gaia.csv

# quick test (first 2 pages of a single municipality)
python lat_long_finder.py --municipality matosinhos --max-pages 2 --output ../data/test_matosinhos.csv
```

| Option | Default | Meaning |
|---|---|---|
| `--operation` | `comprar` | `comprar` (buy) or `arrendar` (rent) |
| `--municipality` | All 18 | Scrape a single municipality slug (see list below) |
| `--max-pages` | `60` | Maximum search result pages per municipality |
| `--output` | `porto_district_all.csv` | Output CSV path (includes `municipality`, `latitude`, and `longitude`) |
| `--min-delay` / `--max-delay` | `12.0` / `30.0` | Random delay range (in seconds) between listing requests |

**Supported `--municipality` values:**
`porto`, `vila-nova-de-gaia`, `matosinhos`, `maia`, `gondomar`, `valongo`, `povoa-de-varzim`, `vila-do-conde`, `penafiel`, `paredes`, `santo-tirso`, `amarante`, `felgueiras`, `lousada`, `pacos-de-ferreira`, `trofa`, `marco-de-canaveses`, `baiao`.

- Rows are flushed immediately after each listing, so interrupting loses nothing.
- Re-running skips listing IDs already present in the CSV (resumable).
- Logs go to the console and `idealista_district.log`.
- Runs in a visible browser window (`headless=False`); if a CAPTCHA appears, solve it in the browser and press `ENTER` in the terminal to continue.

### 3. `plot_map.py`: map the listings

Reads the geocoded CSV (`../data/porto_district_all.csv` or `../data/porto_district_all_geocoded.csv`) and generates an interactive Folium HTML map (`porto_district_all_geocoded_map.html`) with price/typology popups, marker clustering, and layer controls. Open the output `.html` file in any web browser.

```bash
# run with default input/output paths
python plot_map.py

# specify custom input CSV or output HTML map
python plot_map.py --input ../data/porto_district_all.csv --output porto_district_all_geocoded_map.html
```

| Option | Default | Meaning |
|---|---|---|
| `--input` | `../data/porto_district_all.csv` | Input CSV containing `latitude` and `longitude` columns |
| `--output` | `porto_district_all_geocoded_map.html` | Output interactive HTML map path |

- Automatically skips rows missing valid `latitude` or `longitude` values.
- Includes interactive markers displaying price, area ($\text{m}^2$), typology, municipality, and direct listing URLs.

## Troubleshooting

| Problem | Likely fix |
|---|---|
| Idealista shows a verification / "are you a robot" page | Solve it in the open browser window and press `ENTER` in the terminal. If it keeps recurring, stop, wait a few hours, switch networks, or use the official API. |
| Scraper stops early after 1–2 pages | Anti-bot protection returned an empty shell or HTML selectors changed. Check `idealista_district.log` and inspect `parse_listing_cards()` / `parse_detail_page()`. |
| Map is empty or missing listings | Confirm the input CSV has populated `latitude` and `longitude` columns and matches the `--input` path. |
| `PermissionError: [WinError 5]` when saving | File open in Excel or locked by OneDrive. Close it / move the project out of OneDrive. |
| `ModuleNotFoundError` | `pip install` the missing package inside the activated venv (`pandas`, `playwright`, `beautifulsoup4`, `lxml`, `folium`). |