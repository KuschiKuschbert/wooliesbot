#!/usr/bin/env python3
"""
e2e_validate.py — End-to-end price validation for WooliesBot

Four validation layers:
  A  Website (live API) vs data.json stored prices
       - WW PDP items: GET the /productdetails/ URL directly (like-for-like with scraper)
       - WW search items: POST the search API
       - Coles: call BFF via product ID in URL
  B  data.json internal consistency (all_stores ↔ item-level fields ↔ scrape_history)
  C  data.json vs dashboard rendering (emulates app.js eff_price || price logic)
  D  Link validity — confirm each stored URL returns a live page for the right product
       - WW PDP: GET page, check HTTP 200, extract JSON-LD name, verify overlap
       - Coles PDP: BFF call, verify name matches stored name_check

Usage:
  python scripts/e2e_validate.py                  # run all 4 layers, sample 25 items
  python scripts/e2e_validate.py --all            # sample all items for Layer A/D (slow)
  python scripts/e2e_validate.py --sample 50      # custom sample size
  python scripts/e2e_validate.py --layer B        # run only Layer B (fast, no network)
  python scripts/e2e_validate.py --layer C        # run only Layer C (fast, no network)
  python scripts/e2e_validate.py --layer D        # run only Layer D link check
  python scripts/e2e_validate.py --item "Milk"    # filter to items matching name substring
  python scripts/e2e_validate.py --layer D --json-out logs/e2e_links.json
  python scripts/e2e_validate.py --layer D --all --repair-bad-links --json-out logs/e2e_links.json
  python scripts/e2e_validate.py --apply-url-metadata logs/e2e_links.json  # dry-run by default
  python scripts/e2e_validate.py --apply-url-metadata logs/e2e_links.json --write
"""

import argparse
import datetime
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote_plus, quote

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wooliesbot_shared import (
    extract_coles_product_id as _shared_extract_coles_product_id,
    extract_size_signals as _shared_extract_size_signals,
    size_signals_compatible as _shared_size_signals_compatible,
    token_overlap_score as _shared_token_overlap_score,
)
from e2e_validate_lib import url_metadata as _url_metadata

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_JSON = REPO_ROOT / "docs" / "data.json"
METRICS_JSON = REPO_ROOT / "logs" / "scraper_metrics.json"

# ---------------------------------------------------------------------------
# curl_cffi (same library as chef_os.py)
# ---------------------------------------------------------------------------
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    print("WARNING: curl_cffi not available — Layer A (live website check) will be skipped.")

# ---------------------------------------------------------------------------
# Constants (mirror chef_os.py)
# ---------------------------------------------------------------------------
COLES_BFF_KEY = os.environ.get("WOOLIESBOT_COLES_BFF_KEY", "eae83861d1cd4de6bb9cd8a2cd6f041e")
COLES_BFF_STORE_ID = os.environ.get("WOOLIESBOT_COLES_STORE_ID", "0584")
PRICE_UNRELIABLE = 9000.0

UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        "platform": '"macOS"',
        "impersonate": "chrome131",
    },
]

# ---------------------------------------------------------------------------
# Helpers: session creation
# ---------------------------------------------------------------------------
def _make_session(impersonate="chrome131"):
    if not HAS_CFFI:
        return None
    try:
        return cffi_requests.Session(impersonate=impersonate)
    except Exception:
        return cffi_requests.Session(impersonate="chrome124")


# ---------------------------------------------------------------------------
# Helper: search term extraction (mirrors chef_os._extract_search_term_from_url)
# ---------------------------------------------------------------------------
def _extract_search_term(url):
    return _url_metadata.extract_search_term(url)


def _is_search_url(url):
    return _url_metadata.is_search_url(url)


def _is_pdp_url(url):
    return _url_metadata.is_pdp_url(url)


def _extract_coles_product_id(url):
    return _shared_extract_coles_product_id(url)


def _url_type_for_store_url(store, url):
    return _url_metadata.url_type_for_store_url(store, url, _extract_coles_product_id)


def _layer_summary(results):
    return {
        "ok": sum(1 for r in results if r.get("match") == "OK"),
        "diff": sum(1 for r in results if r.get("match") == "DIFF"),
        "warn": sum(1 for r in results if r.get("match") == "WARN"),
        "dead": sum(1 for r in results if r.get("match") == "DEAD"),
        "skip": sum(1 for r in results if r.get("match") == "SKIP"),
        "total": len(results),
    }


def _build_url_metadata_records(layer_d_results):
    return _url_metadata.build_url_metadata_records(
        layer_d_results, _extract_coles_product_id
    )


def _best_search_term_for_item(item, store, live_name=None):
    return _url_metadata.best_search_term_for_item(item, store, live_name=live_name)


def _build_store_search_url(store, term):
    return _url_metadata.build_store_search_url(store, term)


def _repair_bad_link_records(records, items):
    return _url_metadata.repair_bad_link_records(records, items)


def _load_data_container():
    with open(DATA_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return raw, raw
    return raw, raw.get("items", [])


def _save_data_container(raw, items):
    payload = items if isinstance(raw, list) else {**raw, "items": items}
    DATA_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve_item_for_metadata_record(items, record):
    return _url_metadata.resolve_item_for_metadata_record(items, record)


def _set_if_changed(obj, key, value):
    return _url_metadata.set_if_changed(obj, key, value)


def apply_url_metadata_records(items, records):
    return _url_metadata.apply_url_metadata_records(items, records)


def _token_overlap_score(inv_name, scraped_name):
    """Jaccard-like overlap on alphanumeric tokens (mirrors chef_os._token_overlap_score).
    Applies basic plural normalization (strips trailing 's') to improve matching
    for "Carrot" vs "Carrots", "Can" vs "Cans", etc."""
    return _shared_token_overlap_score(inv_name, scraped_name, plural_stem=True)


def _extract_size_signals(text):
    """Extract numeric size signals from a label (mirrors chef_os._extract_size_signals)."""
    return _shared_extract_size_signals(text)


def _size_signals_compatible(inventory_name, scraped_name):
    """Return False when inventory and scraped labels clearly disagree on size
    (mirrors chef_os._size_signals_compatible)."""
    return _shared_size_signals_compatible(inventory_name, scraped_name)


def _extract_ww_json_ld_name_price(html):
    """Extract product name and price from Woolworths JSON-LD embedded in HTML.
    Returns (name, price) or (None, None)."""
    try:
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.IGNORECASE | re.DOTALL,
        ):
            try:
                raw = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue
            # Walk possible wrapper structures
            nodes = raw if isinstance(raw, list) else raw.get("@graph", [raw])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                t = node.get("@type", "")
                if t in ("Product", "product") or (isinstance(t, list) and "Product" in t):
                    name = node.get("name", "")
                    # Price can be in offers.price or offers[0].price
                    offers = node.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price_raw = offers.get("price")
                    try:
                        price = float(price_raw) if price_raw else None
                    except (TypeError, ValueError):
                        price = None
                    if name:
                        return name, price
    except Exception:
        pass
    return None, None


