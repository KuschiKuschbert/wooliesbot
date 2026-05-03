"""Coles JSON / BFF / _next/data search stack (driver + curl_cffi)."""

import json
import logging
import random
import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wooliesbot_shared import extract_coles_product_id as _shared_extract_coles_product_id

from scripts.price_utils import _enrich_with_unit_price, _parse_coles_unit
from scraper.config import _COLES_BFF_STORE_ID, _COLES_BFF_SUBSCRIPTION_KEY
from scraper.matching import _token_overlap_score
from scraper.session import _create_cffi_session, _get_coles_headers, _get_random_ua_profile

_coles_cached_build_id = None  # single global — do not redeclare below

COLES_WARMUP_URL = "https://www.coles.com.au/product/coles-beef-rump-steak-approx.-832g-5132370"


def _parse_coles_build_id_from_html(html):
    """Extract Next.js buildId from Coles HTML (homepage or product shell)."""
    if not html:
        return None
    m = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def _apply_coles_build_id_from_html(html, source="html"):
    """Set global _coles_cached_build_id when found in HTML."""
    global _coles_cached_build_id
    bid = _parse_coles_build_id_from_html(html)
    if bid:
        _coles_cached_build_id = bid
        logging.info(f"Coles buildId from {source}: {bid}")
        return True
    return False


def _refresh_coles_metadata(session):
    """Fetch Coles homepage to extract the current Next.js buildId."""
    global _coles_cached_build_id
    try:
        logging.debug("Refreshing Coles buildId...")
        resp = session.get(
            "https://www.coles.com.au/product/a-123456",
            headers=_get_coles_headers(_get_random_ua_profile()),
            timeout=10,
        )
        if _apply_coles_build_id_from_html(resp.text, "coles_product_shell"):
            return True
    except Exception as e:
        logging.debug(f"Coles metadata refresh failed: {e}")
    return False


def _extract_coles_json(driver):
    """Extract product data from Coles __NEXT_DATA__ (Next.js) embedded in page.
    Returns dict with price, unit_price, unit, name_check, is_special or None."""
    try:
        wait = WebDriverWait(driver, 10)
        nd_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "script#__NEXT_DATA__")))
        nd = json.loads(nd_el.get_attribute("innerHTML"))
        product = nd.get("props", {}).get("pageProps", {}).get("product", {})
        pricing = product.get("pricing", {})
        if not pricing or not pricing.get("now"):
            return None
        price = float(pricing["now"])
        unit_data = pricing.get("unit", {}) or {}
        unit_price_val = unit_data.get("price")
        unit_type = (unit_data.get("ofMeasureUnits") or "").lower()
        unit = "each"
        if unit_type == "kg":
            unit = "kg"
        elif unit_type in ("l", "litre", "ltr"):
            unit = "litre"
        elif "100g" in unit_type:
            unit = "100g"
        up = float(unit_price_val) if unit_price_val else None
        image_uris = product.get("imageUris", [])
        image_url = image_uris[0].get("uri", "") if image_uris else ""
        if image_url and image_url.startswith("/"):
            image_url = "https://www.coles.com.au" + image_url

        return {
            "price": price,
            "unit_price": up,
            "unit": unit,
            "name_check": product.get("name", ""),
            "is_special": pricing.get("promotionType") == "SPECIAL",
            "was_price": pricing.get("was"),
            "image_url": image_url,
        }
    except Exception as e:
        logging.debug(f"  Coles __NEXT_DATA__ parse error: {e}")
    return None


