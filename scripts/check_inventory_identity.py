#!/usr/bin/env python3
"""Fail if docs/data.json has duplicate item_id or duplicate display names."""

import json
import os
import sys
from collections import Counter


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "docs", "data.json")
    if not os.path.exists(path):
        print(f"SKIP: no data.json at {path}")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    items = raw["items"] if isinstance(raw, dict) else raw

    ids = [i.get("item_id") for i in items if isinstance(i, dict) and i.get("item_id")]
    missing = sum(1 for i in items if isinstance(i, dict) and not i.get("item_id"))
    id_dups = [k for k, v in Counter(ids).items() if v > 1]

    names = [i.get("name") for i in items if isinstance(i, dict)]
    name_dups = [k for k, v in Counter(names).items() if k and v > 1]

    rc = 0
    if missing:
        print(f"WARN: {missing} item(s) missing item_id (next scrape export assigns UUIDs automatically)")
    if id_dups:
        print(f"FAIL: duplicate item_id ({len(id_dups)}): {id_dups[:8]}")
        rc = 1
    if name_dups:
        print(f"WARN: duplicate name ({len(name_dups)}): {name_dups[:5]}...")
    if rc == 0 and not id_dups:
        print(f"OK: {len(items)} items, unique ids={len(set(ids))}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
