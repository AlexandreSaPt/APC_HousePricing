#!/usr/bin/env python3
"""
Plot the rows of a geocoded CSV (output of geocode_csv.py) on an interactive
map, colouring each point by its price per m².

Usage:
    pip install pandas folium
    python plot_map.py places_geocoded.csv
    python plot_map.py places_geocoded.csv --price-column "Price per m2" -o map.html

Open the resulting HTML file in any browser. Hover over a point for a quick
summary, click it for details (all the other CSV columns are shown).
"""

import argparse
import functools
import html
import http.server
import re
import socketserver
import sys
import webbrowser
from pathlib import Path

import folium
import pandas as pd
from branca.colormap import LinearColormap


# Base maps. The Esri ones work even when you open the HTML straight from disk.
# OpenStreetMap's own tile server blocks pages opened from disk (no Referer
# header), so "osm" only works together with --serve.
TILE_PRESETS = {
    "esri-street": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        "Tiles &copy; Esri", 19),
    "esri-gray": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "Tiles &copy; Esri", 16),
    "esri-satellite": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Tiles &copy; Esri", 19),
    "osm": (
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "&copy; OpenStreetMap contributors", 19),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def parse_number(value):
    """
    Turn messy price strings into floats. Handles things like:
    '2.500 €/m²', '2 500,50', '€3,200', '2500.75', 2500
    A lone '.' or ',' followed by exactly 3 digits is treated as a thousands
    separator (Portuguese style: '2.500' -> 2500).
    """
    if pd.isna(value):
        return None
    s = str(value).strip()
    s = re.sub(r"[^\d.,\-]", "", s)  # drop currency symbols, spaces, units
    if not s or s in {"-", ".", ","}:
        return None

    if "." in s and "," in s:
        # The last separator is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif "." in s:
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def find_price_column(df: pd.DataFrame, requested: str | None) -> str:
    """Use the requested column, or try to guess it from the column names."""
    if requested:
        for col in df.columns:
            if str(col).strip().lower() == requested.strip().lower():
                return col
        sys.exit(f'Column "{requested}" not found.\nAvailable columns: {list(df.columns)}')

    def norm(c):
        return re.sub(r"[\s_\-/²]", "", str(c).lower())

    # Strongest hints first
    patterns = [
        r"(price|preco|preço|valor).*(m2|sqm|sqft|m)$",
        r"(€|eur).*m",
        r"(m2|sqm).*(price|preco|preço|€|eur)",
        r"price|preco|preço",
    ]
    for pat in patterns:
        matches = [c for c in df.columns if re.search(pat, norm(c))]
        if len(matches) >= 1:
            print(f'Using "{matches[0]}" as the price-per-m² column '
                  f"(override with --price-column).")
            return matches[0]

    sys.exit(
        "Couldn't guess the price-per-m² column. Please pass --price-column.\n"
        f"Available columns: {list(df.columns)}"
    )