def _cffi_fetch_coles_api(session, url, build_id):
    """Fetch Coles product data directly via the Next.js data API (very fast)."""
    try:
        match = re.search(r"/product/([^?#]+)", url)
        if not match:
            return None
        prod_slug = match.group(1)

        api_url = f"https://www.coles.com.au/_next/data/{build_id}/product/{prod_slug}.json"

        resp = session.get(api_url, headers=_get_coles_headers(_get_random_ua_profile()), timeout=10)
        global _coles_cached_build_id
        if resp.status_code == 404:
            logging.warning("Coles _next/data API 404 — buildId may have expired")
            _coles_cached_build_id = None
            return None
        if resp.status_code == 200:
            data = resp.json().get("pageProps", {}).get("product", {})
            pricing = data.get("pricing", {})
            if pricing and pricing.get("now"):
                price = float(pricing["now"])
                unit_data = pricing.get("unit", {}) or {}
                up = float(unit_data.get("price")) if unit_data.get("price") else None
                unit_type = (unit_data.get("ofMeasureUnits") or "").lower()
                unit = "kg" if unit_type == "kg" else ("litre" if unit_type in ("l", "litre") else "each")

                image_uris = data.get("imageUris", [])
                img = "https://www.coles.com.au" + image_uris[0].get("uri", "") if image_uris else ""

                return {
                    "price": price,
                    "unit_price": up,
                    "unit": unit,
                    "image_url": img,
                    "was_price": pricing.get("was"),
                    "is_special": pricing.get("promotionType") == "SPECIAL",
                    "name_check": data.get("name", ""),
                }
    except Exception as e:
        logging.debug(f"Coles API fetch error: {e}")
    return None


def _extract_coles_product_id(url):
    """Extract numeric product ID from a Coles PDP URL (the trailing digits)."""
    return _shared_extract_coles_product_id(url)


def _cffi_fetch_coles_bff(session, url, store_id=None):
    """Fetch Coles product data via the BFF REST API (bypasses Imperva entirely).
    Returns a scrape-result dict on success, None on failure."""
    pid = _extract_coles_product_id(url)
    if not pid:
        return None
    sid = store_id or _COLES_BFF_STORE_ID
    api_url = (
        f"https://www.coles.com.au/api/bff/products/{pid}"
        f"?storeId={sid}&subscription-key={_COLES_BFF_SUBSCRIPTION_KEY}"
    )
    try:
        resp = session.get(api_url, headers={"Accept": "application/json"}, timeout=15)
        if resp.status_code != 200 or not resp.text:
            logging.debug(f"Coles BFF HTTP {resp.status_code} for pid={pid}")
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        pricing = data.get("pricing")
        if not pricing or not pricing.get("now"):
            return None
        price = float(pricing["now"])
        unit_data = pricing.get("unit") or {}
        up_raw = unit_data.get("price")
        up = float(up_raw) if up_raw else None
        measure = (unit_data.get("ofMeasureUnits") or "").lower()
        of_type = (unit_data.get("ofMeasureType") or "").lower()
        if measure in ("kg", "g") or of_type in ("kg", "g"):
            unit = "kg"
        elif measure in ("l", "litre", "ml") or of_type in ("l", "litre", "ml"):
            unit = "litre"
        elif measure == "100g" or of_type == "100g":
            unit = "100g"
        else:
            unit = "each"
        image_uris = data.get("imageUris") or []
        img = ""
        if image_uris:
            uri = image_uris[0].get("uri", "")
            if uri:
                img = f"https://productimages.coles.com.au{uri}" if uri.startswith("/") else uri
        was = pricing.get("was")
        is_special = pricing.get("promotionType") == "SPECIAL"
        return {
            "price": price,
            "unit_price": up,
            "unit": unit,
            "image_url": img,
            "was_price": float(was) if was and float(was) > 0 else None,
            "is_special": is_special,
            "name_check": data.get("name", ""),
        }
    except Exception as e:
        logging.debug(f"Coles BFF error pid={pid}: {e}")
    return None


