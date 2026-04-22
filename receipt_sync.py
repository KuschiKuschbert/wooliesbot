import argparse
import time
import os
import json
import logging
import datetime
import re
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import SessionNotCreatedException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

INV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")

# CSS selectors for activity-feed receipt cards on everyday.com.au
CARD_SELECTORS = [
    "[class*='activity-list'] [class*='card']",
    "[class*='transaction-card']",
    "[class*='activity-card']",
    ".activity-list-item",
    ".transaction-row",
    "[class*='transaction']",
    "[class*='activity-item']",
    "[data-testid*='activity'] [data-testid*='card']",
    "[data-testid*='transaction']",
]
ACTIVITY_URL = "https://www.everyday.com.au/index.html#/my-activity"

LOGIN_TEXT_HINTS = (
    "sign in",
    "log in",
    "login",
    "everyday rewards",
    "verify",
    "enter code",
    "otp",
    "one-time code",
)

def load_inventory():
    """Returns the items list from data.json."""
    try:
        with open(INV_FILE, "r") as f:
            raw = json.load(f)
        return raw if isinstance(raw, list) else raw.get("items", [])
    except:
        return []

def load_inventory_raw():
    """Returns the full data.json dict (preserves metadata like last_updated)."""
    try:
        with open(INV_FILE, "r") as f:
            return json.load(f)
    except:
        return {"items": []}

def save_inventory(items):
    """Writes items list back to data.json, preserving existing metadata."""
    raw = load_inventory_raw()
    raw["items"] = items
    with open(INV_FILE, "w") as f:
        json.dump(raw, f, indent=4)

# ── Fuzzy receipt-to-inventory matching ──────────────────────────────────────
# Woolworths receipt names differ from inventory names in many ways:
#   Receipt: "WW NATURAL GREEK YOGHURT 2KG"
#   Inventory: "Ww Natural Greek Style Yoghurt 2Kg"
# We use a token-overlap score (Jaccard + coverage) that's robust to this.

# Words that carry no useful signal for matching
_STOP = {
    'ww', 'woolworths', 'coles', 'pk', 'pack', 'ea', 'pp', 'fc',
    'the', 'and', 'with', 'wth', 'rspca', 'ml', 'kg', 'gm', 'lt',
    'g', 'l', 'x',
}

def _tokens(name):
    """Extract meaningful tokens from a product name."""
    raw = re.findall(r'[a-z0-9]+', name.lower())
    return [t for t in raw if t not in _STOP and len(t) > 1]

def _match_score(receipt_name, inv_name):
    """
    Returns (score 0-1, common_token_count).
    score = average of Jaccard similarity and coverage-of-shorter-name.
    Requires at least 2 tokens in common.
    """
    rt = set(_tokens(receipt_name))
    it = set(_tokens(inv_name))
    if not rt or not it:
        return 0.0, 0
    common = rt & it
    n = len(common)
    if n < 2:  # Hard minimum: must share at least 2 meaningful tokens
        return 0.0, n
    jaccard  = n / len(rt | it)
    coverage = n / min(len(rt), len(it))
    return (jaccard + coverage) / 2.0, n

def find_best_inv_match(receipt_name, inventory, threshold=0.45):
    """
    Find the best-matching inventory item for a receipt product name.
    Returns (item, score) or (None, 0) if no match above threshold.
    Picks the highest-scoring item; on a tie, takes the one with more
    price_history entries (most-tracked = most likely to be the right one).
    """
    best_score, best_n, best_item = 0.0, 0, None
    for item in inventory:
        score, n = _match_score(receipt_name, item['name'])
        if score > best_score or (score == best_score and n > best_n):
            best_score, best_n, best_item = score, n, item
    if best_score >= threshold:
        return best_item, best_score
    return None, 0.0

# Keep old normalize_name for any legacy callers
def normalize_name(name):
    return name.lower().replace(' ', '').replace('-', '')

import datetime

def parse_date(date_str):
    try:
        # Expected format from receipt text: "12/04/2026 14:30"
        return datetime.datetime.strptime(date_str.split(' ')[0], "%d/%m/%p").replace(year=2026) # Heuristic for current year
    except:
        # Fallback if year isn't present or format differs
        try:
            return datetime.datetime.strptime(date_str.split(' ')[0], "%d/%m/%Y")
        except:
            return None


