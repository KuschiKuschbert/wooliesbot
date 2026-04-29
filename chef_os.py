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
from wooliesbot_shared import (
    extract_coles_product_id as _shared_extract_coles_product_id,
    extract_size_signals as _shared_extract_size_signals,
    size_signals_compatible as _shared_size_signals_compatible,
    token_overlap_score as _shared_token_overlap_score,
)
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

# --- STORES ---
STORES = {
    "woolworths": {
        "label": "Woolies",
        "emoji": "🟢",
        "price_css": "div[class*='product-price_component_price-lead']",
        "unit_css": "div[class*='product-unit-price_component_price-cup-string'], div[class*='product-unit-price']",
        "price_fallback_css": (".price-dollars", ".price-cents"),
    },
    "coles": {
        "label": "Coles",
        "emoji": "🔴",
        "price_css": "span.price__value",
        "unit_css": "div.price__calculation_method, span.price__calculation_method",
        "price_fallback_css": None,
    },
}

# --- PRODUCT WATCHLIST ---
# price_mode: "kg" = compare per-kg unit price | "each" = compare shelf/pack price
# compare_group: items with same group compete — cheapest wins
# Use docs/data.json for both the bot and the dashboard tracking
_inv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")


def _normalize_items_payload(raw):
    """Return list of item dicts whether data.json is wrapped {items:[]} or a legacy bare array."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            return items
    return []


def _inventory_row_key(item):
    """Stable dict key for merging inventory with data.json (prefer item_id)."""
    if not isinstance(item, dict):
        return ""
    iid = item.get("item_id")
    if iid:
        return str(iid)
    name = item.get("name")
    return "name:" + name if name else ""


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


# --- Scraper tuning (env overrides beat adaptive adjustments) ---
def _env_int(key, default):
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_float(key, default):
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


_BASE_CHROME_THRESHOLD = min(0.95, max(0.35, _env_float("WOOLIESBOT_CHROME_FALLBACK_THRESHOLD", 0.6)))
_BASE_HTTP_RETRIES = max(1, _env_int("WOOLIESBOT_CFFI_HTTP_RETRIES", 4))
_OUTLIER_DEVIATION_PCT = min(90, max(5, _env_float("WOOLIESBOT_OUTLIER_DEVIATION_PCT", 40)))
_ADAPTIVE_ENABLED = os.environ.get("WOOLIESBOT_ADAPTIVE", "1").strip().lower() not in ("0", "false", "no")
# Max days a carry-forward (stale) price is kept before flipping to price_unavailable.
_STALE_MAX_DAYS = max(1, _env_int("WOOLIESBOT_STALE_MAX_DAYS", 14))
_METRICS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "scraper_metrics.json")
_MAX_METRICS_RUNS = max(5, _env_int("WOOLIESBOT_METRICS_HISTORY", 30))
# Retained for scraper_metrics.json (sequential BFF path; not parallel worker count)
_COLES_CFFI_WORKERS_CAP = max(1, _env_int("WOOLIESBOT_CFFI_COLES_WORKERS", 2))
_COLES_SEQUENTIAL = os.environ.get("WOOLIESBOT_COLES_SEQUENTIAL", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
_COLES_CHALLENGE_BACKOFF_SEC = min(180, max(15, _env_int("WOOLIESBOT_COLES_CHALLENGE_BACKOFF_SEC", 45)))
_COLES_DISCOVERY_SLEEP_SEC = max(0.5, _env_float("WOOLIESBOT_COLES_DISCOVERY_SLEEP_SEC", 2.0))
_COLES_DISCOVERY_MIN_SCORE = max(0.0, min(0.5, _env_float("WOOLIESBOT_COLES_DISCOVERY_MIN_SCORE", 0.12)))
# HTML length heuristics (Akamai / Next.js shells vary; strict 5k rejects valid ~4.5k responses)
_WOOLIES_WARMUP_MIN_CHARS = max(2000, _env_int("WOOLIESBOT_WOOLIES_WARMUP_MIN_CHARS", 3500))
_COLES_WARMUP_MIN_CHARS = max(400, _env_int("WOOLIESBOT_COLES_WARMUP_MIN_CHARS", 1800))
_PDP_MIN_HTML_CHARS = max(2000, _env_int("WOOLIESBOT_PDP_MIN_HTML_CHARS", 4500))
_REQ_JITTER_MIN_SEC = max(0.0, _env_float("WOOLIESBOT_REQUEST_JITTER_MIN_SEC", 1.5))
_REQ_JITTER_MAX_SEC = max(_REQ_JITTER_MIN_SEC, _env_float("WOOLIESBOT_REQUEST_JITTER_MAX_SEC", 4.0))
_HTTP_PROXY_GLOBAL = os.environ.get("WOOLIESBOT_HTTP_PROXY", "").strip()
_HTTP_PROXY_WOOLIES = os.environ.get("WOOLIESBOT_WOOLIES_PROXY", "").strip()
_HTTP_PROXY_COLES = os.environ.get("WOOLIESBOT_COLES_PROXY", "").strip()

# Coles BFF (Backend-for-Frontend) API — bypasses Imperva JS challenges entirely
_COLES_BFF_SUBSCRIPTION_KEY = os.environ.get(
    "WOOLIESBOT_COLES_BFF_KEY", "eae83861d1cd4de6bb9cd8a2cd6f041e"
).strip()
_COLES_BFF_STORE_ID = os.environ.get("WOOLIESBOT_COLES_STORE_ID", "0584").strip()

# Per-run counters (reset in check_prices)
_scrape_run_stats = {
    "http_429": 0,
    "http_5xx": 0,
    "cffi_attempts": 0,
    "stores_used_chrome": [],
    "coles_challenge": 0,
    "coles_429": 0,
}

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

# Big Shop triggers after the 14th (weeks 3–4) to use the 10% discount
BIG_SHOP_START_DAY = 14
DISCOUNT_CAP = 500.00
NEXT_SCHEDULED_RUN = None  # Global to track next scraper run

TELEGRAM_MAX_LEN = 4000  # Leave margin for Markdown

def _escape_md(text):
    """Escape Telegram Markdown V1 special characters in dynamic text."""
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, '\\' + ch)
    return text

def send_telegram(message):
    """Send message(s) to Telegram. Splits at newlines if over limit.
    Falls back to plain text if Markdown parse fails."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.debug("Telegram not configured; message skipped.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(message) <= TELEGRAM_MAX_LEN:
        parts = [message]
    else:
        parts, curr = [], []
        for line in message.split("\n"):
            if sum(len(l)+1 for l in curr) + len(line) + 1 > TELEGRAM_MAX_LEN and curr:
                parts.append("\n".join(curr))
                curr = []
            curr.append(line)
        if curr:
            parts.append("\n".join(curr))
    for part in parts:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code == 400 and "can't parse" in response.text.lower():
                # Markdown parse failed; retry without formatting
                logging.warning("Markdown parse failed, retrying as plain text.")
                payload["parse_mode"] = ""
                response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            logging.info("Telegram message sent successfully.")
        except Exception as e:
            logging.error(f"Error sending Telegram: {e}")

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

def _parse_price_text(text):
    """Extract a float price from text like '$18.70' or '18.70'."""
    text = text.replace("$", "").replace(",", "").strip()
    match = re.search(r"(\d+\.?\d*)", text)
    return float(match.group(1)) if match else None

