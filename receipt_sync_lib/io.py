from __future__ import annotations

import json
from pathlib import Path

DATA_JSON = Path("docs/data.json")


def load_inventory():
    raw = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw:
        return raw["items"]
    if isinstance(raw, list):
        return raw
    raise ValueError("docs/data.json has unexpected format")


def load_inventory_raw():
    raw = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw:
        return raw, raw["items"]
    if isinstance(raw, list):
        return raw, raw
    raise ValueError("docs/data.json has unexpected format")


def save_inventory(items):
    raw = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "items" in raw:
        raw["items"] = items
        DATA_JSON.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    else:
        DATA_JSON.write_text(
            json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
