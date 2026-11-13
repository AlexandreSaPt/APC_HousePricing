#!/usr/bin/env python3
"""
Geocode a CSV file: reads the "Location" column (a street / address in Porto,
Portugal), looks each one up with OpenStreetMap's Nominatim service, and writes
a new CSV with added `latitude` and `longitude` columns.

Usage:
    pip install pandas requests
    python geocode_csv.py places.csv
    python geocode_csv.py places.csv -o places_geocoded.csv --email you@example.com

Notes:
  * Nominatim's usage policy allows max 1 request/second and requires an
    identifying User-Agent (use --email so they can contact you if needed).
  * Results are cached in a JSON file, so re-running doesn't repeat lookups.
  * Addresses that can't be located are also written to <input>_not_found.csv.
  * The output CSV is updated as it goes and also saved if you stop with Ctrl+C;
    rows not processed yet are marked 'pending'. Re-run to continue where you left off.
"""

import argparse
import json
import os
import re
import signal
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Bounding boxes: (min_lon, min_lat, max_lon, max_lat)
# Porto (with some margin): biases the search and rejects results in the wrong city.
PORTO_BBOX = (-8.75, 41.07, -8.50, 41.23)
# Wider Greater-Porto box, used when the address itself names a neighbouring municipality.
METRO_BBOX = (-8.80, 40.95, -8.40, 41.30)

# Neighbouring municipalities that can show up inside "Porto" listings (accent-free, lowercase).
NEIGHBOUR_MUNICIPALITIES = {
    "vila nova de gaia", "matosinhos", "maia", "gondomar", "valongo",
    "espinho", "vila do conde", "povoa de varzim",
}

STREET_TYPES = {
    "rua", "avenida", "travessa", "praca", "largo", "caminho", "alameda", "estrada",
    "viela", "beco", "calcada", "escadas", "passeio", "jardim", "rotunda", "azinhaga",
    "campo", "cais", "ladeira", "via", "esplanada", "alto",
}

# Abbreviations (only expanded when followed by a dot, e.g. "Dr." -> "Doutor")
ABBREVIATIONS = {
    "r": "Rua", "av": "Avenida", "tv": "Travessa", "trav": "Travessa", "pc": "Praça",
    "pç": "Praça", "lg": "Largo", "cam": "Caminho", "dr": "Doutor", "dra": "Doutora",
    "prof": "Professor", "profa": "Professora", "eng": "Engenheiro", "arq": "Arquitecto",
}

# Small linking words that are often written differently in official vs. map names
# ("Rua do Doutor X" / "Rua Doutor X", "Pintor Amadeu Sousa Cardoso" / "... de Sousa ...")
CONNECTORS = {"de", "do", "da", "dos", "das", "e"}

MONTHS = ("janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|"
          "setembro|outubro|novembro|dezembro")
_UNITS = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
          "onze", "doze", "treze", "catorze", "quinze", "dezasseis", "dezassete", "dezoito", "dezanove"]


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def norm(text: str) -> str:
    """Lowercase, accent-free, single-spaced: for comparing names."""
    return re.sub(r"\s+", " ", strip_accents(text).lower()).strip()


