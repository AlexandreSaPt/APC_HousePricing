#!/usr/bin/env python3
"""
Idealista.pt Scraper - Porto District (Municipality-by-Municipality)

Fixed: Uses a separate tab for detail pages so search results pagination 
never loses context.

Setup:
    pip install playwright beautifulsoup4 lxml
    playwright install chromium
"""

import argparse
import csv
import logging
import random
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://www.idealista.pt"

PORTO_MUNICIPALITIES = [
    "porto",
    "vila-nova-de-gaia",
    "matosinhos",
    "maia",
    "gondomar",
    "valongo",
    "povoa-de-varzim",
    "vila-do-conde",
    "penafiel",
    "paredes",
    "santo-tirso",
    "amarante",
    "felgueiras",
    "lousada",
    "pacos-de-ferreira",
    "trofa",
    "marco-de-canaveses",
    "baiao",
]

CSV_FIELDS = [
    "id", "municipality", "operation", "url", "title", "price_eur", "area_m2", 
    "location", "latitude", "longitude", "bedrooms", "bathrooms", "typology",
    "property_type", "floor", "num_floors", "has_garage", "has_garden", "scraped_at",
]

log = logging.getLogger("idealista")

REALISTIC_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def setup_logging(logfile: str):
    fmt = "%(asctime)s | %(levelname)-7s | %(message)s"
    logging.basicConfig(
        level=logging.INFO, format=fmt, datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(logfile, encoding="utf-8")],
    )


def pause(lo, hi, why=""):
    t = random.uniform(lo, hi)
    log.debug("sleeping %.1fs %s", t, why)
    time.sleep(t)


