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
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util as _ilu
import pathlib as _pl

from logging.handlers import RotatingFileHandler

# undetected_chromedriver is ESSENTIAL for Woolworths/Coles to bypass "Access Denied" screens
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException as SeleniumTimeout
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# curl_cffi for zero-browser search — impersonates Chrome TLS fingerprint to bypass Akamai
from curl_cffi import requests as cffi_requests

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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "8368391759:AAHsHDDhofVl4WQQIWpHsNNPQnzvS80jOmU"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1003888115204"

# --- LOGGING (rotating to prevent disk fill) ---
_log_format = '%(asctime)s - %(levelname)s - %(message)s'
_log_handlers = [
    RotatingFileHandler("chef_os.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"),
    logging.StreamHandler(sys.stdout)
]
for h in _log_handlers:
    h.setFormatter(logging.Formatter(_log_format))
logging.basicConfig(level=logging.DEBUG, handlers=_log_handlers)
if not os.environ.get("TELEGRAM_TOKEN"):
    logging.warning("TELEGRAM_TOKEN not set in env. Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID for security.")

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
try:
    with open(_inv_file, "r") as _f:
        TRACKING_LIST = json.load(_f)
except Exception as e:
    logging.warning(f"Failed to load docs/data.json: {e}")
    TRACKING_LIST = []

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
            if data.get('@type') != 'Product':
                continue
            offers = data.get('offers', {})
            price = offers.get('price')
            if not price or float(price) == 0:
                continue
            spec = offers.get('priceSpecification', {})
            unit_price_val = spec.get('price')
            unit_text = (spec.get('unitText') or '').lower()
            # Determine unit from unitText
            unit = 'each'
            if 'kg' in unit_text and '100g' not in unit_text:
                unit = 'kg'
            elif '100g' in unit_text:
                unit = '100g'
            elif 'ml' in unit_text or 'litre' in unit_text:
                unit = 'litre'
            up = float(unit_price_val) if unit_price_val and float(unit_price_val) > 0 else None
            return {
                "price": float(price),
                "unit_price": up,
                "unit": unit,
                "name_check": data.get('name', ''),
                "image_url": data.get('image', ''),
            }
    except Exception as e:
        logging.debug(f"  Woolworths JSON-LD parse error: {e}")
    return None

_coles_cached_build_id = None  # single global — do NOT redeclare below
_data_write_lock = threading.Lock()  # prevents concurrent data.json writes

def _refresh_coles_metadata(session):
    """Fetch Coles homepage to extract the current Next.js buildId."""
    global _coles_cached_build_id
    try:
        logging.debug("Refreshing Coles buildId...")
        resp = session.get("https://www.coles.com.au/product/a-123456", headers=_get_coles_headers(), timeout=10)
        # Try to find buildId in __NEXT_DATA__
        match = re.search(r'"buildId":"([^"]+)"', resp.text)
        if match:
            _coles_cached_build_id = match.group(1)
            logging.info(f"Coles buildId synchronized: {_coles_cached_build_id}")
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