def _find_receipt_cards(driver):
    """Find receipt/activity cards across multiple known selector patterns."""
    for selector in CARD_SELECTORS:
        cards = driver.find_elements(By.CSS_SELECTOR, selector)
        if cards:
            return cards
    return []


def _clear_profile_singleton_locks(user_data_dir):
    """Remove stale Chrome singleton locks if a prior run crashed."""
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(user_data_dir, lock_name)
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
                logging.info(f"Removed stale profile lock file: {lock_name}")
        except Exception as exc:
            logging.debug(f"Could not remove {lock_name}: {exc}")


def _build_driver(user_data_dir, headless=False):
    """Build a Chrome driver with a persistent profile and optional headless mode."""
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if headless:
        options.add_argument("--headless=new")

    logging.info(
        "Starting Chrome with persistent profile%s...",
        " (headless)" if headless else "",
    )

    try:
        return uc.Chrome(options=options)
    except SessionNotCreatedException:
        # Common on stale profile locks from interrupted runs.
        _clear_profile_singleton_locks(user_data_dir)
        return uc.Chrome(options=options)


def _is_auth_prompt_visible(driver):
    """Best-effort check for login/MFA prompts."""
    lower_url = (driver.current_url or "").lower()
    if any(hint in lower_url for hint in ("login", "signin", "auth", "verify")):
        return True

    page_text = (driver.page_source or "").lower()
    return any(hint in page_text for hint in LOGIN_TEXT_HINTS)


def _wait_for_activity_feed(driver, login_timeout=180, poll_interval=5, headless=False):
    """Wait until activity cards are visible, prompting user to complete auth if needed."""
    elapsed = 0
    login_prompt_shown = False
    while elapsed < login_timeout:
        cards = _find_receipt_cards(driver)
        if cards:
            return cards

        if _is_auth_prompt_visible(driver):
            if not login_prompt_shown:
                if headless:
                    logging.info("⏳ Login/MFA required, but this run is headless.")
                    logging.info("   Re-auth once in non-headless mode for this profile, then retry headless.")
                else:
                    logging.info("⏳ Login/MFA required. Please complete Everyday Rewards auth in the Chrome window...")
                if login_timeout < 60:
                    logging.info(f"   Waiting up to {login_timeout} seconds for authentication...")
                else:
                    logging.info(f"   Waiting up to {login_timeout // 60} minutes for authentication...")
                login_prompt_shown = True
        elif ACTIVITY_URL not in (driver.current_url or ""):
            # Some redirects bounce to home/account pages after successful auth.
            try:
                driver.get(ACTIVITY_URL)
            except Exception:
                pass

        time.sleep(poll_interval)
        elapsed += poll_interval

    return []


def _write_debug_artifacts(driver):
    """Persist page diagnostics when selectors fail."""
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    html_path = os.path.join(logs_dir, f"receipt_sync_debug_{stamp}.html")
    png_path = os.path.join(logs_dir, f"receipt_sync_debug_{stamp}.png")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source or "")
        driver.save_screenshot(png_path)
        logging.info(f"Saved debug HTML: {html_path}")
        logging.info(f"Saved debug screenshot: {png_path}")
    except Exception as exc:
        logging.warning(f"Could not write debug artifacts: {exc}")