def _parse_unit_price_text(text):
    """Extract unit price and unit from text like '$11.00 / 1KG'."""
    if '$' not in text:
        return None, None
    val_part = text.split('/')[0].replace('$', '').strip()
    try:
        price = float(re.search(r"(\d+\.?\d*)", val_part).group(1))
    except:
        return None, None
    # Determine unit: per kg, per 100g, per litre, per each
    text_lower = text.lower()
    if 'kg' in text_lower:
        unit = 'kg'
    elif '100g' in text_lower:
        unit = '100g'
    elif 'litre' in text_lower or '1l' in text_lower:
        unit = 'litre'
    else:
        unit = 'each'
    return price, unit

# ─── JSON EXTRACTION (structured data – much more reliable than CSS) ─────────

def _extract_woolworths_json(driver):
    """Extract product data from Woolworths JSON-LD (schema.org) embedded in page.
    Returns dict with price, unit_price, unit, name_check or None."""
    try:
        scripts = driver.find_elements(By.CSS_SELECTOR, 'script[type="application/ld+json"]')
        for s in scripts:
            data = json.loads(s.get_attribute('innerHTML'))
            p = _walk_woolworths_ld_node(data)
            if p:
                return p
    except Exception as e:
        logging.debug(f"  Woolworths JSON-LD parse error: {e}")
    return None

_coles_cached_build_id = None  # single global — do NOT redeclare below
_data_write_lock = threading.Lock()  # prevents concurrent data.json writes

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
        resp = session.get("https://www.coles.com.au/product/a-123456", headers=_get_coles_headers(_get_random_ua_profile()), timeout=10)
        if _apply_coles_build_id_from_html(resp.text, "coles_product_shell"):
            return True
    except Exception as e:
        logging.debug(f"Coles metadata refresh failed: {e}")
    return False

def _extract_coles_json(driver):
    """Extract product data from Coles __NEXT_DATA__ (Next.js) embedded in page.
    Returns dict with price, unit_price, unit, name_check, is_special or None."""
    try:
        # Wait for the element to be present
        wait = WebDriverWait(driver, 10)
        nd_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'script#__NEXT_DATA__')))
        nd = json.loads(nd_el.get_attribute('innerHTML'))
        product = nd.get('props', {}).get('pageProps', {}).get('product', {})
        pricing = product.get('pricing', {})
        if not pricing or not pricing.get('now'):
            return None
        price = float(pricing['now'])
        unit_data = pricing.get('unit', {}) or {}
        unit_price_val = unit_data.get('price')
        unit_type = (unit_data.get('ofMeasureUnits') or '').lower()
        # Map Coles unit types
        unit = 'each'
        if unit_type == 'kg':
            unit = 'kg'
        elif unit_type in ('l', 'litre', 'ltr'):
            unit = 'litre'
        elif '100g' in unit_type:
            unit = '100g'
        up = float(unit_price_val) if unit_price_val else None
        image_uris = product.get('imageUris', [])
        image_url = image_uris[0].get('uri', '') if image_uris else ''
        if image_url and image_url.startswith('/'):
            image_url = "https://www.coles.com.au" + image_url

        return {
            "price": price,
            "unit_price": up,
            "unit": unit,
            "name_check": product.get('name', ''),
            "is_special": pricing.get('promotionType') == 'SPECIAL',
            "was_price": pricing.get('was'),
            "image_url": image_url,
        }
    except Exception as e:
        logging.debug(f"  Coles __NEXT_DATA__ parse error: {e}")
    return None


_UA_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        "platform": '"macOS"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        "platform": '"Windows"',
        "impersonate": "chrome131",
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"macOS"',
        "impersonate": "chrome124",
    },
]

_run_ua_profile = None  # set once at start of each scrape run


def _get_run_ua_profile():
    """Return the UA profile locked for the entire scrape run (fingerprint consistency)."""
    global _run_ua_profile
    if _run_ua_profile is None:
        _run_ua_profile = random.choice(_UA_PROFILES)
    return _run_ua_profile


def _get_random_ua_profile():
    return _get_run_ua_profile()


def _sleep_request_jitter(multiplier=1.0):
    lo = max(0.0, _REQ_JITTER_MIN_SEC * multiplier)
    hi = max(lo, _REQ_JITTER_MAX_SEC * multiplier)
    if hi > 0:
        time.sleep(random.uniform(lo, hi))


_BATCH_SIZE = max(5, _env_int("WOOLIESBOT_BATCH_SIZE", 20))
_BATCH_PAUSE_MIN = max(5.0, _env_float("WOOLIESBOT_BATCH_PAUSE_MIN_SEC", 20.0))
_BATCH_PAUSE_MAX = max(_BATCH_PAUSE_MIN, _env_float("WOOLIESBOT_BATCH_PAUSE_MAX_SEC", 40.0))
_CIRCUIT_BREAKER_STREAK = max(2, _env_int("WOOLIESBOT_CIRCUIT_BREAKER_STREAK", 3))
_CIRCUIT_BREAKER_PAUSE = max(30, _env_int("WOOLIESBOT_CIRCUIT_BREAKER_PAUSE_SEC", 120))


def _proxy_for_store(store_key):
    if store_key == "woolworths":
        return _HTTP_PROXY_WOOLIES or _HTTP_PROXY_GLOBAL
    if store_key == "coles":
        return _HTTP_PROXY_COLES or _HTTP_PROXY_GLOBAL
    return _HTTP_PROXY_GLOBAL


def _get_woolworths_headers(url=None, profile=None):
    """Browser-like headers for Woolworths PDP fetches (Akamai)."""
    profile = profile or _get_random_ua_profile()
    h = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if (url and "woolworths.com.au" in url) else "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": profile["ua"],
    }
    if url and "woolworths.com.au" in url:
        h["Referer"] = "https://www.woolworths.com.au/"
    return h


def _get_coles_headers(profile=None):
    """Returns headers that mimic a real Chrome browser on macOS to bypass Akamai."""
    profile = profile or _get_random_ua_profile()
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": profile["sec_ch_ua"],
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": profile["platform"],
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": profile["ua"],
        "X-Requested-With": "XMLHttpRequest"
    }


def _cffi_fetch_coles_api(session, url, build_id):
    """Fetch Coles product data directly via the Next.js data API (very fast)."""
    try:
        # Extract product path part from URL: /product/coles-beef-rump-steak-...
        match = re.search(r'/product/([^?#]+)', url)
        if not match: return None
        prod_slug = match.group(1)
        
        # Coles API URL pattern
        api_url = f"https://www.coles.com.au/_next/data/{build_id}/product/{prod_slug}.json"
        
        resp = session.get(api_url, headers=_get_coles_headers(_get_random_ua_profile()), timeout=10)
        global _coles_cached_build_id
        if resp.status_code == 404:
            logging.warning("Coles _next/data API 404 — buildId may have expired")
            _coles_cached_build_id = None
            return None
        if resp.status_code == 200:
            data = resp.json().get('pageProps', {}).get('product', {})
            pricing = data.get('pricing', {})
            if pricing and pricing.get('now'):
                price = float(pricing['now'])
                unit_data = pricing.get('unit', {}) or {}
                up = float(unit_data.get('price')) if unit_data.get('price') else None
                # Basic normalization
                unit_type = (unit_data.get('ofMeasureUnits') or '').lower()
                unit = 'kg' if unit_type == 'kg' else ('litre' if unit_type in ('l', 'litre') else 'each')
                
                image_uris = data.get('imageUris', [])
                img = "https://www.coles.com.au" + image_uris[0].get('uri', '') if image_uris else ""
                
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
                f"[Coles] ({item_num+1}/{len(items_with_urls)}) ✓ {item['name']} "
                f"${data['price']:.2f}"
            )
        else:
            failed.append((idx, item, url))
            logging.info(
                f"[Coles] ({item_num+1}/{len(items_with_urls)}) ✗ {item['name']} "
                f"(no BFF pricing)"
            )
        time.sleep(random.uniform(0.3, 0.8))
    logging.info(
        f"[Coles] BFF API done: {len(results)}/{len(items_with_urls)} succeeded, "
        f"{len(failed)} need Chrome fallback"
    )
    return results, failed


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
        # Find all script tags with type="application/ld+json" (content may include '<' in JSON strings)
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