def _scrape_coles_bff(items_with_urls):
    """Scrape all Coles items via the BFF REST API.
    Returns (list of (idx, data), list of (idx, item, url) failures)."""
    if not items_with_urls:
        return [], []
    logging.info(f"[Coles] BFF API scan for {len(items_with_urls)} items...")
    session = _create_cffi_session("coles")
    results = []
    failed = []
    for item_num, (idx, item, url) in enumerate(items_with_urls):
        data = _cffi_fetch_coles_bff(session, url)
        if data:
            data["store"] = "coles"
            results.append((idx, data))
            logging.info(
                f"[Coles] ({item_num+1}/{len(items_with_urls)}) ✓ {item['name']} ${data['price']:.2f}"
            )
        else:
            failed.append((idx, item, url))
            logging.info(
                f"[Coles] ({item_num+1}/{len(items_with_urls)}) ✗ {item['name']} (no BFF pricing)"
            )
        time.sleep(random.uniform(0.3, 0.8))
    logging.info(
        f"[Coles] BFF API done: {len(results)}/{len(items_with_urls)} succeeded, "
        f"{len(failed)} need Chrome fallback"
    )
    return results, failed


def _extract_coles_json_from_html(html):
    """Extract product data from Coles __NEXT_DATA__ in raw HTML.
    Returns dict with price, unit_price, unit, name_check, is_special or None."""
    try:
        m = re.search(
            r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not m:
            return None
        nd = json.loads(m.group(1).strip())
        product = nd.get("props", {}).get("pageProps", {}).get("product", {})
        pricing = product.get("pricing", {})
        if not pricing or not pricing.get("now"):
            return None
        price = float(pricing["now"])
        unit_data = pricing.get("unit", {}) or {}
        unit_price_val = unit_data.get("price")
        unit_type = (unit_data.get("ofMeasureUnits") or "").lower()
        unit = "each"
        if unit_type == "kg":
            unit = "kg"
        elif unit_type in ("l", "litre", "ltr"):
            unit = "litre"
        elif "100g" in unit_type:
            unit = "100g"
        up = float(unit_price_val) if unit_price_val else None
        image_uris = product.get("imageUris", [])
        image_url = image_uris[0].get("uri", "") if image_uris else ""
        if image_url and image_url.startswith("/"):
            image_url = "https://www.coles.com.au" + image_url

        return {
            "price": price,
            "unit_price": up,
            "unit": unit,
            "name_check": product.get("name", ""),
            "is_special": pricing.get("promotionType") == "SPECIAL",
            "was_price": pricing.get("was"),
            "image_url": image_url,
        }
    except Exception as e:
        logging.debug(f"  Coles __NEXT_DATA__ from HTML parse error: {e}")
    return None


def _coles_body_looks_blocked(text):
    """Detect Akamai / Imperva / bot interstitial HTML (not a product page)."""
    if not text:
        return True
    if "Pardon Our Interruption" in text:
        return True
    low = text.lower()[:3000]
    if "incapsula" in low or "visid_incap" in low or "imperva" in low:
        return True
    if "<html" in low and "application/ld+json" not in low and "__next_data__" not in low:
        if "coles.com.au" in low or "challenge" in low or "akamai" in low:
            return True
    return False


def _cffi_get_coles_build_id(session):
    """Extract Coles Next.js buildId. Tries homepage first, then product page, then cache."""
    global _coles_cached_build_id
    for url in ["https://www.coles.com.au/", COLES_WARMUP_URL]:
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                m = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
                if m:
                    _coles_cached_build_id = m.group(1)
                    logging.info(f"Coles buildId extracted: {_coles_cached_build_id}")
                    return _coles_cached_build_id
        except Exception as e:
            logging.debug(f"Coles buildId attempt ({url[:40]}): {e}")
    if _coles_cached_build_id:
        logging.info(f"Using cached Coles buildId: {_coles_cached_build_id}")
        return _coles_cached_build_id
    try:
        resp = session.get("https://www.coles.com.au/_next/data/", timeout=10)
        if resp.status_code == 404:
            m = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
            if m:
                _coles_cached_build_id = m.group(1)
                logging.info(f"Coles buildId from 404: {_coles_cached_build_id}")
                return _coles_cached_build_id
    except Exception:
        pass
    logging.warning("Coles buildId extraction failed and no cache available")
    return None


def _cffi_search_coles(session, query, build_id, max_results=5):
    """Search Coles via _next/data API using curl_cffi (no browser).
    Returns (results, did_you_mean_list)."""
    global _coles_cached_build_id
    try:
        encoded = query.replace(" ", "+")
        api_url = f"https://www.coles.com.au/_next/data/{build_id}/search/products.json?q={encoded}"
        resp = None
        for attempt in range(2):
            resp = session.get(api_url, headers=_get_coles_headers(_get_random_ua_profile()), timeout=20)
            if resp.status_code == 429 and attempt == 0:
                time.sleep(25 + random.random() * 40)
                continue
            break
        if resp is None:
            return [], None
        if resp.status_code == 404:
            logging.warning("Coles search 404 — buildId may have expired, clearing cache")
            _coles_cached_build_id = None
            return [], None
        if resp.status_code != 200:
            logging.warning(f"Coles search API HTTP {resp.status_code}")
            return [], None
        if _coles_body_looks_blocked(resp.text):
            logging.warning("Coles search blocked (interstitial HTML instead of JSON)")
            return [], None
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logging.warning("Coles search: response was not JSON (likely blocked)")
            return [], None
        sr = data.get("pageProps", {}).get("searchResults", {})
        did_you_mean = sr.get("didYouMean")
        results = []
        for r in sr.get("results", [])[:max_results]:
            if r.get("_type") != "PRODUCT":
                continue
            pricing = r.get("pricing", {})
            now_price = pricing.get("now")
            if now_price is None or now_price <= 0:
                continue
            cup_price, cup_measure = _parse_coles_unit(pricing)
            pid = r.get("id") or r.get("productId") or ""
            result = {
                "name": r.get("name", ""),
                "brand": r.get("brand", ""),
                "product_id": str(pid) if pid is not None else "",
                "price": float(now_price),
                "was_price": pricing.get("was"),
                "cup_price": cup_price,
                "cup_measure": cup_measure,
                "cup_string": pricing.get("comparable", ""),
                "size": r.get("size", ""),
                "on_special": pricing.get("promotionType") == "SPECIAL",
                "store": "coles",
            }
            _enrich_with_unit_price(result)
            results.append(result)
        logging.info(f"Coles search '{query}': {len(results)} results (didYouMean={did_you_mean})")
        return results, did_you_mean
    except Exception as e:
        logging.error(f"Coles search error: {e}")
        return [], None


def _rank_coles_search_results_for_inventory(inventory_name, results):
    """Sort search hits by token overlap with inventory label (best first)."""
    if not results:
        return []
    scored = []
    inv = inventory_name or ""
    for r in results:
        label = f"{r.get('brand', '')} {r.get('name', '')}".strip()
        s = _token_overlap_score(inv, label)
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored]


def _coles_product_url_from_search_hit(hit):
    """Build canonical Coles PDP URL from a search API hit."""
    name = hit.get("name", "") or "product"
    pid = (hit.get("product_id") or "").strip()
    if not pid:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "product"
    return f"https://www.coles.com.au/product/{slug}-{pid}"


def _coles_needs_spelling_retry(query, results, did_you_mean):
    """Check if Coles returned garbage and should retry with didYouMean.
    Returns the corrected query or None."""
    if not did_you_mean or not isinstance(did_you_mean, list):
        return None
    if not results:
        return did_you_mean[0] if did_you_mean else None
    query_lower = query.lower()
    query_words = set(query_lower.split())
    for r in results[:3]:
        name_lower = (r.get("name", "") + " " + r.get("brand", "")).lower()
        if any(w in name_lower for w in query_words if len(w) >= 3):
            return None
    return did_you_mean[0]