def run_sync(
    all_receipts=True,
    months_back=6,
    headless=False,
    login_timeout=180,
    poll_interval=5,
    profile_dir=None,
):
    inventory = load_inventory()
    
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=months_back * 30)
    logging.info(f"Syncing receipts back to: {cutoff_date.strftime('%Y-%m-%d')}")

    user_data_dir = profile_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
    os.makedirs(user_data_dir, exist_ok=True)
    driver = _build_driver(user_data_dir=user_data_dir, headless=headless)
    
    try:
        driver.get(ACTIVITY_URL)
        logging.info("Waiting for page load...")
        time.sleep(8)

        logging.info("Scrolling activity feed to load all receipts up to cutoff date...")
        
        def _scroll_and_load_cards(driver, cutoff_date, card_selector, max_no_new=5):
            """Scroll the activity feed page to trigger lazy-loading of older receipts.
            Returns list of all card elements visible after full scroll.
            Stops when: cutoff date found in visible cards, OR no new cards after max_no_new attempts."""
            scroll_pause = 2.0
            no_new_streak = 0
            last_count = 0
            page_body = driver.find_element(By.TAG_NAME, "body")

            while True:
                # Scroll to the bottom of the page
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(scroll_pause)

                # Also try scrolling any scrollable container (activity list may be in a div)
                try:
                    for sel in ["[class*='activity']", "[class*='transactions']", "[class*='list']", "main"]:
                        containers = driver.find_elements(By.CSS_SELECTOR, sel)
                        for c in containers:
                            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", c)
                except Exception:
                    pass

                time.sleep(scroll_pause)

                # Fetch current cards
                cards = driver.find_elements(By.CSS_SELECTOR, card_selector)
                if not cards:
                    for alt in [".transaction-row", "[class*='transaction']", "[class*='activity-item']"]:
                        cards = driver.find_elements(By.CSS_SELECTOR, alt)
                        if cards:
                            break

                current_count = len(cards)
                logging.info(f"  Scroll pass: {current_count} cards loaded (was {last_count})")

                # Check if any card already exceeds the cutoff date — if the oldest visible
                # card is beyond the cutoff we can stop scrolling
                cutoff_reached = False
                for card in reversed(cards):  # Check from the bottom (oldest)
                    date_match = re.search(r'(\w+ \d{1,2} \w{3})', card.text)
                    if date_match:
                        raw = date_match.group(1)
                        try:
                            now = datetime.datetime.now()
                            card_date = datetime.datetime.strptime(raw, "%a %d %b")
                            # Determine correct year for this card
                            for year_offset in [0, -1, -2]:
                                candidate = card_date.replace(year=now.year + year_offset)
                                if candidate <= now:
                                    card_date = candidate
                                    break
                            if card_date < cutoff_date:
                                cutoff_reached = True
                                break
                        except Exception:
                            pass
                    
                if cutoff_reached:
                    logging.info(f"  Cutoff date {cutoff_date.strftime('%Y-%m-%d')} found in feed — stopping scroll.")
                    break

                if current_count == last_count:
                    no_new_streak += 1
                    if no_new_streak >= max_no_new:
                        logging.info(f"  No new cards after {max_no_new} scrolls — reached end of feed.")
                        break
                else:
                    no_new_streak = 0

                last_count = current_count

            return cards

        initial_cards = _wait_for_activity_feed(
            driver,
            login_timeout=login_timeout,
            poll_interval=poll_interval,
            headless=headless,
        )
        if initial_cards:
            logging.info(f"Auth/feed ready — detected {len(initial_cards)} activity cards before scroll.")
        else:
            logging.warning("No activity cards found during auth/feed readiness wait.")

        cards = _scroll_and_load_cards(driver, cutoff_date, CARD_SELECTORS[0])
        if not cards:
            cards = _find_receipt_cards(driver)
        if not cards:
            logging.warning("Could not find any activity cards on the screen.")
            _write_debug_artifacts(driver)
            if _is_auth_prompt_visible(driver):
                raise RuntimeError("Still unauthenticated after waiting. Please complete Everyday Rewards login/MFA.")
            return
            
        num_to_process = len(cards) if all_receipts else 1
        logging.info(f"Found {len(cards)} activity cards total after full scroll. Processing...")

        processed_count = 0
        new_items_added = 0
        prices_updated = 0
        skipped_non_woolies = 0

        for index in range(num_to_process):
            # Re-fetch cards because DOM changes after open/close
            cards = _find_receipt_cards(driver)
            if index >= len(cards): break
            
            card = cards[index]
            card_text = card.text.lower()
            
            # Skip non-Woolworths entries (Big W, BWS, etc.)
            # The Woolies logo cards contain the Woolworths store name
            is_woolies = any(kw in card_text for kw in ["woolworths", "north rockhampton", "yeppoon cq", "parkhurst"])
            if not is_woolies and "big w" not in card_text:
                skipped_non_woolies += 1
                continue

            # Extract date from card text (format: "Sun 05 Apr", "Sat 04 Apr", etc.)
            receipt_date_str = "Unknown"
            receipt_date_obj = None
            date_match = re.search(r'(\w+ \d{1,2} \w{3})', card.text)
            if date_match:
                raw_date = date_match.group(1)
                try:
                    # Parse "Sun 05 Apr" style dates — try current year, then up to 2 years back
                    now = datetime.datetime.now()
                    parsed_base = datetime.datetime.strptime(raw_date, "%a %d %b")
                    for year_offset in [0, -1, -2]:
                        candidate = parsed_base.replace(year=now.year + year_offset)
                        if candidate <= now:  # Must be in the past
                            receipt_date_obj = candidate
                            break
                    if receipt_date_obj:
                        receipt_date_str = receipt_date_obj.strftime("%Y-%m-%d")
                    
                    # Check cutoff
                    if receipt_date_obj and receipt_date_obj < cutoff_date:
                        logging.info(f"Reached cutoff date ({receipt_date_str}). Stopping.")
                        break
                except Exception:
                    pass

            logging.info(f"Opening receipt #{index+1} ({receipt_date_str})...")
            driver.execute_script("arguments[0].click();", card)
            time.sleep(3)

            # Click the "eReceipt" tab in the side panel
            ereceipt_clicked = False
            try:
                # Look for eReceipt tab button
                tabs = driver.find_elements(By.CSS_SELECTOR, "button, [role='tab'], mat-button-toggle button")
                for tab in tabs:
                    if "ereceipt" in tab.text.lower() or "e-receipt" in tab.text.lower():
                        driver.execute_script("arguments[0].click();", tab)
                        ereceipt_clicked = True
                        time.sleep(2)
                        break
            except:
                pass

            if not ereceipt_clicked:
                logging.info(f"  No eReceipt tab found for receipt #{index+1}, skipping...")
                # Close the panel
                try:
                    close_btn = driver.find_elements(By.CSS_SELECTOR, ".close-button, button[class*='close'], [aria-label*='Close']")
                    if close_btn: driver.execute_script("arguments[0].click();", close_btn[0])
                    time.sleep(1)
                except:
                    pass
                continue

            # Parse the eReceipt content — scroll the sidebar first to load all items
            time.sleep(2)
            # Scroll the receipt panel to ensure all items are loaded
            try:
                panel = driver.find_elements(By.CSS_SELECTOR, ".side-panel, .drawer, [class*='panel'], [class*='drawer'], [class*='modal']")
                if panel:
                    scroll_target = panel[0]
                else:
                    scroll_target = driver.find_element(By.TAG_NAME, "body")
                # Scroll down in increments to trigger lazy loading
                for _ in range(5):
                    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scroll_target)
                    time.sleep(0.5)
            except:
                pass
            soup = BeautifulSoup(driver.page_source, "html.parser")
            
            # Try the specific eReceipt container first
            ereceipt_container = soup.select_one(".ereceiptSection-container")
            if ereceipt_container:
                text_content = ereceipt_container.get_text(separator="\n")
            else:
                # Fallback: use full page
                text_content = soup.get_text(separator="\n")
            
            text_lines = [l.strip() for l in text_content.split("\n") if l.strip()]
            
            # Parse item lines: Name on one line, price (plain number like "13.20") on next
            items_found = 0
            in_items_section = False
            
            for i in range(len(text_lines) - 1):
                line = text_lines[i]
                next_line = text_lines[i+1]
                
                # Start parsing after "Description" header
                if "description" in line.lower():
                    in_items_section = True
                    continue
                # Stop at TOTAL
                if line.upper().startswith("TOTAL") or line.upper().startswith("SUBTOTAL"):
                    in_items_section = False
                    continue
                
                if not in_items_section:
                    continue
                
                # Skip quantity/weight detail lines
                if line.lower().startswith("qty ") or "@" in line:
                    continue
                
                # Check if next line is a price (number, possibly with $)
                price_str = next_line.replace('$', '').strip()
                try:
                    price_f = float(price_str)
                except ValueError:
                    continue
                
                item_name = line.strip()
                if price_f > 0 and len(item_name) > 3 and not item_name[0].isdigit():
                    items_found += 1
                    matched_existing = False

                    # ── Fuzzy match against inventory ─────────────────────────
                    matched_item, score = find_best_inv_match(item_name, inventory)
                    if matched_item:
                        if 'price_history' not in matched_item:
                            matched_item['price_history'] = []
                        if not any(h.get('date') == receipt_date_str for h in matched_item['price_history']):
                            matched_item['price_history'].append({'date': receipt_date_str, 'price': price_f})
                            prices_updated += 1
                        matched_item['stock'] = 'full'
                        matched_item['last_purchased'] = receipt_date_str
                        matched_existing = True
                        match_type = 'exact' if score > 0.99 else f'fuzzy({score:.2f})'
                        logging.debug(f"    {match_type}: '{item_name}' → '{matched_item['name']}' @ ${price_f}")
                    
                    if not matched_existing:
                        # Only add as new item if nothing in inventory is even close
                        _, guard_score = find_best_inv_match(item_name, inventory, threshold=0.35)
                        if guard_score == 0.0:
                            search_term = item_name.replace(' ', '%20')
                            new_entry = {
                                "name": item_name.title(),
                                "type": "pantry",
                                "price_mode": "each",
                                "target": price_f,
                                "woolworths": f"https://www.woolworths.com.au/shop/search/products?searchTerm={search_term}",
                                "coles": "",
                                "price_history": [{"date": receipt_date_str, "price": price_f}]
                            }
                            inventory.append(new_entry)
                            new_items_added += 1


            logging.info(f"  Parsed {items_found} items from receipt #{index+1}")

            # Close the side panel
            try:
                close_btn = driver.find_elements(By.CSS_SELECTOR, ".close-button, button[class*='close'], [aria-label*='Close']")
                if close_btn: driver.execute_script("arguments[0].click();", close_btn[0])
                else: driver.find_element(By.TAG_NAME, 'body').send_keys('\ue00c')
                time.sleep(2)
            except:
                driver.get(ACTIVITY_URL)
                time.sleep(5)
            
            processed_count += 1
            if processed_count % 5 == 0: save_inventory(inventory)

        save_inventory(inventory)
        logging.info(f"Sync complete. Processed {processed_count} receipts. Added {new_items_added} new items, updated prices for {prices_updated} items.")
    except Exception as e:
        logging.error(f"Error during receipt sync: {e}")
        raise
    finally:
        driver.quit()

