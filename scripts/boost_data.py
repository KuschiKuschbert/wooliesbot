import json
import os
import random
from datetime import datetime, timedelta

DATA_FILE = "docs/data.json"
HIST_FILE = "docs/history.json"

def boost_history():
    if not os.path.exists(DATA_FILE):
        print(f"Error: {DATA_FILE} not found.")
        return

    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    if os.path.exists(HIST_FILE):
        # We wipe history to ensure a clean 104-week contiguous run
        history = {}
    else:
        history = {}

    today = datetime.now()
    weeks_to_generate = 104 # 24 months

    print(f"Boosting history for {len(data)} items over {weeks_to_generate} weeks...")

    for item in data:
        name = item["name"]
        target = item.get("target", 10.0)
        shelf_price = item.get("price", target * 1.5)
        
        if name not in history:
            history[name] = {"target": target, "history": []}
        
        existing_dates = {h["date"] for h in history[name]["history"]}
        
        for w in range(weeks_to_generate):
            date_obj = today - timedelta(weeks=w)
            date_str = date_obj.strftime("%Y-%m-%d")
            
            if date_str in existing_dates:
                continue
            
            # Simulate a 1-in-3 chance of being on special
            is_special = random.random() < 0.35
            if is_special:
                # Price is at or slightly below target
                price = target * random.uniform(0.8, 1.0)
            else:
                # Price is closer to shelf price
                price = shelf_price * random.uniform(0.95, 1.05)
            
            # Decide store bias (Woolies vs Coles)
            store = "woolworths" if random.random() < 0.5 else "coles"
            
            history[name]["history"].append({
                "date": date_str,
                "price": round(price, 2),
                "is_special": is_special,
                "store": store
            })
        
        # Sort history by date
        history[name]["history"].sort(key=lambda x: x["date"])

    with open(HIST_FILE, "w") as f:
        json.dump(history, f, indent=4)

    print("Successfully boosted history database.")

if __name__ == "__main__":
    boost_history()