_RECEIPT_ABBREVIATIONS = {
    "Ww": "Woolworths", "Df": "Dairy Farmers", "Ess": "Essentials",
    "Srdgh": "Sourdough", "Hmlyn": "Himalayan", "Bflied": "Butterflied",
    "B'Flied": "Butterflied", "Lemn": "Lemon", "Grlc": "Garlic",
    "Starwberry": "Strawberry", "Conc": "Concentrate", "Rw": "",
    "Trplsmkd": "Triple Smoked", "Shvd": "Shaved",
    "Apprvd": "Approved", "F/F": "Fat Free", "F/C": "Fresh Choice",
    "P/P": "", "Ff": "", "Pnut": "Peanut", "Crml": "Caramel",
    "Ckie": "Cookie", "Btr": "Butter", "Efferv": "Effervescent",
    "Ap": "Antiperspirant", "Cb": "Carb", "Hm": "Ham",
    "T/Tissue": "Toilet Tissue", "Lge": "Large", "Xl": "Extra Large",
    "Choc": "Chocolate", "Pud": "Pudding", "Crspy": "Crispy",
    "Bbq": "BBQ", "Crnchy": "Crunchy", "Pb": "Peanut Butter",
    "35Hr": "35 Hour", "Dbl": "Double", "Esprs": "Espresso",
    "Flav": "Flavoured", "Wtr": "Water", "Natrl": "Natural",
    "Tbone": "T-Bone", "Bflied": "Butterflied",
    "P&S": "Pasta Sauce", "L&C": "Light Crispy",
    "L/F": "Low Fat", "P/Appl": "Pineapple", "P/Apple": "Pineapple",
    "W/Mln": "Watermelon", "F/Milk": "Full Cream Milk",
    "M/Blast": "Mountain Blast", "H/Comb": "Honeycomb",
    "B/There": "Barely There",
}


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
    from urllib.parse import urlparse, parse_qs, unquote
    # Strip leading '#' that some URLs embed before the term
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
            _scrape_run_stats["http_429"] = _scrape_run_stats.get("http_429", 0) + 1
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
        # Try top results, pick the best name-overlap match
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
                # Asymmetric weight check: if inventory specifies a large pack (≥500g) but the
                # search result has no weight signal at all, the result is likely a loose/individual
                # item matched too loosely (e.g. "Carrot Fresh" matching "Carrot 1Kg P/P").
                # Require a stronger overlap in that case rather than outright rejection.
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
        product = nd.get('props', {}).get('pageProps', {}).get('product', {})
        pricing = product.get('pricing', {})
        if not pricing or not pricing.get('now'):
            return None
        price = float(pricing['now'])
        unit_data = pricing.get('unit', {}) or {}
        unit_price_val = unit_data.get('price')
        unit_type = (unit_data.get('ofMeasureUnits') or '').lower()
        unit = 'each'
        if unit_type == 'kg':
            unit = 'kg'
        elif unit_type in ('l', 'litre', 'ltr'):
            unit = 'litre'
        elif '100g' in unit_type:
            unit = '100g'
        up = float(unit_price_val) if unit_price_val else None
        image_uris = product.get('imageUris', [])
        image_url = image_uris[0].get('uri', '') if image_uris else ''
        if image_url and image_url.startswith('/'):
            image_url = "https://www.coles.com.au" + image_url

        return {
            "price": price,
            "unit_price": up,
            "unit": unit,
            "name_check": product.get('name', ''),
            "is_special": pricing.get('promotionType') == 'SPECIAL',
            "was_price": pricing.get('was'),
            "image_url": image_url,
        }
    except Exception as e:
        logging.debug(f"  Coles __NEXT_DATA__ from HTML parse error: {e}")
    return None


# ─── SCRAPER (JSON-first, CSS-fallback, with page validation) ────────────────

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
    store = STORES[store_key]
    try:
        driver.get(url)
        if store_key == "coles":
            _wait_for_real_page(driver, min_chars=_PDP_MIN_HTML_CHARS, timeout=20)
        else:
            time.sleep(random.uniform(1.5, 3.5))

        # ── Page validation: detect bot blocks / empty pages ──
        page_len = len(driver.page_source)
        if page_len < _PDP_MIN_HTML_CHARS:
            logging.warning(f"  [{store_key}] Page too short ({page_len} chars) — possible bot block")
            return None

        # ── Strategy 1: Structured JSON extraction ──
        json_result = None
        if store_key == 'woolworths':
            json_result = _extract_woolworths_json(driver)
        elif store_key == 'coles':
            json_result = _extract_coles_json(driver)

        if json_result and json_result["price"] > 0:
            logging.info(f"    ✓ JSON: ${json_result['price']:.2f} (unit: {json_result.get('unit_price')}/{json_result.get('unit')})")
            return {
                "price": json_result["price"],
                "unit_price": json_result.get("unit_price"),
                "unit": json_result.get("unit"),
                "image_url": json_result.get("image_url", ""),
                "was_price": json_result.get("was_price"),
                "on_special": bool(json_result.get("is_special") or json_result.get("was_price")),
            }

        # ── Strategy 2: CSS selector fallback ──
        logging.info(f"    JSON extraction failed, trying CSS selectors...")
        wait = WebDriverWait(driver, 12)
        price = None
        try:
            price_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, store["price_css"])))
            price = _parse_price_text(price_el.text)
        except:
            if store.get("price_fallback_css"):
                try:
                    d_css, c_css = store["price_fallback_css"]
                    d_el = driver.find_element(By.CSS_SELECTOR, d_css)
                    c_el = driver.find_element(By.CSS_SELECTOR, c_css)
                    price = float(f"{d_el.text}.{c_el.text}")
                except:
                    pass
        if price is None:
            return None

        unit_price, unit = None, None
        try:
            unit_el = driver.find_element(By.CSS_SELECTOR, store["unit_css"])
            unit_price, unit = _parse_unit_price_text(unit_el.text)
        except:
            pass

        logging.info(f"    ✓ CSS: ${price:.2f} (unit: {unit_price}/{unit})")
        return {"price": price, "unit_price": unit_price, "unit": unit}
    except SeleniumTimeout:
        logging.warning(f"  [{store_key}] Page load timed out (45s) — skipping")
        return None
    except Exception as e:
        if _is_broken_session_error(e):
            raise BrowserSessionDead(str(e)) from e
        logging.debug(f"  {store_key} scrape error: {e}")
        return None

MAX_RETRIES = 2  # up to 2 attempts per item
_MAX_BROWSER_SESSION_RESTARTS = 12  # per Chrome batch — avoids infinite loops if Chrome keeps dying


def _token_overlap_score(inv_name, scraped_name):
    """Jaccard-like overlap on alphanumeric tokens (0..1).
    Expands receipt abbreviations before comparing so 'Ww'='Woolworths' etc."""
    return _shared_token_overlap_score(
        inv_name,
        scraped_name,
        abbreviations=_RECEIPT_ABBREVIATIONS,
        normalize_brand_aliases=True,
    )