def _get_coles_headers():
    """Returns headers that mimic a real Chrome browser on macOS to bypass Akamai."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-AU,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
        
        resp = session.get(api_url, headers=_get_coles_headers(), timeout=10)
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
                }
    except Exception as e:
        logging.debug(f"Coles API fetch error: {e}")
    return None


def _extract_woolworths_json_from_html(html):
    """Extract product data from Woolworths JSON-LD in raw HTML.
    Returns dict with price, unit_price, unit, name_check or None."""
    try:
        # Find all script tags with type="application/ld+json"
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([^<]+)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1).strip())
                if data.get('@type') != 'Product':
                    continue
                offers = data.get('offers', {})
                price = offers.get('price')
                if not price or float(price) == 0:
                    continue
                spec = offers.get('priceSpecification', {}) or {}
                unit_price_val = spec.get('price')
                unit_text = (spec.get('unitText') or '').lower()
                unit = 'each'
                if 'kg' in unit_text and '100g' not in unit_text:
                    unit = 'kg'
                elif '100g' in unit_text:
                    unit = '100g'
                elif 'ml' in unit_text or 'litre' in unit_text:
                    unit = 'litre'
                up = float(unit_price_val) if unit_price_val and float(unit_price_val) > 0 else None
                return {
                    "price": float(price),
                    "unit_price": up,
                    "unit": unit,
                    "name_check": data.get('name', ''),
                    "image_url": data.get('image', ''),
                }
            except json.JSONDecodeError:
                continue
    except Exception as e:
        logging.debug(f"  Woolworths JSON-LD from HTML parse error: {e}")
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

def scrape_item_from_store(driver, url, store_key):
    """Scrape price + unit price from a single store product page.
    Strategy: 1) try structured JSON  2) fall back to CSS selectors."""
    store = STORES[store_key]
    try:
        driver.get(url)
        time.sleep(random.uniform(1.5, 3.5))

        # ── Page validation: detect bot blocks / empty pages ──
        page_len = len(driver.page_source)
        if page_len < 5000:
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
        logging.debug(f"  {store_key} scrape error: {e}")
        return None

MAX_RETRIES = 2  # up to 2 attempts per item

def _scrape_store_batch(store_key, items_with_urls):
    """Scrape all items for ONE store in its own browser.
    Includes retry logic: each item gets up to MAX_RETRIES attempts.
    Returns list of (item_index, store_data)."""
    label = STORES[store_key]["label"]
    logging.info(f"[{label}] Starting browser for {len(items_with_urls)} items...")
    driver = get_browser()
    results = []
    try:
        for idx, item, url in items_with_urls:
            logging.info(f"[{label}] {item['name']}")
            data = None
            for attempt in range(MAX_RETRIES):
                data = scrape_item_from_store(driver, url, store_key)
                if data:
                    break
                if attempt < MAX_RETRIES - 1:
                    logging.info(f"[{label}]   Retry #{attempt+2} for {item['name']}...")
                    time.sleep(2 + attempt * 2)  # 2s first retry, 4s second
            if data:
                data["store"] = store_key
                results.append((idx, data))
            else:
                safe_name = item['name'].replace(' ', '_').replace('/', '_')
                try:
                    _ss_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "screenshots")
                    os.makedirs(_ss_dir, exist_ok=True)
                    driver.save_screenshot(os.path.join(_ss_dir, f"error_{safe_name}_{store_key}.png"))
                except Exception:
                    pass
                logging.warning(f"[{label}] ✗ Failed after {MAX_RETRIES} attempts: {item['name']}")
            time.sleep(0.5)
    except Exception as e:
        logging.error(f"[{label}] Browser crashed: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass
    logging.info(f"[{label}] Done. {len(results)}/{len(items_with_urls)} scraped.")
    return store_key, results


def _scrape_store_batch_cffi(store_key, items_with_urls):
    """Scrape all items for ONE store using curl_cffi (no browser).
    Uses parallel fetches. Returns (store_key, list of (idx, data))."""
    label = STORES[store_key]["label"]
    homepage = "https://www.woolworths.com.au/" if store_key == "woolworths" else "https://www.coles.com.au/"
    logging.info(f"[{label}] Starting curl_cffi scan for {len(items_with_urls)} items...")
    session = _create_cffi_session()
    try:
        resp = session.get(homepage, timeout=15)
        if resp.status_code != 200 or len(resp.text) < 5000:
            logging.warning(f"[{label}] Warm-up failed (HTTP {resp.status_code}, {len(resp.text)} chars)")
            return store_key, []
    except Exception as e:
        logging.error(f"[{label}] Warm-up error: {e}")
        return store_key, []

    _CFFI_BATCH_WORKERS = 6

    def fetch_one(args):
        idx, item, url = args
        data = _cffi_fetch_product(session, url, store_key)
        if data:
            data["store"] = store_key
            return (idx, data)
        return (idx, None)

    results = []
    with ThreadPoolExecutor(max_workers=_CFFI_BATCH_WORKERS) as pool:
        futures = [pool.submit(fetch_one, job) for job in items_with_urls]
        for future in as_completed(futures):
            try:
                idx, data = future.result()
                if data:
                    results.append((idx, data))
            except Exception as e:
                logging.debug(f"[{label}] fetch error: {e}")

    logging.info(f"[{label}] Done. {len(results)}/{len(items_with_urls)} scraped.")
    return store_key, results


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

def check_prices():
    """Scrape all items from all stores IN PARALLEL.
    Uses curl_cffi (fast). Falls back to Chrome for a store if >25% items fail."""
    # Build per-store work lists: [(item_index, item, url), ...]
    store_jobs = {}
    for idx, item in enumerate(TRACKING_LIST):
        for store_key in STORES:
            url = item.get(store_key, "")
            if url:
                store_jobs.setdefault(store_key, []).append((idx, item, url))

    total_urls = sum(len(v) for v in store_jobs.values())
    logging.info(f"Starting Price Scan... ({len(TRACKING_LIST)} items, {total_urls} URLs, curl_cffi)")

    # Try curl_cffi first (no browser)
    all_store_results = {}  # store_key -> [(idx, data), ...]
    with ThreadPoolExecutor(max_workers=len(store_jobs)) as pool:
        futures = {
            pool.submit(_scrape_store_batch_cffi, sk, jobs): sk
            for sk, jobs in store_jobs.items()
        }
        for future in as_completed(futures):
            try:
                store_key, batch_results = future.result()
                all_store_results[store_key] = batch_results
            except Exception as e:
                sk = futures[future]
                logging.error(f"Store {sk} cffi scrape failed entirely: {e}")
                all_store_results[sk] = []

    # Fallback: if a store got <75% success, retry with Chrome
    for store_key, jobs in store_jobs.items():
        batch = all_store_results.get(store_key, [])
        if len(jobs) == 0:
            continue
        success_rate = len(batch) / len(jobs)
        if success_rate < 0.75:
            label = STORES[store_key]["label"]
            logging.warning(f"[{label}] cffi success {len(batch)}/{len(jobs)} ({success_rate:.0%}) — falling back to Chrome")
            try:
                _, chrome_results = _scrape_store_batch(store_key, jobs)
                all_store_results[store_key] = chrome_results
            except Exception as e:
                logging.error(f"[{label}] Chrome fallback failed: {e}")

    # Merge results: group by item index, pick cheapest store
    item_store_data = {}  # idx -> [store_data, ...]
    for store_key, batch in all_store_results.items():
        for idx, data in batch:
            item_store_data.setdefault(idx, []).append(data)

    results = []
    # Load inventory to get price_history
    inv_data = {}
    try:
        with open(_inv_file, "r") as f:
            for item in json.load(f):
                inv_data[item["name"]] = item
    except:
        pass

    for idx, item in enumerate(TRACKING_LIST):
        store_results = item_store_data.get(idx, [])
        # Merge price history for averaging
        history = inv_data.get(item["name"], {}).get("price_history", [])
        avg_price = sum(h["price"] for h in history) / len(history) if history else 0

        if not store_results:
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
                "avg_price": avg_price
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
                "avg_price": avg_price
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
            "was_price": best.get("was_price"),
            "on_special": best.get("on_special", False),
            "price_history": history,
            "avg_price": avg_price
        }

        # Handle local image download
        remote_img = best.get("image_url")
        if remote_img:
            local_img_path = _download_product_image(remote_img, item["name"])
            if local_img_path:
                item_result["image_url"] = local_img_path

        results.append(item_result)

    # Scraper Health Check
    unavail_count = sum(1 for r in results if r.get("price_unavailable"))
    if unavail_count > (len(TRACKING_LIST) * 0.25):
        health_msg = f"⚠️ *SCRAPER HEALTH ALERT*\n{unavail_count}/{len(TRACKING_LIST)} items have no reliable price. "
        health_msg += "Site layouts may have changed."
        send_telegram(health_msg)

    logging.info(f"Price Scan complete. {len(results)}/{len(TRACKING_LIST)} items with prices.")
    return results

def _nl(): return "\n"
def _sp(): return "\n\n"  # Section spacing for smartphone


def _make_table(headers, rows, col_align=None):
    """Build monospace table for Telegram. Uses code block for alignment.
    headers, rows: lists of cell values. col_align: '<' left, '>' right per column."""
    if not rows:
        return ""
    n = len(headers)
    col_align = col_align or ["<"] * n

    def safe(c):
        return str(c).replace("`", "'")[:30]

    def width(col_idx):
        items = [safe(h) for h in headers] + [safe(r[col_idx]) for r in rows if col_idx < len(r)]
        return max(len(x) for x in items) if items else 0

    widths = [width(i) for i in range(n)]

    def row(cells):
        parts = []
        for i, c in enumerate(cells):
            w = widths[i] if i < len(widths) else 10
            a = col_align[i] if i < len(col_align) else "<"
            parts.append(f"{safe(c):{a}{w}}")
        return " │ ".join(parts)

    lines = [row(headers), "─" * (sum(widths) + 3 * (n - 1))]
    for r in rows:
        padded = list(r) + [""] * (n - len(r))
        lines.append(row(padded))
    return "```\n" + "\n".join(lines) + "\n```"

# Weekly Essentials checklist (always buy, regardless of specials)
WEEKLY_ESSENTIALS = [
    "Capsicum, Onions, Spinach",
    "Eggs, Cream, Cheese",
    "Avocado, Zucchini",
]

def _resolve_compare_groups(results):
    """For items sharing a compare_group, keep only the cheapest (by eff_price)
    across all stores and products. Winner appears in normal list (no separate block)."""
    groups = {}
    ungrouped = []
    for item in results:
        grp = item.get("compare_group")
        if grp:
            groups.setdefault(grp, []).append(item)
        else:
            ungrouped.append(item)

    winners = []
    for grp, members in groups.items():
        options = []
        for m in members:
            for sk, sd in m.get("all_stores", {}).items():
                if sd["eff_price"] >= _PRICE_UNRELIABLE:
                    continue
                options.append((m, sk, sd["eff_price"]))
        if not options:
            winners.extend(members)
            continue
        # Sort by eff_price, then -pack_litres, then Woolies, then Coke over Pepsi, then name
        options.sort(key=lambda x: (
            x[2],
            -(x[0].get("pack_litres") or 0),
            0 if x[1] == "woolworths" else 1,
            0 if "coke" in x[0]["name"].lower() else 1,
            x[0]["name"],
        ))
        best_item, best_store, best_price = options[0]
        winner = {**best_item, "store": best_store, "eff_price": best_price}
        sd = best_item["all_stores"][best_store]
        winner["price"] = sd["price"]
        winner["unit_price"] = sd.get("unit_price")
        winners.append(winner)

    return ungrouped + winners, []

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


def _format_special_compact(item, qty, type_emoji, cost):
    """One-line format: 4x Name emoji $X/kg store $cost"""
    name = _escape_md(item['name'])
    badge = _store_badge(item.get('store', 'woolworths'))
    price_str = _price_display(item)
    line = f"• {qty}x {name} {type_emoji} {price_str} ✓{badge}"
    if qty > 1:
        line += f" ${cost:.2f}"
    return line


def _format_compact_compare(item, extra=""):
    """Format compact W/C comparison: 'W $X  C $Y  → W 🟢'. extra appended (e.g. '  $74.80' for cost)."""
    woolies_p, coles_p = _item_store_prices(item)
    store = item.get("store", "woolworths")
    best = "W" if store == "woolworths" else "C"
    emoji = STORES.get(store, {}).get("emoji", "")
    line = f"W {woolies_p}  C {coles_p}  → {best} {emoji}"
    if extra:
        line += extra
    return line


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
        now_str = now.strftime("%Y-%m-%d %I:%M %p")
        today_str = now.strftime("%Y-%m-%d")

        # Load existing data to preserve scrape_history and other accumulated fields
        existing_by_name = {}
        if os.path.exists(data_path):
            try:
                with open(data_path, "r") as f:
                    raw = json.load(f)
                existing_items = raw if isinstance(raw, list) else raw.get("items", [])
                existing_by_name = {item["name"]: item for item in existing_items}
            except Exception:
                existing_by_name = {}

        # Merge: overlay fresh results onto existing items, preserving history fields
        merged = []
        for item in results:
            name = item["name"]
            existing = existing_by_name.get(name, {})

            # Carry over accumulated fields that the scraper doesn't produce
            for keep_field in ("scrape_history", "price_history", "brand", "subcategory",
                               "size", "tags", "target_confidence", "target_method",
                               "target_data_points", "target_updated", "last_purchased",
                               "local_image", "on_special", "was_price",
                               "type", "all_stores", "coles"):
                if keep_field in existing and keep_field not in item:
                    item[keep_field] = existing[keep_field]

            # Append today's scrape snapshot to scrape_history
            sh = item.get("scrape_history", [])
            if not sh or sh[-1].get("date") != today_str:
                sh.append({
                    "date": today_str,
                    "price": item.get("eff_price", item.get("price", 0)),
                    "is_special": item.get("on_special", False),
                    "was_price": item.get("was_price"),
                    "store": item.get("store"),
                })
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

def sync_to_github():
    """Commits and pushes the docs/ folder and updated JSON data to GitHub."""
    import subprocess
    try:
        # Update heartbeat file before syncing.
        # NEXT_SCHEDULED_RUN is updated by the main loop AFTER schedule.run_pending();
        # by the time sync_to_github() is called (from a daemon thread), the main loop
        # has already ticked and NEXT_SCHEDULED_RUN reflects the upcoming run.
        heartbeat_path = os.path.join("docs", "heartbeat.json")
        next_run_str = NEXT_SCHEDULED_RUN.isoformat() if NEXT_SCHEDULED_RUN else None
        with open(heartbeat_path, "w") as f:
            json.dump({
                "last_heartbeat": datetime.datetime.now().isoformat(),
                "next_run": next_run_str,
                "status": "active"
            }, f)

        logging.info("Syncing data to GitHub...")
        # Stage all docs/ changes (shell globs don't expand in subprocess.run without shell=True)
        subprocess.run(["git", "add", "docs/"], check=False, capture_output=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain", "docs/"], capture_output=True, text=True)
        if status.stdout.strip():
            subprocess.run(["git", "commit", "-m", "Auto-update dashboard data [skip ci]"], check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True)
            logging.info("Successfully pushed updated data & heartbeat to GitHub.")
        else:
            logging.info("No data changes to commit (heartbeat only).")
    except subprocess.CalledProcessError as e:
        logging.error(f"GitHub sync failed: {e.stderr.decode()}")
    except Exception as e:
        logging.error(f"Error during GitHub sync: {e}")

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
        # Use a real User-Agent to avoid blocks
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
    """Search Coles for items that don't have a Coles URL yet.
    Processes a small batch per cycle to avoid rate limiting.
    Over ~17 cycles (~3 days at 4h intervals) covers all items."""
    import re as _re
    try:
        from curl_cffi import requests as _cffi_requests
    except ImportError:
        logging.debug("curl_cffi not available for Coles discovery")
        return

    data_path = "docs/data.json"
    with open(data_path, "r") as f:
        raw = json.load(f)
    data = raw.get("items", raw) if isinstance(raw, dict) else raw

    no_coles = [i for i in data if not i.get("coles")]
    if not no_coles:
        logging.info("[Coles] All items already have Coles URLs.")
        return

    batch = no_coles[:batch_size]
    logging.info(f"[Coles] Discovering prices for {len(batch)}/{len(no_coles)} items without Coles URLs...")

    session = _cffi_requests.Session(impersonate="safari15_5")
    try:
        session.get("https://www.coles.com.au/", timeout=15)
    except Exception:
        logging.warning("[Coles] Could not reach coles.com.au")
        return

    matched = 0
    cheaper = 0

    for item in batch:
        name = item.get("name", "")
        # Simplify name for search
        search = _re.sub(r'\d+\.?\d*\s*(G|Kg|Ml|L|Pk|Pack)\b', '', name, flags=_re.IGNORECASE)
        search = search.replace("Ww ", "").replace("P/P", "").strip()
        words = [w for w in search.split() if len(w) > 1][:3]
        search = " ".join(words)
        if len(search) < 3:
            continue

        try:
            resp = session.get(
                f"https://www.coles.com.au/search?q={search}",
                timeout=15,
            )
            if "Pardon Our Interruption" in resp.text:
                logging.warning("[Coles] Rate limited — stopping discovery for this cycle.")
                break

            m = _re.search(
                r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>',
                resp.text, _re.I | _re.DOTALL,
            )
            if not m:
                continue

            nd = json.loads(m.group(1).strip())
            results = (nd.get("props", {}).get("pageProps", {})
                        .get("searchResults", {}).get("results", []))
            if not results:
                continue

            r = results[0]
            pricing = r.get("pricing", {})
            price = pricing.get("now")
            if not price or float(price) <= 0:
                continue

            coles_name = r.get("name", "")
            prod_id = r.get("id", "")
            slug = _re.sub(r"[^a-z0-9]+", "-", coles_name.lower()).strip("-")
            cp = float(price)
            cw = pricing.get("was")
            cs = pricing.get("promotionType") == "SPECIAL"

            all_stores = item.get("all_stores", {})
            all_stores["coles"] = {
                "price": cp, "eff_price": cp,
                "was_price": float(cw) if cw else None,
                "on_special": cs,
            }
            item["all_stores"] = all_stores
            item["coles"] = f"https://www.coles.com.au/product/{slug}-{prod_id}"

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
        except Exception:
            pass

        time.sleep(3)

    # Save back — use the write lock to avoid racing with export_data_to_json()
    if matched > 0:
        with _data_write_lock:
            if isinstance(raw, dict):
                raw["items"] = data
            with open(data_path, "w") as f:
                json.dump(raw if isinstance(raw, dict) else data, f, indent=2)

    total = sum(1 for i in data if i.get("coles"))
    logging.info(f"[Coles] Discovered {matched} matches ({cheaper} cheaper). Total with Coles: {total}/{len(data)}")


def run_report(full_list=False, send_telegram_messages=True):
    """Generate and send shopping report.
    full_list: /show_staples - full staples list with all prices. False = deals only.
    send_telegram_messages: whether to output the full report to telegram.
    Big Shop (bulk qty) auto-detected from date (after 14th).
    """
    try:
        today = datetime.datetime.now()
        weekday = today.weekday()  # Mon=0, Sun=6
        is_big_shop = today.day > BIG_SHOP_START_DAY

        raw_results = check_prices()

        # Update JSON file for web dashboard (single source of truth)
        export_data_to_json(raw_results)

        # Re-run smart target engine so targets improve with each scrape cycle
        try:
            _st_path = _pl.Path(__file__).parent / "scripts" / "smart_targets.py"
            _spec = _ilu.spec_from_file_location("smart_targets", _st_path)
            _st = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_st)
            _st.recalculate_targets(dry_run=False)
            logging.info("Smart target recalculation complete.")
        except Exception as _e:
            logging.warning(f"Smart target recalculation skipped: {_e}")
        
        # Gradual Coles price discovery (~20 items per cycle)
        try:
            _discover_coles_prices(batch_size=20)
        except Exception as _e:
            logging.warning(f"Coles discovery skipped: {_e}")

        # Deploy to GitHub pages
        threading.Thread(target=sync_to_github, daemon=True).start()

        if not send_telegram_messages:
            logging.info("send_telegram_messages is False. Exiting early, no message sent.")
            return

        # Count store-confirmed specials for the notification
        specials_count = sum(
            1 for s in raw_results
            if s.get('on_special') or (
                not s.get('price_unavailable')
                and (s.get('eff_price') or s.get('price', 0)) <= (s.get('target') or 0) > 0
            )
        )

        # Minimal Telegram notification — all details are on the dashboard
        summary = (
            f"🛒 *WooliesBot* — prices updated\\.\n"
            f"🏷️ *{specials_count}* deals on right now\\.\n"
            f"🌐 [Open Dashboard](https://KuschiKuschbert\\.github\\.io/wooliesbot/)"
        )

        if send_telegram_messages:
            send_telegram(summary.strip())

    except Exception as e:
        error_trace = traceback.format_exc()
        logging.error(f"Error in run_report: {e}\n{error_trace}")
        send_telegram(f"🚨 *REPORT ERROR*:\n{_escape_md(str(e))}")

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
            if resp.status_code == 200 and len(resp.text) > 5000:
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
        for group in data.get("Products", [])[:max_results]:
            prod = (group.get("Products") or [{}])[0]
            if not prod.get("Name") or prod.get("Price") is None:
                continue
            cup_price, cup_measure = _parse_woolworths_cup(prod.get("CupString", ""))
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
        resp = session.get(
            f"https://www.coles.com.au/_next/data/{build_id}/search/products.json?q={encoded}",
            timeout=15,
        )
        if resp.status_code == 404:
            # buildId likely expired — try to re-extract
            logging.warning(f"Coles search 404 — buildId may have expired, clearing cache")
            _coles_cached_build_id = None
            return [], None
        if resp.status_code != 200:
            logging.warning(f"Coles search API HTTP {resp.status_code}")
            return [], None
        data = resp.json()
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
            result = {
                "name": r.get("name", ""),
                "brand": r.get("brand", ""),
                "price": float(now_price),
                "was_price": pricing.get("was"),
                "cup_price": cup_price,
                "cup_measure": cup_measure,
                "cup_string": pricing.get("comparable", ""),
                "size": r.get("size", ""),
                "on_special": pricing.get("promotionType") is not None,
                "store": "coles",
            }
            _enrich_with_unit_price(result)
            results.append(result)
        logging.info(f"Coles search '{query}': {len(results)} results (didYouMean={did_you_mean})")
        return results, did_you_mean
    except Exception as e:
        logging.error(f"Coles search error: {e}")
        return [], None


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
_CFFI_IMPERSONATIONS = ["chrome124", "chrome120", "chrome116"]


def _create_cffi_session():
    """Create a curl_cffi session with the best available impersonation."""
    for imp in _CFFI_IMPERSONATIONS:
        try:
            return cffi_requests.Session(impersonate=imp)
        except Exception:
            continue
    return cffi_requests.Session(impersonate="chrome124")


def _cffi_fetch_product(session, url, store_key):
    """Fetch product page HTML and extract price JSON. Returns dict or None."""
    try:
        # For Coles, try the specific NEXT.js API first (much faster/cleaner)
        if store_key == "coles":
            # We need a buildId. If we don't have one, we can still try standard HTML fetch.
            global _coles_cached_build_id
            if _coles_cached_build_id:
                data = _cffi_fetch_coles_api(session, url, _coles_cached_build_id)
                if data: return data

        resp = session.get(url, headers=_get_coles_headers() if store_key == "coles" else {}, timeout=15)
        if resp.status_code != 200 or len(resp.text) < 5000:
            return None
        if store_key == "woolworths":
            data = _extract_woolworths_json_from_html(resp.text)
        else:
            data = _extract_coles_json_from_html(resp.text)
            
        if data and data.get("price", 0) > 0:
            return {
                "price": data["price"],
                "unit_price": data.get("unit_price"),
                "unit": data.get("unit", "each"),
                "image_url": data.get("image_url", ""),
                "was_price": data.get("was_price"),
                "on_special": bool(data.get("is_special") or data.get("was_price")),
            }
    except Exception as e:
        logging.debug(f"cffi fetch error {url[:50]}: {e}")
    return None


def _init_search_sessions():
    """Create curl_cffi sessions for both stores and warm them up.
    Returns (woolies_session, coles_session, coles_build_id)."""
    woolies_session = _create_cffi_session()
    coles_session = _create_cffi_session()

    # Warm up Woolworths (get cookies)
    try:
        resp = woolies_session.get("https://www.woolworths.com.au/", timeout=15)
        if resp.status_code != 200 or len(resp.text) < 5000:
            logging.warning(f"Woolworths warm-up: HTTP {resp.status_code}, {len(resp.text)} chars — retrying")
            # Retry with different impersonation
            for imp in _CFFI_IMPERSONATIONS[1:]:
                try:
                    woolies_session = cffi_requests.Session(impersonate=imp)
                    resp = woolies_session.get("https://www.woolworths.com.au/", timeout=15)
                    if len(resp.text) > 5000:
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

def _scheduled_report():
    """Wrapper for schedule to catch errors without killing the scheduler."""
    try:
        run_report()
    except Exception as e:
        logging.error(f"Scheduled report failed: {e}\n{traceback.format_exc()}")
        send_telegram(f"🚨 Scheduled report failed:\n{_escape_md(str(e))}")

if __name__ == "__main__":
    while True:
        try:
            parser = argparse.ArgumentParser(description="WoolesBot - Woolworths and Coles price tracker")
            parser.add_argument("--now", action="store_true", help="Run the report immediately")
            args = parser.parse_args()

            if args.now:
                logging.info("Manual trigger received. Running report now...")
                run_report()
                sys.exit(0)

            # Start Telegram Listener in a background thread
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

            # Update website frequently (every 4 hours) without telegram messages
            schedule.every(4).hours.do(_silent_update)

            # Trigger telegram ping ONLY on Sunday morning with a link to the website
            schedule.every().sunday.at("09:00").do(_sunday_ping)
            
            # Startup notification — welcoming intro + full command reference
            send_telegram("⚙️ *WooliesBot Internal Supervisor active.* System is now monitoring prices and listening for commands.")
            logging.info("WoolesBot is active. Listening for Sunday 9am and /shop command...")
            
            while True:
                # Keep the global next run time synchronized
                next_job = schedule.next_run()
                if next_job:
                    NEXT_SCHEDULED_RUN = next_job
                
                schedule.run_pending()
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
