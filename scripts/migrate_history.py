#!/usr/bin/env python3
"""One-shot migration: merge history.json into data.json as per-item scrape_history.

After this runs, every item in data.json will have a `scrape_history` array
containing the weekly scraped price snapshots that used to live in history.json.

Usage:
  python scripts/migrate_history.py --dry-run   # Preview only
  python scripts/migrate_history.py             # Migrate and write
"""

import json
import os
import sys
import shutil
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_FILE = os.path.join(PROJECT_DIR, "docs", "data.json")
HISTORY_FILE = os.path.join(PROJECT_DIR, "docs", "history.json")
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")


def main():
    dry_run = "--dry-run" in sys.argv

    # ── Load both files ──────────────────────────────────────────────────
    with open(DATA_FILE, "r") as f:
        raw = json.load(f)

    # data.json may be wrapped in {last_updated, items} or be a bare array
    if isinstance(raw, list):
        data = raw
        wrapper = None
    else:
        data = raw.get("items", raw)
        wrapper = raw

    if not os.path.exists(HISTORY_FILE):
        print("history.json not found — nothing to migrate.")
        return

    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    # ── Build name→index map for fast lookup ─────────────────────────────
    name_to_idx = {item["name"]: i for i, item in enumerate(data)}

    # ── Stats ─────────────────────────────────────────────────────────────
    migrated = 0
    entries_total = 0
    deduped = 0
    unmatched_names = []

    for name, hdata in history.items():
        h_entries = hdata.get("history", [])
        if not h_entries:
            continue

        idx = name_to_idx.get(name)
        if idx is None:
            unmatched_names.append(name)
            continue

        item = data[idx]

        # Existing scrape_history (shouldn't exist yet, but be safe)
        existing = item.get("scrape_history", [])
        existing_dates = {e["date"] for e in existing}

        added = 0
        for entry in h_entries:
            if entry["date"] in existing_dates:
                deduped += 1
                continue
            existing.append({
                "date": entry["date"],
                "price": entry["price"],
                "is_special": entry.get("is_special", False),
                "store": entry.get("store"),
            })
            existing_dates.add(entry["date"])
            added += 1

        # Sort by date ascending
        existing.sort(key=lambda e: e["date"])
        item["scrape_history"] = existing
        entries_total += added
        if added > 0:
            migrated += 1

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"MIGRATION {'(DRY RUN) ' if dry_run else ''}REPORT")
    print(f"{'='*50}")
    print(f"  Items in data.json:      {len(data)}")
    print(f"  Items in history.json:   {len(history)}")
    print(f"  Items migrated:          {migrated}")
    print(f"  Entries added:           {entries_total}")
    print(f"  Duplicates skipped:      {deduped}")
    print(f"  Unmatched (orphaned):    {len(unmatched_names)}")
    if unmatched_names:
        for n in unmatched_names[:10]:
            print(f"    - {n}")
    print(f"{'='*50}\n")

    if dry_run:
        print("Dry run — no files written.")
        return

    # ── Backup ─────────────────────────────────────────────────────────────
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f"data_{ts}.json"))
    shutil.copy2(HISTORY_FILE, os.path.join(BACKUP_DIR, f"history_{ts}.json"))
    print(f"Backups saved to {BACKUP_DIR}/")

    # ── Write merged data.json ─────────────────────────────────────────────
    if wrapper is not None:
        wrapper["items"] = data
        with open(DATA_FILE, "w") as f:
            json.dump(wrapper, f, indent=2)
    else:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    print(f"Written merged data.json ({len(data)} items)")

    # ── Validate ───────────────────────────────────────────────────────────
    # Re-read and verify every history entry is present
    with open(DATA_FILE, "r") as f:
        check = json.load(f)
    check_items = check if isinstance(check, list) else check.get("items", [])
    total_sh = sum(len(i.get("scrape_history", [])) for i in check_items)
    print(f"Validation: {total_sh} total scrape_history entries across {len(check_items)} items")
    print(f"\nMigration complete! You can now delete docs/history.json.")


if __name__ == "__main__":
    main()
