import time
import os
import json
import logging
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration
KEEP_URL = "https://keep.google.com/u/0/#LIST/1GgwJb6E6EKBsypmg3_xq6nfqxccfz_XRjbzCpupi_qeBZ2VHl57Bf5ybkK37R7_iNTHz"
# Use the local file path for the dashboard to read its localStorage
DASHBOARD_URL = "file://" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")

def run_keep_sync():
    """
    Synchronizes the shopping list from the dashboard to Google Keep.
    Strategy:
    1. Open local dashboard, fetch current shopping list from localStorage.
    2. Open Google Keep list.
    3. Bulk tick all items and use 'Delete ticked items' to wipe.
    4. Populate new items by typing + Enter.
    """
    user_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    
    logging.info("🚀 Starting Chrome for Google Keep sync...")
    driver = uc.Chrome(options=options)
    
    try:
        # 1. Get Shopping List from Dashboard
        logging.info(f"Opening local dashboard to fetch shopping list...")
        driver.get(DASHBOARD_URL)
        time.sleep(3)
        
        shopping_list = driver.execute_script("return JSON.parse(localStorage.getItem('shoppingList') || '[]')")
        
        if not shopping_list:
            logging.warning("⚠️ Shopping list is empty. Nothing to sync.")
            driver.quit()
            return

        logging.info(f"📋 Found {len(shopping_list)} items in list. Syncing to Keep...")

        # 2. Open Google Keep
        driver.get(KEEP_URL)
        logging.info("Waiting for Google Keep to load...")
        time.sleep(8) 

        # 3. Clear existing list
        logging.info("Wiping existing list...")
        # Tick all unchecked items
        driver.execute_script("""
            const unchecked = document.querySelectorAll('div[role="checkbox"][aria-checked="false"]');
            unchecked.forEach(cb => cb.click());
        """)
        time.sleep(2)

        # Delete ticked items via 'More' menu
        try:
            more_btn = driver.find_element(By.CSS_SELECTOR, 'div[aria-label="More"]')
            driver.execute_script("arguments[0].click();", more_btn)
            time.sleep(1)
            
            menu_items = driver.find_elements(By.CSS_SELECTOR, 'div[role="menuitem"]')
            deleted = False
            for item in menu_items:
                if "Delete ticked items" in item.text:
                    driver.execute_script("arguments[0].click();", item)
                    logging.info("✅ Bulk cleared list items.")
                    deleted = True
                    time.sleep(2)
                    break
            if not deleted:
                logging.info("No items found to delete (list might already be empty).")
        except Exception as e:
            logging.warning(f"Could not bulk clear. Error: {e}")

        # 4. Populate new items
        logging.info("Adding new items...")
        try:
            # Click the 'Add list item' area to focus the input
            add_trigger = driver.find_element(By.CSS_SELECTOR, 'div[aria-label="Add list item"]')
            driver.execute_script("arguments[0].click();", add_trigger)
            time.sleep(1)
            
            for item in shopping_list:
                item_text = f"{item['qty']}x {item['name']}"
                # Focus the active input (Google Keep uses a contenteditable/combobox structure)
                active_element = driver.switch_to.active_element
                active_element.send_keys(item_text)
                active_element.send_keys(Keys.ENTER)
                time.sleep(0.4)
                
            logging.info(f"✅ Successfully added {len(shopping_list)} items.")
        except Exception as e:
            logging.error(f"Failed to add items: {e}")

        # 5. Final Save/Close
        try:
            # Look for the close button text or specific class
            close_buttons = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"]')
            for btn in close_buttons:
                if "Close" in btn.text:
                    driver.execute_script("arguments[0].click();", btn)
                    break
        except:
            pass
            
        logging.info("🎉 Sync complete! Check your Google Keep app.")
        time.sleep(2)

    except Exception as e:
        logging.error(f"An error occurred during sync: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    run_keep_sync()