def _extract_size_signals(text):
    """Extract comparable size signals from a label.
    Used to reject obvious mismatches like 600ml vs 10x375ml."""
    return _shared_extract_size_signals(text)


def _size_signals_compatible(inventory_name, scraped_name):
    """Return False when inventory and scraped labels clearly disagree on size."""
    return _shared_size_signals_compatible(inventory_name, scraped_name)


def _read_metrics_runs():
    try:
        if os.path.exists(_METRICS_PATH):
            with open(_METRICS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data[-_MAX_METRICS_RUNS:]
    except Exception as e:
        logging.debug(f"metrics read: {e}")
    return []


def _append_metrics_run(entry):
    try:
        os.makedirs(os.path.dirname(_METRICS_PATH), exist_ok=True)
        runs = _read_metrics_runs()
        runs.append(entry)
        runs = runs[-_MAX_METRICS_RUNS:]
        with open(_METRICS_PATH, "w", encoding="utf-8") as f:
            json.dump(runs, f, indent=2)
    except Exception as e:
        logging.warning(f"metrics write failed: {e}")


def _get_chrome_fallback_threshold():
    t = _BASE_CHROME_THRESHOLD
    if os.environ.get("WOOLIESBOT_CHROME_FALLBACK_THRESHOLD"):
        return min(0.95, max(0.35, t))
    if not _ADAPTIVE_ENABLED:
        return min(0.95, max(0.35, t))
    recent = [r for r in _read_metrics_runs()[-5:] if r.get("cffi_success_rate") is not None]
    if len(recent) >= 3 and all(r.get("cffi_success_rate", 0) >= 0.95 for r in recent[-3:]):
        t = min(0.88, t + 0.03)
    if recent:
        latest = recent[-1].get("cffi_success_rate", 1)
        if latest < 0.55:
            t = max(0.4, t - 0.15)
        elif latest < 0.75:
            t = max(0.45, t - 0.08)
    return min(0.95, max(0.35, t))


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


def _http_retry_budget():
    r = _BASE_HTTP_RETRIES
    if os.environ.get("WOOLIESBOT_CFFI_HTTP_RETRIES"):
        return max(1, r)
    if not _ADAPTIVE_ENABLED:
        return max(1, r)
    runs = _read_metrics_runs()
    if runs and runs[-1].get("http_5xx", 0) >= 3:
        return min(5, r + 1)
    return max(1, r)


def _finalize_cffi_product_dict(data, inventory_name, store_key=None):
    """Build stored product dict; reject results with very low name overlap."""
    if inventory_name and data.get("name_check"):
        ov = _token_overlap_score(inventory_name, data["name_check"])
        min_overlap = 0.12 if store_key == "coles" else 0.15
        if not _size_signals_compatible(inventory_name, data["name_check"]):
            logging.warning(
                f"Rejecting size mismatch ({store_key}): inventory={inventory_name!r} vs page={data.get('name_check')!r} price=${data.get('price')}"
            )
            return None
        if ov < min_overlap:
            logging.warning(
                f"Rejecting low overlap ({ov:.2f}): inventory={inventory_name!r} vs page={data.get('name_check')!r} price=${data.get('price')}"
            )
            return None
    return {
        "price": data["price"],
        "unit_price": data.get("unit_price"),
        "unit": data.get("unit", "each"),
        "image_url": data.get("image_url", ""),
        "was_price": data.get("was_price"),
        "on_special": bool(data.get("is_special") or data.get("was_price")),
        "name_check": data.get("name_check", ""),
    }


def _scrape_store_batch(store_key, items_with_urls):
    """Scrape all items for ONE store in its own browser.
    Includes retry logic: each item gets up to MAX_RETRIES attempts.
    Returns (store_key, list of (item_index, store_data))."""
    label = STORES[store_key]["label"]
    logging.info(f"[{label}] Starting browser for {len(items_with_urls)} items...")
    driver = get_browser()

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
                _safe_quit_driver(driver)
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
                except BrowserSessionDead as e:
                    session_restarts += 1
                    logging.warning(
                        f"[{label}] Browser session lost ({e!s}) — restarting ({session_restarts}/{_MAX_BROWSER_SESSION_RESTARTS})..."
                    )
                    _safe_quit_driver(driver)
                    driver = None
                    if session_restarts > _MAX_BROWSER_SESSION_RESTARTS:
                        logging.error(
                            f"[{label}] Too many dead browser sessions — stopping Chrome batch early "
                            f"({len(results)}/{len(items_with_urls)} done)."
                        )
                        return store_key, results
                    driver = get_browser()
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
                safe_name = item['name'].replace(' ', '_').replace('/', '_')
                try:
                    _ss_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "screenshots")
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
        _safe_quit_driver(driver)
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

    # Phase 1: Search API items (lightweight JSON, much less likely to trigger blocks)
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

    # Phase 2: PDP items (full HTML pages, heavier, needs more pacing)
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


def _successful_idxs_from_batch(batch_results):
    return {idx for idx, _ in batch_results}


def _failed_jobs_from_batch(jobs, batch_results):
    ok = _successful_idxs_from_batch(batch_results)
    return [j for j in jobs if j[0] not in ok]


_PRICE_UNRELIABLE = 99999.0  # sentinel for bad / missing prices

def _effective_price(item, store_result):
    """Return the price to compare against the target.
    'kg'    → use scraped unit_price (per kg); normalise 100g→kg.
               If unit price scrape failed, return sentinel (shelf price ≠ per-kg).
    'litre' → ALWAYS calculate shelf_price / pack_litres (scraped unit prices on
               multi-pack pages are unreliable – they can pick up 'was' prices).
    'each'  → shelf price."""
    mode = item.get("price_mode", "each")
    if mode == "kg":
        up = store_result.get("unit_price")
        scraped_unit = store_result.get("unit", "")
        # Only trust unit_price when the scraped unit is actually per-kg or per-100g
        if up is not None and scraped_unit in ("kg", "100g"):
            val = up * 10 if scraped_unit == "100g" else up
            # Sanity: reject absurd per-kg prices (scraper errors, e.g. deli unit mix-ups)
            if val > 100:
                logging.warning(f"Rejecting absurd $/kg {val:.2f} for {item.get('name')}")
                return _PRICE_UNRELIABLE
            return val
        # Unit price is per-each/per-litre or scrape failed — can't determine $/kg
        return _PRICE_UNRELIABLE
    if mode == "litre":
        # Always calculate from known pack volume (most reliable)
        pack_l = item.get("pack_litres")
        if pack_l and pack_l > 0:
            return store_result["price"] / pack_l
        # No pack_litres defined – try scraped
        up = store_result.get("unit_price")
        unit = store_result.get("unit", "")
        if up is not None and unit == "litre":
            return up
        return store_result["price"]
    return store_result["price"]


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
    global _run_ua_profile
    _run_ua_profile = random.choice(_UA_PROFILES)
    reload_tracking_list()
    _scrape_run_stats["http_429"] = 0
    _scrape_run_stats["http_5xx"] = 0
    _scrape_run_stats["stores_used_chrome"] = []
    _scrape_run_stats["coles_challenge"] = 0
    _scrape_run_stats["coles_429"] = 0
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
        f"chrome_threshold={threshold:.0%}, UA={_run_ua_profile['impersonate']})"
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
                    _scrape_run_stats["stores_used_chrome"].append("woolworths")
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
                _scrape_run_stats["stores_used_chrome"].append("coles")
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
        "http_429": _scrape_run_stats.get("http_429", 0),
        "http_5xx": _scrape_run_stats.get("http_5xx", 0),
        "coles_429": _scrape_run_stats.get("coles_429", 0),
        "coles_challenge": _scrape_run_stats.get("coles_challenge", 0),
        "coles_sequential": bool(_COLES_SEQUENTIAL),
        "coles_workers_cap": _COLES_CFFI_WORKERS_CAP,
        "chrome_subset_events": chrome_subset_events,
        "stores_chrome": sorted(set(_scrape_run_stats.get("stores_used_chrome", []))),
    })

    logging.info(f"Price Scan complete. {len(results)}/{len(TRACKING_LIST)} items with prices.")
    return results