def _get_woolworths_pdp_headers(url):
    """Return headers suitable for a WW PDP GET request."""
    profile = UA_PROFILES[0]
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": profile["ua"],
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Referer": "https://www.woolworths.com.au/",
    }


# ---------------------------------------------------------------------------
# Layer A helpers: live price fetch
# ---------------------------------------------------------------------------

def _warm_woolworths_session(session):
    """Warm up the session so cookies are set."""
    try:
        profile = UA_PROFILES[0]
        resp = session.get(
            "https://www.woolworths.com.au/",
            headers={"User-Agent": profile["ua"]},
            timeout=15,
        )
        return resp.status_code == 200 and len(resp.text) > 50000
    except Exception:
        return False


def _fetch_woolworths_live(session, url, inventory_name):
    """Fetch the current price for a Woolworths item.

    Branches on URL type for like-for-like comparison with the scraper:
    - PDP URL (/productdetails/): GET the page directly and extract JSON-LD price.
      This avoids false positives from the search API returning a different pack.
    - Search URL: POST the search API (original behaviour).

    Returns {"price": float, "name": str, "via": str} or None.
    """
    if not session:
        return None
    if _is_pdp_url(url):
        return _fetch_woolworths_pdp(session, url, inventory_name)
    # --- Search API path ---
    search_term = _extract_search_term(url) if _is_search_url(url) else inventory_name
    if not search_term:
        return None
    profile = UA_PROFILES[0]
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://www.woolworths.com.au/shop/search/products",
        "User-Agent": profile["ua"],
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Origin": "https://www.woolworths.com.au",
    }
    payload = {
        "Filters": [],
        "IsSpecial": False,
        "Location": f"/shop/search/products?searchTerm={search_term}",
        "PageNumber": 1,
        "PageSize": 3,
        "SearchTerm": search_term,
        "SortType": "TraderRelevance",
    }
    try:
        resp = session.post(
            "https://www.woolworths.com.au/apis/ui/Search/products",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        products = data.get("Products") or []
        if not products:
            return None
        for bundle in products[:3]:
            for prod in (bundle.get("Products") or [])[:1]:
                price = prod.get("Price")
                if price and price > 0:
                    return {
                        "price": float(price),
                        "eff_price": float(price),
                        "name": prod.get("Name", ""),
                        "was_price": prod.get("WasPrice"),
                        "on_special": bool(prod.get("IsSpecial")),
                        "via": "search",
                    }
    except Exception as e:
        logging.debug(f"Woolworths search API error for '{inventory_name}': {e}")
    return None


def _fetch_woolworths_pdp(session, url, inventory_name):
    """GET a Woolworths PDP URL and extract price + name from embedded JSON-LD.
    Returns {"price": float, "name": str, "via": "pdp", "http_status": int} or None.
    """
    try:
        resp = session.get(url, headers=_get_woolworths_pdp_headers(url), timeout=20)
        if resp.status_code == 404:
            return {"price": None, "name": None, "via": "pdp", "http_status": 404}
        if resp.status_code != 200 or len(resp.text) < 5000:
            return {"price": None, "name": None, "via": "pdp", "http_status": resp.status_code}
        name, price = _extract_ww_json_ld_name_price(resp.text)
        return {
            "price": price,
            "name": name,
            "via": "pdp",
            "http_status": resp.status_code,
        }
    except Exception as e:
        logging.debug(f"WW PDP fetch error for '{inventory_name}': {e}")
    return None


def _fetch_coles_live(session, url):
    """Fetch the current price from Coles BFF API.
    Returns {"price": float, "eff_price": float, "name": str} or None."""
    if not session:
        return None
    pid = _extract_coles_product_id(url)
    if not pid:
        return None
    api_url = (
        f"https://www.coles.com.au/api/bff/products/{pid}"
        f"?storeId={COLES_BFF_STORE_ID}&subscription-key={COLES_BFF_KEY}"
    )
    try:
        resp = session.get(api_url, headers={"Accept": "application/json"}, timeout=15)
        if resp.status_code != 200 or not resp.text:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        pricing = data.get("pricing")
        if not pricing or not pricing.get("now"):
            return None
        price = float(pricing["now"])
        was = pricing.get("was")
        return {
            "price": price,
            "eff_price": price,
            "name": data.get("name", ""),
            "brand": data.get("brand", ""),
            "size": data.get("size", ""),
            "was_price": float(was) if was and float(was) > 0 else None,
            "on_special": pricing.get("promotionType") == "SPECIAL",
        }
    except Exception as e:
        logging.debug(f"Coles BFF error for url={url}: {e}")
    return None


# ---------------------------------------------------------------------------
# Dashboard rendering emulator (mirrors app.js logic)
# ---------------------------------------------------------------------------

def _dashboard_displayed_price(item):
    """Emulate app.js: eff_price || price (line 692).
    Returns the float price the dashboard would show, or None if unavailable."""
    if item.get("price_unavailable"):
        return None
    eff = item.get("eff_price")
    price = item.get("price")
    if eff and eff < PRICE_UNRELIABLE:
        return float(eff)
    if price and price > 0:
        return float(price)
    return None


def _dashboard_store_prices(item):
    """Emulate app.js store comparison (lines 733-739).
    Returns {"woolworths": float|None, "coles": float|None}."""
    all_stores = item.get("all_stores") or {}
    result = {}
    for store in ("woolworths", "coles"):
        sd = all_stores.get(store)
        if sd:
            ep = sd.get("eff_price")
            p = sd.get("price")
            if ep and ep < PRICE_UNRELIABLE:
                result[store] = float(ep)
            elif p and p > 0:
                result[store] = float(p)
            else:
                result[store] = None
        else:
            result[store] = None
    return result


# ---------------------------------------------------------------------------
# Load data.json
# ---------------------------------------------------------------------------

def load_data():
    with open(DATA_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return raw
    return raw.get("items", [])


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_price(p):
    if p is None:
        return "—"
    try:
        return f"${float(p):.2f}"
    except (TypeError, ValueError):
        return str(p)


def _price_match(a, b, tolerance=0.02):
    """Return True if prices are within tolerance (default 2 cents)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


COL_W = [40, 10, 10, 10, 8, 22]
HEADERS = ["Item", "Live", "Stored", "Display", "Match", "Notes"]

def _print_header(title):
    print(f"\n{'='*110}")
    print(f"  {title}")
    print(f"{'='*110}")
    row = "  ".join(h.ljust(COL_W[i]) for i, h in enumerate(HEADERS))
    print(row)
    print("-" * 110)


def _print_row(item_name, live, stored, display, match, notes=""):
    cells = [
        item_name[:38].ljust(COL_W[0]),
        _fmt_price(live).ljust(COL_W[1]),
        _fmt_price(stored).ljust(COL_W[2]),
        _fmt_price(display).ljust(COL_W[3]),
        match.ljust(COL_W[4]),
        notes[:COL_W[5]],
    ]
    print("  ".join(cells))


def _print_summary(results):
    ok = sum(1 for r in results if r["match"] == "OK")
    diff = sum(1 for r in results if r["match"] == "DIFF")
    warn = sum(1 for r in results if r["match"] == "WARN")
    skip = sum(1 for r in results if r["match"] == "SKIP")
    print(f"\n  Summary: {ok} OK  |  {diff} DIFF  |  {warn} WARN  |  {skip} SKIP  |  {len(results)} total")


# ---------------------------------------------------------------------------
# Layer A: Live website vs data.json
# ---------------------------------------------------------------------------

def run_layer_a(items, sample_size=25, filter_name=None):
    """For a sample of items, fetch live prices and compare to data.json.

    WW PDP items: GET /productdetails/ URL (like-for-like with scraper).
    WW search items: POST search API.
    Coles: call BFF via product ID in URL.
    """
    print("\n\nLAYER A — Website (live API) vs data.json")

    if not HAS_CFFI:
        print("  SKIP: curl_cffi not installed.")
        return []

    # Filter
    if filter_name:
        items = [i for i in items if filter_name.lower() in i.get("name", "").lower()]

    # Pick a representative sample across both stores
    has_ww = [i for i in items if i.get("woolworths")]
    has_coles = [i for i in items if i.get("coles")]
    sample_ww_n = min(len(has_ww), sample_size // 2)
    sample_c_n = min(len(has_coles), sample_size - sample_ww_n)
    sampled_ww = random.sample(has_ww, sample_ww_n)
    remaining_coles = [i for i in has_coles if i not in sampled_ww]
    sampled_coles_only = random.sample(remaining_coles, min(len(remaining_coles), sample_c_n))
    sampled = sampled_ww + sampled_coles_only

    print(f"  Sample: {len(sampled)} items ({sample_ww_n} WW + {len(sampled_coles_only)} Coles-only)")
    print("  Warming up Woolworths session...")

    session = _make_session()
    ww_ok = _warm_woolworths_session(session) if session else False
    if not ww_ok:
        print("  WARNING: Woolworths warm-up failed — WW live checks may be unreliable")

    _print_header("Layer A: Live Price vs Stored Price")
    results = []

    for item in sampled:
        name = item.get("name", "?")
        item_id = item.get("item_id")
        ww_url = item.get("woolworths", "")
        coles_url = item.get("coles", "")
        stored_ww = None
        stored_coles = None
        all_stores = item.get("all_stores") or {}

        # For comparison we always use the raw pack price (not normalised eff_price),
        # so that live BFF/search prices (which return pack prices) are comparable.
        if all_stores.get("woolworths"):
            sd = all_stores["woolworths"]
            stored_ww = sd.get("price")
            if stored_ww and float(stored_ww) >= PRICE_UNRELIABLE:
                stored_ww = None

        if all_stores.get("coles"):
            sd = all_stores["coles"]
            stored_coles = sd.get("price")
            if stored_coles and float(stored_coles) >= PRICE_UNRELIABLE:
                stored_coles = None

        # -- Woolworths live check --
        if ww_url:
            live = _fetch_woolworths_live(session, ww_url, name)
            live_price = live["price"] if live else None
            via = (live or {}).get("via", "search")
            http_status = (live or {}).get("http_status")
            if http_status == 404:
                match = "DEAD"
                notes = "PDP 404 — link broken"
            elif stored_ww is None:
                match = "SKIP"
                notes = "no WW price in all_stores — skipped compare"
            elif live_price is None:
                match = "SKIP"
                notes = f"blocked/no result (via {via})"
            elif _price_match(live_price, stored_ww):
                match = "OK"
                notes = f"via {via}"
            else:
                delta_amt = live_price - float(stored_ww)
                live_name = (live or {}).get("name", "")
                notes = f"Δ${delta_amt:+.2f} via {via}"
                if live_name:
                    notes += f" [{live_name[:12]}]"
                # Search API: small deltas are often a different pack/flavour.
                if via == "search" and abs(delta_amt) <= 2.0:
                    match = "WARN"
                    notes += " (search ambiguity)"
                else:
                    mx = max(live_price, float(stored_ww))
                    mn = min(live_price, float(stored_ww))
                    ratio = mn / mx if mx > 0 else 1.0
                    # Moderate drift since last scrape, or deli/unit display vs JSON-LD mismatch.
                    if abs(delta_amt) <= 7.0 or ratio <= 0.38:
                        match = "WARN"
                        notes += " (live drift / unit ambiguity)"
                    else:
                        match = "DIFF"
            _print_row(f"{name} (WW)", live_price, stored_ww, None, match, notes)
            results.append({"item": name, "store": "woolworths", "live": live_price,
                            "stored": stored_ww, "match": match, "notes": notes})
            time.sleep(0.8 if via == "pdp" else 0.5)

        # -- Coles live check --
        if coles_url and _extract_coles_product_id(coles_url):
            live = _fetch_coles_live(session, coles_url)
            live_price = live["price"] if live else None
            if stored_coles is None:
                match = "SKIP"
                notes = "no Coles price in all_stores — skipped compare"
            elif live_price is None:
                match = "SKIP"
                notes = "no BFF result"
            elif _price_match(live_price, stored_coles):
                match = "OK"
                notes = ""
            else:
                delta_amt = live_price - float(stored_coles)
                notes = f"Δ${delta_amt:+.2f}"
                mx = max(live_price, float(stored_coles))
                mn = min(live_price, float(stored_coles))
                ratio = mn / mx if mx > 0 else 1.0
                if abs(delta_amt) <= 7.0 or ratio <= 0.38:
                    match = "WARN"
                    notes += " (live drift / unit ambiguity)"
                else:
                    match = "DIFF"
            _print_row(f"{name} (Coles)", live_price, stored_coles, None, match, notes)
            results.append({"item": name, "store": "coles", "live": live_price,
                            "stored": stored_coles, "match": match, "notes": notes})
            time.sleep(0.3)

    _print_summary(results)

    # Classify mismatches
    diffs = [r for r in results if r["match"] == "DIFF"]
    if diffs:
        print(f"\n  DIFF classification:")
        for r in diffs:
            if r["stored"] is None:
                print(f"    → {r['item']} ({r['store']}): stored=None — likely stale/unavailable in data.json")
            elif r["live"] is None:
                print(f"    → {r['item']} ({r['store']}): live=None — blocked or product moved")
            elif abs(r["live"] - float(r["stored"])) < 0.50:
                print(f"    → {r['item']} ({r['store']}): small Δ {r['notes']} — possible price update since scrape")
            else:
                print(f"    → {r['item']} ({r['store']}): large Δ {r['notes']} — SCRAPER BUG candidate")

    return results


# ---------------------------------------------------------------------------
# Layer B: data.json internal consistency
# ---------------------------------------------------------------------------

def _layer_b_canonical_snapshot_price(item):
    """Match chef_os export_data_to_json snapshot_price (eff_price preferred over shelf price)."""
    sp = item.get("eff_price", item.get("price", 0))
    if sp is None:
        sp = item.get("price")
    return sp


def run_layer_b(items, filter_name=None):
    """Verify data.json internal consistency:
    - item.price / item.eff_price match best store in all_stores
    - scrape_history[-1].price matches export snapshot field (eff_price or price)
    - no orphan fields (price but no all_stores, etc.)
    """
    print("\n\nLAYER B — data.json Internal Consistency")

    if filter_name:
        items = [i for i in items if filter_name.lower() in i.get("name", "").lower()]

    from collections import Counter

    ids = [i.get("item_id") for i in items if isinstance(i, dict) and i.get("item_id")]
    id_dup = [k for k, v in Counter(ids).items() if v > 1]
    names = [i.get("name") for i in items if isinstance(i, dict)]
    name_dup = [k for k, v in Counter(names).items() if k and v > 1]
    missing_id = sum(1 for i in items if isinstance(i, dict) and not i.get("item_id"))

    if id_dup:
        print(f"  IDENTITY FAIL: duplicate item_id ({len(id_dup)} keys): {id_dup[:6]}")
    if name_dup:
        print(f"  IDENTITY WARN: duplicate display name ({len(name_dup)}): {name_dup[:4]}...")
    if missing_id:
        print(f"  IDENTITY WARN: {missing_id} items missing item_id")

    import datetime
    today = datetime.date.today().isoformat()
    results = []
    _print_header("Layer B: Internal Consistency")

    for item in items:
        name = item.get("name", "?")
        notes_list = []
        match = "OK"

        price = item.get("price")
        eff_price = item.get("eff_price")
        all_stores = item.get("all_stores") or {}
        price_unavail = item.get("price_unavailable", False)
        stale = item.get("stale", False)
        sh = item.get("scrape_history") or []

        # B1: price_unavailable items should have no meaningful price
        if price_unavail:
            if price and float(price) > 0 and float(price) < PRICE_UNRELIABLE:
                notes_list.append("price_unavailable but price>0")
                match = "WARN"

        # B2: item.price should be consistent with all_stores[item_store].price.
        # DIFF: item.price is HIGHER than all_stores (a genuine bug — should never cost more than scraped).
        # WARN: item.price is LOWER than all_stores (likely intentional: carry-forward of a special price
        #       via _maybe_confirm_outlier_price, or same-day price rise after special ended).
        if not price_unavail and not stale and all_stores:
            item_store = item.get("store")
            price_mode = item.get("price_mode", "each")
            if item_store and item_store in all_stores:
                sd = all_stores[item_store]
                stored_price = sd.get("price")
                if stored_price and float(stored_price) < PRICE_UNRELIABLE:
                    check_val = price if price_mode == "each" else eff_price
                    stored_check = stored_price if price_mode == "each" else sd.get("eff_price", stored_price)
                    if check_val is not None and not _price_match(check_val, stored_check):
                        delta = float(check_val) - float(stored_check)
                        if delta > 0.02:
                            # item.price is HIGHER than what was scraped — bug
                            notes_list.append(
                                f"item.price={_fmt_price(check_val)} ABOVE all_stores[{item_store}]="
                                f"{_fmt_price(stored_check)} (+${delta:.2f})"
                            )
                            match = "DIFF"
                        else:
                            # item.price is LOWER — carried-forward special or outlier hold
                            notes_list.append(
                                f"item.price={_fmt_price(check_val)} below all_stores[{item_store}]="
                                f"{_fmt_price(stored_check)} (special carry-forward?)"
                            )
                            if match == "OK":
                                match = "WARN"

        # B3: eff_price should not be unreliable unless price_unavailable
        if not price_unavail and eff_price is not None and float(eff_price) >= PRICE_UNRELIABLE:
            notes_list.append(f"eff_price={eff_price} (unreliable) but price_unavailable=False")
            match = "WARN"

        # B4: scrape_history last entry vs canonical snapshot value (same as export_data_to_json).
        # Note: scrape_history records only once per day (first run of the day), so
        # same-day price changes create a legitimate small delta. Use tiered thresholds.
        if sh and not stale and not price_unavail:
            last_entry = sh[-1]
            hist_price = last_entry.get("price")
            hist_store = last_entry.get("store")
            item_store = item.get("store")
            canonical = _layer_b_canonical_snapshot_price(item)
            if hist_price is not None and canonical is not None:
                hist_compare = float(hist_price)
                # Litre rows can carry historical shelf snapshots while canonical is $/L.
                # Accept whichever interpretation is closer to canonical.
                if item.get("price_mode") == "litre":
                    pack_l = item.get("pack_litres")
                    if isinstance(pack_l, (int, float)) and float(pack_l) > 0:
                        shelf_norm = float(hist_price) / float(pack_l)
                        if abs(shelf_norm - float(canonical)) < abs(hist_compare - float(canonical)):
                            hist_compare = shelf_norm

                delta = abs(hist_compare - float(canonical))
                if hist_store == item_store:
                    if delta > 1.00:
                        # For litre rows, compare shelf-equivalent delta as well.
                        litre_same_store_warn = False
                        if item.get("price_mode") == "litre":
                            pack_l = item.get("pack_litres")
                            if isinstance(pack_l, (int, float)) and float(pack_l) > 0:
                                shelf_snapshot = float(hist_price)
                                shelf_canonical = float(canonical) * float(pack_l)
                                if abs(shelf_snapshot - shelf_canonical) <= 1.00:
                                    litre_same_store_warn = True

                        if litre_same_store_warn:
                            notes_list.append("mid-day price change (litre-normalized snapshot)")
                            if match == "OK":
                                match = "WARN"
                            continue

                        notes_list.append(
                            f"scrape_history[-1]={_fmt_price(hist_compare)} Δ{delta:+.2f} "
                            f"vs snapshot={_fmt_price(canonical)} (same store={item_store})"
                        )
                        match = "DIFF"
                    elif delta > 0.01:
                        notes_list.append(f"mid-day price change Δ${delta:.2f} (store={item_store})")
                        if match == "OK":
                            match = "WARN"
                elif hist_store != item_store and delta > 1.00:
                    notes_list.append(
                        f"store changed {hist_store}→{item_store}, "
                        f"hist={_fmt_price(hist_price)} vs snapshot={_fmt_price(canonical)}"
                    )
                    if match == "OK":
                        match = "WARN"

        # B5: scrape_history last entry date should be today (if not stale)
        if sh and not stale and not price_unavail:
            last_date = sh[-1].get("date", "")
            if last_date != today:
                notes_list.append(f"last scrape_history date={last_date!r} (not today {today!r})")
                match = "WARN"

        # B6: if all_stores is empty and item is not price_unavailable, warn
        if not price_unavail and not all_stores:
            notes_list.append("all_stores empty but not price_unavailable")
            match = "WARN"

        # B7: unit semantics guard for litre items.
        # In production data, many liquid items legitimately keep pack-level pricing
        # while still carrying unit="litre", so this is an advisory warning, not a hard fail.
        item_unit = (item.get("unit") or "").lower()
        price_mode = item.get("price_mode", "each")
        if item_unit == "litre" and price_mode != "litre":
            notes_list.append(f"unit=litre but price_mode={price_mode!r} (expected 'litre')")
            if match == "OK":
                match = "WARN"

        if price_mode == "litre":
            pack_l = item.get("pack_litres")
            if not isinstance(pack_l, (int, float)) or float(pack_l) <= 0:
                notes_list.append("price_mode=litre but pack_litres missing/invalid")
                if match == "OK":
                    match = "WARN"

            for sk, sd in all_stores.items():
                ep = sd.get("eff_price")
                up = sd.get("unit_price")
                if ep is None or up is None:
                    continue
                try:
                    epf = float(ep)
                    upf = float(up)
                except (TypeError, ValueError):
                    continue
                if epf >= PRICE_UNRELIABLE:
                    continue
                if abs(epf - upf) > 0.05:
                    notes_list.append(
                        f"all_stores[{sk}] eff_price={_fmt_price(epf)} vs unit_price={_fmt_price(upf)}"
                    )
                    if match == "OK":
                        match = "WARN"

        if match != "OK" or not results or len(results) < 5:
            # Print all non-OK and first few OK for reference
            _print_row(name, price, eff_price, None, match, "; ".join(notes_list)[:22])
        elif match == "OK" and len([r for r in results if r["match"] == "OK"]) <= 3:
            _print_row(name, price, eff_price, None, match, "(sample)")

        results.append({"item": name, "match": match, "notes": "; ".join(notes_list)})

    if id_dup:
        results.append(
            {
                "item": "__inventory_identity__",
                "match": "DIFF",
                "notes": f"duplicate item_id count={len(id_dup)}",
            }
        )
    if name_dup:
        results.append(
            {
                "item": "__duplicate_names__",
                "match": "WARN",
                "notes": f"duplicate name rows={len(name_dup)}",
            }
        )

    ok = sum(1 for r in results if r["match"] == "OK")
    warn = sum(1 for r in results if r["match"] == "WARN")
    diff = sum(1 for r in results if r["match"] == "DIFF")
    print(f"  ... {ok + warn + diff} items checked (only issues and samples shown above)")
    _print_summary(results)

    return results


# ---------------------------------------------------------------------------
# Layer C: data.json vs dashboard rendering
# ---------------------------------------------------------------------------

def run_layer_c(items, filter_name=None):
    """Verify dashboard would display the correct prices:
    - Emulate eff_price || price from app.js
    - Check store comparison rows (all_stores.woolworths/coles)
    - Verify on_special / was_price logic matches the hasSaneWas condition
    """
    print("\n\nLAYER C — data.json vs Dashboard Rendering")

    if filter_name:
        items = [i for i in items if filter_name.lower() in i.get("name", "").lower()]

    results = []
    _print_header("Layer C: Dashboard Rendering Consistency")

    for item in items:
        name = item.get("name", "?")
        notes_list = []
        match = "OK"

        price = item.get("price")
        eff_price = item.get("eff_price")
        on_special = item.get("on_special", False)
        was_price = item.get("was_price")
        price_unavail = item.get("price_unavailable", False)

        # C1: Dashboard main price = eff_price || price
        displayed = _dashboard_displayed_price(item)

        if not price_unavail:
            if displayed is None:
                notes_list.append("dashboard would show nothing (no eff_price or price)")
                match = "WARN"
            elif eff_price and float(eff_price) < PRICE_UNRELIABLE:
                if not _price_match(displayed, eff_price):
                    notes_list.append(f"display={_fmt_price(displayed)} ≠ eff_price={_fmt_price(eff_price)}")
                    match = "DIFF"

        # C2: hasSaneWas check (app.js line 718): on_special && was_price > shelfPrice && was_price < shelfPrice * 4
        shelf_price = price if price else displayed
        if on_special and was_price and shelf_price:
            has_sane_was = float(was_price) > float(shelf_price) and float(was_price) < float(shelf_price) * 4
            if not has_sane_was:
                notes_list.append(
                    f"on_special=True but was_price={_fmt_price(was_price)} fails sanity check "
                    f"(shelf={_fmt_price(shelf_price)}) — dashboard won't show Was/Save badge"
                )
                match = "WARN"

        # C3: store comparison row — both stores must have eff_price or price
        store_prices = _dashboard_store_prices(item)
        all_stores = item.get("all_stores") or {}
        if all_stores.get("woolworths") and all_stores.get("coles"):
            # Both stores present in data.json; check dashboard would render both
            if store_prices["woolworths"] is None:
                notes_list.append("WW in all_stores but dashboard price=None (unreliable?)")
                match = "WARN"
            if store_prices["coles"] is None:
                notes_list.append("Coles in all_stores but dashboard price=None (unreliable?)")
                match = "WARN"

        if match != "OK":
            _print_row(name, eff_price, price, displayed, match, "; ".join(notes_list)[:22])
        elif len([r for r in results if r["match"] == "OK"]) <= 3:
            _print_row(name, eff_price, price, displayed, match, "(sample)")

        results.append({"item": name, "match": match, "notes": "; ".join(notes_list),
                        "displayed": displayed})

    ok = sum(1 for r in results if r["match"] == "OK")
    warn = sum(1 for r in results if r["match"] == "WARN")
    diff = sum(1 for r in results if r["match"] == "DIFF")
    print(f"  ... {ok + warn + diff} items checked (only issues and samples shown above)")
    _print_summary(results)

    # Classify mismatches
    issues = [r for r in results if r["match"] in ("DIFF", "WARN")]
    if issues:
        print(f"\n  Issue classification:")
        for r in issues:
            notes = r["notes"]
            if "was_price" in notes:
                print(f"    → DASHBOARD BUG candidate: {r['item']} — {notes}")
            elif "eff_price" in notes or "display" in notes:
                print(f"    → EXPORT BUG candidate: {r['item']} — {notes}")
            else:
                print(f"    → WARN: {r['item']} — {notes}")

    return results


# ---------------------------------------------------------------------------
# Layer D: Link validity (PDP URL health check + name matching)
# ---------------------------------------------------------------------------

# Column widths for Layer D table (different headers)
_D_COL_W = [38, 8, 8, 35, 7, 8]
_D_HEADERS = ["Item", "Store", "HTTP", "Live name", "Overlap", "Match"]


def _print_d_header(title):
    print(f"\n{'='*110}")
    print(f"  {title}")
    print(f"{'='*110}")
    row = "  ".join(h.ljust(_D_COL_W[i]) for i, h in enumerate(_D_HEADERS))
    print(row)
    print("-" * 110)


def _print_d_row(item_name, store, http_status, live_name, overlap, match):
    status_str = str(http_status) if http_status else "—"
    overlap_str = f"{overlap:.2f}" if overlap is not None else "—"
    live_str = (live_name or "—")[:33]
    cells = [
        item_name[:36].ljust(_D_COL_W[0]),
        store[:6].ljust(_D_COL_W[1]),
        status_str.ljust(_D_COL_W[2]),
        live_str.ljust(_D_COL_W[3]),
        overlap_str.ljust(_D_COL_W[4]),
        match.ljust(_D_COL_W[5]),
    ]
    print("  ".join(cells))


def _layer_d_name_overlap(item, live_label: str) -> float:
    """Best overlap of live label vs inventory name and vs name_check (when set)."""
    inv = item.get("name") or ""
    nc = (item.get("name_check") or "").strip()
    ov = _token_overlap_score(inv, live_label)
    if nc:
        ov = max(ov, _token_overlap_score(nc, live_label))
    return ov


def run_layer_d(items, sample_size=25, filter_name=None):
    """Link validity check.

    For Woolworths PDP URLs (/productdetails/):
      - GET the URL, check HTTP status
      - Extract product name from JSON-LD
      - Verify name overlap with stored item name / name_check
    For Coles PDP URLs:
      - Call BFF, check product ID is live
      - Verify BFF-returned name overlaps with stored name_check
    Search URLs and items with no URL are skipped with a note.
    """
    print("\n\nLAYER D — Link Validity (URL health + name matching)")

    if not HAS_CFFI:
        print("  SKIP: curl_cffi not installed.")
        return []

    if filter_name:
        items = [i for i in items if filter_name.lower() in i.get("name", "").lower()]

    # Sample items that have at least one URL
    has_url = [i for i in items if i.get("woolworths") or i.get("coles")]
    sampled = random.sample(has_url, min(len(has_url), sample_size))

    pdp_ww = sum(1 for i in sampled if _is_pdp_url(i.get("woolworths", "")))
    search_ww = sum(1 for i in sampled if _is_search_url(i.get("woolworths", "")) and not _is_pdp_url(i.get("woolworths", "")))
    coles_items = sum(1 for i in sampled if _extract_coles_product_id(i.get("coles", "")))
    print(f"  Sample: {len(sampled)} items | WW PDP={pdp_ww}  WW search={search_ww}  Coles BFF={coles_items}")
    print("  Warming up Woolworths session...")

    session = _make_session()
    ww_ok = _warm_woolworths_session(session) if session else False
    if not ww_ok:
        print("  WARNING: Woolworths warm-up failed — WW PDP checks may be unreliable")

    _print_d_header("Layer D: Link Validity")
    results = []

    for item in sampled:
        name = item.get("name", "?")
        item_id = item.get("item_id")
        ww_url = item.get("woolworths", "")
        coles_url = item.get("coles", "")

        # -- Woolworths link check --
        if ww_url:
            if _is_pdp_url(ww_url):
                result = _fetch_woolworths_pdp(session, ww_url, name)
                if result is None:
                    _print_d_row(name, "WW", None, None, None, "SKIP")
                    results.append({"item": name, "store": "woolworths", "url": ww_url,
                                    "item_id": item_id,
                                    "match": "SKIP", "notes": "fetch failed"})
                else:
                    http_status = result.get("http_status")
                    live_name = (result.get("name") or "").strip()
                    if http_status == 404:
                        _print_d_row(name, "WW", 404, None, None, "DEAD")
                        results.append({"item": name, "store": "woolworths", "url": ww_url,
                                        "item_id": item_id,
                                        "match": "DEAD", "notes": "404 — link broken"})
                    elif not live_name:
                        _print_d_row(name, "WW", http_status, None, None, "SKIP")
                        results.append({"item": name, "store": "woolworths", "url": ww_url,
                                        "item_id": item_id,
                                        "match": "SKIP", "notes": f"HTTP {http_status}, no name in JSON-LD"})
                    else:
                        overlap = _layer_d_name_overlap(item, live_name)
                        if overlap < 0.10:
                            match = "DIFF"
                        elif overlap < 0.25:
                            match = "WARN"
                        else:
                            match = "OK"
                        _print_d_row(name, "WW", http_status, live_name, overlap, match)
                        results.append({"item": name, "store": "woolworths", "url": ww_url,
                                        "item_id": item_id,
                                        "match": match, "overlap": overlap,
                                        "live_name": live_name, "http_status": http_status,
                                        "notes": f"overlap={overlap:.2f}"})
                time.sleep(1.0)
            else:
                # Search URL — no PDP to validate
                _print_d_row(name, "WW", "—", "search URL — skipped", None, "SKIP")
                results.append({"item": name, "store": "woolworths", "url": ww_url,
                                "item_id": item_id,
                                "match": "SKIP", "notes": "WW search URL, no PDP link to check"})

        # -- Coles link check --
        if coles_url and _extract_coles_product_id(coles_url):
            live = _fetch_coles_live(session, coles_url)
            if live is None:
                _print_d_row(name, "Coles", None, None, None, "DEAD")
                results.append({"item": name, "store": "coles", "url": coles_url,
                                "item_id": item_id,
                                "match": "DEAD", "notes": "BFF returned nothing — product ID invalid or delisted"})
            else:
                # Build full label including size so size-signal check works correctly.
                # BFF separates size from name (e.g. name="Max No Sugar Cola Bottle", size="1.25L").
                live_name = live.get("name", "")
                live_label = " ".join(filter(None, [live.get("brand", ""), live_name, live.get("size", "")])).strip()
                overlap = _layer_d_name_overlap(item, live_label) if live_label else None
                size_ok = _size_signals_compatible(name, live_label)
                if overlap is None:
                    match = "SKIP"
                elif not size_ok:
                    # Same SKU line often varies by retailer pack weight (250g punnet vs 480g tray).
                    match = "WARN" if overlap >= 0.33 else "DIFF"
                elif overlap < 0.10:
                    match = "DIFF"
                elif overlap < 0.25:
                    match = "WARN"
                else:
                    match = "OK"
                display_name = live_label if live_label else live_name
                _print_d_row(name, "Coles", 200, display_name, overlap, match)
                results.append({"item": name, "store": "coles", "url": coles_url,
                                "item_id": item_id,
                                "match": match, "overlap": overlap,
                                "live_name": display_name,
                                "notes": (f"size_conflict" if not size_ok else f"overlap={overlap:.2f}") if overlap is not None else "no name"})
            time.sleep(0.3)

    ok = sum(1 for r in results if r["match"] == "OK")
    diff = sum(1 for r in results if r["match"] == "DIFF")
    warn = sum(1 for r in results if r["match"] == "WARN")
    dead = sum(1 for r in results if r["match"] == "DEAD")
    skip = sum(1 for r in results if r["match"] == "SKIP")
    print(f"\n  Summary: {ok} OK  |  {diff} DIFF  |  {warn} WARN  |  {dead} DEAD  |  {skip} SKIP  |  {len(results)} total")

    dead_links = [r for r in results if r["match"] == "DEAD"]
    if dead_links:
        print(f"\n  DEAD links ({len(dead_links)}) — update URLs in data.json:")
        for r in dead_links:
            print(f"    → [{r['store'].upper()}] {r['item']}: {r['url']}")

    name_diffs = [r for r in results if r["match"] == "DIFF"]
    if name_diffs:
        print(f"\n  Name mismatches ({len(name_diffs)}) — URL points to wrong product:")
        for r in name_diffs:
            print(f"    → [{r['store'].upper()}] {r['item']}: live='{r.get('live_name', '?')[:40]}' overlap={r.get('overlap', '?'):.2f}")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WooliesBot end-to-end price validator")
    parser.add_argument("--all", action="store_true", help="Check all items in Layer A/D (slow)")
    parser.add_argument("--sample", type=int, default=25, help="Sample size for Layer A/D (default: 25)")
    parser.add_argument("--layer", choices=["A", "B", "C", "D"], help="Run only one layer")
    parser.add_argument("--item", type=str, default=None, help="Filter by item name substring")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit with code 1 if any layer has DIFF or DEAD (WARN/SKIP do not fail).",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        help="Write machine-readable report JSON to this path.",
    )
    parser.add_argument(
        "--apply-url-metadata",
        type=str,
        default=None,
        help="Apply url_metadata_records from a JSON report into docs/data.json.",
    )
    parser.add_argument(
        "--repair-bad-links",
        action="store_true",
        help="Convert Layer D DEAD/DIFF PDP URLs to store search fallback URLs before output/apply.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="When used with --apply-url-metadata, persist changes to docs/data.json (default is dry-run).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if args.seed is not None:
        random.seed(args.seed)

    if args.write and not args.apply_url_metadata:
        parser.error("--write requires --apply-url-metadata")

    print(f"\nWooliesBot e2e Validator")
    print(f"  data.json: {DATA_JSON}")
    print(f"  Last modified: {DATA_JSON.stat().st_mtime:.0f} "
          f"({__import__('datetime').datetime.fromtimestamp(DATA_JSON.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')})")

    items = load_data()
    print(f"  Loaded {len(items)} items from data.json")

    # Print last scraper metrics
    if METRICS_JSON.exists():
        try:
            runs = json.loads(METRICS_JSON.read_text())
            r = runs[-1] if runs else {}
            print(f"  Last scrape: {r.get('ts', '?')}  |  cffi_rate={r.get('cffi_success_rate', '?'):.1%}"
                  f"  |  items={r.get('items', '?')}")
        except Exception:
            pass

    all_results = {}
    apply_only_mode = bool(args.apply_url_metadata) and args.layer is None and not args.all and not args.item and args.sample == 25
    run_a = (not apply_only_mode) and args.layer in (None, "A")
    run_b = (not apply_only_mode) and args.layer in (None, "B")
    run_c = (not apply_only_mode) and args.layer in (None, "C")
    run_d = (not apply_only_mode) and args.layer in (None, "D")

    if run_b:
        all_results["B"] = run_layer_b(items, filter_name=args.item)

    if run_c:
        all_results["C"] = run_layer_c(items, filter_name=args.item)

    if run_a:
        sample_size = len(items) if args.all else args.sample
        all_results["A"] = run_layer_a(items, sample_size=sample_size, filter_name=args.item)

    if run_d:
        sample_size = len(items) if args.all else args.sample
        all_results["D"] = run_layer_d(items, sample_size=sample_size, filter_name=args.item)

    # Final summary
    print("\n" + "=" * 110)
    print("  FINAL SUMMARY")
    print("=" * 110)
    if apply_only_mode:
        print("  Validation layers skipped (apply-only mode).")
    layer_summaries = {}
    for layer, results in all_results.items():
        s = _layer_summary(results)
        layer_summaries[layer] = s
        ok = s["ok"]
        diff = s["diff"]
        warn = s["warn"]
        dead = s["dead"]
        skip = s["skip"]
        status = "PASS" if diff == 0 and dead == 0 else "FAIL"
        dead_str = f"  DEAD={dead}" if dead else ""
        print(f"  Layer {layer}: {status}  |  OK={ok}  DIFF={diff}  WARN={warn}{dead_str}  SKIP={skip}  total={len(results)}")

    # Mismatch classification guide
    has_issues = any(
        any(r["match"] in ("DIFF", "DEAD") for r in res)
        for res in all_results.values()
    )
    if has_issues:
        print("\n  Mismatch guide:")
        print("    Layer A DIFF  → SCRAPER BUG: live website price differs from data.json")
        print("                    Fix: chef_os.py check_prices() or _cffi_fetch_product helpers")
        print("    Layer B DIFF  → EXPORT BUG: check_prices() result correct but data.json wrong")
        print("                    Fix: chef_os.py export_data_to_json() (line 2064)")
        print("    Layer C DIFF  → DASHBOARD BUG: data.json correct but app.js renders it wrong")
        print("                    Fix: docs/app.js rendering code (lines 692-739)")
        print("    Layer D DEAD  → BROKEN LINK: stored URL returns 404 — update in data.json")
        print("    Layer D DIFF  → WRONG PRODUCT: URL points to a different item — update in data.json")
    print()

    layer_d_records = _build_url_metadata_records(all_results.get("D", []))
    if args.repair_bad_links and layer_d_records:
        layer_d_records, repair_stats = _repair_bad_link_records(layer_d_records, items)
        print("  Repair candidates:")
        print(f"    eligible: {repair_stats['eligible']}")
        print(f"    repaired: {repair_stats['repaired']}")
        print(f"    already_search: {repair_stats['already_search']}")
        print(f"    unchanged: {repair_stats['unchanged']}")
        print(f"    not_found: {repair_stats['not_found']}")

    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "data_json_path": str(DATA_JSON),
            "data_json_last_modified_unix": DATA_JSON.stat().st_mtime,
            "item_count": len(items),
            "args": {
                "all": bool(args.all),
                "sample": args.sample,
                "layer": args.layer,
                "item": args.item,
                "seed": args.seed,
                "strict_exit": bool(args.strict_exit),
            },
            "layer_summaries": layer_summaries,
            "results": all_results,
            "url_metadata_records": layer_d_records,
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  Wrote JSON report: {out_path}")

    if args.apply_url_metadata:
        apply_path = Path(args.apply_url_metadata)
        if not apply_path.is_absolute():
            apply_path = REPO_ROOT / apply_path
        if not apply_path.exists():
            print(f"  ERROR: metadata file not found: {apply_path}")
            sys.exit(2)
        report = json.loads(apply_path.read_text(encoding="utf-8"))
        records = report.get("url_metadata_records", [])
        if not isinstance(records, list):
            print(f"  ERROR: invalid url_metadata_records in {apply_path}")
            sys.exit(2)

        if args.repair_bad_links:
            records, repair_stats = _repair_bad_link_records(records, items)
            print("  Repair before apply:")
            print(f"    eligible: {repair_stats['eligible']}  repaired: {repair_stats['repaired']}  already_search: {repair_stats['already_search']}  unchanged: {repair_stats['unchanged']}  not_found: {repair_stats['not_found']}")

        raw_container, mutable_items = _load_data_container()
        stats = apply_url_metadata_records(mutable_items, records)
        mode = "WRITE" if args.write else "DRY-RUN"
        print(f"\n  URL metadata apply ({mode})")
        print(f"    source file: {apply_path}")
        print(f"    records total: {stats['records_total']}")
        print(f"    items touched: {stats['applied']}")
        print(f"    fields changed: {stats['changed_fields']}")
        print(f"    skipped invalid: {stats['skipped_invalid']}")
        print(f"    skipped not_found: {stats['skipped_not_found']}")
        print(f"    skipped ambiguous: {stats['skipped_ambiguous']}")
        if args.write:
            _save_data_container(raw_container, mutable_items)
            print(f"    wrote: {DATA_JSON}")
        else:
            print("    no file write performed (use --write to persist)")

    if getattr(args, "strict_exit", False):
        failed = False
        for res in all_results.values():
            if any(r["match"] in ("DIFF", "DEAD") for r in res):
                failed = True
        if failed:
            sys.exit(1)


if __name__ == "__main__":
    main()