def to_int(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def is_blocked(html: str) -> bool:
    low = html.lower()
    return any(k in low for k in (
        "captcha-delivery", "datadome", "geo.captcha", "access denied",
        "unusual activity", "pedido bloqueado"))


def check_and_solve_block(page, url):
    html = page.content()
    if is_blocked(html):
        log.warning("CAPTCHA/Block detected on %s", url)
        input(">>> Solve challenge in browser window, then press ENTER... ")
        page.wait_for_timeout(2000)
        return page.content()
    return html


def accept_cookies(page):
    for sel in ("#didomi-notice-agree-button", "button:has-text('Aceitar')", "button:has-text('Aceito')"):
        try:
            page.click(sel, timeout=3000)
            log.info("Cookie banner accepted")
            return
        except Exception:
            continue


def parse_listing_cards(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    cards_elements = soup.select("article.item, article.item-multimedia-container, .items-container article")
    
    results = []
    for card in cards_elements:
        link = card.select_one("a.item-link, a[href*='/imovel/']")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        m = re.search(r"/imovel/(\d+)", href)
        if not m:
            continue

        price_elem = card.select_one(".item-price, .price-row, .item-price-container")
        price = to_int(price_elem.get_text("", strip=True) if price_elem else None)
        
        rec = {
            "id": m.group(1),
            "url": BASE + href if href.startswith("/") else href,
            "title": link.get_text(" ", strip=True),
            "price_eur": price,
        }

        for span in card.select(".item-detail, .item-amenities span"):
            txt = span.get_text(" ", strip=True)
            if re.match(r"^T\d", txt, re.I):
                rec["typology"] = txt.upper().split()[0]
            elif "m²" in txt or "m2" in txt:
                rec["area_m2"] = to_int(txt.split("m")[0].replace(",", "."))

        results.append(rec)
    return results


def parse_detail_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out = {}

    h1 = soup.select_one("h1 .main-info__title-main, h1")
    if h1:
        out["title"] = h1.get_text(" ", strip=True)
    loc = soup.select_one("#headerMap li, .main-info__title-minor")
    headers = [li.get_text(" ", strip=True) for li in soup.select("#headerMap li")]
    out["location"] = ", ".join(headers) if headers else (
        loc.get_text(" ", strip=True) if loc else None)

    p = soup.select_one(".info-data-price, .price-container .txt-bold")
    if p:
        out["price_eur"] = to_int(p.get_text(strip=True))

    feats = [li.get_text(" ", strip=True)
             for li in soup.select(".details-property_features li, .details-property li")]
    blob = " | ".join(feats)
    low = blob.lower()

    m = re.search(r"(\d[\d.,]*)\s*m²\s*(?:construídos|úteis)?", blob)
    if m:
        out["area_m2"] = to_int(m.group(1).split(",")[0])

    m = re.search(r"(\d+)\s*quarto", low)
    if m:
        out["bedrooms"] = int(m.group(1))
    m = re.search(r"(\d+)\s*casa[s]? de banho", low)
    if m:
        out["bathrooms"] = int(m.group(1))

    m = re.search(r"\bT(\d+)\b", blob)
    if m:
        out["typology"] = f"T{m.group(1)}"
        out.setdefault("bedrooms", int(m.group(1)))

    out["has_garage"] = bool(re.search(r"garagem|estacionamento incluído", low))
    out["has_garden"] = bool(re.search(r"jardim", low))

    m = re.search(r"(\d+)\.?º?\s*andar|andar\s*(\d+)|(\d+)ª?\s*planta", low)
    if m:
        out["floor"] = int([g for g in m.groups() if g][0])
    elif "rés-do-chão" in low or "r/c" in low or "rés do chão" in low:
        out["floor"] = 0
    m = re.search(r"(\d+)\s*(?:andares|pisos)", low)
    if m:
        out["num_floors"] = int(m.group(1))

    t = (out.get("title") or "").lower() + " " + low
    if re.search(r"moradia|casa |vivenda|quinta|chalet|casa de campo", t):
        out["property_type"] = "house"
    elif re.search(r"apartamento|andar|duplex|loft|estúdio|penthouse|cobertura", t):
        out["property_type"] = "apartment"

    scripts = " ".join(s.get_text() for s in soup.find_all("script"))
    mlat = re.search(r"latitude\W{1,4}(-?\d{1,2}\.\d+)", scripts)
    mlon = re.search(r"longitude\W{1,4}(-?\d{1,3}\.\d+)", scripts)
    if mlat and mlon:
        out["latitude"], out["longitude"] = float(mlat.group(1)), float(mlon.group(1))
    return out


def load_existing_ids(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        return {row["id"] for row in csv.DictReader(f) if "id" in row}


def main():
    ap = argparse.ArgumentParser(description="Idealista.pt District Scraper by Municipality")
    ap.add_argument("--operation", choices=["comprar", "arrendar"], default="comprar")
    ap.add_argument("--municipality", choices=PORTO_MUNICIPALITIES, help="Scrape a single municipality")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--output", default="porto_district_all.csv")
    ap.add_argument("--min-delay", type=float, default=12.0)
    ap.add_argument("--max-delay", type=float, default=30.0)
    args = ap.parse_args()

    setup_logging("idealista_district.log")
    out_path = Path(args.output)
    seen = load_existing_ids(out_path)
    new_file = not out_path.exists()

    targets = [args.municipality] if args.municipality else PORTO_MUNICIPALITIES
    op_path = "comprar-casas" if args.operation == "comprar" else "arrendar-casas"

    log.info("=" * 60)
    log.info("Target Municipalities : %d", len(targets))
    log.info("Operation            : %s", args.operation)
    log.info("Output file          : %s (%d listings already saved)", out_path, len(seen))
    log.info("=" * 60)

    with sync_playwright() as pw, out_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()

        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-PT",
            timezone_id="Europe/Lisbon",
            viewport={"width": 1366, "height": 850},
            extra_http_headers=REALISTIC_HEADERS
        )

        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.navigator.chrome = { runtime: {} };
        """)

        # Main search page tab
        search_page = ctx.new_page()

        try:
            for concelho in targets:
                start_url = f"{BASE}/{op_path}/{concelho}/"
                log.info("\n>>> STARTING MUNICIPALITY: %s (%s)", concelho.upper(), start_url)

                search_page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
                search_page.wait_for_timeout(2000)
                check_and_solve_block(search_page, start_url)
                accept_cookies(search_page)

                for page_no in range(1, args.max_pages + 1):
                    log.info("[%s] Parsing search page %d...", concelho, page_no)
                    
                    # Scroll down naturally to load all listing cards and bottom pagination controls
                    search_page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
                    search_page.wait_for_timeout(1000)
                    search_page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                    search_page.wait_for_timeout(1500)

                    html = search_page.content()
                    check_and_solve_block(search_page, search_page.url)

                    cards = parse_listing_cards(html)
                    if not cards:
                        log.info("[%s] No cards found on page %d. Moving to next municipality.", concelho, page_no)
                        break

                    log.info("[%s] Found %d listings on page %d", concelho, len(cards), page_no)

                    # Process listings in a separate tab to preserve search page state
                    for i, card in enumerate(cards, 1):
                        if card["id"] in seen:
                            log.info("  [%d/%d] Skip %s (already saved)", i, len(cards), card["id"])
                            continue

                        pause(args.min_delay, args.max_delay, "(listing pause)")
                        
                        detail_tab = ctx.new_page()
                        try:
                            detail_tab.goto(card["url"], wait_until="domcontentloaded", timeout=60000)
                            detail_tab.wait_for_timeout(1500)
                            detail_html = check_and_solve_block(detail_tab, card["url"])
                            detail = parse_detail_page(detail_html)
                        except Exception as e:
                            log.warning("  [%d/%d] Failed loading detail page %s: %s", i, len(cards), card["id"], e)
                            continue
                        finally:
                            detail_tab.close()

                        rec = {**card, **{k: v for k, v in detail.items() if v is not None}}
                        rec["municipality"] = concelho
                        rec["operation"] = args.operation
                        rec["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

                        writer.writerow(rec)
                        fh.flush()
                        seen.add(rec["id"])

                        log.info("  [%d/%d] Saved %s | %s€ | %s m² | %s",
                                 i, len(cards), rec["id"], rec.get("price_eur"),
                                 rec.get("area_m2"), rec.get("typology"))

                    # Now back on search_page, locate and click the "Seguinte" button
                    pause(4.0, 8.0, "(before next page click)")
                    next_button = search_page.query_selector("li.next a, a.icon-arrow-right-after, a:has-text('Seguinte')")
                    if next_button:
                        try:
                            next_button.click()
                            search_page.wait_for_load_state("domcontentloaded", timeout=60000)
                            search_page.wait_for_timeout(2000)
                        except PWTimeout:
                            log.warning("[%s] Timeout clicking Next page button.", concelho)
                            break
                    else:
                        log.info("[%s] Next page button not found. Reached end of %s.", concelho, concelho)
                        break

        except KeyboardInterrupt:
            log.warning("Script stopped by user.")
        finally:
            browser.close()

    log.info("Scraping completed. File saved to %s", out_path.resolve())


if __name__ == "__main__":
    main()