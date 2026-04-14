#!/usr/bin/env python3
"""Smart Target Price Engine — Data-driven target recalculation for WooliesBot.

Uses receipt history (price_history in data.json) and scraper history (history.json)
to compute statistically meaningful target prices.

3-Tier Algorithm:
  Gold   (≥4 observations) → 25th percentile of observed prices
  Silver (2-3 observations) → Minimum observed price
  Bronze (0-1 observations) → Category median × 0.90

Usage:
  python scripts/smart_targets.py              # Recalculate and write
  python scripts/smart_targets.py --dry-run    # Preview changes only
"""

import json
import os
import sys
import logging
import datetime
import statistics

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_FILE = os.path.join(PROJECT_DIR, "docs", "data.json")
HISTORY_FILE = os.path.join(PROJECT_DIR, "docs", "history.json")

# Safeguards
MIN_TARGET = 0.50        # Never set target below this
MAX_SENTINEL = 1000      # Filter out error prices (99999, etc.)
MAX_TARGET_RATIO = 1.5   # Target ≤ 1.5× minimum observed price


def load_data():
    """Load data.json inventory."""
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def load_history():
    """Load history.json scraper history."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def save_data(data):
    """Write data.json inventory back."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


def get_all_prices(item, history_data):
    """Merge receipt price_history + scraper history into a single price list.
    Filters out sentinel/error prices."""
    prices = []

    # Receipt data (stored in data.json per item)
    for entry in item.get("price_history", []):
        p = entry.get("price", 0)
        if 0 < p < MAX_SENTINEL:
            prices.append(p)

    # Scraper data (stored in history.json by name)
    name = item.get("name", "")
    hist_entry = history_data.get(name, {})
    for entry in hist_entry.get("history", []):
        p = entry.get("price", 0)
        if 0 < p < MAX_SENTINEL:
            prices.append(p)

    return prices


def percentile(data, pct):
    """Calculate percentile value. pct is 0-100."""
    if not data:
        return 0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def compute_category_medians(data, history_data):
    """Compute median current price per category (type/subcategory).
    Used as fallback for Bronze-tier items."""
    by_type = {}
    by_subcat = {}

    for item in data:
        price = item.get("eff_price") or item.get("price", 0)
        if price <= 0 or price >= MAX_SENTINEL:
            # Try using historical prices instead
            all_p = get_all_prices(item, history_data)
            price = statistics.median(all_p) if all_p else 0

        if price <= 0:
            continue

        item_type = item.get("type", "pantry")
        subcat = item.get("subcategory", "")

        by_type.setdefault(item_type, []).append(price)
        if subcat:
            by_subcat.setdefault(subcat, []).append(price)

    type_medians = {k: statistics.median(v) for k, v in by_type.items() if v}
    subcat_medians = {k: statistics.median(v) for k, v in by_subcat.items() if v}

    return type_medians, subcat_medians


def recalculate_targets(dry_run=False):
    """Main entry point: recalculate all targets using the 3-tier algorithm.

    Returns a summary dict with counts and details."""
    data = load_data()
    history_data = load_history()
    type_medians, subcat_medians = compute_category_medians(data, history_data)

    summary = {
        "gold": 0,
        "silver": 0,
        "bronze": 0,
        "unchanged": 0,
        "total": len(data),
        "changes": [],
    }

    for item in data:
        name = item.get("name", "Unknown")
        old_target = item.get("target", 0)
        all_prices = get_all_prices(item, history_data)
        n = len(all_prices)

        new_target = old_target
        confidence = "low"
        method = "unchanged"

        if n >= 4:
            # GOLD: 25th percentile
            new_target = round(percentile(all_prices, 25), 2)
            confidence = "high"
            method = f"p25 of {n} prices"
            summary["gold"] += 1

        elif n >= 2:
            # SILVER: Minimum observed
            new_target = round(min(all_prices), 2)
            confidence = "medium"
            method = f"min of {n} prices"
            summary["silver"] += 1

        else:
            # BRONZE: Use own data first, category median as last resort
            if n == 1:
                # Single observation: use it as the target (best we have)
                new_target = round(all_prices[0], 2)
                method = "single observation"
            else:
                # Zero observations: try category median × 0.90
                subcat = item.get("subcategory", "")
                item_type = item.get("type", "pantry")
                cat_median = subcat_medians.get(subcat) or type_medians.get(item_type)

                # Only use category median if item's current price is in the same
                # rough ballpark (within 3×). Prevents $4 target for $79 items.
                current_price = item.get("eff_price") or item.get("price", 0)
                if (cat_median and cat_median > 0 and current_price > 0
                        and current_price < MAX_SENTINEL
                        and 0.3 < (cat_median / current_price) < 3.0):
                    new_target = round(cat_median * 0.90, 2)
                    method = f"category '{subcat or item_type}' median×0.90"
                elif current_price > 0 and current_price < MAX_SENTINEL:
                    # No good category match — use current price as target
                    # (essentially: "this is what it costs")
                    new_target = round(current_price, 2)
                    method = "current price (no history)"
                # else: keep old_target as-is

            confidence = "low"
            summary["bronze"] += 1

        # Safeguards
        if new_target < MIN_TARGET:
            new_target = MIN_TARGET

        # Cap at 1.5× minimum observed (prevents overly generous targets)
        if all_prices:
            cap = round(min(all_prices) * MAX_TARGET_RATIO, 2)
            if new_target > cap:
                new_target = cap

        # Never set target above current price (would mark everything as above target)
        current_price = item.get("eff_price") or item.get("price", 0)
        if current_price > 0 and current_price < MAX_SENTINEL and new_target > current_price:
            new_target = current_price

        # Apply
        if abs(new_target - old_target) > 0.01:
            change = {
                "name": name,
                "old_target": old_target,
                "new_target": new_target,
                "confidence": confidence,
                "method": method,
                "data_points": n,
            }
            summary["changes"].append(change)
            logging.info(
                f"{'[DRY] ' if dry_run else ''}TARGET: {name}: "
                f"${old_target:.2f} → ${new_target:.2f} ({method}, {confidence})"
            )
        else:
            summary["unchanged"] += 1

        # Write back to item
        if not dry_run:
            item["target"] = new_target
            item["target_confidence"] = confidence
            item["target_method"] = method
            item["target_data_points"] = n
            item["target_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")
        else:
            # Still annotate for preview
            item["target_confidence"] = confidence
            item["target_method"] = method
            item["target_data_points"] = n

    if not dry_run:
        save_data(data)
        logging.info(f"Saved {len(data)} items to {DATA_FILE}")

    # Summary report
    changed = len(summary["changes"])
    logging.info(
        f"\n{'='*50}\n"
        f"SMART TARGET RECALCULATION {'(DRY RUN) ' if dry_run else ''}COMPLETE\n"
        f"{'='*50}\n"
        f"  Total items:    {summary['total']}\n"
        f"  Gold   (≥4 pts): {summary['gold']}\n"
        f"  Silver (2-3 pts): {summary['silver']}\n"
        f"  Bronze (0-1 pts): {summary['bronze']}\n"
        f"  Changed:        {changed}\n"
        f"  Unchanged:      {summary['unchanged']}\n"
        f"{'='*50}"
    )

    return summary


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logging.info("DRY RUN MODE — no changes will be written")
    recalculate_targets(dry_run=dry_run)