def number_to_words(n: int) -> str:
    if n < 20:
        return _UNITS[n]
    tens = {20: "vinte", 30: "trinta"}[n // 10 * 10]
    return tens if n % 10 == 0 else f"{tens} e {_UNITS[n % 10]}"


WORD_TO_NUM = {norm(number_to_words(n)): n for n in range(1, 32)}


def expand_abbreviations(text: str) -> str:
    pattern = r"\b(" + "|".join(map(re.escape, ABBREVIATIONS)) + r")\.(?=\s|$)"
    return re.sub(pattern, lambda m: ABBREVIATIONS[m.group(1).lower()], text, flags=re.I)


def clean_segment(seg: str) -> str:
    seg = re.sub(r"\bs\s*/\s*n\b", "", seg, flags=re.I)  # "s/n" = sem número (no number)
    seg = expand_abbreviations(seg)
    return re.sub(r"\s+", " ", seg).strip(" ,-")


def split_alternatives(seg: str) -> list:
    """'Aldoar - Foz do Douro - Nevogilde' -> ['Aldoar', 'Foz do Douro', 'Nevogilde']"""
    return [a.strip() for a in re.split(r"\s+-\s+", seg) if a.strip()]


def loosen(name: str) -> str:
    """Drop de/do/da/dos/das/e. A search with fewer words still matches the fuller name."""
    return " ".join(w for w in name.split() if norm(w) not in CONNECTORS)


def date_variants(name: str) -> list:
    """Street names named after dates: '9 de Julho' <-> 'Nove de Julho'."""
    out = []
    m = re.search(rf"\b(\d{{1,2}}) de ({MONTHS})\b", name, re.I)
    if m and 1 <= int(m.group(1)) <= 31:
        out.append(name[:m.start(1)] + number_to_words(int(m.group(1))) + name[m.end(1):])
    m = re.search(rf"\b((?:vinte|trinta)(?: e \w+)?|\w+) de ({MONTHS})\b", name, re.I)
    if m:
        n = WORD_TO_NUM.get(norm(m.group(1)))
        if n:
            out.append(name[:m.start(1)] + str(n) + name[m.end(1):])
    return out


def find_column(df: pd.DataFrame, wanted: str) -> str:
    """Find the column matching `wanted`, ignoring case and extra spaces."""
    for col in df.columns:
        if str(col).strip().lower() == wanted.strip().lower():
            return col
    raise KeyError(
        f'Column "{wanted}" not found. Available columns: {list(df.columns)}'
    )


def in_bbox(lat: float, lon: float, bbox) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


# --------------------------------------------------------------------------- #
# Turning one messy address into an ordered list of searches
# --------------------------------------------------------------------------- #
def build_queries(raw_address: str, city: str, country: str, allow_area: bool = True):
    """
    Understand listings shaped like
        "<street>[, <number>], <neighbourhood>, <parish(es)>, <municipality>"
    and return (queries, bbox), where queries is a list of (text, status), most
    specific first. Statuses: ok / approx (street) / approx (name variant) / approx (area).
    """
    segs = [clean_segment(x) for x in raw_address.split(",")]
    segs = [x for x in segs if x]
    while segs and norm(segs[-1]) in {norm(city), norm(country)}:
        segs.pop()
    if not segs:
        return [(f"{raw_address}, {city}, {country}", "ok")], PORTO_BBOX

    # A neighbouring municipality named in the address replaces the default city
    target_city, bbox = city, PORTO_BBOX
    for i, seg in enumerate(segs[1:], start=1):
        if norm(seg) in NEIGHBOUR_MUNICIPALITIES:
            target_city, bbox = seg, METRO_BBOX
            segs.pop(i)
            break

    first, rest = segs[0], segs[1:]

    # House number: either its own segment (", 66,") or trailing in the street ("Rua X 66")
    number = None
    if rest and re.fullmatch(r"\d+[A-Za-z]?(\s*[-/]\s*\d+[A-Za-z]?)?", rest[0]):
        number = rest.pop(0).replace(" ", "")

    # Street vs. area ("Bairro ..." entries are neighbourhoods, sometimes with a street inside)
    street_names, area_names = [], []
    if re.match(r"^bairro\b", first, re.I):
        body = re.sub(r"^bairro\s+", "", first, flags=re.I)
        if re.match(r"^bairro\b", body, re.I):  # "Bairro Bairro das Artes"
            area_names.append(body)
        else:
            alts = split_alternatives(body)
            for alt in alts:
                if norm(alt.split()[0]) in STREET_TYPES:
                    street_names.append(alt)
                else:
                    area_names.append(alt)
            if alts and alts[0] in area_names:
                area_names.append(f"Bairro {alts[0]}")
    else:
        m = re.search(r"\s(\d+[A-Za-z]?)$", first)
        if m and not number:
            number, first = m.group(1), first[:m.start()].strip()
        street_names.append(first)

    queries = []

    def add(text, status):
        item = (f"{text}, {target_city}, {country}", status)
        if item[0] not in {q for q, _ in queries}:
            queries.append(item)

    # Broader place names mentioned in the listing, most local first
    context = []
    for seg in ([rest[0]] if rest else []) + rest[1:]:
        context.extend(split_alternatives(seg)[:2])
    parish_like = []
    for seg in ([rest[-1]] if rest else []) + ([rest[0]] if rest else []):
        parish_like.extend(split_alternatives(seg)[:2])

    for name in street_names:
        if number:
            add(f"{name} {number}", "ok")
        add(name, "ok" if not number else "approx (street)")
        for v in date_variants(name):
            add(v, "approx (name variant)")
        loose = loosen(name)
        if loose and loose != name:
            add(loose, "approx (name variant)")
        seen_ctx = []
        for alt in parish_like:
            if alt not in seen_ctx and len(seen_ctx) < 3:
                seen_ctx.append(alt)
                add(f"{name}, {alt}", "approx (street)")

    if allow_area:
        for name in area_names:
            add(name, "approx (area)")
        for alt in list(dict.fromkeys(context))[:4]:
            add(alt, "approx (area)")

    return queries, bbox


def query_nominatim(session: requests.Session, query: str, bbox=PORTO_BBOX, retries: int = 3):
    """Run one Nominatim search. Returns (lat, lon, display_name) or None."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "pt",
        # viewbox biases results toward Porto without excluding anything else
        "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
        "bounded": 0,
    }
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(NOMINATIM_URL, params=params, timeout=20)
            if resp.status_code == 429:  # rate limited
                time.sleep(5 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data:
                top = data[0]
                return float(top["lat"]), float(top["lon"]), top.get("display_name", "")
            return None
        except (requests.RequestException, ValueError) as exc:
            print(f"  ! request error ({exc}), attempt {attempt}/{retries}", file=sys.stderr)
            time.sleep(2 * attempt)
    return None


def geocode(session, raw_address: str, city: str, country: str, delay: float, allow_area: bool = True):
    """Try the generated searches in order; return the first result that lies in the right area."""
    queries, bbox = build_queries(raw_address, city, country, allow_area)
    notes = []
    for q, status in queries:
        result = query_nominatim(session, q, bbox)
        time.sleep(delay)  # respect rate limit after every request
        if result:
            lat, lon, display = result
            if in_bbox(lat, lon, bbox):
                return lat, lon, display, status, ""
            # Found something, but outside the expected area -> probably the wrong place
            print(f"  ! result outside expected area for '{q}', trying next option", file=sys.stderr)
            notes.append(f"'{q}' -> outside area: {display} ({lat:.5f}, {lon:.5f})")
        else:
            notes.append(f"'{q}' -> no result")
    return None, None, None, "not found", " | ".join(notes)


def atomic_write(path: Path, writer, retries: int = 8):
    """Write to a temp file, then swap it in. On Windows the target can be briefly
    locked (Excel, OneDrive, antivirus), so retry, and never crash the run if it stays locked."""
    tmp = path.with_name(path.name + ".tmp")
    writer(tmp)
    for attempt in range(1, retries + 1):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.5 * attempt)

    # Still locked: save a side copy so no work is lost, and keep going
    fallback = path.with_name(f"{path.stem}.locked_{int(time.time())}{path.suffix}")
    try:
        os.replace(tmp, fallback)
        print(f"  ! '{path.name}' is locked (open in Excel?). Saved to '{fallback.name}' instead.",
              file=sys.stderr)
    except OSError:
        print(f"  ! Could not write '{path.name}' (file locked). Will retry on next save.",
              file=sys.stderr)
    return False


def main():
    p = argparse.ArgumentParser(description="Add latitude/longitude to a CSV using its Location column.")
    p.add_argument("input", help="Input CSV path")
    p.add_argument("-o", "--output", help="Output CSV path (default: <input>_geocoded.csv)")
    p.add_argument("-c", "--column", default="Location", help='Column with the address (default: "Location")')
    p.add_argument("--city", default="Porto")
    p.add_argument("--country", default="Portugal")
    p.add_argument("--sep", default=None, help="CSV delimiter (default: auto-detect)")
    p.add_argument("--encoding", default="utf-8-sig", help="CSV encoding (default: utf-8-sig; try latin-1 if you get errors)")
    p.add_argument("--email", default="", help="Your contact email, included in the User-Agent (recommended)")
    p.add_argument("--delay", type=float, default=1.1, help="Seconds between requests (min 1 for Nominatim)")
    p.add_argument("--cache", default="geocode_cache.json", help="Cache file path")
    p.add_argument("--not-found-output",
                   help="CSV listing the addresses that could not be found (default: <input>_not_found.csv)")
    p.add_argument("--no-area-fallback", action="store_true",
                   help="Never fall back to the neighbourhood/parish centre when an exact "
                        "street can't be found (such rows are left as 'not found' instead)")
    p.add_argument("--retry-failed", action="store_true",
                   help="Ignore cached 'not found' results and try those addresses again "
                        "(use after improving the script)")
    p.add_argument("--save-every", type=int, default=5,
                   help="Update the output CSV every N rows (default: 5). It is also saved on Ctrl+C.")
    args = p.parse_args()
    args.save_every = max(1, args.save_every)

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + "_geocoded.csv")
    nf_path = Path(args.not_found_output) if args.not_found_output else in_path.with_name(in_path.stem + "_not_found.csv")

    # sep=None + python engine lets pandas sniff commas vs semicolons
    df = pd.read_csv(in_path, sep=args.sep, engine="python", dtype=str, encoding=args.encoding)
    loc_col = find_column(df, args.column)

    # Load cache
    cache_path = Path(args.cache)
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}

    session = requests.Session()
    ua = "porto-csv-geocoder/1.0" + (f" ({args.email})" if args.email else "")
    session.headers.update({"User-Agent": ua, "Accept-Language": "pt,en"})

    total = len(df)
    lats, lons, matched, statuses = [], [], [], []

    def save_output():
        """Write the output CSV with everything done so far (remaining rows marked 'pending')."""
        done = len(lats)
        pad = total - done
        out_df = df.copy()
        out_df["latitude"] = lats + [None] * pad
        out_df["longitude"] = lons + [None] * pad
        out_df["matched_address"] = matched + [None] * pad
        out_df["geocode_status"] = statuses + ["pending"] * pad
        atomic_write(out_path, lambda t: out_df.to_csv(t, index=False, encoding="utf-8-sig"))
        save_not_found()

    def save_not_found():
        """One line per unique address that couldn't be located, with what was tried."""
        groups = {}
        for i, (status, raw) in enumerate(zip(statuses, df[loc_col].iloc[:len(statuses)]), start=1):
            if status != "not found":
                continue
            address = str(raw).strip()
            g = groups.setdefault(address.lower(), {"location": address, "rows": [], "tried": ""})
            g["rows"].append(str(i))
            g["tried"] = cache.get(address.lower(), {}).get("note", "")
        nf_df = pd.DataFrame(
            [{"location": g["location"], "input_rows": ";".join(g["rows"]), "what_was_tried": g["tried"]}
             for g in groups.values()],
            columns=["location", "input_rows", "what_was_tried"],
        )
        atomic_write(nf_path, lambda t: nf_df.to_csv(t, index=False, encoding="utf-8-sig"))

    def save_cache():
        atomic_write(cache_path, lambda t: t.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"))

    # Treat "terminate" signals (e.g. closing the terminal) like Ctrl+C
    def _raise_interrupt(signum, frame):
        raise KeyboardInterrupt
    for sig_name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), _raise_interrupt)

    interrupted = False
    try:
        for idx, raw in enumerate(df[loc_col], start=1):
            address = "" if pd.isna(raw) else str(raw).strip()
            if not address:
                lats.append(None); lons.append(None); matched.append(None); statuses.append("empty")
                continue

            key = address.lower()
            cached = cache.get(key)
            if cached is not None and not (args.retry_failed and cached.get("status") == "not found"):
                entry = cached
            else:
                print(f"[{idx}/{total}] {address}")
                lat, lon, display, status, note = geocode(session, address, args.city, args.country, args.delay,
                                                          allow_area=not args.no_area_fallback)
                entry = {"lat": lat, "lon": lon, "display": display, "status": status, "note": note}
                cache[key] = entry
                save_cache()

            lats.append(entry["lat"])
            lons.append(entry["lon"])
            matched.append(entry["display"])
            statuses.append(entry["status"])

            if len(lats) % args.save_every == 0:
                save_output()
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted - saving what has been done so far...")
    finally:
        # Runs on normal finish, Ctrl+C, and unexpected errors alike
        save_output()
        save_cache()

    done = len(lats)
    ok = sum(1 for s in statuses if s and s.startswith(("ok", "approx")))
    if interrupted:
        print(f"Saved {done}/{total} rows ({ok} geocoded) -> {out_path}")
        print("Run the same command again to continue: finished addresses come from the cache.")
    else:
        print(f"\nDone. {ok}/{total} rows geocoded -> {out_path}")
    n_area = sum(1 for s in statuses if s == "approx (area)")
    if n_area:
        print(f"{n_area} rows only matched a neighbourhood/parish centre ('approx (area)'): "
              f"their coordinates are approximate and several may share the same point.")
    failed = [s for s in statuses if s == "not found"]
    if failed:
        print(f"{len(failed)} rows not found -> list of unique addresses saved to {nf_path}")

if __name__ == "__main__":
    main()