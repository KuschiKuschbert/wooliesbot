#!/usr/bin/env python3
"""Smart Target Price Engine — Data-driven target recalculation for WooliesBot.

Uses scrape_history and price_history from data.json (single source of truth)
to compute target prices representing "what this item costs when on special."

Algorithm:
  Gold   (≥10 observations) → median of "sale cluster" prices (>10% below median)
  Silver (4-9 observations)  → 20th percentile of observed prices
  Bronze (0-3 observations)  → current price (no history yet)

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

# Safeguards
MIN_TARGET = 0.50        # Never set target below this
MAX_SENTINEL = 1000      # Filter out error prices (99999, etc.)
MAX_TARGET_RATIO = 1.5   # Target ≤ 1.5× minimum observed price


_data_wrapper = None  # Stores the top-level dict if data.json uses {items: [...]} format

def load_data():
    """Load data.json inventory. Handles both list and {items: [...]} formats."""
    global _data_wrapper
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        _data_wrapper = None
        return raw
    _data_wrapper = raw
    return raw.get("items", [])


def save_data(data):
    """Write data.json inventory back, preserving wrapper format."""
    global _data_wrapper
    with open(DATA_FILE, "w") as f:
        if _data_wrapper is not None:
            _data_wrapper["items"] = data
            json.dump(_data_wrapper, f, indent=2)
        else:
            json.dump(data, f, indent=2)


def get_all_prices(item):
    """Collect all observed prices from scrape_history + price_history.
    Returns a flat list of prices, filtered of sentinel/error values."""
    prices = []

    # Scraper snapshots (weekly, from chef_os)
    for entry in item.get("scrape_history", []):
        p = entry.get("price", 0)
        if 0 < p < MAX_SENTINEL:
            prices.append(p)

    # Receipt data (from receipt_sync)
    for entry in item.get("price_history", []):
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


def compute_category_medians(data):
    """Compute median current price per category (type/subcategory).
    Used as fallback for Bronze-tier items."""
    by_type = {}
    by_subcat = {}

    for item in data:
        price = item.get("eff_price") or item.get("price", 0)
        if price <= 0 or price >= MAX_SENTINEL:
            # Try using historical prices instead
            all_p = get_all_prices(item)
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
    """Main entry point: recalculate all targets using the tiered algorithm.

    Returns a summary dict with counts and details."""
    data = load_data()
    type_medians, subcat_medians = compute_category_medians(data)

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
        all_prices = get_all_prices(item)
        n = len(all_prices)
        current_price = item.get("eff_price") or item.get("price", 0)

        new_target = old_target
        confidence = "low"
        method = "unchanged"

        # ── PRIORITY 1: Retailer says "on special" right now ──
        # Use was_price as target — that's the regular price the store normally charges.
        # If the store marks "Was $11, Now $8.50", the was_price ($11) becomes the
        # ceiling and the current price is the genuine special price.
        was = item.get("was_price")
        on_special = item.get("on_special", False)

        # Also check all_stores for any store running a special
        best_was = None
        for sk, sd in item.get("all_stores", {}).items():
            sw = sd.get("was_price")
            if sw and sw > 0:
                if best_was is None or sw > best_was:
                    best_was = sw

        if was and float(was) > 0 and on_special:
            was = float(was)
            best_was = was

        if best_was and best_was > current_price and current_price > 0:
            # Store confirms a special — use was_price as the target
            new_target = round(best_was, 2)
            confidence = "high"
            method = f"was ${best_was:.2f}, now ${current_price:.2f} (store special)"
            summary["gold"] += 1

        # ── PRIORITY 2: Enough historical data for statistical target ──
        elif n >= 10:
            # Sale-cluster detection: find prices >10% below median
            median_price = statistics.median(all_prices)
            sale_threshold = median_price * 0.90
            sale_prices = [p for p in all_prices if p <= sale_threshold]

            if len(sale_prices) >= 3:
                new_target = round(statistics.median(sale_prices), 2)
                confidence = "high"
                sale_pct = len(sale_prices) / n * 100
                method = f"sale median ({len(sale_prices)}/{n} obs, {sale_pct:.0f}% on sale)"
            else:
                new_target = round(percentile(all_prices, 15), 2)
                confidence = "high"
                method = f"p15 of {n} prices (few sales detected)"
            summary["gold"] += 1

        elif n >= 4:
            # SILVER: p20 with fewer data points
            new_target = round(percentile(all_prices, 20), 2)
            confidence = "medium"
            method = f"p20 of {n} prices"
            summary["silver"] += 1

        else:
            # BRONZE: Not enough data for a reliable target.
            # Only set a target if we have receipt data showing a DIFFERENT price.
            receipt_prices = [e.get("price", 0) for e in item.get("price_history", [])
                              if 0 < e.get("price", 0) < MAX_SENTINEL]

            if receipt_prices and min(receipt_prices) < current_price * 0.95:
                new_target = round(min(receipt_prices), 2)
                method = f"receipt min of {len(receipt_prices)} purchases"
            else:
                new_target = 0
                method = "watching (need more data)"

            confidence = "low"
            summary["bronze"] += 1

        # Safeguards (only apply when we have a real target, not "watching")
        if new_target > 0:
            if new_target < MIN_TARGET:
                new_target = MIN_TARGET

            # Cap at 1.5× minimum observed (prevents overly generous targets)
            if all_prices:
                cap = round(min(all_prices) * MAX_TARGET_RATIO, 2)
                if new_target > cap:
                    new_target = cap

            # Never set target above current price
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
        # Recompute is_special flags in scrape_history against new targets
        specials_updated = 0
        for item in data:
            new_target = item.get("target", 0)
            if not new_target:
                continue
            for entry in item.get("scrape_history", []):
                was = entry.get("is_special", False)
                entry["is_special"] = entry.get("price", 0) <= new_target
                if was != entry["is_special"]:
                    specials_updated += 1

        save_data(data)
        logging.info(f"Saved {len(data)} items to {DATA_FILE}")
        logging.info(f"Recomputed is_special on {specials_updated} scrape_history entries")

    # Summary report
    changed = len(summary["changes"])
    logging.info(
        f"\n{'='*50}\n"
        f"SMART TARGET RECALCULATION {'(DRY RUN) ' if dry_run else ''}COMPLETE\n"
        f"{'='*50}\n"
        f"  Total items:     {summary['total']}\n"
        f"  Gold   (≥10 pts): {summary['gold']}\n"
        f"  Silver (4-9 pts): {summary['silver']}\n"
        f"  Bronze (0-3 pts): {summary['bronze']}\n"
        f"  Changed:         {changed}\n"
        f"  Unchanged:       {summary['unchanged']}\n"
        f"{'='*50}"
    )

    return summary


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logging.info("DRY RUN MODE — no changes will be written")
    recalculate_targets(dry_run=dry_run)