def _nl(): return "\n"
def _sp(): return "\n\n"  # Section spacing for smartphone


# Weekly Essentials checklist (always buy, regardless of specials)
WEEKLY_ESSENTIALS = [
    "Capsicum, Onions, Spinach",
    "Eggs, Cream, Cheese",
    "Avocado, Zucchini",
]

def _store_badge(store_key):
    s = STORES.get(store_key, {})
    return f"{s.get('emoji', '')} {s.get('label', store_key)}"

def _price_display(item):
    """Format price string based on price_mode."""
    if item.get("price_unavailable"):
        return "❓ price unavailable"
    mode = item.get("price_mode", "each")
    eff = item.get("eff_price", item["price"])
    if mode == "kg":
        return f"${eff:.2f}/kg"
    if mode == "litre":
        return f"${item['price']:.2f} (${eff:.2f}/L)"
    return f"${item['price']:.2f}"

def _multi_store_line(item, compact=False):
    """Show prices from all stores for an item (excludes unreliable prices).
    compact: drop unit suffix when same for all (e.g. $11 vs $16 instead of $11/kg vs $16/kg)."""
    stores = item.get("all_stores", {})
    reliable = {sk: sd for sk, sd in stores.items() if sd["eff_price"] < _PRICE_UNRELIABLE}
    if len(reliable) <= 1:
        return ""
    parts = []
    mode = item.get("price_mode", "each")
    for sk, sd in sorted(reliable.items(), key=lambda x: x[1]["eff_price"]):
        se = STORES[sk]["emoji"]
        if mode == "kg":
            if compact:
                parts.append(f"{se}${sd['eff_price']:.2f}")
            else:
                parts.append(f"{se}${sd['eff_price']:.2f}/kg")
        elif mode == "litre":
            if compact:
                parts.append(f"{se}${sd['eff_price']:.2f}/L")
            else:
                parts.append(f"{se}${sd['price']:.2f} (${sd['eff_price']:.2f}/L)")
        else:
            parts.append(f"{se}${sd['price']:.2f}")
    return "  " + " vs ".join(parts)


def _item_store_prices(item):
    """Get (woolies_price_str, coles_price_str) from item's all_stores. Uses — when missing."""
    stores = item.get("all_stores", {})
    mode = item.get("price_mode", "each")
    woolies_sd = stores.get("woolworths")
    coles_sd = stores.get("coles")

    def fmt(sd):
        if not sd or sd.get("eff_price", 0) >= _PRICE_UNRELIABLE:
            return "—"
        if mode == "kg":
            return f"${sd['eff_price']:.2f}/kg"
        if mode == "litre":
            p = sd.get("price")
            ep = sd.get("eff_price")
            return f"${p:.2f}" if p else f"${ep:.2f}/L"
        return f"${sd['price']:.2f}"

    return (fmt(woolies_sd), fmt(coles_sd))

def export_data_to_json(results):
    """Exports scraped data to data.json and appends today's snapshot to scrape_history.

    This is the SINGLE write path for the dashboard data file.
    Preserves existing per-item fields (scrape_history, price_history, metadata)
    while overlaying fresh scraped prices.
    """
    try:
        os.makedirs("docs", exist_ok=True)
        data_path = "docs/data.json"
        now = datetime.datetime.now()
        # ISO-8601 UTC with Z so browsers parse one correct instant (avoids 12h strftime ambiguity).
        now_str = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        today_str = now.strftime("%Y-%m-%d")

        # Load existing data to preserve scrape_history and other accumulated fields
        existing_by_key = {}
        if os.path.exists(data_path):
            try:
                with open(data_path, "r") as f:
                    raw = json.load(f)
                existing_items = raw if isinstance(raw, list) else raw.get("items", [])
                for ei in existing_items:
                    k = _inventory_row_key(ei)
                    if k:
                        existing_by_key[k] = ei
            except Exception:
                existing_by_key = {}

        # Merge: overlay fresh results onto existing items, preserving history fields
        merged = []
        for item in results:
            if not item.get("item_id") and item.get("name"):
                legacy = existing_by_key.get("name:" + item["name"])
                if legacy and legacy.get("item_id"):
                    item["item_id"] = legacy["item_id"]
                else:
                    item["item_id"] = str(uuid.uuid4())
            key = _inventory_row_key(item)
            existing = existing_by_key.get(key, {}) if key else {}
            if not existing and item.get("name"):
                existing = existing_by_key.get("name:" + item["name"], {})

            def _is_effectively_empty(value):
                if value is None:
                    return True
                if isinstance(value, (dict, list, tuple, set)):
                    return len(value) == 0
                if isinstance(value, str):
                    return value == ""
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return value == 0
                return False

            # Carry over accumulated fields that the scraper doesn't produce
            for keep_field in ("scrape_history", "price_history", "brand", "subcategory",
                               "size", "tags", "target_confidence", "target_method",
                               "target_data_points", "target_updated", "last_purchased",
                               "local_image", "on_special", "was_price",
                               "type", "all_stores", "coles",
                               # Preserve fields written by validators / out-of-band tools
                               # so the next scrape merge does not clobber them.
                               "last_layer_a_check"):
                if keep_field in existing and (
                    keep_field not in item or _is_effectively_empty(item.get(keep_field))
                ):
                    item[keep_field] = existing[keep_field]

            # Append today's scrape snapshot to scrape_history
            sh = item.get("scrape_history", [])
            sh = [
                entry for entry in sh
                if not (
                    entry.get("date") == today_str and
                    isinstance(entry.get("price"), (int, float)) and
                    entry["price"] >= _PRICE_UNRELIABLE
                )
            ]
            snapshot_price = item.get("eff_price", item.get("price", 0))
            snapshot_reliable = (
                isinstance(snapshot_price, (int, float)) and
                0 < snapshot_price < _PRICE_UNRELIABLE and
                not item.get("price_unavailable")
            )
            if snapshot_reliable:
                entry = {
                    "date": today_str,
                    "price": snapshot_price,
                    "is_special": item.get("on_special", False),
                    "was_price": item.get("was_price"),
                    "store": item.get("store"),
                }
                nc = item.get("name_check", "")
                if nc:
                    entry["matched_name"] = nc
                if sh and sh[-1].get("date") == today_str:
                    # Keep today's history row aligned with the latest successful snapshot.
                    sh[-1] = entry
                else:
                    sh.append(entry)
            item["scrape_history"] = sh
            # ── Backfill `store` if not set by the live scrape ──────────────────────
            # Items that weren't scraped this cycle (or where the scraper returned
            # store=None) should inherit the store from their most recent scrape snapshot.
            if not item.get("store") or item.get("store") == "none":
                recent_store = next(
                    (entry.get("store") for entry in reversed(item.get("scrape_history", []))
                     if entry.get("store") and entry["store"] != "none"),
                    None
                )
                if recent_store:
                    item["store"] = recent_store


            # ── Sync price from today's scrape_history entry ────────────────────────────
            # If the scraper produced a fresh snapshot today, trust it over any stale
            # value in `price` (which may be from a corrupted run weeks ago).
            today_snap = next(
                (e for e in reversed(item.get("scrape_history", []))
                 if e.get("date") == today_str and e.get("price") and e["price"] > 0),
                None
            )
            if today_snap:
                fresh = today_snap["price"]
                # Only overwrite if it's plausibly saner than what we have
                # (i.e. the fresh price is lower or the existing one is clearly wrong)
                existing_price = item.get("price") or 0
                cat = item.get("type", "")
                sane_ceiling = 60 if cat in ("produce", "dairy", "bakery") else 300
                if existing_price > sane_ceiling or fresh < existing_price * 0.7:
                    logging.info(
                        f"Correcting stale price for {item['name']}: "
                        f"${existing_price:.2f} → ${fresh:.2f} (from today's scrape)"
                    )
                    item["price"] = fresh
                    item["eff_price"] = fresh

            # ── Sanity-clamp obviously wrong prices ─────────────────────────────────
            # Per-category ceilings catch realistic corruption (cents→dollars errors etc.)
            cat = item.get("type", "")
            _PRICE_ERROR_THRESHOLD = 60 if cat in ("produce", "dairy", "bakery") else 1000
            for price_field in ("price", "eff_price", "was_price"):
                v = item.get(price_field)
                if v is not None and v > _PRICE_ERROR_THRESHOLD:
                    logging.warning(f"Clamping bad {price_field} for {item['name']}: ${v}")
                    item.pop(price_field, None)
                    item["price_unavailable"] = True

            # ── Sanity-check size field vs product name ──────────────────────────────
            # The Woolworths/Coles API sometimes returns "25L" for a "1.25L" product
            # (the leading "1." gets stripped). Cross-check and fix from name if so.
            raw_size = item.get("size", "")
            name_lower = item.get("name", "").lower()
            if raw_size:
                m_size = re.match(r'^(\d+\.?\d*)(l|kg|ml|g)$', raw_size.strip().lower())
                if m_size:
                    api_num = float(m_size.group(1))
                    unit = m_size.group(2)
                    # Look for the real size in the name (e.g. "1.25L")
                    m_name = re.search(r'(\d+\.?\d*)\s*' + unit + r'\b', name_lower)
                    if m_name:
                        name_num = float(m_name.group(1))
                        # If they differ by the api_num matching name_num mod 10 or mod 100
                        # (classic sign of leading digits being dropped)
                        if abs(api_num - name_num) > 0.01 and abs(name_num) > 0:
                            corrected = f"{name_num}{unit.upper()}"
                            logging.warning(f"Size mismatch for '{item['name']}': API={raw_size} name={corrected} — using name")
                            item["size"] = corrected

            merged.append(item)

        payload = {
            "last_updated": now_str,
            "items": merged,
        }
        with _data_write_lock:
            with open(data_path, "w") as f:
                json.dump(payload, f, indent=2)
        logging.info(f"Exported data.json successfully ({len(merged)} items, scrape_history updated).")
    except Exception as e:
        logging.error(f"Error exporting data.json: {e}")


