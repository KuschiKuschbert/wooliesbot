import time
import requests
import schedule
import datetime
import sys
import traceback
import argparse
import threading
import logging
import random
import re
import os
import json
import uuid
import importlib.util as _ilu
import pathlib as _pl

from logging.handlers import RotatingFileHandler

# undetected_chromedriver is ESSENTIAL for Woolworths/Coles to bypass "Access Denied" screens
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException as SeleniumTimeout
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# curl_cffi for zero-browser search — impersonates Chrome TLS fingerprint to bypass Akamai
from curl_cffi import requests as cffi_requests
from wooliesbot_runtime import acquire_file_lock as _acquire_file_lock
from wooliesbot_runtime import release_file_lock as _release_file_lock

# --- CONFIGURATION (env vars or .env file; see .env.example) ---
def _load_dotenv():
    """Load .env if present (no extra deps)."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass
_load_dotenv()


def _env_truthy(name, default=False):
    """True if env var is set to 1/true/yes/on (or not 0/false when default=True)."""
    v = (os.environ.get(name) or "").strip().lower()
    if default:
        return v not in ("0", "false", "no", "off", "")
    return v in ("1", "true", "yes", "on")


# Cross-process mutex so launchd + `chef_os.py --now` cannot race on data.json
_SCRAPE_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chef_os_scrape.lock")
_GIT_PUSH_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chef_os_git_push.lock")


def _acquire_scrape_lock():
    """Return fd if exclusive lock acquired, else None (another run_report is active)."""
    return _acquire_file_lock(_SCRAPE_LOCK_PATH, logging.warning)


def _release_scrape_lock(fd):
    _release_file_lock(fd)


def _acquire_git_push_lock():
    """Exclusive lock so concurrent sync_to_github runs do not interleave git operations."""
    return _acquire_file_lock(_GIT_PUSH_LOCK_PATH, logging.warning)


def _release_git_push_lock(fd):
    _release_file_lock(fd)


TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

# --- LOGGING (rotating to prevent disk fill) ---
_log_format = '%(asctime)s - %(levelname)s - %(message)s'
_log_level = logging.DEBUG if _env_truthy("WOOLIESBOT_DEBUG", default=False) else logging.INFO
_log_handlers = [
    RotatingFileHandler("chef_os.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"),
    logging.StreamHandler(sys.stdout)
]
for h in _log_handlers:
    h.setFormatter(logging.Formatter(_log_format))
logging.basicConfig(level=_log_level, handlers=_log_handlers)
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    logging.warning(
        "Telegram disabled: set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env for notifications and bot commands."
    )

from constants import BIG_SHOP_START_DAY, DISCOUNT_CAP, STORES, _PRICE_UNRELIABLE
from notifications.messages import (
    WEEKLY_ESSENTIALS,
    _build_run_summary,
    _build_weekly_shopping_reminder,
    _item_store_prices,
    _multi_store_line,
    _nl,
    _price_display,
    _sp,
    _store_badge,
)
from notifications.telegram import _escape_md, send_telegram
from scripts.data_json_utils import (
    _atomic_write_json,
    _data_write_lock,
    _inventory_row_key,
    _normalize_items_payload,
)
from scripts.github_sync import export_data_to_json, sync_to_github
from scripts.price_utils import (
    _effective_price,
    _enrich_with_unit_price,
    _normalize_unit_price,
    _parse_coles_unit,
    _parse_price_text,
    _parse_unit_price_text,
    _parse_woolworths_cup,
)
from scraper.config import (
    _COLES_CFFI_WORKERS_CAP,
    _COLES_DISCOVERY_MIN_SCORE,
    _COLES_DISCOVERY_SLEEP_SEC,
    _COLES_SEQUENTIAL,
    _env_float,
    _env_int,
)
from scraper.batch import (
    _cffi_fetch_product,
    _failed_jobs_from_batch,
    _init_search_sessions,
    _scrape_store_batch,
    _scrape_store_batch_cffi,
    scrape_item_from_store,
)
from scraper.coles import (
    _apply_coles_build_id_from_html,
    _cffi_get_coles_build_id,
    _cffi_search_coles,
    _coles_body_looks_blocked,
    _coles_needs_spelling_retry,
    _coles_product_url_from_search_hit,
    _rank_coles_search_results_for_inventory,
    _refresh_coles_metadata,
    _scrape_coles_bff,
)
from scraper.matching import _extract_size_signals, _size_signals_compatible, _token_overlap_score
from scraper.metrics import _append_metrics_run, _get_chrome_fallback_threshold
from scraper.run_state import reset_scrape_run_stats, scrape_run_stats
from scraper.session import _create_cffi_session, _get_random_ua_profile, _get_run_ua_profile, reset_run_ua_profile
from scraper.woolworths import _cffi_search_woolworths

# --- PRODUCT WATCHLIST ---
# price_mode: "kg" = compare per-kg unit price | "each" = compare shelf/pack price
# compare_group: items with same group compete — cheapest wins
# Use docs/data.json for both the bot and the dashboard tracking
_inv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")


def _load_items_from_disk():
    """Read docs/data.json and return items list (never raises)."""
    try:
        with open(_inv_file, "r") as _f:
            return _normalize_items_payload(json.load(_f))
    except Exception as e:
        logging.warning(f"Failed to load docs/data.json: {e}")
        return []


try:
    TRACKING_LIST = _load_items_from_disk()
except Exception:
    TRACKING_LIST = []


def reload_tracking_list():
    """Reload inventory from disk into TRACKING_LIST (for long-running daemon)."""
    global TRACKING_LIST
    TRACKING_LIST = _load_items_from_disk()
    return TRACKING_LIST


# --- Scraper tuning (orchestrator-only; shared HTTP tuning lives in scraper.config) ---
_OUTLIER_DEVIATION_PCT = min(90, max(5, _env_float("WOOLIESBOT_OUTLIER_DEVIATION_PCT", 40)))
# Max days a carry-forward (stale) price is kept before flipping to price_unavailable.
_STALE_MAX_DAYS = max(1, _env_int("WOOLIESBOT_STALE_MAX_DAYS", 14))

# --- B-LIST: Shelf-stable items to add when Big Shop cart is under $500 ---
# Use these to bridge the gap and maximise the 10% Everyday Extra discount (saves ~$50)
B_LIST_BRIDGE_ITEMS = [
    "Toilet Paper (Quilton)",
    "Laundry Powder (Omo)",
    "Olive Oil",
    "Energizer Batteries AA",
    "Peanut Butter (Pics)",
    "Coffee (Nescafe Espresso)",
    "Dishmatic Refills",
]

NEXT_SCHEDULED_RUN = None  # Global to track next scraper run


class BrowserSessionDead(Exception):
    """Raised when the Chrome/WebDriver session is no longer usable (window closed, invalid session)."""


def _is_broken_session_error(exc):
    """True if the driver must be recreated before any further commands."""
    if isinstance(exc, (InvalidSessionIdException, NoSuchWindowException)):
        return True
    msg = str(exc).lower()
    return (
        "no such window" in msg
        or "invalid session id" in msg
        or "web view not found" in msg
        or "chrome not reachable" in msg
    )


def _safe_quit_driver(driver):
    if not driver:
        return
    try:
        driver.quit()
    except Exception:
        pass


def get_browser():
    # Use undetected_chromedriver to bypass Woolworths bot detection
    options = uc.ChromeOptions()
    options.add_argument("--headless=new") # Modern headless mode to bypass Akamai without window
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = uc.Chrome(options=options)
    # Prevent indefinite hangs on slow/stuck pages (e.g. captcha, network)
    driver.set_page_load_timeout(45)
    return driver


_MAX_BROWSER_SESSION_RESTARTS = 12  # per Chrome batch — avoids infinite loops if Chrome keeps dying


def _build_inv_map_from_file():
    """Merge key (item_id or name:*) -> item dict for price_history merge."""
    inv_data = {}
    try:
        with open(_inv_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for it in _normalize_items_payload(raw):
            k = _inventory_row_key(it)
            if k:
                inv_data[k] = it
    except Exception:
        pass
    return inv_data


def _maybe_confirm_outlier_price(item, item_result, inv_data):
    """One extra cffi fetch if shelf price diverges sharply from historical median."""
    prev = inv_data.get(_inventory_row_key(item)) or {}
    if not prev.get("price_history") and item.get("name"):
        prev = inv_data.get("name:" + item["name"], {})
    hist = prev.get("price_history") or []
    prices = [h["price"] for h in hist if h.get("price") is not None and h["price"] > 0]
    if len(prices) < 3:
        return item_result
    prices_sorted = sorted(prices)
    median_p = prices_sorted[len(prices_sorted) // 2]
    new_p = item_result.get("price") or 0
    if median_p <= 0 or new_p <= 0:
        return item_result
    dev = abs(new_p - median_p) / median_p
    if dev <= (_OUTLIER_DEVIATION_PCT / 100.0):
        return item_result
    sk = item_result.get("store")
    if sk not in STORES or sk == "none":
        return item_result
    url = item.get(sk)
    if not url:
        return item_result
    logging.info(f"Outlier confirm ({dev:.0%} vs median ${median_p:.2f}): {item['name']}")
    session = _create_cffi_session(sk)
    try:
        hw = "https://www.woolworths.com.au/" if sk == "woolworths" else "https://www.coles.com.au/"
        wresp = session.get(hw, timeout=15)
        if sk == "coles" and wresp.status_code == 200:
            _apply_coles_build_id_from_html(wresp.text, "outlier_coles_warmup")
        data = _cffi_fetch_product(session, url, sk, inventory_name=item.get("name"))
    except Exception as e:
        logging.debug(f"Outlier re-fetch failed: {e}")
        return item_result
    if not isinstance(data, dict):
        return item_result
    new_shelf = data.get("price")
    if not new_shelf:
        return item_result
    new_eff = _effective_price(item, data)
    if new_eff >= _PRICE_UNRELIABLE:
        return item_result
    old_dev = abs(new_p - median_p)
    new_dev = abs(new_shelf - median_p)
    if new_dev >= old_dev and abs(new_shelf - median_p) / median_p > (_OUTLIER_DEVIATION_PCT / 100.0):
        return item_result
    ep = _effective_price(item, data)
    all_stores = dict(item_result.get("all_stores") or {})
    if sk in all_stores:
        all_stores[sk] = {
            "price": data["price"],
            "unit_price": data.get("unit_price"),
            "eff_price": ep,
            "was_price": data.get("was_price"),
            "on_special": data.get("on_special", False),
        }
    out = {
        **item,
        "price": data["price"],
        "unit_price": data.get("unit_price"),
        "unit": data.get("unit"),
        "eff_price": ep,
        "store": sk,
        "all_stores": all_stores,
        "price_unavailable": item_result.get("price_unavailable", False),
        "image_url": data.get("image_url", item_result.get("image_url", "")),
        "was_price": data.get("was_price"),
        "on_special": data.get("on_special", False),
        "price_history": item_result.get("price_history", []),
        "avg_price": item_result.get("avg_price", 0),
    }
    remote_img = data.get("image_url") or out.get("image_url")
    if remote_img and not str(remote_img).startswith("images/"):
        local_img_path = _download_product_image(remote_img, item["name"])
        if local_img_path:
            out["image_url"] = local_img_path
    return out


def check_prices():
    """Scrape all items: Woolworths via curl_cffi (sequential), Coles via BFF API.
    Chrome fallback for items where the primary API path fails."""
    reset_run_ua_profile()
    reload_tracking_list()
    reset_scrape_run_stats()
    chrome_subset_events = 0

    store_jobs = {}
    for idx, item in enumerate(TRACKING_LIST):
        for store_key in STORES:
            url = item.get(store_key, "")
            if url:
                store_jobs.setdefault(store_key, []).append((idx, item, url))

    total_urls = sum(len(v) for v in store_jobs.values())
    threshold = _get_chrome_fallback_threshold()
    logging.info(
        f"Starting Price Scan... ({len(TRACKING_LIST)} items, {total_urls} URLs, "
        f"chrome_threshold={threshold:.0%}, UA={_get_run_ua_profile()['impersonate']})"
    )

    all_store_results = {}

    # Woolworths: curl_cffi first (sequential), then Chrome for failures
    woolies_jobs = store_jobs.get("woolworths", [])
    if woolies_jobs:
        _, batch1 = _scrape_store_batch_cffi("woolworths", woolies_jobs)
        failed = _failed_jobs_from_batch(woolies_jobs, batch1)
        batch_merged = list(batch1)
        if failed:
            logging.info(f"[Woolies] Retrying {len(failed)} failed URL(s) (cffi, 2nd pass)...")
            time.sleep(random.uniform(15, 25))
            _, batch2 = _scrape_store_batch_cffi("woolworths", failed)
            by_idx = {i: d for i, d in batch_merged}
            for i, d in batch2:
                by_idx[i] = d
            batch_merged = list(by_idx.items())

        success_rate = len(batch_merged) / len(woolies_jobs) if woolies_jobs else 1.0
        if success_rate < threshold:
            failed_chrome = _failed_jobs_from_batch(woolies_jobs, batch_merged)
            if failed_chrome:
                logging.warning(
                    f"[Woolies] cffi {len(batch_merged)}/{len(woolies_jobs)} ({success_rate:.0%}) "
                    f"— Chrome for {len(failed_chrome)} remaining"
                )
                try:
                    _, chrome_results = _scrape_store_batch("woolworths", failed_chrome)
                    chrome_subset_events += 1
                    scrape_run_stats["stores_used_chrome"].append("woolworths")
                    by_idx = {i: d for i, d in batch_merged}
                    for i, d in chrome_results:
                        by_idx[i] = d
                    batch_merged = list(by_idx.items())
                except Exception as e:
                    logging.error(f"[Woolies] Chrome subset failed: {e}")
        all_store_results["woolworths"] = batch_merged

    # Coles: BFF API first (bypasses Imperva), Chrome fallback for failures
    coles_jobs = store_jobs.get("coles", [])
    if coles_jobs:
        bff_results, bff_failed = _scrape_coles_bff(coles_jobs)
        all_coles = list(bff_results)
        if bff_failed:
            logging.info(
                f"[Coles] Chrome fallback for {len(bff_failed)} items without BFF pricing..."
            )
            try:
                _, chrome_results = _scrape_store_batch("coles", bff_failed)
                chrome_subset_events += 1
                scrape_run_stats["stores_used_chrome"].append("coles")
                all_coles.extend(chrome_results)
            except Exception as e:
                logging.error(f"[Coles] Chrome fallback failed: {e}")
        all_store_results["coles"] = all_coles

    total_jobs = sum(len(v) for v in store_jobs.values()) if store_jobs else 0
    cffi_ok = sum(len(all_store_results.get(sk, [])) for sk in store_jobs)
    cffi_rate = (cffi_ok / total_jobs) if total_jobs else 1.0

    # Merge results: group by item index, pick cheapest store
    item_store_data = {}
    for store_key, batch in all_store_results.items():
        for idx, data in batch:
            item_store_data.setdefault(idx, []).append(data)

    results = []
    inv_data = _build_inv_map_from_file()

    def _has_reliable_price(value):
        return isinstance(value, (int, float)) and 0 < value < _PRICE_UNRELIABLE

    stale_flipped_count = [0]  # mutable container so inner functions can increment

    def _carry_forward_previous(item, existing, history, avg_price, reason):
        """Keep last-known-good price when a live scrape fails.

        Returns None when:
        - No reliable previous price exists.
        - The existing item has already been stale for > WOOLIESBOT_STALE_MAX_DAYS
          (default 14), in which case the caller will emit price_unavailable=True
          and the item drops from deals/comparisons until a live scrape succeeds.
        """
        prev_price = existing.get("price")
        prev_eff = existing.get("eff_price")
        if not (_has_reliable_price(prev_price) and _has_reliable_price(prev_eff)):
            return None

        # Cap carry-forward staleness: if the item was already stale before this
        # cycle and has been so for longer than the configured max, stop carrying.
        existing_stale_as_of = existing.get("stale_as_of")
        if existing.get("stale") and existing_stale_as_of:
            try:
                stale_since = datetime.date.fromisoformat(existing_stale_as_of)
                age_days = (datetime.date.today() - stale_since).days
                if age_days > _STALE_MAX_DAYS:
                    stale_flipped_count[0] += 1
                    logging.info(
                        f"  {item.get('name','?')}: stale carry-forward exceeded {_STALE_MAX_DAYS}d "
                        f"(stale since {existing_stale_as_of}) — flipping to price_unavailable"
                    )
                    return None
            except (ValueError, TypeError):
                pass

        store = existing.get("store")
        all_stores = existing.get("all_stores") or {}
        if (not store or store == "none") and all_stores:
            store = next(iter(all_stores.keys()), "none")
        return {
            **item,
            "price": prev_price,
            "unit_price": existing.get("unit_price"),
            "unit": existing.get("unit"),
            "eff_price": prev_eff,
            "store": store or "none",
            "all_stores": all_stores,
            "price_unavailable": False,
            "image_url": existing.get("image_url", ""),
            "was_price": existing.get("was_price"),
            "on_special": existing.get("on_special", False),
            "price_history": history,
            "avg_price": avg_price,
            "stale": True,
            "stale_source": reason,
            "stale_as_of": existing_stale_as_of or datetime.datetime.now().strftime("%Y-%m-%d"),
        }

    for idx, item in enumerate(TRACKING_LIST):
        store_results = item_store_data.get(idx, [])
        existing = inv_data.get(_inventory_row_key(item)) or {}
        if not existing and item.get("name"):
            existing = inv_data.get("name:" + item["name"], {})
        # Merge price history for averaging
        history = existing.get("price_history", [])
        avg_price = sum(h["price"] for h in history) / len(history) if history else 0

        if not store_results:
            carried = _carry_forward_previous(item, existing, history, avg_price, "scrape_failed")
            if carried:
                logging.warning(f"  {item['name']}: scrape failed — carrying forward last known good price")
                results.append(carried)
            else:
                results.append({
                    **item,
                    "price": 0,
                    "unit_price": None,
                    "unit": None,
                    "eff_price": _PRICE_UNRELIABLE,
                    "store": "none",
                    "all_stores": {},
                    "price_unavailable": True,
                    "image_url": "",
                    "price_history": history,
                    "avg_price": avg_price,
                    "stale": False,
                })
            continue


        best = min(store_results, key=lambda sr: _effective_price(item, sr))
        eff = _effective_price(item, best)
        # Build all_stores, excluding stores where price is unreliable
        all_stores = {}
        for sr in store_results:
            ep = _effective_price(item, sr)
            if ep >= _PRICE_UNRELIABLE:
                logging.info(f"  Unreliable price for {sr['store']}/{item['name']} — shelf ${sr['price']:.2f}, unit scrape failed")
                continue
            all_stores[sr["store"]] = {
                "price": sr["price"],
                "unit_price": sr.get("unit_price"),
                "eff_price": ep,
                "was_price": sr.get("was_price"),
                "on_special": sr.get("on_special", False),
            }
        # If all stores were unreliable, keep item but flag it
        if not all_stores and eff >= _PRICE_UNRELIABLE:
            carried = _carry_forward_previous(item, existing, history, avg_price, "all_stores_unreliable")
            if carried:
                logging.warning(f"  {item['name']}: all stores unreliable — carrying forward last known good price")
                results.append(carried)
            else:
                logging.info(f"  {item['name']}: all stores unreliable — marking as price_unavailable")
                results.append({
                    **item,
                    "price": best["price"],
                    "unit_price": best.get("unit_price"),
                    "unit": best.get("unit"),
                    "eff_price": _PRICE_UNRELIABLE,
                    "store": best["store"],
                    "all_stores": {},
                    "price_unavailable": True,
                    "image_url": best.get("image_url", ""),
                    "price_history": history,
                    "avg_price": avg_price,
                    "stale": False,
                })
            continue
        # If best was unreliable but some stores are OK, re-pick best from reliable stores
        if eff >= _PRICE_UNRELIABLE and all_stores:
            best_store_key = min(all_stores, key=lambda sk: all_stores[sk]["eff_price"])
            sd = all_stores[best_store_key]
            eff = sd["eff_price"]
            # Find the original store_result to get full data
            for sr in store_results:
                if sr["store"] == best_store_key:
                    best = sr
                    break
        # Normalize was_price to same units as eff_price for litre/kg items
        raw_was = best.get("was_price")
        if raw_was and item.get("price_mode") == "litre":
            pl = item.get("pack_litres")
            if pl and pl > 0:
                raw_was = raw_was / pl
        item_result = {
            **item,
            "price": best["price"],
            "unit_price": best.get("unit_price"),
            "unit": best.get("unit"),
            "eff_price": eff,
            "store": best["store"],
            "all_stores": all_stores,
            "price_unavailable": False,
            "image_url": best.get("image_url", ""),
            "was_price": round(raw_was, 2) if raw_was else None,
            "on_special": best.get("on_special", False),
            "price_history": history,
            "avg_price": avg_price,
            "stale": False,
            "name_check": best.get("name_check", ""),
        }

        # Handle local image download
        remote_img = best.get("image_url")
        if remote_img:
            local_img_path = _download_product_image(remote_img, item["name"])
            if local_img_path:
                item_result["image_url"] = local_img_path

        item_result = _maybe_confirm_outlier_price(item, item_result, inv_data)
        results.append(item_result)

    # Per-item failure counter + auto-quarantine
    # Fresh successful scrape -> consecutive_failures=0; price_unavailable -> +1;
    # carry-forward (stale=True) -> unchanged so a known-bad item does not silently
    # reset its counter. Items hitting WOOLIESBOT_QUARANTINE_THRESHOLD (default 6)
    # are flagged quarantined=True and emit a distinct one-shot Telegram alert.
    quarantine_threshold = max(2, _env_int("WOOLIESBOT_QUARANTINE_THRESHOLD", 6))
    newly_quarantined = []
    for r in results:
        existing = inv_data.get(_inventory_row_key(r)) or {}
        if not existing and r.get("name"):
            existing = inv_data.get("name:" + r["name"], {})
        prev_count = int(existing.get("consecutive_failures", 0) or 0)
        was_quarantined = bool(existing.get("quarantined", False))
        if r.get("price_unavailable"):
            new_count = prev_count + 1
        elif r.get("stale"):
            new_count = prev_count
        else:
            new_count = 0
        r["consecutive_failures"] = new_count
        is_quarantined = new_count >= quarantine_threshold
        r["quarantined"] = is_quarantined
        if is_quarantined and not was_quarantined:
            newly_quarantined.append((r.get("name", "?"), new_count))
    if newly_quarantined:
        sample = "\n".join(f"  - {n} ({c} fails)" for n, c in newly_quarantined[:10])
        send_telegram(
            f"🚧 *[ITEM QUARANTINED]* {len(newly_quarantined)} item(s) failed "
            f"≥{quarantine_threshold} consecutive cycles:\n{sample}\n"
            f"Likely a dead URL or persistent vendor mismatch — review manually."
        )
        logging.warning(
            f"  {len(newly_quarantined)} item(s) quarantined after {quarantine_threshold} consecutive failures."
        )

    # Scraper Health Check
    unavail_count = sum(1 for r in results if r.get("price_unavailable"))
    stale_count = sum(1 for r in results if r.get("stale"))
    flipped = stale_flipped_count[0]
    if flipped > 0:
        logging.warning(
            f"  {flipped} item(s) exceeded stale carry-forward cap ({_STALE_MAX_DAYS}d) "
            f"and were flipped to price_unavailable."
        )
    if unavail_count > (len(TRACKING_LIST) * 0.25):
        health_msg = f"⚠️ *SCRAPER HEALTH ALERT*\n{unavail_count}/{len(TRACKING_LIST)} items have no reliable price. "
        if flipped > 0:
            health_msg += f"{flipped} item(s) flipped from stale to unavailable (>{_STALE_MAX_DAYS}d old). "
        health_msg += "Site layouts may have changed."
        send_telegram(health_msg)

    # Per-store success-rate floor — catches a single-vendor outage (e.g. Coles BFF down)
    # that the global unavail_count check above can miss when the other store is healthy.
    # Threshold env-overridable: WOOLIESBOT_STORE_FLOOR (default 0.80 = 80%).
    store_floor = float(os.environ.get("WOOLIESBOT_STORE_FLOOR", "0.80"))
    store_min_items = int(os.environ.get("WOOLIESBOT_STORE_FLOOR_MIN_ITEMS", "20"))
    per_store_alerts = []
    for store_key in ("woolworths", "coles"):
        expected = sum(1 for it in TRACKING_LIST if it.get(store_key))
        if expected < store_min_items:
            continue
        success = sum(
            1 for r in results
            if not r.get("price_unavailable")
            and (r.get("all_stores") or {}).get(store_key)
        )
        rate = success / expected
        logging.info(f"  {store_key}: {success}/{expected} success rate = {rate:.1%}")
        if rate < store_floor:
            per_store_alerts.append((store_key, success, expected, rate))
    if per_store_alerts:
        lines = [
            f"{store.upper()}: {ok}/{exp} = {pct:.0%} (floor {store_floor:.0%})"
            for store, ok, exp, pct in per_store_alerts
        ]
        send_telegram(
            "🏪 *[VENDOR DEGRADED]* Per-store success rate below floor:\n"
            + "\n".join(lines)
            + "\nLikely a single-vendor API outage (BFF / Search). Cross-check with vendor status."
        )

    _append_metrics_run({
        "ts": datetime.datetime.now().isoformat(),
        "items": len(TRACKING_LIST),
        "total_url_jobs": total_jobs,
        "cffi_success_rate": round(cffi_rate, 4),
        "http_429": scrape_run_stats.get("http_429", 0),
        "http_5xx": scrape_run_stats.get("http_5xx", 0),
        "coles_429": scrape_run_stats.get("coles_429", 0),
        "coles_challenge": scrape_run_stats.get("coles_challenge", 0),
        "coles_sequential": bool(_COLES_SEQUENTIAL),
        "coles_workers_cap": _COLES_CFFI_WORKERS_CAP,
        "chrome_subset_events": chrome_subset_events,
        "stores_chrome": sorted(set(scrape_run_stats.get("stores_used_chrome", []))),
    })

    logging.info(f"Price Scan complete. {len(results)}/{len(TRACKING_LIST)} items with prices.")
    return results


def _slugify_name(name):
    """Turns an item name into a safe filename."""
    s = str(name).strip().replace(' ', '_')
    return re.sub(r'(?u)[^-\w.]', '', s).lower()

def _download_product_image(url, name):
    """Downloads an image for the product name to docs/images if older than 30 days or missing."""
    if not url: return ""
    
    img_dir = os.path.join("docs", "images")
    os.makedirs(img_dir, exist_ok=True)
    slug = _slugify_name(name)
    local_filename = f"{slug}.jpg"
    local_path = os.path.join(img_dir, local_filename)
    
    # Check if we need to download
    if os.path.exists(local_path):
        mtime = os.path.getmtime(local_path)
        age_days = (time.time() - mtime) / (60 * 60 * 24)
        if age_days < 30:
            return f"images/{local_filename}"
            
    try:
        profile = _get_random_ua_profile()
        headers = {
            "User-Agent": profile["ua"]
        }
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            with open(local_path, "wb") as f:
                f.write(response.content)
            logging.info(f"Downloaded image for '{name}' → {local_filename}")
            return f"images/{local_filename}"
        else:
            logging.debug(f"Image download HTTP {response.status_code} for {url}")
    except Exception as e:
        logging.warning(f"Failed to download image for '{name}': {e}")
    
    return ""

def _discover_coles_prices(batch_size=20):
    """Search Coles via _next/data JSON API (same stack as main scraper) and rank hits by name overlap.
    Paced sleeps + adaptive backoff when searches return nothing (possible soft block)."""
    data_path = "docs/data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("items", raw) if isinstance(raw, dict) else raw

    no_coles = [i for i in data if not i.get("coles")]
    if not no_coles:
        logging.info("[Coles] All items already have Coles URLs.")
        return

    batch = no_coles[:batch_size]
    logging.info(f"[Coles] Discovering URLs for {len(batch)}/{len(no_coles)} items (API search + ranking)...")

    session = _create_cffi_session("coles")
    try:
        w = session.get("https://www.coles.com.au/", timeout=20)
        if w.status_code != 200 or _coles_body_looks_blocked(w.text):
            logging.warning("[Coles discovery] Homepage blocked or failed — aborting this cycle")
            return
        _apply_coles_build_id_from_html(w.text, "coles_discovery_warmup")
    except Exception as e:
        logging.warning(f"[Coles] Could not reach coles.com.au: {e}")
        return

    bid = _cffi_get_coles_build_id(session)
    if not bid:
        logging.warning("[Coles discovery] No buildId — aborting")
        return

    matched = 0
    cheaper = 0
    sleep_mult = 1.0

    for item in batch:
        name = item.get("name", "")
        search = re.sub(r"\d+\.?\d*\s*(G|Kg|Ml|L|Pk|Pack)\b", "", name, flags=re.IGNORECASE)
        search = search.replace("Ww ", "").replace("P/P", "").strip()
        words = [w for w in search.split() if len(w) > 1][:3]
        search = " ".join(words)
        if len(search) < 3:
            continue

        try:
            results, did_you_mean = _cffi_search_coles(session, search, bid, max_results=12)
            retry_q = _coles_needs_spelling_retry(search, results, did_you_mean)
            if retry_q:
                logging.info(f"[Coles discovery] spelling retry: {search!r} → {retry_q!r}")
                results, _ = _cffi_search_coles(session, retry_q, bid, max_results=12)

            if not results:
                sleep_mult = min(3.0, sleep_mult * 1.2)
                time.sleep(_COLES_DISCOVERY_SLEEP_SEC * sleep_mult)
                continue

            ranked = _rank_coles_search_results_for_inventory(name, results)
            best = ranked[0]
            # Include size field so _size_signals_compatible can see "1.25L", "10 Pack", etc.
            # Coles BFF separates size from name (e.g. name="Max No Sugar Cola Bottle", size="1.25L")
            label = " ".join(filter(None, [best.get("brand", ""), best.get("name", ""), best.get("size", "")])).strip()
            score = _token_overlap_score(name, label)
            if score < _COLES_DISCOVERY_MIN_SCORE or not _size_signals_compatible(name, label):
                logging.info(f"[Coles] Skipping low match (score={score:.2f}) for {name!r} vs {label!r}")
                time.sleep(_COLES_DISCOVERY_SLEEP_SEC * sleep_mult)
                continue
            # Asymmetric guard: if inventory specifies a size but result label still has none,
            # require stronger overlap (mirrors the WW large-weight guard).
            inv_sig = _extract_size_signals(name)
            res_sig = _extract_size_signals(label)
            if any(inv_sig[k] for k in ("packs", "volumes_ml", "weights_g")) and \
                    not any(res_sig[k] for k in ("packs", "volumes_ml", "weights_g")):
                if score < 0.50:
                    logging.info(
                        f"[Coles] Skipping asymmetric size (score={score:.2f}<0.50): "
                        f"{name!r} vs {label!r}"
                    )
                    time.sleep(_COLES_DISCOVERY_SLEEP_SEC * sleep_mult)
                    continue

            purl = _coles_product_url_from_search_hit(best)
            if not purl:
                logging.warning(f"[Coles] No product id in search hit for {name!r}")
                time.sleep(_COLES_DISCOVERY_SLEEP_SEC * sleep_mult)
                continue

            cp = float(best["price"])
            cw = best.get("was_price")
            cs = bool(best.get("on_special"))

            all_stores = item.get("all_stores", {})
            all_stores["coles"] = {
                "price": cp,
                "eff_price": cp,
                "was_price": float(cw) if cw else None,
                "on_special": cs,
            }
            item["all_stores"] = all_stores
            item["coles"] = purl

            wp = item.get("eff_price") or item.get("price", 999)
            if cp < wp:
                item["store"] = "coles"
                item["price"] = cp
                item["eff_price"] = cp
                if cs and cw:
                    item["was_price"] = float(cw)
                    item["on_special"] = True
                cheaper += 1

            matched += 1
            sleep_mult = max(1.0, sleep_mult * 0.92)
        except Exception as ex:
            logging.debug(f"[Coles discovery] item error: {ex}")

        time.sleep(_COLES_DISCOVERY_SLEEP_SEC * sleep_mult)

    if matched > 0:
        with _data_write_lock:
            if isinstance(raw, dict):
                raw["items"] = data
            _atomic_write_json(data_path, raw if isinstance(raw, dict) else data)

    total = sum(1 for i in data if i.get("coles"))
    logging.info(f"[Coles] Discovered {matched} matches ({cheaper} cheaper). Total with Coles: {total}/{len(data)}")


def _recalculate_smart_targets():
    """Re-run smart target recalculation in-process."""
    _st_path = _pl.Path(__file__).parent / "scripts" / "smart_targets.py"
    _spec = _ilu.spec_from_file_location("smart_targets", _st_path)
    _st = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_st)
    _st.recalculate_targets(dry_run=False)


_LINK_HEAL_URL_CAP = int(os.environ.get("WOOLIESBOT_LINK_HEAL_CAP", "10"))


def _run_local_link_self_heal(report_basename="e2e_validate_links_local.json"):
    """Run Layer D scan/apply with auto-repair, writing a JSON report under logs/.

    Aborts the apply step if the scan reports more than _LINK_HEAL_URL_CAP URLs
    to change — a sudden large batch of URL mutations is a signal of a scraper
    anomaly or bad upstream redirect, not routine self-healing.
    """
    import json as _json
    import subprocess

    root_dir = os.path.dirname(os.path.abspath(__file__))
    validator = os.path.join(root_dir, "scripts", "e2e_validate.py")
    links_report = os.path.join(root_dir, "logs", report_basename)
    os.makedirs(os.path.dirname(links_report), exist_ok=True)

    if not os.path.exists(validator):
        logging.warning("Link self-heal skipped: scripts/e2e_validate.py not found.")
        return False

    scan_cmd = [
        sys.executable,
        validator,
        "--layer",
        "D",
        "--all",
        "--repair-bad-links",
        "--json-out",
        links_report,
    ]
    apply_cmd = [
        sys.executable,
        validator,
        "--apply-url-metadata",
        links_report,
        "--repair-bad-links",
        "--write",
    ]

    scan_res = subprocess.run(scan_cmd, cwd=root_dir, capture_output=True, text=True)
    if scan_res.returncode != 0:
        logging.warning(
            "Layer D self-heal scan failed (continuing scrape): "
            f"{(scan_res.stderr or scan_res.stdout or '').strip()[:400]}"
        )
        return False

    # Inspect the report before applying so we can bail on unexpected bulk changes.
    changed_count = 0
    try:
        report_data = _json.loads(open(links_report, encoding="utf-8").read())
        changed_count = len(report_data.get("url_metadata_records", []))
        logging.info(f"Link self-heal scan: {changed_count} URL record(s) to apply.")
    except Exception as read_exc:
        logging.warning(f"Link self-heal: could not read scan report ({read_exc}); skipping apply.")
        return False

    if changed_count > _LINK_HEAL_URL_CAP:
        logging.warning(
            f"Link self-heal CAPPED: scan reported {changed_count} URL changes, "
            f"which exceeds the cap of {_LINK_HEAL_URL_CAP} "
            f"(set WOOLIESBOT_LINK_HEAL_CAP to raise). Apply skipped."
        )
        return False

    apply_res = subprocess.run(apply_cmd, cwd=root_dir, capture_output=True, text=True)
    if apply_res.returncode != 0:
        logging.warning(
            "Layer D self-heal apply failed (continuing scrape): "
            f"{(apply_res.stderr or apply_res.stdout or '').strip()[:400]}"
        )
        return False

    logging.info(f"Local link self-heal completed ({changed_count} URL(s) updated).")
    return True


def run_report(full_list=False, send_telegram_messages=True):
    """Generate and send shopping report.
    full_list: /show_staples - full staples list with all prices. False = deals only.
    send_telegram_messages: whether to output the full report to telegram.
    Big Shop (bulk qty) auto-detected from date (after 14th).
    Uses a cross-process file lock so only one scrape mutates data.json at a time.
    """
    lock_fd = _acquire_scrape_lock()
    if lock_fd is None:
        logging.warning(
            "Scrape skipped: another run_report is already in progress (lock held)."
        )
        return
    notify_errors = send_telegram_messages or _env_truthy("WOOLIESBOT_TELEGRAM_ERRORS")
    global NEXT_SCHEDULED_RUN
    try:
        today = datetime.datetime.now()
        weekday = today.weekday()  # Mon=0, Sun=6
        is_big_shop = today.day > BIG_SHOP_START_DAY

        raw_results = check_prices()

        # Update JSON file for web dashboard (single source of truth)
        export_data_to_json(raw_results)

        # Re-run smart target engine so targets improve with each scrape cycle
        try:
            _recalculate_smart_targets()
            logging.info("Smart target recalculation complete.")
        except Exception as _e:
            logging.warning(f"Smart target recalculation skipped: {_e}")
        
        # Gradual Coles price discovery (~20 items per cycle)
        try:
            _discover_coles_prices(batch_size=20)
        except Exception as _e:
            logging.warning(f"Coles discovery skipped: {_e}")

        # Local self-heal: run Layer D auto-repair so manual/launchd scrapes also fix bad links.
        # Enabled by default; set WOOLIESBOT_LINK_SELF_HEAL=0 to disable.
        if _env_truthy("WOOLIESBOT_LINK_SELF_HEAL", default=True):
            try:
                _run_local_link_self_heal(report_basename="e2e_validate_links_local.json")
            except Exception as _e:
                logging.warning(f"Local link self-heal skipped: {_e}")

        # Deploy to GitHub pages — capture next run on this thread so heartbeat.json matches schedule
        next_for_heartbeat = None
        try:
            next_for_heartbeat = schedule.next_run()
        except Exception:
            pass
        if next_for_heartbeat:
            NEXT_SCHEDULED_RUN = next_for_heartbeat
        sync_kw = {"next_scheduled": next_for_heartbeat}
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # Actions must finish git push before the process exits (--now uses sys.exit).
            sync_to_github(**sync_kw)
        else:
            threading.Thread(target=sync_to_github, kwargs=sync_kw, daemon=True).start()

        if not send_telegram_messages:
            logging.info("send_telegram_messages is False. Exiting early, no message sent.")
            return

        summary = _build_run_summary(raw_results, now_dt=today)

        if send_telegram_messages:
            send_telegram(summary)

    except Exception as e:
        error_trace = traceback.format_exc()
        logging.error(f"Error in run_report: {e}\n{error_trace}")
        if notify_errors:
            send_telegram(f"🚨 *REPORT ERROR*:\n{_escape_md(str(e))}")
    finally:
        _release_scrape_lock(lock_fd)

def _sort_key_unit_price(r):
    """Sort key: by normalized unit price (None → infinity so they sink)."""
    p = r.get("norm_unit_price")
    return p if p is not None else float("inf")


def _result_display_name(r):
    """Format result name for display. Deduplicates brand if already in name."""
    brand = (r.get("brand") or "").strip()
    name = (r.get("name") or "").strip()
    if brand and name.lower().startswith(brand.lower()):
        return name  # Brand already in name, don't double it
    return f"{brand} {name}".strip() if brand else name


def _result_price_str(r):
    """Format price string with unit price."""
    price = r.get("price", 0)
    norm_price = r.get("norm_unit_price")
    norm_label = r.get("norm_unit_label", "")
    size = r.get("size", "")

    base = f"${price:.2f}"
    if size:
        base += f" {_escape_md(size)}"
    if norm_price is not None and norm_label:
        base += f" (${norm_price:.2f}{norm_label})"
    return base


def search_and_compare(query):
    """Search both stores for a product and send comparison to Telegram.
    Uses curl_cffi (no browser) with unit price sorting and spelling correction."""
    try:
        send_telegram(f"🔍 Searching for _{_escape_md(query)}_ ...")
        woolies_session, coles_session, coles_build_id = _init_search_sessions()

        woolies_results = []
        coles_results = []
        correction_note = ""

        # ── Woolworths search ──
        if woolies_session:
            woolies_results, suggested = _cffi_search_woolworths(woolies_session, query)
            if suggested and suggested.lower() != query.lower():
                correction_note = f"_Showing results for: {_escape_md(suggested)}_" + _nl()

        # ── Coles search (with spelling correction) ──
        if coles_session and coles_build_id:
            coles_results, did_you_mean = _cffi_search_coles(coles_session, query, coles_build_id)
            retry_query = _coles_needs_spelling_retry(query, coles_results, did_you_mean)
            if retry_query:
                logging.info(f"Coles spelling retry: '{query}' → '{retry_query}'")
                coles_results, _ = _cffi_search_coles(coles_session, retry_query, coles_build_id)
                if not correction_note:
                    correction_note = f"_Showing results for: {_escape_md(retry_query)}_" + _nl()

        if not woolies_results and not coles_results:
            send_telegram(f"😕 No results found for '{_escape_md(query)}' at either store.")
            return

        # ── Merge & sort by unit price ──
        all_results = woolies_results[:5] + coles_results[:5]
        all_results.sort(key=_sort_key_unit_price)

        # Determine common unit for display
        unit_labels = [r["norm_unit_label"] for r in all_results if r.get("norm_unit_price") is not None]
        common_unit = unit_labels[0] if unit_labels else ""

        msg = f"🔍 *SEARCH: {_escape_md(query)}*" + _sp()
        if correction_note:
            msg += correction_note + _nl()

        # ── Cheapest overall (by unit price) ──
        valid_results = [r for r in all_results if r.get("norm_unit_price") is not None]
        if valid_results:
            cheapest = valid_results[0]
            store_key = cheapest["store"]
            store_emoji = STORES.get(store_key, {}).get("emoji", "")
            store_label = STORES.get(store_key, {}).get("label", store_key)
            ch_name = _escape_md(_result_display_name(cheapest))
            msg += f"💰 *CHEAPEST ({_escape_md(common_unit)}):*" + _nl()
            msg += f"{store_emoji} {ch_name}" + _nl()
            msg += f"{_result_price_str(cheapest)}" + _nl()
            if cheapest.get("on_special"):
                msg += "⚡ ON SPECIAL" + _nl()
            msg += _nl()

        # ── All results sorted by unit price ──
        sort_label = f"by ${common_unit.lstrip('/')}" if common_unit else "by price"
        msg += f"📊 *ALL RESULTS* ({_escape_md(sort_label)}):" + _nl()
        for r in all_results[:8]:
            store_char = "W" if r["store"] == "woolworths" else "C"
            emoji = STORES.get(r["store"], {}).get("emoji", "")
            name = _result_display_name(r)
            price_str = _result_price_str(r)
            if r.get("on_special"):
                price_str += " ⚡"
            msg += _escape_md(name) + _nl()
            msg += f"  {store_char} {price_str} {emoji}" + _nl()
        send_telegram(msg.strip())

    except Exception as e:
        logging.error(f"Search error: {e}\n{traceback.format_exc()}")
        send_telegram(f"🚨 Search failed: {_escape_md(str(e))}")

# ─── SHOPPING LIST (/list) ───────────────────────────────────────────────────

LIST_EXPIRY_HOURS = 4  # auto-delete list after this many hours of inactivity

_shopping_list = {
    "items": [],           # [{"query": str, "woolies": {...}, "coles": {...}, "cheapest_store": str}]
    "last_updated": None,  # datetime or None
    "last_message": "",    # cached report text for /list show
}
_list_pending_items = []   # items waiting for add/new confirmation

def _list_is_expired():
    """Check if the shopping list has expired (no activity for LIST_EXPIRY_HOURS)."""
    if not _shopping_list["last_updated"]:
        return True
    age = (datetime.datetime.now() - _shopping_list["last_updated"]).total_seconds()
    return age > LIST_EXPIRY_HOURS * 3600

def _list_clear():
    """Clear the shopping list."""
    _shopping_list["items"] = []
    _shopping_list["last_updated"] = None
    _shopping_list["last_message"] = ""

def _search_batch(queries):
    """Search multiple items across both stores using curl_cffi (no browser).
    Returns list of {query, woolies: {...}, coles: {...}, cheapest_store}.
    Includes unit price data and Coles spelling correction."""
    woolies_session, coles_session, coles_build_id = _init_search_sessions()
    results = []
    woolies_cache = {}
    coles_cache = {}

    # ── Woolworths: API calls for all items ──
    if woolies_session:
        logging.info(f"[List] Searching {len(queries)} items on Woolworths...")
        for q in queries:
            try:
                w_results, _ = _cffi_search_woolworths(woolies_session, q, max_results=1)
                if w_results:
                    woolies_cache[q] = w_results[0]
                time.sleep(0.2)
            except Exception as e:
                logging.debug(f"[List] Woolies search '{q}' failed: {e}")
    else:
        logging.warning("[List] Woolworths session failed")

    # ── Coles: API calls for all items (with spelling correction) ──
    if coles_session and coles_build_id:
        logging.info(f"[List] Searching {len(queries)} items on Coles...")
        for q in queries:
            try:
                c_results, did_you_mean = _cffi_search_coles(coles_session, q, coles_build_id, max_results=1)
                # Spelling correction: retry if results look wrong
                retry_query = _coles_needs_spelling_retry(q, c_results, did_you_mean)
                if retry_query:
                    logging.info(f"[List] Coles spelling retry: '{q}' → '{retry_query}'")
                    c_results, _ = _cffi_search_coles(coles_session, retry_query, coles_build_id, max_results=1)
                if c_results:
                    coles_cache[q] = c_results[0]
                time.sleep(0.2)
            except Exception as e:
                logging.debug(f"[List] Coles search '{q}' failed: {e}")
    else:
        logging.warning("[List] Coles session failed")

    # ── Merge results per query (compare by unit price when available) ──
    for q in queries:
        w = woolies_cache.get(q)
        c = coles_cache.get(q)
        cheapest_store = None
        if w and c:
            # Prefer unit price comparison; fall back to shelf price
            w_up = w.get("norm_unit_price")
            c_up = c.get("norm_unit_price")
            if w_up is not None and c_up is not None:
                cheapest_store = "woolworths" if w_up <= c_up else "coles"
            else:
                cheapest_store = "woolworths" if w["price"] <= c["price"] else "coles"
        elif w:
            cheapest_store = "woolworths"
        elif c:
            cheapest_store = "coles"
        results.append({
            "query": q,
            "woolies": w,
            "coles": c,
            "cheapest_store": cheapest_store,
        })
    return results


def _format_list_item_price(r):
    """Format a single item price with unit price for the /list report."""
    if not r:
        return ""
    price_str = f"${r['price']:.2f}"
    norm = r.get("norm_unit_price")
    label = r.get("norm_unit_label", "")
    if norm is not None and label:
        price_str += f" (${norm:.2f}{label})"
    return price_str


def _format_list_report():
    """Format the shopping list comparison report as a table: Item | Woolies | Coles | Best."""
    items = _shopping_list["items"]
    if not items:
        return "🛒 Shopping list is empty."

    woolies_total = 0.0
    coles_total = 0.0
    woolies_wins = 0
    coles_wins = 0
    list_lines = []

    for item in items:
        q = item["query"]
        w = item.get("woolies")
        c = item.get("coles")
        best = item.get("cheapest_store")

        woolies_p = _format_list_item_price(w) if w else "—"
        coles_p = _format_list_item_price(c) if c else "—"

        if not w and not c:
            list_lines.append((q, "  —  ❓ not found"))
            continue

        best_char = "W" if best == "woolworths" else "C"
        emoji = STORES.get(best, {}).get("emoji", "") if best else ""
        list_lines.append((q, f"  W {woolies_p}  C {coles_p}  → {best_char} {emoji}"))

        if best == "woolworths" and w:
            woolies_total += w["price"]
            woolies_wins += 1
            coles_total += c["price"] if c else w["price"]
        elif best == "coles" and c:
            coles_total += c["price"]
            coles_wins += 1
            woolies_total += w["price"] if w else c["price"]
        elif w:
            woolies_total += w["price"]
            woolies_wins += 1
            coles_total += w["price"]
        elif c:
            coles_total += c["price"]
            coles_wins += 1
            woolies_total += c["price"]

    msg = f"🛒 *SHOPPING LIST* ({len(items)} items)" + _sp()
    for name, cmp_line in list_lines:
        msg += _escape_md(name) + _nl() + cmp_line + _nl()
    msg += _sp() + "💰 *TOTALS*" + _nl()
    msg += f"🟢 All at Woolies: ${woolies_total:.2f}" + _nl()
    msg += f"🔴 All at Coles: ${coles_total:.2f}" + _nl()

    # Split shop total (cheapest per item)
    split_total = 0.0
    for item in items:
        w = item.get("woolies")
        c = item.get("coles")
        best = item.get("cheapest_store")
        if best == "woolworths" and w:
            split_total += w["price"]
        elif best == "coles" and c:
            split_total += c["price"]
        elif w:
            split_total += w["price"]
        elif c:
            split_total += c["price"]

    msg += f"✅ Split shop: ${split_total:.2f} ({woolies_wins}x 🟢 + {coles_wins}x 🔴)" + _nl()

    all_store_total = min(woolies_total, coles_total) if woolies_total > 0 and coles_total > 0 else max(woolies_total, coles_total)
    if split_total < all_store_total:
        saving = all_store_total - split_total
        msg += f"💡 _Split saves ${saving:.2f} vs single store_" + _nl()

    return msg.strip()

def run_list_search(new_queries, mode="new"):
    """Run the shopping list search. mode='new' replaces, mode='add' appends."""
    try:
        if mode == "new":
            _list_clear()

        total_queries = [q.strip() for q in new_queries if q.strip()]
        existing_queries = [item["query"] for item in _shopping_list["items"]]
        # Only search items not already in the list
        to_search = [q for q in total_queries if q not in existing_queries]
        all_queries = existing_queries + to_search

        if not all_queries:
            send_telegram("🛒 No items to search.")
            return

        send_telegram(f"🔍 Searching {len(to_search)} item{'s' if len(to_search) != 1 else ''} across 🟢 Woolies + 🔴 Coles...")

        # Search only new items
        if to_search:
            search_results = _search_batch(to_search)
            _shopping_list["items"].extend(search_results)

        _shopping_list["last_updated"] = datetime.datetime.now()
        report = _format_list_report()
        _shopping_list["last_message"] = report
        send_telegram(report)

    except Exception as e:
        logging.error(f"List search error: {e}\n{traceback.format_exc()}")
        send_telegram(f"🚨 List search failed: {_escape_md(str(e))}")

INTRO_LINE = "Hey! It's your WoolesBot, ready to go shopping with you."

HELP_TEXT = INTRO_LINE + """

