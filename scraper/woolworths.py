"""Woolworths JSON-LD / Search API extraction (driver + curl_cffi)."""

import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from constants import _RECEIPT_ABBREVIATIONS
from scripts.price_utils import _parse_woolworths_cup, _enrich_with_unit_price
from scraper.matching import _extract_size_signals, _token_overlap_score
from scraper.run_state import scrape_run_stats
from scraper.session import _get_run_ua_profile


def _extract_woolworths_json(driver):
    """Extract product data from Woolworths JSON-LD (schema.org) embedded in page.
    Returns dict with price, unit_price, unit, name_check or None."""
    from selenium.webdriver.common.by import By

    try:
        scripts = driver.find_elements(By.CSS_SELECTOR, 'script[type="application/ld+json"]')
        for s in scripts:
            data = json.loads(s.get_attribute("innerHTML"))
            p = _walk_woolworths_ld_node(data)
            if p:
                return p
    except Exception as e:
        logging.debug(f"  Woolworths JSON-LD parse error: {e}")
    return None


def _woolworths_product_from_ld_dict(data):
    """Build product scrape dict from a schema.org Product JSON-LD object."""
    if not isinstance(data, dict):
        return None
    t = data.get("@type")
    if t != "Product" and not (isinstance(t, list) and "Product" in t):
        return None
    offers = data.get("offers", {})
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    price = offers.get("price")
    if not price or float(price) == 0:
        return None
    spec = offers.get("priceSpecification", {}) or {}
    if isinstance(spec, list) and spec:
        spec = spec[0]
    if not isinstance(spec, dict):
        spec = {}
    unit_price_val = spec.get("price")
    unit_text = (spec.get("unitText") or "").lower()
    unit = "each"
    if "kg" in unit_text and "100g" not in unit_text:
        unit = "kg"
    elif "100g" in unit_text:
        unit = "100g"
    elif "ml" in unit_text or "litre" in unit_text:
        unit = "litre"
    up = float(unit_price_val) if unit_price_val and float(unit_price_val) > 0 else None
    img = data.get("image", "")
    if isinstance(img, list) and img:
        img = img[0]
    return {
        "price": float(price),
        "unit_price": up,
        "unit": unit,
        "name_check": data.get("name", ""),
        "image_url": img if isinstance(img, str) else "",
    }


def _walk_woolworths_ld_node(node):
    """Depth-first search for Product nodes (@graph, arrays, nested)."""
    p = _woolworths_product_from_ld_dict(node)
    if p:
        return p
    if isinstance(node, dict):
        g = node.get("@graph")
        if isinstance(g, list):
            for el in g:
                p = _walk_woolworths_ld_node(el)
                if p:
                    return p
        for v in node.values():
            if isinstance(v, (dict, list)):
                p = _walk_woolworths_ld_node(v)
                if p:
                    return p
    elif isinstance(node, list):
        for el in node:
            p = _walk_woolworths_ld_node(el)
            if p:
                return p
    return None


def _extract_woolworths_json_from_html(html):
    """Extract product data from Woolworths JSON-LD in raw HTML.
    Returns dict with price, unit_price, unit, name_check or None."""
    try:
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                raw = json.loads(m.group(1).strip())
                p = _walk_woolworths_ld_node(raw)
                if p:
                    return p
            except json.JSONDecodeError:
                continue
    except Exception as e:
        logging.debug(f"  Woolworths JSON-LD from HTML parse error: {e}")
    return None


def _is_woolworths_search_url(url):
    return "searchTerm=" in url or "/search/" in url