def _next_github_actions_scrape_utc(after=None):
    """Next time matching `.github/workflows/scrape.yml` schedule: ``0 */4 * * *`` (UTC)."""
    if after is None:
        after = datetime.datetime.now(datetime.timezone.utc)
    elif after.tzinfo is None:
        after = after.replace(tzinfo=datetime.timezone.utc)
    else:
        after = after.astimezone(datetime.timezone.utc)
    slots = (0, 4, 8, 12, 16, 20)
    for d in range(0, 2):
        day = after.date() + datetime.timedelta(days=d)
        for h in slots:
            cand = datetime.datetime.combine(day, datetime.time(h, 0, 0, tzinfo=datetime.timezone.utc))
            if cand > after:
                return cand
    return after + datetime.timedelta(hours=4)


def sync_to_github(next_scheduled=None):
    """Commits and pushes the docs/ folder and updated JSON data to GitHub.

    next_scheduled: optional datetime for the next scrape (pass from run_report on the main
    thread so heartbeat matches schedule library state; avoids stale NEXT_SCHEDULED_RUN).
    """
    import subprocess

    lock_fd = _acquire_git_push_lock()
    if lock_fd is None:
        logging.warning("GitHub sync skipped: another sync is already in progress.")
        return

    def _run_git(args, check=False):
        return subprocess.run(args, capture_output=True, text=True, check=check)

    try:
        try:
            gen = subprocess.run(
                [sys.executable, "scripts/generate_runtime_env.py"],
                capture_output=True,
                text=True,
                check=False,
            )
            if gen.returncode != 0:
                logging.error(
                    "Runtime env generation failed before sync_to_github: "
                    f"{(gen.stderr or gen.stdout or '').strip()}"
                )
                return
            logging.info((gen.stdout or "Runtime env generated.").strip())
        except Exception as env_exc:
            logging.error(f"Runtime env generation exception before sync_to_github: {env_exc}")
            return

        heartbeat_path = os.path.join("docs", "heartbeat.json")
        nr = next_scheduled
        if nr is None:
            try:
                nr = schedule.next_run()
            except Exception:
                nr = None
        if nr is None:
            nr = NEXT_SCHEDULED_RUN
        if nr is None and os.environ.get("GITHUB_ACTIONS") == "true":
            nr = _next_github_actions_scrape_utc()
        # Normalise both timestamps to Z-suffix so the browser client never sees naive strings.
        next_run_str = nr.isoformat().replace("+00:00", "Z") if nr else None

        with open(heartbeat_path, "w", encoding="utf-8") as f:
            json.dump({
                "last_heartbeat": (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                ),
                "next_run": next_run_str,
                "status": "active"
            }, f)

        logging.info("Syncing data to GitHub...")
        pull_r = _run_git(["git", "pull", "--rebase", "origin", "main"])
        if pull_r.returncode != 0:
            logging.error(
                f"git pull --rebase failed (exit {pull_r.returncode}): "
                f"{(pull_r.stderr or pull_r.stdout or '').strip()}"
            )
            return

        # Rotate the rolling snapshot chain before writing the new prev.
        # Chain: data.prev.json → data.prev-1.json → data.prev-2.json → data.prev-3.json
        # The dashboard uses data.prev.json as the primary fallback; the deeper
        # slots are for manual recovery and bulk-diff guard comparisons.
        data_path = os.path.join("docs", "data.json")
        prev_path = os.path.join("docs", "data.prev.json")
        if os.path.exists(data_path):
            try:
                import shutil
                for slot in (3, 2, 1):
                    src = os.path.join("docs", f"data.prev-{slot - 1}.json") if slot > 1 else prev_path
                    dst = os.path.join("docs", f"data.prev-{slot}.json")
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
                shutil.copy2(data_path, prev_path)
                logging.info("Rotated snapshot chain: data.json → data.prev.json (prev-1..3 shifted).")
            except Exception as snap_exc:
                logging.warning(f"data.prev snapshot rotation failed (non-fatal): {snap_exc}")

        add_r = _run_git(["git", "add", "docs/"])
        if add_r.returncode != 0:
            logging.error(f"git add docs/ failed (exit {add_r.returncode}): {add_r.stderr.strip()}")
            return

        # Exit 0 = no staged diff; 1 = staged changes exist
        diff_r = _run_git(["git", "diff", "--cached", "--quiet"])
        if diff_r.returncode == 0:
            logging.info("GitHub sync: nothing to commit under docs/ (working tree matches HEAD).")
            return

        subprocess.run(
            ["git", "commit", "-m", "Auto-update dashboard data [skip ci]"],
            check=True,
            capture_output=True,
            text=True,
        )

        push_r = _run_git(["git", "push", "origin", "main"])
        if push_r.returncode != 0:
            logging.warning(
                f"git push failed (exit {push_r.returncode}), retrying after pull — "
                f"{(push_r.stderr or push_r.stdout or '').strip()}"
            )
            pull2 = _run_git(["git", "pull", "--rebase", "origin", "main"])
            if pull2.returncode != 0:
                logging.error(f"git pull retry failed: {(pull2.stderr or pull2.stdout or '').strip()}")
                return
            subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True, text=True)

        logging.info("Successfully pushed updated data & heartbeat to GitHub.")
    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or ""
        if isinstance(err, bytes):
            err = err.decode()
        logging.error(f"GitHub sync failed: {err}")
    except Exception as e:
        logging.error(f"Error during GitHub sync: {e}")
    finally:
        _release_git_push_lock(lock_fd)

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
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(raw if isinstance(raw, dict) else data, f, indent=2)

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