📋 *Reports*
/shop — Spot bargains: only items on special below your target price
/show\\_staples — Your kitchen staples: full list with all prices, specials highlighted

🔍 *Search & Compare*
/find chicken thighs — Search any item across Woolies + Coles
/list milk, eggs, bread — Compare a shopping list
/list show — Show current list again
/list clear — Delete the list

⚙️ *System*
/web — Link to the online dashboard
/ping — Am I alive?
/restart — Restart the bot
/help — This message"""

def _strip_bot_suffix(cmd):
    """Remove @botname suffix from commands: /shop@MyBot → /shop"""
    return cmd.split("@")[0]

def telegram_bot_listener():
    """Polls Telegram for commands like /shop."""
    if not TELEGRAM_TOKEN:
        logging.info("Telegram bot listener disabled (no TELEGRAM_TOKEN).")
        return
    last_update_id = 0
    logging.info("Telegram Bot Listener started.")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            # Network timeout = long-poll timeout + buffer for network latency
            response = requests.get(url, params=params, timeout=45)
            response.raise_for_status()
            updates = response.json().get("result", [])
            
            for update in updates:
                last_update_id = update["update_id"]
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")

                if chat_id:
                    logging.debug(f"Telegram from {chat_id}: '{text}'")
                
                cmd = _strip_bot_suffix((text or "").strip().lower())
                
                # /ping — health check (any chat)
                if cmd == "/ping":
                    send_telegram("🏓 Pong! I'm online!")
                    continue
                
                # /help — show commands (any chat)
                if cmd == "/help":
                    send_telegram(HELP_TEXT)
                    continue
                
                # /restart — restart the bot (launchd will auto-restart)
                if cmd == "/restart":
                    if chat_id != TELEGRAM_CHAT_ID:
                        logging.warning(f"Unauthorized restart attempt from chat {chat_id}")
                        continue
                    logging.info("Restart requested via Telegram.")
                    send_telegram("🔄 Restarting WoolesBot...")
                    # Acknowledge the update so we don't re-process it after launchd restarts us
                    try:
                        requests.get(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                            params={"offset": last_update_id + 1},
                            timeout=5,
                        )
                    except Exception:
                        pass
                    time.sleep(1)
                    os._exit(0)  # Hard exit — launchd KeepAlive restarts us
                
                # /web — send link to dashboard
                if cmd == "/web":
                    send_telegram("🌐 *Web Dashboard*\nCheck out the deals and price trends here:\nhttps://KuschiKuschbert.github.io/wooliesbot/")
                    continue
                
                # /find <query> — ad-hoc product search
                raw_text = (text or "").strip()
                if cmd.startswith("/find"):
                    if chat_id != TELEGRAM_CHAT_ID:
                        logging.warning(f"Unauthorized chat {chat_id} (expected {TELEGRAM_CHAT_ID})")
                        continue
                    # Extract search query: "/find gorgonzola cheese" → "gorgonzola cheese"
                    query = raw_text[len("/find"):].strip()
                    # Also strip @botname if present: "/find@MyBot gorgonzola" → "gorgonzola"
                    if query.startswith("@"):
                        query = query.split(" ", 1)[-1].strip() if " " in query else ""
                    if not query:
                        send_telegram("Usage: /find _item name_\nExample: /find gorgonzola cheese")
                        continue
                    logging.info(f"Search command: '{query}'")
                    threading.Thread(
                        target=lambda q=query: search_and_compare(q),
                        daemon=True
                    ).start()
                    continue

                # /list — shopping list commands
                if cmd.startswith("/list"):
                    if chat_id != TELEGRAM_CHAT_ID:
                        logging.warning(f"Unauthorized chat {chat_id} (expected {TELEGRAM_CHAT_ID})")
                        continue

                    # Sub-commands: /list show, /list clear
                    list_arg = raw_text.split(None, 1)[1].strip() if " " in raw_text else ""
                    list_arg_lower = list_arg.lower()

                    if cmd == "/list_add":
                        # Confirm: add pending items to existing list
                        if _list_pending_items:
                            logging.info(f"List add: {_list_pending_items}")
                            items_copy = list(_list_pending_items)
                            _list_pending_items.clear()
                            threading.Thread(
                                target=lambda q=items_copy: run_list_search(q, mode="add"),
                                daemon=True
                            ).start()
                        else:
                            send_telegram("Nothing to add. Use /list item1, item2, ...")
                        continue

                    if cmd == "/list_new":
                        # Confirm: replace list with pending items
                        if _list_pending_items:
                            logging.info(f"List new: {_list_pending_items}")
                            items_copy = list(_list_pending_items)
                            _list_pending_items.clear()
                            threading.Thread(
                                target=lambda q=items_copy: run_list_search(q, mode="new"),
                                daemon=True
                            ).start()
                        else:
                            send_telegram("Nothing to search. Use /list item1, item2, ...")
                        continue

                    if list_arg_lower == "show":
                        if _list_is_expired():
                            _list_clear()
                            send_telegram("🛒 No active shopping list. Send /list item1, item2, ...")
                        elif _shopping_list["last_message"]:
                            send_telegram(_shopping_list["last_message"])
                        else:
                            send_telegram("🛒 Shopping list is empty.")
                        continue

                    if list_arg_lower == "clear":
                        _list_clear()
                        send_telegram("🗑 Shopping list cleared.")
                        continue

                    # /list item1, item2, item3 — create or extend list
                    if not list_arg:
                        if _shopping_list["items"] and not _list_is_expired():
                            send_telegram(_shopping_list["last_message"] or _format_list_report())
                        else:
                            send_telegram("Usage: /list milk, eggs, bread\nSearches both stores and compares prices.")
                        continue

                    new_items = [i.strip() for i in list_arg.split(",") if i.strip()]
                    if not new_items:
                        send_telegram("Usage: /list milk, eggs, bread")
                        continue

                    # Auto-expire old list
                    if _list_is_expired():
                        _list_clear()

                    # If list already has items, ask add or replace
                    if _shopping_list["items"]:
                        existing_names = ", ".join(item["query"] for item in _shopping_list["items"])
                        _list_pending_items.clear()
                        _list_pending_items.extend(new_items)
                        msg = f"🛒 You have a list: _{_escape_md(existing_names)}_" + _nl()
                        msg += _nl()
                        msg += "/list\\_add — Add these items to it" + _nl()
                        msg += "/list\\_new — Start fresh with just these"
                        send_telegram(msg)
                        continue

                    # No existing list — create new
                    logging.info(f"List new: {new_items}")
                    threading.Thread(
                        target=lambda q=list(new_items): run_list_search(q, mode="new"),
                        daemon=True
                    ).start()
                    continue

                # /shop — deals only (auto Big Shop from date)
                if cmd == "/shop":
                    if chat_id != TELEGRAM_CHAT_ID:
                        logging.warning(f"Unauthorized chat {chat_id} (expected {TELEGRAM_CHAT_ID})")
                        continue
                    logging.info("Command: /shop (deals only)")
                    send_telegram("👨‍🍳 Scanning Woolies + Coles (parallel)... ~3 min.")
                    threading.Thread(target=lambda: run_report(full_list=False), daemon=True).start()
                    continue
                # /show_staples — full staples list with all prices (auto Big Shop from date)
                if cmd == "/show_staples":
                    if chat_id != TELEGRAM_CHAT_ID:
                        logging.warning(f"Unauthorized chat {chat_id} (expected {TELEGRAM_CHAT_ID})")
                        continue
                    logging.info("Command: /show_staples (full list)")
                    send_telegram("👨‍🍳 Scanning Woolies + Coles (parallel)... ~3 min.")
                    threading.Thread(target=lambda: run_report(full_list=True), daemon=True).start()
                    continue
                    
        except requests.exceptions.Timeout:
            logging.debug("Telegram long-poll timed out (normal).")
        except Exception as e:
            logging.error(f"Telegram listener error: {e}")
            time.sleep(10)
        
        time.sleep(1)

if __name__ == "__main__":
    while True:
        try:
            parser = argparse.ArgumentParser(description="WoolesBot - Woolworths and Coles price tracker")
            parser.add_argument("--now", action="store_true", help="Run the report immediately")
            args = parser.parse_args()

            if args.now:
                logging.info("Manual trigger received. Running one-shot pipeline...")
                import subprocess
                pipeline_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "scripts",
                    "scrape_pipeline.py",
                )
                run_now = subprocess.run(
                    [sys.executable, pipeline_script, "--notify", "success"],
                    check=False,
                )
                sys.exit(run_now.returncode)

            # Start Telegram Listener only when credentials are configured
            if TELEGRAM_TOKEN:
                threading.Thread(target=telegram_bot_listener, daemon=True).start()

            # ---------- Deep Receipt Sync (one-shot on next cycle) ----------
            _DEEP_SYNC_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deep_sync_needed")

            def _run_deep_receipt_sync():
                """Launch receipt_sync.py in a subprocess so Chrome opens for login."""
                import subprocess
                logging.info("🧾 Deep receipt sync starting (24 months) — Chrome will open for login...")
                try:
                    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
                    py = venv_python if os.path.exists(venv_python) else sys.executable
                    sync_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "receipt_sync.py")
                    subprocess.run([py, sync_script], check=False)
                    logging.info("🧾 Deep receipt sync complete.")
                    send_telegram("🧾 *Deep Receipt Sync complete!* Price history enriched — targets will improve on next scrape cycle.")
                except Exception as e:
                    logging.error(f"Deep receipt sync failed: {e}")

            def _maybe_run_deep_sync():
                """Check flag and trigger sync exactly once."""
                if os.path.exists(_DEEP_SYNC_FLAG):
                    try:
                        os.remove(_DEEP_SYNC_FLAG)
                    except Exception:
                        pass
                    threading.Thread(target=_run_deep_receipt_sync, daemon=False).start()

            def _silent_update():
                logging.info("Running scheduled silent update...")
                _maybe_run_deep_sync()
                try:
                    with cffi_requests.Session(impersonate="chrome124") as session:
                        _refresh_coles_metadata(session)
                    run_report(full_list=True, send_telegram_messages=False)
                except Exception as e:
                    logging.error(f"Silent update failed: {e}")

            def _sunday_ping():
                logging.info("Running Sunday scheduled report...")
                _maybe_run_deep_sync()
                try:
                    with cffi_requests.Session(impersonate="chrome124") as session:
                        _refresh_coles_metadata(session)
                    run_report(full_list=True, send_telegram_messages=False)
                    send_telegram("🛒 *Weekly Prices Updated!*\n\nYour items have been freshly scanned and synced to the dashboard.\n\n🌐 [View Web Dashboard](https://KuschiKuschbert.github.io/wooliesbot/)")
                except Exception as e:
                    logging.error(f"Sunday ping failed: {e}")
                    send_telegram(f"🚨 Sunday ping failed:\n{str(e)}")

            # Deep sync flag is written once manually (or by an external trigger).
            # Do NOT auto-write it here — that would trigger a 24-month re-scrape
            # on every cold boot after the first run.
            _DEEP_SYNC_FLAG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deep_sync_needed")

            # Avoid stacking duplicate jobs if the outer supervisor loop restarts after a crash
            schedule.clear()

            # Update website frequently (every 4 hours) without telegram messages
            schedule.every(4).hours.do(_silent_update)

            # Trigger telegram ping ONLY on Sunday morning with a link to the website
            schedule.every().sunday.at("09:00").do(_sunday_ping)
            
            # Startup notification — optional (set WOOLIESBOT_TELEGRAM_STARTUP=0 to mute; default on)
            _su = (os.environ.get("WOOLIESBOT_TELEGRAM_STARTUP") or "1").strip().lower()
            if _su not in ("0", "false", "no", "off"):
                send_telegram(
                    "⚙️ *WooliesBot Internal Supervisor active.* "
                    "System is now monitoring prices and listening for commands."
                )
            logging.info("WoolesBot is active. Listening for Sunday 9am and /shop command...")
            
            while True:
                schedule.run_pending()
                next_job = schedule.next_run()
                if next_job:
                    NEXT_SCHEDULED_RUN = next_job
                time.sleep(60)
                
        except KeyboardInterrupt:
            logging.info("WoolesBot stopped by user.")
            sys.exit(0)
        except Exception as e:
            error_trace = traceback.format_exc()
            logging.critical(f"CRITICAL: Main loop crashed. Restarting in 30s...\nError: {e}\n{error_trace}")
            try:
                send_telegram(f"⚠️ *WooliesBot Supervisor Error:* Main loop crashed. Attempting auto-restart in 30s...\n\n`{str(e)[:100]}`")
            except Exception:
                pass
            time.sleep(30)

        # `e` is only in scope here if the inner except ran; guard accordingly
        try:
            send_telegram(f"🚨 *FATAL CRASH*: WoolesBot stopped.\n{_escape_md(str(e))}")
        except Exception:
            pass
        sys.exit(1)