def _clean_search_term(inventory_name):
    """Build a friendlier search term from a raw inventory name.
    Strips sizes, receipt abbreviations, and punctuation that confuse search."""
    t = inventory_name
    t = re.sub(r"\d+\.?\d*\s*(g|kg|ml|l|pk|pack|ea|each)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d+[xX]\d+\b", "", t)
    for abbr, full in _RECEIPT_ABBREVIATIONS.items():
        t = re.sub(rf"\b{re.escape(abbr)}\b", full, t, flags=re.IGNORECASE)
    t = re.sub(r"[#&/]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = [w for w in t.split() if len(w) > 1][:6]
    return " ".join(words)


def _extract_search_term_from_url(url):
    """Pull the search term from a Woolworths search URL.
    Handles edge cases: literal '&' / '#' inside the value, fragment leakage."""
    url = url.replace("searchTerm=#", "searchTerm=")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    term = qs.get("searchTerm", [""])[0]
    if not term and parsed.fragment:
        term = parsed.fragment
    return unquote(term).strip() if term else ""


def _cffi_search_woolworths_product(session, search_term, inventory_name=None):
    """Use the Woolworths search API to get the top product result with pricing.
    Returns a product dict or None."""
    api_url = "https://www.woolworths.com.au/apis/ui/Search/products"
    profile = _get_run_ua_profile()
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
        resp = session.post(api_url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 429:
            scrape_run_stats["http_429"] = scrape_run_stats.get("http_429", 0) + 1
            return None
        if resp.status_code != 200:
            logging.debug(f"Woolworths search API HTTP {resp.status_code} for '{search_term[:30]}'")
            return None
        data = resp.json()
        products = data.get("Products") or []
        if not products:
            return None
        bundle = products[0]
        items = bundle.get("Products") or []
        if not items:
            return None
        _SIZE_MODIFIERS = {"small", "mini", "large", "bulk", "family", "mega"}
        inv_text = (inventory_name or search_term).lower()
        inv_sizes = _SIZE_MODIFIERS & set(re.findall(r"[a-z]+", inv_text))
        best_result = None
        best_overlap = 0.0
        for bundle in products[:3]:
            for prod in (bundle.get("Products") or [])[:1]:
                price = prod.get("Price")
                if not price or price <= 0:
                    continue
                api_name = prod.get("Name", "")
                ov = _token_overlap_score(inventory_name or search_term, api_name)
                if inv_sizes:
                    result_sizes = _SIZE_MODIFIERS & set(re.findall(r"[a-z]+", api_name.lower()))
                    if result_sizes and result_sizes != inv_sizes:
                        logging.debug(f"Search skip conflicting size: want={inv_sizes} got={result_sizes} name={api_name!r}")
                        continue
                    if not result_sizes:
                        ov *= 0.5
                if ov < 0.2:
                    logging.debug(f"Search skip low overlap ({ov:.2f}): want={inventory_name!r} got={api_name!r}")
                    continue
                a_sig = _extract_size_signals(inventory_name or search_term)
                b_sig = _extract_size_signals(api_name)
                if a_sig["weights_g"] and not b_sig["weights_g"]:
                    if any(w >= 500 for w in a_sig["weights_g"]) and ov < 0.35:
                        logging.debug(
                            f"Search skip large-weight mismatch ({ov:.2f}<0.35): "
                            f"want={inventory_name!r} got={api_name!r}"
                        )
                        continue
                if ov > best_overlap:
                    best_overlap = ov
                    best_result = prod
        if not best_result:
            return None
        prod = best_result
        price = prod.get("Price")
        cup_str = prod.get("CupString", "")
        cup_price = prod.get("CupPrice")
        cup_measure = (prod.get("CupMeasure") or "").strip().upper().replace(" ", "")
        unit = "each"
        if cup_measure in ("1KG", "KG"):
            unit = "kg"
        elif cup_measure in ("100G", "10G"):
            unit = "100g"
        elif cup_measure in ("1L", "100ML", "10ML"):
            unit = "litre"
        up = float(cup_price) if cup_price and float(cup_price) > 0 else None
        if unit == "100g" and up:
            pass
        elif unit == "litre" and up and "100ML" in cup_measure:
            up = up * 10
        elif unit == "litre" and up and "10ML" in cup_measure:
            up = up * 100
        elif unit == "100g" and up and "10G" in cup_measure:
            up = up * 10
        was = prod.get("WasPrice")
        image = prod.get("MediumImageFile") or prod.get("SmallImageFile") or ""
        return {
            "price": float(price),
            "unit_price": up,
            "unit": unit,
            "name_check": prod.get("Name", ""),
            "image_url": image,
            "was_price": float(was) if was and float(was) > float(price) else None,
            "is_special": bool(prod.get("IsOnSpecial")),
        }
    except Exception as e:
        logging.debug(f"Woolworths search API error for '{search_term[:30]}': {e}")
        return None


def _cffi_search_woolworths(session, query, max_results=5):
    """Search Woolworths via their internal API using curl_cffi (no browser).
    Session must have visited the homepage first for cookies."""
    try:
        resp = session.post(
            "https://www.woolworths.com.au/apis/ui/Search/products",
            json={
                "SearchTerm": query,
                "PageSize": max_results,
                "PageNumber": 1,
                "SortType": "TraderRelevance",
                "Location": f"/shop/search/products?searchTerm={query}",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logging.warning(f"Woolworths search API HTTP {resp.status_code}")
            return [], None
        data = resp.json()
        suggested = data.get("SuggestedTerm")
        results = []
        for group in (data.get("Products") or [])[:max_results]:
            prod = (group.get("Products") or [{}])[0]
            if not prod.get("Name") or prod.get("Price") is None:
                continue
            cup_price, cup_measure = _parse_woolworths_cup(prod.get("CupString", ""))
            raw_sc = prod.get("Stockcode") if prod.get("Stockcode") is not None else prod.get("StockCode")
            stockcode = None
            if raw_sc is not None:
                try:
                    stockcode = int(raw_sc)
                except (TypeError, ValueError):
                    stockcode = None
            url_slug = (prod.get("UrlFriendlyName") or "").strip()
            result = {
                "name": prod.get("Name", ""),
                "brand": prod.get("Brand", ""),
                "price": float(prod.get("Price", 0)),
                "was_price": prod.get("WasPrice"),
                "cup_price": cup_price if cup_price else prod.get("CupPrice"),
                "cup_measure": cup_measure if cup_measure else prod.get("CupMeasure", ""),
                "cup_string": prod.get("CupString", ""),
                "size": prod.get("PackageSize", ""),
                "on_special": prod.get("IsOnSpecial", False),
                "store": "woolworths",
                "stockcode": stockcode,
                "url_friendly": url_slug,
            }
            _enrich_with_unit_price(result)
            results.append(result)
        logging.info(f"Woolworths search '{query}': {len(results)} results (suggested={suggested})")
        return results, suggested
    except Exception as e:
        logging.error(f"Woolworths search error: {e}")
        return [], None