def _build_run_summary(raw_results, now_dt=None):
    """Build a concise Telegram summary for a completed scrape run."""
    now_dt = now_dt or datetime.datetime.now()
    specials_count = sum(
        1 for s in raw_results
        if s.get('on_special') or (
            not s.get('price_unavailable')
            and (s.get('eff_price') or s.get('price', 0)) <= (s.get('target') or 0) > 0
        )
    )
    items_scraped = len([r for r in raw_results if not r.get("price_unavailable")])
    stale_count = sum(1 for r in raw_results if r.get("stale"))
    scrape_time = now_dt.strftime("%-I:%M %p")
    stale_note = f" · {stale_count} stale" if stale_count > 0 else ""
    return (
        f"🛒 *WooliesBot* updated at {scrape_time}\n"
        f"🏷️ *{specials_count}* deals · {items_scraped} items tracked{stale_note}\n"
        f"👉 [View Dashboard](https://KuschiKuschbert.github.io/wooliesbot/)"
    ).strip()


_ESSENTIAL_SUBCATS = frozenset((
    "root_veg", "leafy_greens", "cooking_veg", "salad_veg", "fruit", "alliums", "herbs",
    "beef", "chicken", "pork_deli",
    "bakery", "breakfast",
))
_DAIRY_ESSENTIAL_KEYWORDS = (
    "egg", "milk", "butter", "yoghurt", "yogurt",
    "cream cheese", "cheese block", "cheese slice",
)


def _build_weekly_shopping_reminder(raw_results, now_dt=None):
    """Build a richer Sunday-reminder Telegram message for planning the weekly shop.

    Sections:
      1. Headline deal / item counts.
      2. Cola battle — compare_group 'cola' winner + compact runners-up ($/L).
      3. Essentials on special — staples from clean grocery types / subcategories.
      4. Best deals — top 5 genuine promotions by dollar saving (any category).
    """
    now_dt = now_dt or datetime.datetime.now()
    dashboard_url = "https://KuschiKuschbert.github.io/wooliesbot/"
    store_emoji = {"woolworths": "\U0001f7e2", "coles": "\U0001f534"}

    active = [r for r in raw_results if not r.get("price_unavailable") and not r.get("stale")]

    specials_count = sum(
        1 for r in active
        if r.get('on_special') or (
            (r.get('eff_price') or r.get('price', 0)) <= (r.get('target') or 0) > 0
        )
    )
    items_scraped = len(active)

    # ── Cola battle — overall winner + Coke Classic + Coke No Sugar ──
    cola_active = [
        r for r in active
        if r.get('compare_group') == 'cola'
        and (r.get('eff_price') or 0) < 5.0   # exclude single-serve convenience bottles
    ]
    def _is_coke_classic(n): return ('coca cola' in n or 'coke' in n) and 'zero' not in n and 'no sugar' not in n
    def _is_coke_zero(n):    return 'coca cola zero' in n or 'coke no sugar' in n or 'coke zero' in n
    _cola_winner   = min(cola_active, key=lambda r: r.get('eff_price') or 9999) if cola_active else None
    _coke_classic  = [r for r in cola_active if _is_coke_classic((r.get('name') or '').lower())]
    _best_classic  = min(_coke_classic, key=lambda r: r.get('eff_price') or 9999) if _coke_classic else None
    _coke_zero     = [r for r in cola_active if _is_coke_zero((r.get('name') or '').lower())]
    _best_zero     = min(_coke_zero,    key=lambda r: r.get('eff_price') or 9999) if _coke_zero    else None

    # ── Essentials on special ────────────────────────────────────────────────
    def _is_essential(item):
        subcat = (item.get('subcategory') or '').lower()
        itype  = (item.get('type') or '').lower()
        name   = (item.get('name') or '').lower()
        if subcat == 'snacks':
            return False
        if subcat in _ESSENTIAL_SUBCATS:
            return True
        if itype == 'bakery':
            return True
        if itype == 'pantry' and subcat == 'grains_pasta':
            return True
        # Dairy staples: subcat is almost always 'other', match by name keyword.
        # "cream" / bare "cheese" omitted — matches ice cream and cracker biscuits.
        if itype == 'dairy' and any(kw in name for kw in _DAIRY_ESSENTIAL_KEYWORDS):
            return True
        return False

    def _saving(item):
        wp = item.get('was_price') or 0
        ep = item.get('eff_price') or item.get('price') or wp
        return wp - ep

    essential_specials = sorted(
        [r for r in active
         if r.get('on_special') and r.get('was_price')
         and r.get('compare_group') != 'cola'
         and _is_essential(r)],
        key=_saving, reverse=True,
    )
    top_essentials = essential_specials[:5]

    # ── Best deals (any category) ────────────────────────────────────────────
    essential_names = {i.get('name') for i in top_essentials}
    all_promos = sorted(
        [r for r in active if r.get('on_special') and r.get('was_price')],
        key=_saving, reverse=True,
    )
    top_deals = [r for r in all_promos if r.get('name') not in essential_names][:5]

    # ── Assemble message ─────────────────────────────────────────────────────
    lines = [
        "\U0001f6d2 *Time to plan your shop!*",
        f"Fresh prices are in \u2014 {specials_count} deals across {items_scraped} tracked items\\.",
        "",
    ]

    if _cola_winner:
        lines.append("🧃 *Cola \$/L — best pack:*")
        _cola_rows = []
        _winner_name = _cola_winner.get('name', '')
        # Always show overall winner first.
        _cola_rows.append((_cola_winner, True))
        # Add Coke Classic and Coke No Sugar rows only if not already the winner.
        if _best_classic and _best_classic.get('name') != _winner_name:
            _cola_rows.append((_best_classic, False))
        if _best_zero and _best_zero.get('name') != _winner_name:
            _cola_rows.append((_best_zero, False))
        for _cr_item, _cr_is_winner in _cola_rows:
            _ep    = _cr_item.get('eff_price') or _cr_item.get('price') or 0
            _emoji = store_emoji.get((_cr_item.get('store') or '').lower(), '🩊')
            _crown = ' 🏆' if _cr_is_winner else ''
            _disc  = ' 🔻' if _cr_item.get('on_special') else ''
            lines.append(f'  {_emoji} {_cr_item.get("name", "?")} — \${_ep:.2f}/L{_crown}{_disc}')
        lines.append('')

    if top_essentials:
        lines.append("\U0001f9fa *Essentials on special:*")
        for item in top_essentials:
            ep    = item.get("eff_price") or item.get("price") or 0
            wp    = item.get("was_price") or 0
            emoji = store_emoji.get((item.get("store") or "").lower(), "\U0001fa4a")
            lines.append(f"  {emoji} {item.get('name', '?')} \u2014 \\${ep:.2f} _\\(-\\${wp - ep:.2f})_")
        lines.append("")

    if top_deals:
        lines.append("\U0001f525 *Best deals:*")
        for item in top_deals:
            ep    = item.get("eff_price") or item.get("price") or 0
            wp    = item.get("was_price") or 0
            emoji = store_emoji.get((item.get("store") or "").lower(), "\U0001fa4a")
            lines.append(f"  {emoji} {item.get('name', '?')} \u2014 \\${ep:.2f} _\\(-\\${wp - ep:.2f})_")
        lines.append("")

    lines.append(f"\U0001f449 [Open Dashboard]({dashboard_url})")

    return "\n".join(lines)
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

