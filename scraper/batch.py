"""Chrome + curl_cffi batch scraping (single-store batches, PDP + search paths)."""

import logging
import os
import random
import time

from curl_cffi import requests as cffi_requests
from selenium.common.exceptions import TimeoutException as SeleniumTimeout
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from constants import STORES
from scripts.price_utils import _parse_price_text, _parse_unit_price_text
from scraper.config import (
    _BATCH_PAUSE_MAX,
    _BATCH_PAUSE_MIN,
    _BATCH_SIZE,
    _CIRCUIT_BREAKER_PAUSE,
    _CIRCUIT_BREAKER_STREAK,
    _COLES_WARMUP_MIN_CHARS,
    _PDP_MIN_HTML_CHARS,
    _WOOLIES_WARMUP_MIN_CHARS,
)
from scraper.coles import _apply_coles_build_id_from_html, _extract_coles_json, _refresh_coles_metadata
from scraper.matching import _finalize_cffi_product_dict
from scraper.run_state import scrape_run_stats
from scraper.session import (
    _CFFI_IMPERSONATIONS,
    _create_cffi_session,
    _get_woolworths_headers,
    _http_retry_budget,
    _proxy_for_store,
    _sleep_request_jitter,
    _get_run_ua_profile,
)
from scraper.woolworths import (
    _cffi_search_woolworths_product,
    _clean_search_term,
    _extract_search_term_from_url,
    _extract_woolworths_json,
    _extract_woolworths_json_from_html,
    _is_woolworths_search_url,
)

MAX_RETRIES = 2  # up to 2 attempts per item