def build_popup(row: pd.Series, location_col: str | None, price_col: str, price: float, label: str) -> str:
    """HTML popup with the price on top and every other column below."""
    skip = {"latitude", "longitude"}
    lines = []
    if location_col:
        lines.append(f"<b>{html.escape(str(row[location_col]))}</b>")
    lines.append(f"<b>{html.escape(label)}:</b> {price:,.0f}")
    for col, val in row.items():
        if col in skip or col in {location_col, price_col} or pd.isna(val):
            continue
        lines.append(f"{html.escape(str(col))}: {html.escape(str(val))}")
    return "<div style='font-size:12px;max-height:220px;overflow:auto'>" + "<br>".join(lines) + "</div>"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Map a geocoded CSV coloured by price per m².")
    p.add_argument("input", help="Geocoded CSV (with latitude and longitude columns)")
    p.add_argument("-o", "--output", help="Output HTML (default: <input>_map.html)")
    p.add_argument("--price-column", help="Name of the price-per-m² column (auto-detected if omitted)")
    p.add_argument("--location-column", default="Location", help='Address column for labels (default: "Location")')
    p.add_argument("--lat", default="latitude", help="Latitude column name")
    p.add_argument("--lon", default="longitude", help="Longitude column name")
    p.add_argument("--sep", default=None, help="CSV delimiter (default: auto-detect)")
    p.add_argument("--encoding", default="utf-8-sig")
    p.add_argument("--radius", type=int, default=8, help="Marker radius in pixels (default: 8)")
    p.add_argument("--clip", type=float, default=2.0,
                   help="Percentile clipping for the colour scale, so a few outliers "
                        "don't wash out the colours. 0 = use full min/max (default: 2)")
    p.add_argument("--area-column",
                   help="Optional area (m²) column. If given, the colour is price / area, "
                        "i.e. the price column is treated as the TOTAL price")
    p.add_argument("--tiles", default="esri-street",
                   help="Base map: " + ", ".join(TILE_PRESETS) + " (default: esri-street). "
                        "A folium tile name or a custom tile URL also works.")
    p.add_argument("--serve", action="store_true",
                   help="After saving, serve the map on http://localhost and open it in your "
                        "browser (needed for --tiles osm). Press Ctrl+C to stop.")
    p.add_argument("--port", type=int, default=8000, help="Port for --serve (default: 8000)")
    args = p.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + "_map.html")

    df = pd.read_csv(in_path, sep=args.sep, engine="python", dtype=str, encoding=args.encoding)

    for needed in (args.lat, args.lon):
        if needed not in df.columns:
            sys.exit(f'Column "{needed}" not found. Did you run geocode_csv.py first?\n'
                     f"Available columns: {list(df.columns)}")

    price_col = find_price_column(df, args.price_column)
    location_col = next((c for c in df.columns
                         if str(c).strip().lower() == args.location_column.strip().lower()), None)

    # Clean the numeric columns
    df["_lat"] = pd.to_numeric(df[args.lat], errors="coerce")
    df["_lon"] = pd.to_numeric(df[args.lon], errors="coerce")
    df["_price"] = df[price_col].map(parse_number)
    price_label = str(price_col)

    if args.area_column:
        area_col = next((c for c in df.columns
                         if str(c).strip().lower() == args.area_column.strip().lower()), None)
        if area_col is None:
            sys.exit(f'Column "{args.area_column}" not found.\nAvailable columns: {list(df.columns)}')
        area = df[area_col].map(parse_number)
        df["_price"] = df["_price"] / area.where(area > 0)  # NaN where area is missing/zero
        price_label = "Price per m²"
        print(f'Computing price per m² as "{price_col}" / "{area_col}".')

    total = len(df)
    no_coords = df["_lat"].isna() | df["_lon"].isna()
    no_price = df["_price"].isna()
    plot_df = df[~no_coords & ~no_price].copy()

    print(f"{total} rows: {len(plot_df)} plotted, "
          f"{int(no_coords.sum())} without coordinates, "
          f"{int((~no_coords & no_price).sum())} with coordinates but no usable price.")
    if plot_df.empty:
        sys.exit("Nothing to plot.")

    # Colour scale (clipped at percentiles so outliers don't dominate)
    if args.clip > 0:
        vmin = float(plot_df["_price"].quantile(args.clip / 100))
        vmax = float(plot_df["_price"].quantile(1 - args.clip / 100))
    else:
        vmin, vmax = float(plot_df["_price"].min()), float(plot_df["_price"].max())
    if vmin == vmax:  # avoid a degenerate scale
        vmin, vmax = vmin - 1, vmax + 1

    colormap = LinearColormap(
        colors=["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"],  # blue -> red
        vmin=vmin, vmax=vmax,
        caption=price_label,
    )

    # Map centred on the data
    centre = [plot_df["_lat"].mean(), plot_df["_lon"].mean()]
    if args.tiles in TILE_PRESETS:
        url, attr, max_zoom = TILE_PRESETS[args.tiles]
        m = folium.Map(location=centre, zoom_start=13, tiles=None, control_scale=True)
        folium.TileLayer(tiles=url, attr=attr, max_zoom=max_zoom, name=args.tiles).add_to(m)
    else:
        m = folium.Map(location=centre, zoom_start=13, tiles=args.tiles, control_scale=True)

    for _, row in plot_df.iterrows():
        price = row["_price"]
        colour = colormap(min(max(price, vmin), vmax))
        label = f"{row[location_col]} — {price:,.0f}" if location_col else f"{price:,.0f}"
        folium.CircleMarker(
            location=[row["_lat"], row["_lon"]],
            radius=args.radius,
            color="#333333",
            weight=0.8,
            fill=True,
            fill_color=colour,
            fill_opacity=0.85,
            tooltip=html.escape(label),
            popup=folium.Popup(build_popup(row, location_col, price_col, price, price_label), max_width=320),
        ).add_to(m)

    colormap.add_to(m)
    m.fit_bounds([[plot_df["_lat"].min(), plot_df["_lon"].min()],
                  [plot_df["_lat"].max(), plot_df["_lon"].max()]], padding=(30, 30))

    m.save(out_path)
    print(f"Colour scale: {vmin:,.0f} – {vmax:,.0f}")
    print(f"Map saved to {out_path} (open it in your browser).")

    if args.serve:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(out_path.parent.resolve()))
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
            url = f"http://localhost:{args.port}/{out_path.name}"
            print(f"Serving at {url}  (Ctrl+C to stop)")
            webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nStopped.")


if __name__ == "__main__":
    main()