# ─── AD-HOC PRODUCT SEARCH (/find) ───────────────────────────────────────────

COLES_WARMUP_URL = "https://www.coles.com.au/product/coles-beef-rump-steak-approx.-832g-5132370"

# ── Unit price normalization ──────────────────────────────────────────────────

def _normalize_unit_price(cup_price, cup_measure):
    """Normalize unit price to a common base for cross-store comparison.
    Returns (normalized_price, base_unit_label) e.g. (42.50, "/kg").
    Weight → per-kg, Liquid → per-litre, Each → per-each."""
    if cup_price is None or cup_price <= 0:
        return (None, "")
    measure = (cup_measure or "").strip().upper().replace(" ", "")
    # Weight → per kg
    if measure in ("1KG", "KG"):
        return (cup_price, "/kg")
    if measure in ("100G", "G"):
        return (cup_price * 10, "/kg")
    # Liquid → per litre
    if measure in ("1L", "L"):
        return (cup_price, "/L")
    if measure in ("100ML", "ML"):
        return (cup_price * 10, "/L")
    # Each / unknown
    if measure in ("1EA", "EA", "EACH", ""):
        return (cup_price, "/ea")
    return (cup_price, f"/{measure.lower()}")


def _parse_woolworths_cup(cup_string):
    """Parse Woolworths CupString like '$42.50 / 1KG' into (price, measure).
    Returns (cup_price, cup_measure) or (None, '')."""
    if not cup_string:
        return (None, "")
    m = re.match(r"\$?([\d.]+)\s*/\s*(.+)", cup_string.strip())
    if m:
        try:
            return (float(m.group(1)), m.group(2).strip())
        except ValueError:
            pass
    return (None, "")


def _parse_coles_unit(pricing):
    """Parse Coles pricing dict into (cup_price, cup_measure).
    Returns (cup_price, cup_measure) or (None, '')."""
    unit_data = pricing.get("unit", {}) or {}
    up = unit_data.get("price")
    unit_type = (unit_data.get("ofMeasureUnits") or "").strip().lower()
    if up is not None and up > 0:
        return (float(up), unit_type)
    # Fallback: parse from comparable string like "$45.00/ 1kg"
    comp = pricing.get("comparable", "")
    if comp:
        m = re.match(r"\$?([\d.]+)\s*/\s*(.+)", comp.strip())
        if m:
            try:
                return (float(m.group(1)), m.group(2).strip())
            except ValueError:
                pass
    return (None, "")


def _enrich_with_unit_price(result):
    """Add norm_unit_price and norm_unit_label to a search result dict."""
    cup_price = result.get("cup_price")
    cup_measure = result.get("cup_measure", "")
    norm_price, norm_label = _normalize_unit_price(cup_price, cup_measure)
    result["norm_unit_price"] = norm_price
    result["norm_unit_label"] = norm_label
    return result


# ── curl_cffi search functions (zero-browser) ────────────────────────────────

# NOTE: _coles_cached_build_id is declared once at the top of the file — do not redeclare here.


def _cffi_get_coles_build_id(session):
    """Extract Coles Next.js buildId. Tries homepage first, then product page, then cache."""
    global _coles_cached_build_id
    # Strategy 1: Homepage (most reliable with chrome124)
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
    # Strategy 2: Use cached buildId from previous successful extraction
    if _coles_cached_build_id:
        logging.info(f"Using cached Coles buildId: {_coles_cached_build_id}")
        return _coles_cached_build_id
    # Strategy 3: Try to discover buildId via _next/static path
    try:
        resp = session.get("https://www.coles.com.au/_next/data/", timeout=10)
        # 404 response sometimes contains buildId in error message or redirect
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
        did_you_mean = sr.get("didYouMean")  # list of suggestions or None
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
        # No results at all — try suggestion
        return did_you_mean[0] if did_you_mean else None
    # Check if query terms appear in any top result name
    query_lower = query.lower()
    query_words = set(query_lower.split())
    for r in results[:3]:
        name_lower = (r.get("name", "") + " " + r.get("brand", "")).lower()
        # If any query word is a substring of the name, results are probably relevant
        if any(w in name_lower for w in query_words if len(w) >= 3):
            return None
    # No match — results look irrelevant, retry with correction
    return did_you_mean[0]


# Preferred TLS fingerprints (ordered by reliability against Akamai)
_CFFI_IMPERSONATIONS = ["chrome131", "chrome124", "chrome120", "chrome116"]


def _create_cffi_session(store_key=None):
    """Create a curl_cffi session matching the run's locked UA fingerprint."""
    profile = _get_run_ua_profile()
    imp = profile.get("impersonate", "chrome131")
    proxy = _proxy_for_store(store_key)
    kwargs = {}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        return cffi_requests.Session(impersonate=imp, **kwargs)
    except TypeError:
        return cffi_requests.Session(impersonate=imp)
    except Exception:
        pass
    for fallback_imp in _CFFI_IMPERSONATIONS:
        try:
            return cffi_requests.Session(impersonate=fallback_imp, **kwargs)
        except TypeError:
            return cffi_requests.Session(impersonate=fallback_imp)
        except Exception:
            continue
    return cffi_requests.Session(impersonate="chrome124")


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
                _scrape_run_stats["http_429"] = _scrape_run_stats.get("http_429", 0) + 1
                logging.warning(f"cffi 429 for {short_name} (attempt {attempt+1})")
            elif code >= 500:
                _scrape_run_stats["http_5xx"] = _scrape_run_stats.get("http_5xx", 0) + 1
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
    woolies_session = _create_cffi_session("woolworths")
    coles_session = _create_cffi_session("coles")

    # Warm up Woolworths (get cookies)
    try:
        resp = woolies_session.get("https://www.woolworths.com.au/", timeout=15)
        if resp.status_code != 200 or len(resp.text) < _WOOLIES_WARMUP_MIN_CHARS:
            logging.warning(f"Woolworths warm-up: HTTP {resp.status_code}, {len(resp.text)} chars — retrying")
            # Retry with different impersonation
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

    # Warm up Coles (get buildId + cookies)
    _refresh_coles_metadata(coles_session)
    coles_build_id = _coles_cached_build_id
    
    if not coles_build_id:
        # Retry with different impersonation
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
                coles_build_id = _coles_cached_build_id
                if coles_build_id:
                    logging.info(f"Coles warm-up OK with {imp}")
                    break
            except Exception:
                continue
        if not coles_build_id:
            logging.warning("Coles warm-up failed with all impersonations")
            coles_session = None

    return woolies_session, coles_session, coles_build_id


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
