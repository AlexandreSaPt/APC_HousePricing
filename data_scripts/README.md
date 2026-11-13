# data_scripts: installation and usage

Scripts that build the datasets stored in `../data/`. For the overall
repository layout, see the [global README](../README.md).

> **Authorship note:** `idealista_scraper.py` was generated with Claude
> (Anthropic's assistant). `lat_long_finder.py` and `plot_map.py` were **not**
> written by Claude, so their descriptions here are based on filenames and
> outputs. Please review and correct where needed.

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

### 1. `idealista_scraper.py`: collect listings

```bash
# small test first
python idealista_scraper.py --max-listings 10 --output ../data/porto_district_all.csv

# full run (buying)
python idealista_scraper.py --output ../data/porto_district_all.csv

# rent instead of buy
python idealista_scraper.py --operation arrendar --output ../data/porto_district_rent.csv

# single municipality
python idealista_scraper.py --url https://www.idealista.pt/comprar-casas/matosinhos/
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
| Idealista shows a verification / "are you a robot" page | You are being blocked. Stop, wait a few hours, use a different network, or use the official API. |
| Scraped columns come back empty | Site HTML changed. Update `parse_listing_card()` / `parse_detail_page()` in `idealista_scraper.py`. |
| `PermissionError: [WinError 5]` when saving | File open in Excel or locked by OneDrive. Close it / move the project out of OneDrive. |
| `ModuleNotFoundError` | `pip install` the missing package inside the activated venv. |