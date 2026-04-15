import time
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_handler = RotatingFileHandler("logs/keep_sync.log", maxBytes=1*1024*1024, backupCount=2)
_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
_stream = logging.StreamHandler()
_stream.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_handler, _stream])

# ── Config from .env ──────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_dotenv():
    """Load .env file into os.environ (same pattern as chef_os.py)."""
    env_path = os.path.join(_SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

KEEP_URL      = os.environ.get("GOOGLE_KEEP_URL", "")
DASHBOARD_URL = "file://" + os.path.join(_SCRIPT_DIR, "docs", "index.html")


def run_keep_sync():
    """
    Synchronizes the shopping list from the dashboard to Google Keep.

    Strategy:
    1. Open local dashboard, fetch current shopping list from localStorage.
    2. Open Google Keep list.
    3. Bulk tick all unchecked items and delete them.
    4. Populate new items by typing + Enter.
    """
    if not KEEP_URL:
        logging.error(
            "GOOGLE_KEEP_URL is not set. Add it to your .env file:\n"
            "  GOOGLE_KEEP_URL=https://keep.google.com/u/0/#LIST/your_list_id"
        )
        return

    user_data_dir = os.path.join(_SCRIPT_DIR, "chrome_profile")
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")

    logging.info("🚀 Starting Chrome for Google Keep sync...")
    driver = uc.Chrome(options=options)
    wait = WebDriverWait(driver, 20)  # up to 20s per element

    try:
        # ── 1. Get Shopping List from Dashboard ──────────────────────────────
        logging.info("Opening local dashboard to fetch shopping list...")
        driver.get(DASHBOARD_URL)
        # Wait for the page JS to initialise (shoppingList in localStorage)
        time.sleep(2)

        shopping_list = driver.execute_script(
            "return JSON.parse(localStorage.getItem('shoppingList') || '[]')"
        )

        if not shopping_list:
            logging.warning("⚠️ Shopping list is empty. Nothing to sync.")
            driver.quit()
            return

        logging.info(f"📋 Found {len(shopping_list)} items in list. Syncing to Keep...")

        # ── 2. Open Google Keep ───────────────────────────────────────────────
        driver.get(KEEP_URL)
        logging.info("Waiting for Google Keep to load...")

        # Wait until the note body area is visible rather than a fixed sleep
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div[role="checkbox"], div[aria-label="Add list item"]')
            ))
        except Exception:
            logging.warning("Google Keep took too long to load — proceeding anyway")
            time.sleep(4)

        # ── 3. Clear existing list ────────────────────────────────────────────
        logging.info("Wiping existing list...")
        unchecked = driver.find_elements(By.CSS_SELECTOR, 'div[role="checkbox"][aria-checked="false"]')
        if unchecked:
            driver.execute_script("""
                document.querySelectorAll('div[role="checkbox"][aria-checked="false"]')
                        .forEach(cb => cb.click());
            """)
            time.sleep(1)

            # Delete via 'More' menu → 'Delete ticked items'
            try:
                more_btn = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'div[aria-label="More"]')
                ))
                driver.execute_script("arguments[0].click();", more_btn)

                # Wait for the menu to appear
                time.sleep(0.6)
                menu_items = driver.find_elements(By.CSS_SELECTOR, 'div[role="menuitem"]')
                deleted = False
                for mi in menu_items:
                    if "Delete ticked items" in mi.text or "Delete checked items" in mi.text:
                        driver.execute_script("arguments[0].click();", mi)
                        logging.info("✅ Bulk cleared list items.")
                        deleted = True
                        time.sleep(1)
                        break
                if not deleted:
                    # Try pressing Escape to close menu and continue anyway
                    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                    logging.info("No 'Delete ticked items' found — list may already be empty.")
            except Exception as e:
                logging.warning(f"Could not bulk clear: {e}")
        else:
            logging.info("No unchecked items to clear.")

        # ── 4. Populate new items ─────────────────────────────────────────────
        logging.info("Adding new items...")
        try:
            add_trigger = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'div[aria-label="Add list item"]')
            ))
            driver.execute_script("arguments[0].click();", add_trigger)
            time.sleep(0.5)

            added = 0
            for item in shopping_list:
                qty = item.get("qty", 1)
                name = item.get("name", "")
                item_text = f"{qty}x {name}" if qty > 1 else name

                active_element = driver.switch_to.active_element
                active_element.send_keys(item_text)
                active_element.send_keys(Keys.ENTER)
                time.sleep(0.35)  # small gap between items
                added += 1

            logging.info(f"✅ Successfully added {added} items.")

        except Exception as e:
            logging.error(f"Failed to add items: {e}")

        # ── 5. Close the note ─────────────────────────────────────────────────
        try:
            close_btn = driver.find_element(By.XPATH, '//div[@role="button" and contains(., "Close")]')
            driver.execute_script("arguments[0].click();", close_btn)
        except Exception:
            # Fallback: click outside the note
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass

        logging.info("🎉 Sync complete! Check your Google Keep app.")
        time.sleep(1)

    except Exception as e:
        logging.error(f"An error occurred during sync: {e}", exc_info=True)
    finally:
        driver.quit()


if __name__ == "__main__":
    run_keep_sync()