def _wait_for_real_page(driver, min_chars=5000, timeout=25):
    """Poll page_source until it grows past a JS-challenge interstitial.
    Returns True once the page exceeds *min_chars*, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if len(driver.page_source) >= min_chars:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def scrape_item_from_store(driver, url, store_key):
    """Scrape price + unit price from a single store product page.
    Strategy: 1) try structured JSON  2) fall back to CSS selectors."""
    import chef_os as _co

    store = STORES[store_key]
    try:
        driver.get(url)
        if store_key == "coles":
            _wait_for_real_page(driver, min_chars=_PDP_MIN_HTML_CHARS, timeout=20)
        else:
            time.sleep(random.uniform(1.5, 3.5))

        page_len = len(driver.page_source)
        if page_len < _PDP_MIN_HTML_CHARS:
            logging.warning(f"  [{store_key}] Page too short ({page_len} chars) — possible bot block")
            return None

        json_result = None
        if store_key == "woolworths":
            json_result = _extract_woolworths_json(driver)
        elif store_key == "coles":
            json_result = _extract_coles_json(driver)

        if json_result and json_result["price"] > 0:
            logging.info(
                f"    ✓ JSON: ${json_result['price']:.2f} (unit: {json_result.get('unit_price')}/{json_result.get('unit')})"
            )
            return {
                "price": json_result["price"],
                "unit_price": json_result.get("unit_price"),
                "unit": json_result.get("unit"),
                "image_url": json_result.get("image_url", ""),
                "was_price": json_result.get("was_price"),
                "on_special": bool(json_result.get("is_special") or json_result.get("was_price")),
            }

        logging.info("    JSON extraction failed, trying CSS selectors...")
        wait = WebDriverWait(driver, 12)
        price = None
        try:
            price_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, store["price_css"])))
            price = _parse_price_text(price_el.text)
        except Exception:
            if store.get("price_fallback_css"):
                try:
                    d_css, c_css = store["price_fallback_css"]
                    d_el = driver.find_element(By.CSS_SELECTOR, d_css)
                    c_el = driver.find_element(By.CSS_SELECTOR, c_css)
                    price = float(f"{d_el.text}.{c_el.text}")
                except Exception:
                    pass
        if price is None:
            return None

        unit_price, unit = None, None
        try:
            unit_el = driver.find_element(By.CSS_SELECTOR, store["unit_css"])
            unit_price, unit = _parse_unit_price_text(unit_el.text)
        except Exception:
            pass

        logging.info(f"    ✓ CSS: ${price:.2f} (unit: {unit_price}/{unit})")
        return {"price": price, "unit_price": unit_price, "unit": unit}
    except SeleniumTimeout:
        logging.warning(f"  [{store_key}] Page load timed out (45s) — skipping")
        return None
    except Exception as e:
        if _co._is_broken_session_error(e):
            raise _co.BrowserSessionDead(str(e)) from e
        logging.debug(f"  {store_key} scrape error: {e}")
        return None


def _scrape_store_batch(store_key, items_with_urls):
    """Scrape all items for ONE store in its own browser.
    Includes retry logic: each item gets up to MAX_RETRIES attempts.
    Returns (store_key, list of (item_index, store_data))."""
    import chef_os as _co

    label = STORES[store_key]["label"]
    logging.info(f"[{label}] Starting browser for {len(items_with_urls)} items...")
    driver = _co.get_browser()

    if store_key == "coles":
        try:
            driver.get("https://www.coles.com.au/")
            if _wait_for_real_page(driver, min_chars=_COLES_WARMUP_MIN_CHARS, timeout=30):
                logging.info(f"[{label}] Browser warm-up done (Imperva challenge passed)")
            else:
                page_len = len(driver.page_source) if driver else 0
                logging.warning(
                    f"[{label}] Warm-up blocked by Imperva ({page_len} chars after 30s) "
                    f"— skipping Chrome batch entirely"
                )
                _co._safe_quit_driver(driver)
                return store_key, []
        except Exception as e:
            logging.debug(f"[{label}] Browser warm-up: {e}")

    session_restarts = 0
    results = []
    fail_streak = 0
    try:
        for item_num, (idx, item, url) in enumerate(items_with_urls):
            logging.info(f"[{label}] ({item_num+1}/{len(items_with_urls)}) {item['name']}")
            data = None
            attempt = 0
            while attempt < MAX_RETRIES:
                try:
                    data = scrape_item_from_store(driver, url, store_key)
                except _co.BrowserSessionDead as e:
                    session_restarts += 1
                    logging.warning(
                        f"[{label}] Browser session lost ({e!s}) — restarting ({session_restarts}/{_co._MAX_BROWSER_SESSION_RESTARTS})..."
                    )
                    _co._safe_quit_driver(driver)
                    driver = None
                    if session_restarts > _co._MAX_BROWSER_SESSION_RESTARTS:
                        logging.error(
                            f"[{label}] Too many dead browser sessions — stopping Chrome batch early "
                            f"({len(results)}/{len(items_with_urls)} done)."
                        )
                        return store_key, results
                    driver = _co.get_browser()
                    continue
                if data:
                    break
                attempt += 1
                if attempt < MAX_RETRIES:
                    logging.info(f"[{label}]   Retry #{attempt+1} for {item['name']}...")
                    time.sleep(3 + attempt * 3)
            if data:
                data["store"] = store_key
                results.append((idx, data))
                fail_streak = 0
            else:
                fail_streak += 1
                safe_name = item["name"].replace(" ", "_").replace("/", "_")
                try:
                    _ss_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "screenshots")
                    os.makedirs(_ss_dir, exist_ok=True)
                    driver.save_screenshot(os.path.join(_ss_dir, f"error_{safe_name}_{store_key}.png"))
                except Exception:
                    pass
                logging.warning(f"[{label}] ✗ Failed after {MAX_RETRIES} attempts: {item['name']}")
                if fail_streak >= _CIRCUIT_BREAKER_STREAK:
                    pause = _CIRCUIT_BREAKER_PAUSE + random.uniform(10, 25)
                    logging.warning(f"[{label}] Circuit breaker: {fail_streak} failures — pausing {pause:.0f}s")
                    time.sleep(pause)
                    fail_streak = 0
            time.sleep(random.uniform(2.0, 4.5))
    except Exception as e:
        logging.error(f"[{label}] Browser crashed: {e}")
    finally:
        _co._safe_quit_driver(driver)
    logging.info(f"[{label}] Done. {len(results)}/{len(items_with_urls)} scraped.")
    return store_key, results


def _scrape_store_batch_cffi(store_key, items_with_urls):
    """Scrape items for ONE store using curl_cffi (no browser).
    Returns (store_key, list of (idx, data)) — successes only.

    Always runs sequentially with a single session to avoid fingerprint
    multiplexing and nghttp2 thread-safety issues.  Batch pauses and a
    circuit breaker reduce Akamai / Imperva detection risk.
    """
    label = STORES[store_key]["label"]
    if not items_with_urls:
        return store_key, []

    if store_key == "coles":
        logging.info(f"[{label}] Skipping curl_cffi (Imperva JS challenge — Chrome only)")
        return store_key, []

    homepage = "https://www.woolworths.com.au/" if store_key == "woolworths" else "https://www.coles.com.au/"
    logging.info(f"[{label}] Starting curl_cffi scan for {len(items_with_urls)} items (sequential)...")
    session = _create_cffi_session(store_key)
    try:
        resp = session.get(homepage, timeout=15)
        if resp.status_code != 200 or len(resp.text) < _WOOLIES_WARMUP_MIN_CHARS:
            logging.warning(
                f"[{label}] Warm-up issue (HTTP {resp.status_code}, {len(resp.text)} chars) — retrying TLS profiles"
            )
            ok = False
            for imp in _CFFI_IMPERSONATIONS:
                try:
                    proxy = _proxy_for_store(store_key)
                    if proxy:
                        session = cffi_requests.Session(
                            impersonate=imp,
                            proxies={"http": proxy, "https": proxy},
                        )
                    else:
                        session = cffi_requests.Session(impersonate=imp)
                    resp = session.get(homepage, timeout=15)
                    if resp.status_code == 200 and len(resp.text) >= _WOOLIES_WARMUP_MIN_CHARS:
                        logging.info(f"[{label}] Warm-up OK with impersonate={imp}")
                        ok = True
                        break
                except Exception as e:
                    logging.debug(f"[{label}] warm-up {imp}: {e}")
            if not ok:
                logging.warning(f"[{label}] Warm-up failed — blocked or unreachable via curl_cffi")
                return store_key, []
        logging.info(f"[{label}] Warm-up OK ({len(resp.text)} chars, cookies={len(session.cookies)})")
    except Exception as e:
        logging.error(f"[{label}] Warm-up error: {e}")
        return store_key, []

    time.sleep(random.uniform(2.0, 4.0))

    def _warm_new_session():
        """Create a fresh session and warm it up with the homepage."""
        s = _create_cffi_session(store_key)
        try:
            wr = s.get(homepage, timeout=15)
            if wr.status_code == 200 and len(wr.text) >= _WOOLIES_WARMUP_MIN_CHARS:
                return s
        except Exception:
            pass
        return s

    pdp_items = [(i, it, u) for i, it, u in items_with_urls if not _is_woolworths_search_url(u)]
    search_items = [(i, it, u) for i, it, u in items_with_urls if _is_woolworths_search_url(u)]
    logging.info(f"[{label}] {len(pdp_items)} PDP URLs, {len(search_items)} search API items")

    results = []

    if search_items:
        logging.info(f"[{label}] Phase 1: Search API for {len(search_items)} items")
        fail_streak = 0
        for batch_start in range(0, len(search_items), _BATCH_SIZE):
            batch = search_items[batch_start : batch_start + _BATCH_SIZE]
            batch_num = batch_start // _BATCH_SIZE + 1
            total_batches = (len(search_items) + _BATCH_SIZE - 1) // _BATCH_SIZE
            logging.info(
                f"[{label}] Search batch {batch_num}/{total_batches} "
                f"({len(batch)} items, {len(results)} scraped so far)"
            )
            for idx, item, url in batch:
                _sleep_request_jitter(0.5)
                inv_name = item.get("name", "")
                search_term = _extract_search_term_from_url(url)
                if not search_term:
                    search_term = inv_name
                data = _cffi_search_woolworths_product(session, search_term, inv_name)
                if not data and search_term != inv_name:
                    clean = _clean_search_term(inv_name)
                    if clean and clean != search_term:
                        _sleep_request_jitter(0.3)
                        data = _cffi_search_woolworths_product(session, clean, inv_name)
                if data:
                    result = _finalize_cffi_product_dict(data, inv_name, store_key=store_key)
                    if result:
                        result["store"] = store_key
                        results.append((idx, result))
                        fail_streak = 0
                    else:
                        fail_streak += 1
                else:
                    fail_streak += 1
                    if fail_streak >= _CIRCUIT_BREAKER_STREAK * 2:
                        logging.warning(f"[{label}] Search API: {fail_streak} consecutive misses — rotating session")
                        time.sleep(random.uniform(10, 20))
                        session = _warm_new_session()
                        fail_streak = 0
            if batch_start + _BATCH_SIZE < len(search_items):
                pause = random.uniform(8.0, 15.0)
                logging.info(f"[{label}] Search batch pause: {pause:.1f}s")
                time.sleep(pause)
        logging.info(f"[{label}] Search API done: {len(results)}/{len(search_items)} found")

    if pdp_items:
        logging.info(f"[{label}] Phase 2: PDP scrape for {len(pdp_items)} items")
        if search_items:
            pause = random.uniform(15, 25)
            logging.info(f"[{label}] Phase transition pause: {pause:.1f}s — rotating session")
            time.sleep(pause)
            session = _warm_new_session()
        fail_streak = 0
        circuit_breaks = 0
        for batch_start in range(0, len(pdp_items), _BATCH_SIZE):
            batch = pdp_items[batch_start : batch_start + _BATCH_SIZE]
            batch_num = batch_start // _BATCH_SIZE + 1
            total_batches = (len(pdp_items) + _BATCH_SIZE - 1) // _BATCH_SIZE
            logging.info(
                f"[{label}] PDP batch {batch_num}/{total_batches} "
                f"({len(batch)} items, {len(results)} scraped so far)"
            )
            for idx, item, url in batch:
                _sleep_request_jitter(1.0)
                inv_name = item.get("name", "")
                result = _cffi_fetch_product(session, url, store_key, inventory_name=inv_name)
                if isinstance(result, dict):
                    result["store"] = store_key
                    results.append((idx, result))
                    fail_streak = 0
                elif result == _CFFI_RESULT_NO_PRICE:
                    clean = _clean_search_term(inv_name)
                    if clean:
                        _sleep_request_jitter(0.3)
                        fallback = _cffi_search_woolworths_product(session, clean, inv_name)
                        if fallback:
                            fb = _finalize_cffi_product_dict(fallback, inv_name, store_key=store_key)
                            if fb:
                                fb["store"] = store_key
                                results.append((idx, fb))
                                logging.info(f"    ✓ PDP→Search fallback: {inv_name}")
                    fail_streak = 0
                else:
                    fail_streak += 1
                    if fail_streak >= _CIRCUIT_BREAKER_STREAK:
                        circuit_breaks += 1
                        pause = _CIRCUIT_BREAKER_PAUSE + random.uniform(15, 45)
                        logging.warning(
                            f"[{label}] Circuit breaker #{circuit_breaks}: {fail_streak} consecutive blocks "
                            f"— pausing {pause:.0f}s then rotating session"
                        )
                        time.sleep(pause)
                        session = _warm_new_session()
                        logging.info(f"[{label}] Fresh session created after circuit breaker")
                        fail_streak = 0
                        if circuit_breaks >= 4:
                            logging.warning(
                                f"[{label}] Too many circuit breaks ({circuit_breaks}) — stopping PDP early "
                                f"({len(results)}/{len(items_with_urls)} done)"
                            )
                            return store_key, results
            if batch_start + _BATCH_SIZE < len(pdp_items):
                pause = random.uniform(_BATCH_PAUSE_MIN, _BATCH_PAUSE_MAX)
                logging.info(f"[{label}] PDP batch pause: {pause:.1f}s — rotating session")
                time.sleep(pause)
                session = _warm_new_session()

    logging.info(f"[{label}] Done. {len(results)}/{len(items_with_urls)} scraped.")
    return store_key, results


_CFFI_RESULT_NO_PRICE = "NO_PRICE"
_CFFI_RESULT_BLOCKED = "BLOCKED"


def _cffi_fetch_product(session, url, store_key, inventory_name=None):
    """Fetch product HTML via curl_cffi and extract structured JSON data.

    Returns:
        dict  — success (product data)
        "NO_PRICE" — page loaded fine but product has no price (out of stock, etc.)
        "BLOCKED"  — HTTP error or short page (Akamai block)
        None  — exception / unknown failure
    """
    budget = _http_retry_budget()
    profile = _get_run_ua_profile()
    short_name = (inventory_name or url.split("/")[-1])[:45]

    for attempt in range(budget):
        try:
            headers = _get_woolworths_headers(url, profile)
            if attempt > 0:
                time.sleep(3.0 + random.uniform(1, 4) + attempt * 2.0)
            resp = session.get(url, headers=headers, timeout=20)
            code = resp.status_code
            body_len = len(resp.text)
            if code == 429:
                scrape_run_stats["http_429"] = scrape_run_stats.get("http_429", 0) + 1
                logging.warning(f"cffi 429 for {short_name} (attempt {attempt+1})")
            elif code >= 500:
                scrape_run_stats["http_5xx"] = scrape_run_stats.get("http_5xx", 0) + 1
            if code in (429, 502, 503, 504) and attempt < budget - 1:
                time.sleep(8.0 + random.uniform(2, 10) + attempt * 5.0)
                continue
            if code == 403:
                logging.debug(f"cffi 403 for {short_name} ({body_len} chars)")
                return _CFFI_RESULT_BLOCKED
            if code == 404:
                logging.debug(f"cffi 404 for {short_name}")
                return _CFFI_RESULT_NO_PRICE
            if code != 200:
                logging.debug(f"cffi HTTP {code} for {short_name} ({body_len} chars)")
                return _CFFI_RESULT_BLOCKED
            if body_len < _PDP_MIN_HTML_CHARS:
                logging.debug(f"cffi short page for {short_name} ({body_len} chars — probably blocked)")
                return _CFFI_RESULT_BLOCKED
            data = _extract_woolworths_json_from_html(resp.text)
            if data and data.get("price", 0) > 0:
                result = _finalize_cffi_product_dict(data, inventory_name, store_key=store_key)
                return result if result else _CFFI_RESULT_NO_PRICE
            logging.debug(f"cffi no price in HTML for {short_name} ({body_len} chars)")
            return _CFFI_RESULT_NO_PRICE
        except Exception as e:
            logging.debug(f"cffi fetch error {short_name}: {e}")
    return _CFFI_RESULT_BLOCKED
    return None


def _init_search_sessions():
    """Create curl_cffi sessions for both stores and warm them up.
    Returns (woolies_session, coles_session, coles_build_id)."""
    import scraper.coles as _col

    woolies_session = _create_cffi_session("woolworths")
    coles_session = _create_cffi_session("coles")

    try:
        resp = woolies_session.get("https://www.woolworths.com.au/", timeout=15)
        if resp.status_code != 200 or len(resp.text) < _WOOLIES_WARMUP_MIN_CHARS:
            logging.warning(f"Woolworths warm-up: HTTP {resp.status_code}, {len(resp.text)} chars — retrying")
            for imp in _CFFI_IMPERSONATIONS[1:]:
                try:
                    proxy = _proxy_for_store("woolworths")
                    if proxy:
                        woolies_session = cffi_requests.Session(
                            impersonate=imp,
                            proxies={"http": proxy, "https": proxy},
                        )
                    else:
                        woolies_session = cffi_requests.Session(impersonate=imp)
                    resp = woolies_session.get("https://www.woolworths.com.au/", timeout=15)
                    if resp.status_code == 200 and len(resp.text) >= _WOOLIES_WARMUP_MIN_CHARS:
                        logging.info(f"Woolworths warm-up OK with {imp}")
                        break
                except Exception:
                    continue
            else:
                logging.warning("Woolworths warm-up failed with all impersonations")
                woolies_session = None
    except Exception as e:
        logging.error(f"Woolworths session error: {e}")
        woolies_session = None

    _refresh_coles_metadata(coles_session)
    coles_build_id = _col._coles_cached_build_id

    if not coles_build_id:
        for imp in _CFFI_IMPERSONATIONS[1:]:
            try:
                proxy = _proxy_for_store("coles")
                if proxy:
                    coles_session = cffi_requests.Session(
                        impersonate=imp,
                        proxies={"http": proxy, "https": proxy},
                    )
                else:
                    coles_session = cffi_requests.Session(impersonate=imp)
                _refresh_coles_metadata(coles_session)
                coles_build_id = _col._coles_cached_build_id
                if coles_build_id:
                    logging.info(f"Coles warm-up OK with {imp}")
                    break
            except Exception:
                continue
        if not coles_build_id:
            logging.warning("Coles warm-up failed with all impersonations")
            coles_session = None

    return woolies_session, coles_session, coles_build_id


def _successful_idxs_from_batch(batch_results):
    return {idx for idx, _ in batch_results}


def _failed_jobs_from_batch(jobs, batch_results):
    ok = _successful_idxs_from_batch(batch_results)
    return [j for j in jobs if j[0] not in ok]