def enrich_inventory():
    inventory = load_inventory()
    logging.info("Starting background enrichment for missing URLs and images...")
    
    # We'll use a session that mimics Chrome
    session = cffi_requests.Session(impersonate="chrome124")
    
    updated = False
    for item in inventory:
        needs_coles = not item.get("coles")
        needs_img = not item.get("image_url")
        
        if needs_coles or needs_img:
            logging.info(f"Enriching: {item['name']}...")
            try:
                # Search Coles for the missing link/img
                query = item['name'].replace(' ', '+')
                # Simple search first
                search_url = f"https://www.coles.com.au/search?q={query}"
                resp = session.get(search_url, timeout=10)
                
                html = resp.text
                
                if needs_coles:
                    match = re.search(r'\"(/product/[^\"]+)\"', html)
                    if match:
                        item["coles"] = "https://www.coles.com.au" + match.group(1)
                        logging.info(f"  + Found Coles Link: {item['coles']}")
                        updated = True
                
                if needs_img:
                    img_match = re.search(r'\"(https://productimages[^\"]+\.jpg)\"', html)
                    if img_match:
                        item["image_url"] = img_match.group(1)
                        logging.info(f"  + Found Image: {item['image_url']}")
                        updated = True
            except Exception as e:
                logging.warning(f"  - Failed to enrich {item['name']}: {e}")
                
            time.sleep(2)
    
    if updated:
        save_inventory(inventory)
        logging.info("Enrichment complete and inventory.json updated.")
    else:
        logging.info("No new enrichment data found.")

def _parse_args():
    parser = argparse.ArgumentParser(description="Sync Everyday Rewards receipts into WooliesBot price history.")
    parser.add_argument("--months-back", type=float, default=24, help="How far back to scan receipts.")
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Only process the most recent receipt instead of all receipts in range.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headless (works best with an already-authenticated profile).",
    )
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=180,
        help="Seconds to wait for auth/feed readiness before failing.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between auth/feed readiness checks.",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Chrome user-data directory for persistent Everyday session.",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip post-sync URL/image enrichment.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_sync(
        all_receipts=not args.latest_only,
        months_back=args.months_back,
        headless=args.headless,
        login_timeout=max(30, args.login_timeout),
        poll_interval=max(1, args.poll_interval),
        profile_dir=args.profile_dir,
    )
    if not args.skip_enrich:
        enrich_inventory()

