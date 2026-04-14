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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

INV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")

def load_inventory():
    try:
        with open(INV_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_inventory(inv):
    with open(INV_FILE, "w") as f:
        json.dump(inv, f, indent=4)

def normalize_name(name):
    # simple standardizer for fuzzy matching
    return name.lower().replace(" ", "").replace("-", "")

def fuzzy_match(new_name, inventory):
    norm_new = normalize_name(new_name)
    for item in inventory:
        if normalize_name(item["name"]) in norm_new or norm_new in normalize_name(item["name"]):
            return True
    return False

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

def run_sync(all_receipts=True, months_back=6):
    inventory = load_inventory()
    
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=months_back * 30)
    logging.info(f"Syncing receipts back to: {cutoff_date.strftime('%Y-%m-%d')}")

    user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    
    logging.info("Starting Chrome with persistent profile...")
    driver = uc.Chrome(options=options)
    
    try:
        driver.get("https://www.everyday.com.au/index.html#/my-activity")
        logging.info("Waiting for page load...")
        time.sleep(8)
        
        # Wait up to 3 minutes for user to complete manual login + SMS
        login_timeout = 180  # seconds
        poll_interval = 5
        elapsed = 0
        while elapsed < login_timeout:
            url = driver.current_url.lower()
            if "login" not in url and "signin" not in url and "auth" not in url:
                logging.info("Login detected — user is authenticated!")
                break
            if elapsed == 0:
                logging.info("⏳ Login required. Please log in to Everyday Rewards in the Chrome window...")
                logging.info(f"   Waiting up to {login_timeout // 60} minutes for you to complete login + SMS...")
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            logging.error("Login timed out after 3 minutes. Please try again.")
            return

        logging.info("Attempting to parse receipts...")
        time.sleep(5)
        
        # Correct selector based on live inspection of the Everyday Rewards portal
        CARD_SELECTOR = "div.transaction-row.clickable"
        cards = driver.find_elements(By.CSS_SELECTOR, CARD_SELECTOR)
        if not cards:
            logging.warning("Could not find activity cards. Trying alternative selectors...")
            # Fallback: try broader selectors
            for alt_sel in [".transaction-row", "[class*='transaction']", "[class*='activity']"]:
                cards = driver.find_elements(By.CSS_SELECTOR, alt_sel)
                if cards:
                    logging.info(f"Found {len(cards)} cards with selector: {alt_sel}")
                    break
        if not cards:
            logging.warning("Could not find any activity cards on the screen.")
            return
            
        num_to_process = len(cards) if all_receipts else 1
        logging.info(f"Found {len(cards)} activity cards. Processing...")

        processed_count = 0
        new_items_added = 0
        prices_updated = 0
        skipped_non_woolies = 0

        for index in range(num_to_process):
            # Re-fetch cards because DOM changes after open/close
            cards = driver.find_elements(By.CSS_SELECTOR, CARD_SELECTOR)
            if not cards:
                cards = driver.find_elements(By.CSS_SELECTOR, ".transaction-row")
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
            import re as _re
            date_match = _re.search(r'(\w+ \d{1,2} \w{3})', card.text)
            if date_match:
                raw_date = date_match.group(1)
                try:
                    # Parse "Sun 05 Apr" style dates
                    receipt_date_obj = datetime.datetime.strptime(raw_date, "%a %d %b")
                    # Assume current year (or previous year if month is in the future)
                    now = datetime.datetime.now()
                    receipt_date_obj = receipt_date_obj.replace(year=now.year)
                    if receipt_date_obj > now:
                        receipt_date_obj = receipt_date_obj.replace(year=now.year - 1)
                    receipt_date_str = receipt_date_obj.strftime("%Y-%m-%d")
                    
                    # Check cutoff
                    if receipt_date_obj < cutoff_date:
                        logging.info(f"Reached cutoff date ({receipt_date_str}). Stopping.")
                        break
                except:
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
                    norm_name = normalize_name(item_name)
                    
                    for item in inventory:
                        if normalize_name(item["name"]) == norm_name:
                            if "price_history" not in item: item["price_history"] = []
                            if not any(h.get("date") == receipt_date_str for h in item["price_history"]):
                                item["price_history"].append({"date": receipt_date_str, "price": price_f})
                                prices_updated += 1
                            
                            # Auto-Stocking implementation
                            item["stock"] = "full"
                            item["last_purchased"] = receipt_date_str
                            
                            matched_existing = True
                            break
                    
                    if not matched_existing and not fuzzy_match(item_name, inventory):
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
                driver.get("https://www.everyday.com.au/index.html#/my-activity")
                time.sleep(5)
            
            processed_count += 1
            if processed_count % 5 == 0: save_inventory(inventory)

        save_inventory(inventory)
        logging.info(f"Sync complete. Processed {processed_count} receipts. Added {new_items_added} new items, updated prices for {prices_updated} items.")
    except Exception as e:
        logging.error(f"Error during receipt sync: {e}")
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

if __name__ == "__main__":
    # 1. Sync Receipts (User handles login in the window)
    run_sync(all_receipts=True, months_back=24)
    # 2. Enrich the newly discovered items
    enrich_inventory()

